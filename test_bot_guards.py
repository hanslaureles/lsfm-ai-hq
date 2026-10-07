"""
Phase 6B: the bots never send twice. Hermetic: fake channels, temp state files,
subprocess lock holders; no Discord connection.
"""

import asyncio
import datetime as real_datetime
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import bot_sakura

LONG_TEXT = ("A sentence for the briefing. " * 300).strip()  # > 3800 chars: two embed parts


class FakeChannel:
    """Records embed titles; the send with index fail_on (1-based) raises."""

    name = "daily-briefing"

    def __init__(self, fail_on=None):
        self.sent, self.fail_on, self.calls = [], fail_on, 0

    async def send(self, content=None, embed=None, **kwargs):
        self.calls += 1
        if self.calls == self.fail_on:
            raise RuntimeError("Discord 503")
        self.sent.append(embed.title if embed is not None else content)


def fake_datetime_module(now):
    class FakeDateTime(real_datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    class FakeDate(real_datetime.date):
        @classmethod
        def today(cls):
            return now.date()

    return types.SimpleNamespace(datetime=FakeDateTime, date=FakeDate, timedelta=real_datetime.timedelta)


class SakuraBriefingTest(unittest.TestCase):
    """The scheduled briefing, end to end through send_clean_embeds."""

    def run_check(self, channel, state_file, now):
        with mock.patch.object(bot_sakura, "bot", types.SimpleNamespace(guilds=[object()])), \
                mock.patch.object(bot_sakura, "find_channel_by_name", return_value=channel), \
                mock.patch.object(bot_sakura, "generate_morning_briefing", return_value=LONG_TEXT), \
                mock.patch.object(bot_sakura, "BRIEFING_DATE_FILE", state_file), \
                mock.patch.object(bot_sakura, "datetime", fake_datetime_module(now)):
            asyncio.run(bot_sakura.post_daily_briefing_if_due())

    def test_a_briefing_cut_off_at_part_2_is_not_sent_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "last_briefing_date.txt"
            channel = FakeChannel(fail_on=2)
            now = real_datetime.datetime(2026, 1, 5, 9, 0)
            self.run_check(channel, state, now)
            self.run_check(channel, state, now + real_datetime.timedelta(minutes=30))  # the next loop tick
        part_1 = [t for t in channel.sent if t and "(Part 1/" in t]
        self.assertEqual(len(part_1), 1, f"part 1 sent {len(part_1)} times: {channel.sent}")
        notices = [t for t in channel.sent if t and "stopped partway" in t]
        self.assertEqual(len(notices), 1, channel.sent)
        self.assertIn("!briefing", notices[0])

    def test_a_good_day_sends_every_part_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "last_briefing_date.txt"
            channel = FakeChannel()
            now = real_datetime.datetime(2026, 1, 5, 9, 0)
            self.run_check(channel, state, now)
            self.run_check(channel, state, now + real_datetime.timedelta(minutes=30))
            self.assertEqual(state.read_text(encoding="utf-8"), "2026-01-05")
        self.assertEqual([t[-10:] for t in channel.sent], ["(Part 1/3)", "(Part 2/3)", "(Part 3/3)"])

    def test_nothing_before_8(self):
        with tempfile.TemporaryDirectory() as tmp:
            channel = FakeChannel()
            self.run_check(channel, Path(tmp) / "s.txt", real_datetime.datetime(2026, 1, 5, 7, 59))
        self.assertEqual(channel.sent, [])


class RunDailyTest(unittest.TestCase):
    def setUp(self):
        import daily_once
        self.d = daily_once
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.state = Path(tmp.name) / "memory" / "last_x_date.txt"
        self.sent, self.notices = [], []

    def run_job(self, prepare, deliver=None, today="2026-01-05", state=None, due=None):
        async def default_deliver(x):
            self.sent.append(x)

        async def notify(text):
            self.notices.append(text)

        return asyncio.run(self.d.run_daily(state or self.state, today, "test job (!x)", prepare,
                                            deliver or default_deliver, notify=notify, due=due))

    @staticmethod
    def returns(value):
        async def prepare():
            return value
        return prepare

    def test_prepare_failure_claims_nothing_and_the_next_check_delivers_once(self):
        async def outage():
            raise TimeoutError("LLM down")
        self.assertEqual(self.run_job(outage), self.d.NOT_READY)
        self.assertFalse(self.state.exists())
        self.assertEqual(self.run_job(self.returns(None)), self.d.NOT_READY)  # None: not ready either
        self.assertEqual(self.run_job(self.returns("text")), self.d.DELIVERED)
        self.assertEqual(self.run_job(self.returns("text")), self.d.SKIPPED)
        self.assertEqual(self.sent, ["text"])
        self.assertEqual(self.state.read_text(encoding="utf-8"), "2026-01-05")

    def test_a_failed_delivery_is_claimed_noticed_once_and_never_resent(self):
        async def broken(x):
            self.sent.append(x)
            raise RuntimeError("Discord 503")
        self.assertEqual(self.run_job(self.returns("text"), broken), self.d.DELIVER_FAILED)
        self.assertEqual(self.run_job(self.returns("text"), broken), self.d.SKIPPED)
        self.assertEqual(self.sent, ["text"])
        self.assertEqual(len(self.notices), 1)
        self.assertIn("!x", self.notices[0])

    def test_no_claim_no_send(self):
        blocker = self.state.parent  # a file where the state folder should be
        blocker.write_text("not a folder", encoding="utf-8")
        self.assertEqual(self.run_job(self.returns("text")), self.d.CLAIM_FAILED)
        self.assertEqual(self.sent, [])

    def test_two_overlapping_runs_deliver_once(self):
        # Codex 6B B1: both passed the first check during prepare, then both claimed.
        async def both():
            gate = asyncio.Event()
            arrived = []

            async def prepare():
                arrived.append(1)
                if len(arrived) == 2:
                    gate.set()
                await gate.wait()  # both runs are past the first check before either claims
                return "text"

            async def deliver(x):
                self.sent.append(x)

            return await asyncio.gather(*(self.d.run_daily(self.state, "2026-01-05", "test job (!x)", prepare, deliver)
                                          for _ in range(2)))

        results = asyncio.run(both())
        self.assertEqual(sorted(results), sorted([self.d.DELIVERED, self.d.SKIPPED]))
        self.assertEqual(self.sent, ["text"])

    def test_due_rule_and_next_day(self):
        self.run_job(self.returns("a"))
        self.assertEqual(self.run_job(self.returns("b"), today="2026-01-06"), self.d.DELIVERED)
        self.assertEqual(self.run_job(self.returns("c"), today="2026-01-06", due=lambda last: True), self.d.DELIVERED)
        self.assertEqual(self.sent, ["a", "b", "c"])


class EveryJobUsesRunDailyTest(unittest.TestCase):
    """A scheduled job that writes its own date file again would bring the duplicates back."""

    JOBS = {"bot_sakura.py": ["post_daily_briefing_if_due", "post_evening_rollup_if_due"],
            "bot_eunchae.py": ["watchdog_loop"],
            "bot_kazuha.py": ["post_research_scout_if_due"],
            "bot_yunjin.py": ["post_portfolio_audit_if_due"],
            "bot_chaewon.py": ["post_weekly_resume_rebuild_if_due"]}

    def test_each_job_calls_run_daily_and_writes_no_date_file(self):
        import ast
        root = Path(__file__).resolve().parent
        for name, funcs in self.JOBS.items():
            tree = ast.parse((root / name).read_text(encoding="utf-8"))
            defs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)}
            for func in funcs:
                with self.subTest(f"{name}:{func}"):
                    calls = [n for n in ast.walk(defs[func]) if isinstance(n, ast.Call)]
                    names = {getattr(c.func, "id", None) or getattr(c.func, "attr", None) for c in calls}
                    self.assertIn("run_daily", names)
                    self.assertNotIn("write_text", names)


HOLD = (
    "import sys, time, instance_lock\n"
    "from pathlib import Path\n"
    "assert instance_lock.acquire(Path(sys.argv[1]), Path(sys.argv[2]))\n"
    "print('held', flush=True)\n"
    "time.sleep(60)\n"
)


class InstanceLockTest(unittest.TestCase):
    def setUp(self):
        import instance_lock
        self.lk = instance_lock
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.lock, self.pid = Path(tmp.name) / "bots.lock", Path(tmp.name) / "bots.pid"

    def holder(self, *extra):
        import subprocess
        import sys
        p = subprocess.Popen([sys.executable, "-c", HOLD, str(self.lock), str(self.pid), *extra],
                             cwd=Path(__file__).resolve().parent, stdout=subprocess.PIPE, text=True)
        self.addCleanup(lambda: (p.kill(), p.wait(), p.stdout.close()))
        self.assertEqual(p.stdout.readline().strip(), "held")
        return p

    def try_acquire(self):
        """acquire() in a fresh process (this one may already hold a lock from another test)."""
        import subprocess
        import sys
        code = "import sys, instance_lock\nfrom pathlib import Path\nprint(instance_lock.acquire(Path(sys.argv[1]), Path(sys.argv[2])))"
        out = subprocess.run([sys.executable, "-c", code, str(self.lock), str(self.pid)],
                             cwd=Path(__file__).resolve().parent, capture_output=True, text=True)
        return out.stdout.strip()

    def test_a_second_instance_is_refused_until_the_first_dies(self):
        p = self.holder()
        self.assertEqual(self.pid.read_text(encoding="utf-8"), str(p.pid))
        self.assertEqual(self.try_acquire(), "False")
        p.kill()
        p.wait()
        self.assertEqual(self.try_acquire(), "True")

    def test_require_single_instance_exits_1_with_the_holders_pid(self):
        import contextlib
        import io
        p = self.holder()
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as cm:
            self.lk.require_single_instance(self.lock, self.pid)
        self.assertEqual(cm.exception.code, 1)
        self.assertIn(f"PID {p.pid}", out.getvalue())

    def test_stop_kills_only_a_bot_holder(self):
        self.assertEqual(self.lk.stop(self.lock, self.pid), "not running")
        other = self.holder()  # holds the lock but its command line names no bot
        self.assertIn("nothing stopped", self.lk.stop(self.lock, self.pid))
        self.assertIsNone(other.poll())
        other.kill()
        other.wait()
        bot = self.holder("run_all.py")  # "run_all.py" in its command line
        self.assertIn(f"stopped PID {bot.pid}", self.lk.stop(self.lock, self.pid))
        self.assertIsNotNone(bot.wait(timeout=10))

    def test_stop_never_kills_a_process_that_does_not_hold_the_lock(self):
        # Codex 6B B3: a stale PID file naming another bot-looking process got that process killed.
        import subprocess
        import sys
        holder = self.holder("run_all.py")
        bystander = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", "run_all.py"])
        self.addCleanup(lambda: (bystander.kill(), bystander.wait()))
        self.pid.write_text(str(bystander.pid), encoding="utf-8")  # stale / wrong PID file
        self.assertIn("nothing stopped", self.lk.stop(self.lock, self.pid))
        self.assertIsNone(bystander.poll())
        self.assertIsNone(holder.poll())
        self.pid.write_text(str(holder.pid), encoding="utf-8")
        self.assertIn(f"stopped PID {holder.pid}", self.lk.stop(self.lock, self.pid))
        self.assertIsNotNone(holder.wait(timeout=10))
        self.assertIsNone(bystander.poll())

    def test_run_all_kills_nothing_and_refuses_a_second_start(self):
        import run_all
        self.assertFalse(hasattr(run_all, "enforce_single_instance"))
        self.assertNotIn("psutil", (Path(__file__).resolve().parent / "run_all.py").read_text(encoding="utf-8"))
        with mock.patch.object(run_all, "require_single_instance", side_effect=SystemExit(1)), \
                self.assertRaises(SystemExit):
            asyncio.run(run_all.main())

    def test_start_and_stop_scripts_kill_no_python_by_name(self):
        root = Path(__file__).resolve().parent
        for bat in ("start_squad.bat", "stop_squad.bat"):
            with self.subTest(bat):
                text = (root / bat).read_text(encoding="utf-8").lower()
                self.assertNotIn("/im python", text)
        self.assertIn("instance_lock.py\" stop", (root / "stop_squad.bat").read_text(encoding="utf-8"))

    def test_batch_files_are_plain_ascii(self):
        # cmd shows a .bat in the console's code page: a UTF-8 "—" became "ΓÇö" in the window title.
        root = Path(__file__).resolve().parent
        for bat in ("start_squad.bat", "stop_squad.bat"):
            with self.subTest(bat):
                self.assertTrue((root / bat).read_bytes().isascii())

    def test_echo_lines_escape_ampersands(self):
        # A bare & in echo starts a second command: "Freeing RAM & GPU" printed half and ran "GPU".
        import re
        root = Path(__file__).resolve().parent
        for bat in ("start_squad.bat", "stop_squad.bat"):
            for line in (root / bat).read_text(encoding="utf-8").splitlines():
                if line.lstrip().lower().startswith("echo"):
                    with self.subTest(bat=bat, line=line):
                        self.assertIsNone(re.search(r"(?<!\^)&", line))

    def test_without_a_console_output_goes_to_the_log_file(self):
        # 7A-3: the logon task runs pythonw (no window), where sys.stdout and sys.stderr are None.
        import sys
        import run_all
        with tempfile.TemporaryDirectory() as d:
            log_path = Path(d) / "bots.log"
            with mock.patch.object(sys, "stdout", None), mock.patch.object(sys, "stderr", None):
                log = run_all.log_to_file_without_console(log_path)
                print("⚠️ LSFM bots are already running (PID 1).")
                print("traceback line", file=sys.stderr)
            log.close()
            lines = log_path.read_text(encoding="utf-8").splitlines()
        self.assertRegex(lines[0], r"^===== \d{4}-\d\d-\d\d \d\d:\d\d:\d\d start \(no console\)$")
        self.assertEqual(lines[1:], ["⚠️ LSFM bots are already running (PID 1).", "traceback line"])

    def test_a_log_that_cannot_be_opened_never_stops_the_start(self):
        # Another process may hold bots.log (a cmd >> redirect locks it): write a per-process file.
        import os
        import sys
        import run_all
        with tempfile.TemporaryDirectory() as d:
            blocked = Path(d) / "bots.log"
            blocked.mkdir()  # opening a directory for append fails on Windows and Linux
            with mock.patch.object(sys, "stdout", None), mock.patch.object(sys, "stderr", None):
                log = run_all.log_to_file_without_console(blocked)
                print("still logged")
            log.close()
            text = (Path(d) / f"bots-{os.getpid()}.log").read_text(encoding="utf-8")
        self.assertTrue(text.endswith("still logged\n"))

    def test_with_a_console_nothing_is_redirected(self):
        import sys
        import run_all
        with tempfile.TemporaryDirectory() as d:
            before = sys.stdout
            self.assertIsNone(run_all.log_to_file_without_console(Path(d) / "bots.log"))
            self.assertIs(sys.stdout, before)
            self.assertFalse((Path(d) / "bots.log").exists())


if __name__ == "__main__":
    unittest.main()
