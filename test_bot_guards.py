"""
Phase 6B: the bots never send twice. Hermetic: fake channels, temp state files,
subprocess lock holders; no Discord connection.
"""

import asyncio
import datetime as real_datetime
import os
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

    JOBS = {"bot_sakura.py": ["post_daily_briefing_if_due", "post_evening_rollup_if_due", "post_job_hunt_if_due"],
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


def _owner_is(owner_id):
    """A Bot.is_owner stand-in: async, compares the user's id."""
    async def is_owner(user):
        return user.id == owner_id
    return is_owner


class OwnerGuardTest(unittest.TestCase):
    """LSFM B2: anyone in the server could run !commit, !pr, !clean, !mode, !hunt and approve cards."""

    BOTS = ("sakura", "chaewon", "kazuha", "yunjin", "eunchae")

    def test_every_bot_installs_the_guard_on_commands_and_chat(self):
        for name in self.BOTS:
            with self.subTest(bot=name):
                src = (Path(__file__).parent / f"bot_{name}.py").read_text(encoding="utf-8")
                self.assertIn("owner_guard.install(bot)", src)
                self.assertIn("if message.author.bot or not await owner_guard.allowed(bot, message.author):", src)

    def test_install_adds_an_owner_check_and_reads_the_override(self):
        import owner_guard
        checks = []
        fake = types.SimpleNamespace(owner_id=None, add_check=checks.append, is_owner=_owner_is(42))
        with mock.patch.dict(os.environ, {"DISCORD_OWNER_ID": "42"}):
            owner_guard.install(fake)
        self.assertEqual(fake.owner_id, 42)
        ctx = lambda uid: types.SimpleNamespace(author=types.SimpleNamespace(id=uid))
        self.assertEqual((asyncio.run(checks[0](ctx(42))), asyncio.run(checks[0](ctx(5)))), (True, False))
        self.assertEqual((asyncio.run(owner_guard.allowed(fake, 42)), asyncio.run(owner_guard.allowed(fake, 5))),
                         (True, False))
        with mock.patch.dict(os.environ, {"DISCORD_OWNER_ID": ""}):
            other = types.SimpleNamespace(owner_id=None, add_check=checks.append)
            owner_guard.install(other)
        self.assertIsNone(other.owner_id)  # no override: discord.py asks Discord for the app owner

    def test_a_stranger_in_chat_is_ignored(self):
        processed = []

        async def process_commands(message):
            processed.append(message.content)
        fake_bot = types.SimpleNamespace(user=types.SimpleNamespace(id=1), is_owner=_owner_is(7),
                                         process_commands=process_commands)
        for author_id, expected in ((99, []), (7, ["!clean"])):
            message = types.SimpleNamespace(author=types.SimpleNamespace(id=author_id, bot=False), content="!clean")
            with mock.patch.object(bot_sakura, "bot", fake_bot):
                asyncio.run(bot_sakura.on_message(message))
            self.assertEqual(processed, expected)


class FakeCardMessage:
    _next = 100

    def __init__(self, content=None, embed=None, files=None):
        FakeCardMessage._next += 1
        self.id, self.content, self.embeds, self.files = FakeCardMessage._next, content, [embed] if embed else [], files or []
        self.reactions, self.edits = [], []

    async def add_reaction(self, emoji):
        self.reactions.append(emoji)

    async def edit(self, embed=None, **kwargs):
        self.edits.append(embed)


class FakeCardChannel:
    name = "approvals"

    def __init__(self):
        self.messages = []

    async def send(self, content=None, embed=None, files=None, **kwargs):
        msg = FakeCardMessage(content, embed, files)
        self.messages.append(msg)
        return msg

    async def fetch_message(self, msg_id):
        return next(m for m in self.messages if m.id == msg_id)


class JobHuntBotTest(unittest.TestCase):
    def setUp(self):
        # Hermetic: deliver_job_hunt scans JOB_HUNT_DIR for uncarded jobs; never the live job-hunt folder.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patch = mock.patch.object(bot_sakura.job_hunt, "JOB_HUNT_DIR", Path(tmp.name))
        patch.start()
        self.addCleanup(patch.stop)

    def folder(self, root, name, status="queued"):
        d = Path(root) / name
        d.mkdir()
        bot_sakura.job_hunt.write_meta(d, {"id": name, "company": "Acme", "role": "Junior AI Engineer", "site": "LinkedIn",
                                           "posted": "2026-10-15", "fit": 80, "why": "Fits.", "salary": "", "thin": False,
                                           "status": status, "card_msg_id": 0})
        return d

    def meta(self, d):
        import json
        return json.loads((d / "meta.json").read_text(encoding="utf-8"))

    def test_slots(self):  # Review Focus 4
        at = lambda h, m=0: bot_sakura.job_hunt_slot(real_datetime.datetime(2026, 10, 15, h, m))
        self.assertIsNone(at(9, 59))
        self.assertEqual((at(10, 20), at(19, 59)), ("2026-10-15-am", "2026-10-15-am"))
        self.assertEqual((at(20), at(21, 30)), ("2026-10-15-pm", "2026-10-15-pm"))

    def tick(self, state_file, now, result, channel):
        with mock.patch.object(bot_sakura, "JOB_HUNT_SLOT_FILE", state_file), \
                mock.patch.object(bot_sakura, "datetime", fake_datetime_module(now)), \
                mock.patch.object(bot_sakura, "first_channel", return_value=channel), \
                mock.patch.object(bot_sakura, "run_skill", return_value=result) as run:
            asyncio.run(bot_sakura.post_job_hunt_if_due())
        return run

    def test_late_evening_runs_pm_once(self):
        from skills import SkillResult
        done = SkillResult("done", "ok", {"jobs": [], "skipped": [], "failed_sources": [], "slot": "x"})
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "slot.txt"
            state.write_text("2026-10-15-am", encoding="utf-8")
            channel = FakeCardChannel()
            run = self.tick(state, real_datetime.datetime(2026, 10, 15, 21, 30), done, channel)
            self.assertEqual(run.call_args.args, ("chaewon.hunt", {"slot": "2026-10-15-pm"}))
            run2 = self.tick(state, real_datetime.datetime(2026, 10, 15, 21, 45), done, channel)
            run2.assert_not_called()
            self.assertEqual(state.read_text(encoding="utf-8"), "2026-10-15-pm")
            self.assertEqual(len(channel.messages), 1)  # the header only: no jobs

    def test_failed_hunt_claims_nothing(self):
        from skills import SkillResult
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "slot.txt"
            channel = FakeCardChannel()
            self.tick(state, real_datetime.datetime(2026, 10, 15, 10, 5), SkillResult("failed", "x"), channel)
            self.assertFalse(state.exists())
            self.assertEqual(channel.messages, [])

    def test_deliver_posts_header_and_one_card_per_job(self):
        from skills import SkillResult
        with tempfile.TemporaryDirectory() as tmp:
            a, b = self.folder(tmp, "a"), self.folder(tmp, "b", status="needs_claude")
            (a / "Hans_Aaron_Laureles_Resume.pdf").write_bytes(b"%PDF")
            result = SkillResult("done", "ok", {"jobs": [str(a), str(b)], "skipped": [{}], "failed_sources": [], "slot": "s"})
            channel = FakeCardChannel()
            with mock.patch.object(bot_sakura, "save_pending_proposal") as save, \
                    mock.patch.object(bot_sakura, "ObsidianClient", create=True):
                asyncio.run(bot_sakura.deliver_job_hunt(channel, result))
            self.assertEqual(len(channel.messages), 3)
            self.assertIn("2 ready for review, 1 skipped", channel.messages[0].content)
            for msg in channel.messages[1:]:
                self.assertEqual(msg.reactions, ["✅", "❌"])
            self.assertEqual(save.call_args_list[0].args[1], {"type": "job_hunt", "folder": str(a)})
            self.assertEqual(self.meta(a)["card_msg_id"], channel.messages[1].id)
            self.assertEqual(len(channel.messages[1].files), 1)
            self.assertIn("tries again on the next hunt", channel.messages[2].embeds[0].description)
            self.assertNotIn("Claude", channel.messages[0].content + channel.messages[2].embeds[0].description)

    def test_card_shows_gaps_warnings_and_the_last_retry(self):
        meta = {"status": "queued", "why": "Python agents match.", "gaps": ["3 years of SQL"],
                "warnings": ["'data pipelines' - not in the facts"], "id": "x"}
        text = bot_sakura.build_job_card(meta).description
        self.assertIn("**Gaps:** 3 years of SQL", text)
        self.assertIn("Check before sending:** 'data pipelines'", text)
        spent = bot_sakura.build_job_card({"status": "needs_claude", "retries": bot_sakura.job_hunt.MAX_RETRIES})
        self.assertIn("paste the posting in #job-tailoring", spent.description)

    def test_busy_hunt_leaves_the_slot_unclaimed(self):  # Codex F1, through the real run_skill and hunt
        with tempfile.TemporaryDirectory() as tmp:
            state, channel = Path(tmp) / "slot.txt", FakeCardChannel()
            with bot_sakura.job_hunt.HUNT_LOCK, \
                    mock.patch.object(bot_sakura, "JOB_HUNT_SLOT_FILE", state), \
                    mock.patch.object(bot_sakura, "datetime", fake_datetime_module(real_datetime.datetime(2026, 10, 15, 10, 5))), \
                    mock.patch.object(bot_sakura, "first_channel", return_value=channel):
                asyncio.run(bot_sakura.post_job_hunt_if_due())
            self.assertFalse(state.exists())
            self.assertEqual(channel.messages, [])

    def test_timeout_cancels_the_hunt_and_claims_nothing(self):  # Codex F2
        async def too_slow(*a, **k):
            raise TimeoutError("job hunt timed out after 660 s")

        bot_sakura.job_hunt.CANCEL.clear()
        try:
            with mock.patch.object(bot_sakura, "run_blocking", side_effect=too_slow):
                self.assertIsNone(asyncio.run(bot_sakura.prepare_job_hunt("2026-10-15-am")))
            self.assertTrue(bot_sakura.job_hunt.CANCEL.is_set())
        finally:
            bot_sakura.job_hunt.CANCEL.clear()

    def test_an_unopenable_pdf_is_left_off_and_the_other_is_closed(self):  # Codex F3
        from skills import SkillResult
        with tempfile.TemporaryDirectory() as tmp:
            a = self.folder(tmp, "a")
            (a / "Hans_Aaron_Laureles_Resume.pdf").write_bytes(b"%PDF")
            (a / "Hans_Aaron_Laureles_Cover_Letter.pdf").write_bytes(b"%PDF")
            opened = []

            def file_or_locked(path, filename=None):
                if "Cover" in path:
                    raise PermissionError("locked by another process")
                f = mock.MagicMock()
                opened.append(f)
                return f

            channel = FakeCardChannel()
            result = SkillResult("done", "ok", {"jobs": [str(a)], "skipped": [], "failed_sources": [], "slot": "s"})
            with mock.patch.object(bot_sakura.discord, "File", side_effect=file_or_locked), \
                    mock.patch.object(bot_sakura, "save_pending_proposal"), \
                    mock.patch.object(bot_sakura, "ObsidianClient", create=True), \
                    mock.patch.object(bot_sakura.job_hunt, "uncarded_folders", return_value=[]):
                asyncio.run(bot_sakura.deliver_job_hunt(channel, result))
            self.assertEqual(len(channel.messages[1].files), 1)
            opened[0].close.assert_called_once()

    def test_deliver_also_cards_jobs_a_cancelled_run_left(self):  # Codex F2: finished before a timeout, never carded
        from skills import SkillResult
        with tempfile.TemporaryDirectory() as tmp:
            a, b = self.folder(tmp, "a"), self.folder(tmp, "b")
            result = SkillResult("done", "ok", {"jobs": [str(a)], "skipped": [], "failed_sources": [], "slot": "s"})
            channel = FakeCardChannel()
            with mock.patch.object(bot_sakura, "save_pending_proposal"), \
                    mock.patch.object(bot_sakura, "ObsidianClient", create=True), \
                    mock.patch.object(bot_sakura.job_hunt, "uncarded_folders", return_value=[str(a), str(b)]):
                asyncio.run(bot_sakura.deliver_job_hunt(channel, result))
            self.assertEqual(len(channel.messages), 3)  # header + a + b, a only once
            self.assertTrue(self.meta(b)["card_msg_id"])

    def test_apply_job_reaction(self):  # Review Focus 3
        with tempfile.TemporaryDirectory() as tmp:
            cases = [("queued", "✅", "approved"), ("queued", "❌", "rejected"), ("rejected", "✅", "approved"),
                     ("approved", "❌", "rejected"), ("needs_claude", "❌", "rejected"), ("ready", "❌", "withdrawn"),
                     ("applied", "❌", None), ("ready", "✅", None)]
            for i, (before, emoji, after) in enumerate(cases):
                with self.subTest(f"{before} {emoji}"):
                    d = self.folder(tmp, f"j{i}", status=before)
                    self.assertEqual(bot_sakura.apply_job_reaction(d, emoji), after)
                    self.assertEqual(self.meta(d)["status"], after or before)

    def test_job_reaction_skips_apply_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self.folder(tmp, "a")
            channel = FakeCardChannel()
            card = asyncio.run(channel.send(embed=bot_sakura.build_job_card(self.meta(d))))
            payload = types.SimpleNamespace(user_id=7, emoji="❌", message_id=card.id, channel_id=1)
            fake_bot = types.SimpleNamespace(user=types.SimpleNamespace(id=1), get_channel=lambda _id: channel,
                                             is_owner=_owner_is(7))
            with mock.patch.object(bot_sakura, "bot", fake_bot), \
                    mock.patch.object(bot_sakura, "get_pending_proposal", return_value={"type": "job_hunt", "folder": str(d)}), \
                    mock.patch.object(bot_sakura, "mark_proposal_status") as mark, \
                    mock.patch.object(bot_sakura, "analyze_and_tailor") as tailor:
                asyncio.run(bot_sakura.on_raw_reaction_add(payload))
            mark.assert_not_called()
            tailor.assert_not_called()
            self.assertEqual(self.meta(d)["status"], "rejected")
            self.assertIn("Rejected", card.edits[0].footer.text)
            self.assertIn("· a", card.edits[0].footer.text)  # the job id survives the footer edit

    def test_only_the_owner_can_approve_or_skip_a_card(self):  # LSFM B2
        with tempfile.TemporaryDirectory() as tmp:
            d = self.folder(tmp, "a")
            channel = FakeCardChannel()
            card = asyncio.run(channel.send(embed=bot_sakura.build_job_card(self.meta(d))))
            fake_bot = types.SimpleNamespace(user=types.SimpleNamespace(id=1), get_channel=lambda _id: channel,
                                             is_owner=_owner_is(7))
            with mock.patch.object(bot_sakura, "bot", fake_bot), \
                    mock.patch.object(bot_sakura, "get_pending_proposal", return_value={"type": "job_hunt", "folder": str(d)}):
                asyncio.run(bot_sakura.on_raw_reaction_add(
                    types.SimpleNamespace(user_id=99, emoji="✅", message_id=card.id, channel_id=1)))
            self.assertEqual(self.meta(d)["status"], "queued")
            self.assertEqual(card.edits, [])

    def test_the_card_shows_the_cover_letter(self):  # LSFM B2: ✅ after reading the letter, not blind
        import json
        with tempfile.TemporaryDirectory() as tmp:
            d = self.folder(tmp, "a")
            (d / "tailoring.json").write_text(json.dumps({"cover_paragraphs": ["Dear Acme,", "I build agents."]}),
                                              encoding="utf-8-sig")
            embed = bot_sakura.build_job_card(self.meta(d), bot_sakura._read_letter(d))
            self.assertIn("**Cover letter**\nDear Acme,\n\nI build agents.", embed.description)
            long = bot_sakura.build_job_card(self.meta(d), ["word " * 2000])
            self.assertLessEqual(len(long.description), 4096)
            self.assertTrue(long.description.endswith("(full text in the PDF)"))
            self.assertEqual(bot_sakura._read_letter(Path(tmp) / "missing"), [])

    def test_reaction_on_a_deleted_job_folder_changes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(bot_sakura.apply_job_reaction(Path(tmp) / "gone", "✅"))

    def test_one_failed_card_does_not_stop_the_rest(self):
        from skills import SkillResult
        with tempfile.TemporaryDirectory() as tmp:
            a, b, c = self.folder(tmp, "a"), self.folder(tmp, "b"), self.folder(tmp, "c")
            (b / "meta.json").write_text("{broken", encoding="utf-8")
            result = SkillResult("done", "ok", {"jobs": [str(a), str(b), str(c)], "skipped": [], "failed_sources": [],
                                                "slot": "s"})
            channel = FakeCardChannel()
            with mock.patch.object(bot_sakura, "save_pending_proposal"), \
                    mock.patch.object(bot_sakura, "ObsidianClient", create=True), \
                    mock.patch.object(bot_sakura.job_hunt, "uncarded_folders", return_value=[]):
                asyncio.run(bot_sakura.deliver_job_hunt(channel, result))
            self.assertEqual(len(channel.messages), 4)  # header, a, c, the not-sent notice
            self.assertTrue(self.meta(a)["card_msg_id"] and self.meta(c)["card_msg_id"])
            self.assertIn("could not be sent", channel.messages[3].content)
            self.assertIn("b", channel.messages[3].content)

    def test_card_id_keeps_a_status_the_claude_run_wrote_meanwhile(self):
        from skills import SkillResult
        with tempfile.TemporaryDirectory() as tmp:
            a = self.folder(tmp, "a")

            def claude_run_marks_ready(*_):  # runs between the upload and the card id write
                meta = self.meta(a)
                meta["status"] = "ready"
                bot_sakura.job_hunt.write_meta(a, meta)

            result = SkillResult("done", "ok", {"jobs": [str(a)], "skipped": [], "failed_sources": [], "slot": "s"})
            channel = FakeCardChannel()
            with mock.patch.object(bot_sakura, "save_pending_proposal", side_effect=claude_run_marks_ready), \
                    mock.patch.object(bot_sakura, "ObsidianClient", create=True), \
                    mock.patch.object(bot_sakura.job_hunt, "uncarded_folders", return_value=[]):
                asyncio.run(bot_sakura.deliver_job_hunt(channel, result))
            self.assertEqual(self.meta(a)["status"], "ready")
            self.assertEqual(self.meta(a)["card_msg_id"], channel.messages[1].id)


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
            run_all.prepare_start(marker=Path(self.lock.parent) / "bots.stopped")

    # ADR-001 R-1: the LSFM Bots task re-runs run_all.py every 5 minutes, so a refused or
    # paused start must leave bots.log alone, and stop_squad.bat must keep the bots stopped.

    def entry(self, *, console: bool, lock_refused: bool = False, while_locking=None):
        import sys
        import run_all
        log_path = self.lock.parent / "bots.log"
        marker = self.lock.parent / "bots.stopped"
        lock = mock.Mock(side_effect=while_locking or (SystemExit(1) if lock_refused else None))

        async def no_bots():
            return None
        with mock.patch.object(run_all, "require_single_instance", lock), \
                mock.patch.object(run_all, "main", no_bots), \
                mock.patch.object(sys, "stdout", sys.stdout if console else None), \
                mock.patch.object(sys, "stderr", sys.stderr if console else None):
            try:
                run_all.entry(log_path=log_path, marker=marker)
                code = None
            except SystemExit as e:
                code = e.code
        return code, lock, log_path, marker

    def test_second_start_does_not_touch_log(self):
        code, lock, log_path, _ = self.entry(console=False, lock_refused=True)
        self.assertEqual(code, 1)
        self.assertFalse(log_path.exists())

    def test_scheduled_start_respects_stop_marker(self):
        (self.lock.parent / "bots.stopped").write_text("2026-10-12T20:00:00", encoding="utf-8")
        code, lock, log_path, marker = self.entry(console=False)
        self.assertEqual(code, 0)
        lock.assert_not_called()
        self.assertFalse(log_path.exists())
        self.assertTrue(marker.exists())

    def test_stay_stopped_during_a_scheduled_start_wins(self):
        # Codex R-1 fix 1: stop_squad.bat writes the marker while a scheduled start is between
        # its early marker check and the lock. The start must still back off once it holds the lock.
        marker = self.lock.parent / "bots.stopped"

        def stop_squad_writes_marker():
            marker.write_text("2026-10-12T20:00:00", encoding="utf-8")
        code, lock, log_path, _ = self.entry(console=False, while_locking=stop_squad_writes_marker)
        self.assertEqual(code, 0)
        lock.assert_called_once()
        self.assertFalse(log_path.exists())

    def test_stay_stopped_writes_marker_before_stopping(self):
        # Codex R-1 fix 1: a scheduled start that takes the lock the moment the old process dies
        # must already see the marker.
        marker = self.lock.parent / "bots.stopped"
        seen = []
        with mock.patch.object(self.lk, "stop", lambda lock_file, pid_file: seen.append(marker.exists()) or "stopped PID 1"):
            self.lk.stop_on_purpose(self.lock, self.pid, marker)
        self.assertEqual(seen, [True])

    def test_console_start_clears_stop_marker(self):
        (self.lock.parent / "bots.stopped").write_text("2026-10-12T20:00:00", encoding="utf-8")
        code, lock, _, marker = self.entry(console=True)
        self.assertIsNone(code)
        lock.assert_called_once()
        self.assertFalse(marker.exists())

    def test_stop_command_pauses_scheduled_restarts(self):
        marker = self.lock.parent / "bots.stopped"
        result = self.lk.stop_on_purpose(self.lock, self.pid, marker)
        self.assertTrue(result.startswith("not running"), result)
        self.assertIn("start_squad.bat", result)
        self.assertTrue(marker.exists())

    def test_plain_stop_leaves_scheduled_restarts_on(self):
        # The restart recipe (HANDOFF §1) is `instance_lock.py stop`, then Start-ScheduledTask:
        # only stop_squad.bat (--stay-stopped) may keep the task's starts away.
        marker = self.lock.parent / "bots.stopped"
        paths = dict(lock_file=self.lock, pid_file=self.pid, marker=marker)
        self.assertEqual(self.lk.cli(["stop"], **paths), "not running")
        self.assertFalse(marker.exists())
        self.assertIn("start_squad.bat", self.lk.cli(["stop", "--stay-stopped"], **paths))
        self.assertTrue(marker.exists())
        with self.assertRaises(SystemExit):
            self.lk.cli(["stopp"], **paths)

    def test_stop_squad_stays_stopped(self):
        text = (Path(__file__).resolve().parent / "stop_squad.bat").read_text(encoding="utf-8")
        self.assertIn('instance_lock.py" stop --stay-stopped', text)

    def test_stop_marker_is_git_ignored(self):
        import subprocess
        root = Path(__file__).resolve().parent
        rel = self.lk.STOP_MARKER.relative_to(root).as_posix()
        rc = subprocess.run(["git", "check-ignore", "-q", rel], cwd=root,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
        if rc == 128:  # not a git checkout (an export folder): the rule must still be there
            self.assertIn(rel, (root / ".gitignore").read_text(encoding="utf-8").splitlines())
        else:
            self.assertEqual(rc, 0, rel)

    @unittest.skipUnless((Path(__file__).resolve().parent / "scripts" / "register_bot_task.ps1").exists(),
                         "private task script: not in the public export")
    def test_logon_task_repeats_every_5_minutes(self):
        text = (Path(__file__).resolve().parent / "scripts" / "register_bot_task.ps1").read_text(encoding="utf-8")
        self.assertIn("-RepetitionInterval (New-TimeSpan -Minutes 5)", text)
        self.assertIn("-MultipleInstances IgnoreNew", text)  # a running bot process is never started twice

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


class FakeMessage:
    def __init__(self):
        self.edits = []

    async def edit(self, **kwargs):
        self.edits.append(kwargs)

    async def delete(self):
        pass


class FakeCtx:
    """Enough of a discord.py Context for a command callback: replies are recorded."""

    def __init__(self):
        self.replies = []

    async def reply(self, content=None, **kwargs):
        self.replies.append({"content": content, **kwargs})
        return FakeMessage()


class RecordingCtx(FakeCtx):
    """FakeCtx that keeps each status message, so a test can read its edits."""

    def __init__(self):
        super().__init__()
        self.messages = []

    async def reply(self, content=None, **kwargs):
        await super().reply(content, **kwargs)
        self.messages.append(FakeMessage())
        return self.messages[-1]

    def edited_text(self):
        return " ".join(str(e.get("content", "")) for m in self.messages for e in m.edits)


class ConfirmMessage:
    _next = 900

    def __init__(self, content=None):
        ConfirmMessage._next += 1
        self.id, self.content, self.reactions, self.edits = ConfirmMessage._next, content, [], []

    async def add_reaction(self, emoji):
        self.reactions.append(emoji)

    async def edit(self, **kwargs):
        self.edits.append(kwargs.get("content"))

    async def delete(self):
        pass


class ConfirmCtx:
    """A command context with an author, replies that can be reacted to, and typing()."""

    def __init__(self, author_id=7):
        self.author = types.SimpleNamespace(id=author_id)
        self.messages = []

    async def reply(self, content=None, **kwargs):
        self.messages.append(ConfirmMessage(content))
        return self.messages[-1]

    def typing(self):
        class _Typing:
            async def __aenter__(self_inner):
                return None

            async def __aexit__(self_inner, *exc):
                return False
        return _Typing()

    def texts(self):
        return [m.content for m in self.messages] + [e for m in self.messages for e in m.edits]


def fake_bot(reactions):
    """bot.wait_for that offers each (emoji, user_id) to the check on the latest message, then times out."""
    async def wait_for(event, timeout=None, check=None):
        assert event == "reaction_add"
        for emoji, user_id in reactions:
            target = fake_bot.ctx.messages[-1]
            reaction = types.SimpleNamespace(emoji=emoji, message=types.SimpleNamespace(id=target.id))
            user = types.SimpleNamespace(id=user_id)
            if check(reaction, user):
                return reaction, user
        raise asyncio.TimeoutError
    return types.SimpleNamespace(wait_for=wait_for)


class ConfirmActionTest(unittest.TestCase):
    """ADR-001 R4: bulk actions wait 60 s for the command author's ✅/❌."""

    def ask(self, reactions):
        from discord_utils import confirm_action
        ctx = ConfirmCtx(author_id=7)
        fake_bot.ctx = ctx
        ok = asyncio.run(confirm_action(fake_bot(reactions), ctx, "Archive up to 50 promo and newsletter emails from Primary?"))
        return ok, ctx

    def test_confirm_action_ignores_other_users_and_times_out(self):
        ok, ctx = self.ask([("✅", 99)])  # someone else's ✅
        self.assertIs(ok, False)
        self.assertEqual(ctx.messages[0].reactions, ["✅", "❌"])
        self.assertIn("No answer in 60 s. Nothing was changed.", ctx.texts())

    def test_check_mark_from_the_author_confirms(self):
        ok, ctx = self.ask([("👍", 7), ("✅", 7)])
        self.assertIs(ok, True)

    def test_cross_cancels(self):
        ok, ctx = self.ask([("❌", 7)])
        self.assertIs(ok, False)
        self.assertIn("Cancelled. Nothing was changed.", ctx.texts())


class SakuraConfirmTest(unittest.TestCase):
    def run_clean(self, reactions):
        import skills
        import gmail_engine
        sweeps = []
        ctx = ConfirmCtx(author_id=7)
        fake_bot.ctx = ctx
        with mock.patch.object(bot_sakura, "bot", fake_bot(reactions)), \
                mock.patch.object(gmail_engine, "is_configured", return_value=True), \
                mock.patch.object(skills, "gmail_is_configured", lambda: True), \
                mock.patch.object(skills, "sweep_inbox", lambda n: sweeps.append(n) or {
                    "success": True, "total_scanned": 40, "promos_swept": 12, "newsletters_swept": 3,
                    "total_swept": 15, "retained_in_inbox": 25, "categories": {}}):
            asyncio.run(bot_sakura.clean_command.callback(ctx))
        return sweeps, ctx

    def test_clean_runs_only_after_check_mark(self):
        sweeps, ctx = self.run_clean([])  # no answer
        self.assertEqual(sweeps, [])
        sweeps, ctx = self.run_clean([("❌", 7)])
        self.assertEqual(sweeps, [])
        sweeps, ctx = self.run_clean([("✅", 7)])
        self.assertEqual(sweeps, [50])


class ChaewonScheduledRebuildTest(unittest.TestCase):
    def test_scheduled_resume_rebuild_does_not_ask(self):  # Review Focus 1
        with mock.patch.dict("os.environ", {"CHAEWON_BOT_TOKEN": os.environ.get("CHAEWON_BOT_TOKEN") or "test-placeholder"}):
            import bot_chaewon
        import skills
        builds = []
        asked = mock.AsyncMock(side_effect=AssertionError("the Sunday job must not ask"))
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(bot_chaewon, "LAST_REBUILD_DATE_FILE", Path(tmp) / "last.txt"), \
                mock.patch.object(bot_chaewon, "bot", types.SimpleNamespace(guilds=[])), \
                mock.patch.object(bot_chaewon, "confirm_action", asked), \
                mock.patch.object(skills, "compile_master_resume", lambda **k: builds.append(k) or {
                    "success": True, "pdf_path": Path("r.pdf"), "size_kb": 82.9}):
            asyncio.run(bot_chaewon.post_weekly_resume_rebuild_if_due(force=True))
        self.assertEqual(len(builds), 1)
        asked.assert_not_called()


class ChaewonCommandsTest(unittest.TestCase):
    """ADR-001 R3: !rebuild_resume and !scout go through the skill registry and keep their embeds."""

    def setUp(self):
        # bot_chaewon exits at import without a token (CI and worktrees have no .env); this placeholder never connects.
        with mock.patch.dict("os.environ", {"CHAEWON_BOT_TOKEN": os.environ.get("CHAEWON_BOT_TOKEN") or "test-placeholder"}):
            import bot_chaewon
        import skills
        self.bot, self.skills = bot_chaewon, skills

    def patch(self, name, value):
        p = mock.patch.object(self.skills, name, value)
        p.start()
        self.addCleanup(p.stop)

    def approve(self, answer=True):
        p = mock.patch.object(self.bot, "confirm_action", mock.AsyncMock(return_value=answer))
        p.start()
        self.addCleanup(p.stop)

    def test_rebuild_resume_reports_the_skill_failure(self):
        self.approve()
        self.patch("compile_master_resume", lambda **k: {"success": False, "error": "Edge not found"})
        ctx = RecordingCtx()
        asyncio.run(self.bot.cmd_rebuild_resume.callback(ctx))
        self.assertIn("Edge not found", ctx.edited_text())

    def test_rebuild_resume_shares_the_build_lock_with_ciel(self):
        self.approve()
        calls = []
        self.patch("compile_master_resume", lambda **k: calls.append(k) or {"success": True})
        ctx = RecordingCtx()
        with self.skills.RESUME_BUILD_LOCK:  # a Ciel build is running
            asyncio.run(self.bot.cmd_rebuild_resume.callback(ctx))
        self.assertEqual(calls, [])
        self.assertIn("still running", ctx.edited_text())

    def test_rebuild_resume_asks_first_and_cross_builds_nothing(self):  # ADR-001 R4
        self.approve(False)
        calls = []
        self.patch("compile_master_resume", lambda **k: calls.append(k))
        asyncio.run(self.bot.cmd_rebuild_resume.callback(RecordingCtx()))
        self.assertEqual(calls, [])
        self.bot.confirm_action.assert_awaited_once()
        self.assertEqual(self.bot.confirm_action.await_args.args[2],
                         "Rebuild the master resume PDF and overwrite the portfolio and Obsidian copies?")

    def test_scout_uses_the_skill_and_keeps_the_embed(self):
        seen = {}

        async def jobs(query, location, limit):
            seen.update(query=query, location=location, limit=limit)
            return [{"title": "AI Engineer", "company": "ECLARO", "url": "https://x/1", "score": 88, "geo": "Remote",
                     "salary": "Competitive", "source": "LinkedIn"}]

        self.patch("fetch_live_remote_jobs", jobs)
        ctx = RecordingCtx()
        asyncio.run(self.bot.cmd_scout.callback(ctx, query="remote ai engineer"))
        self.assertEqual(seen, {"query": "remote ai engineer", "location": "Remote", "limit": 5})
        embed = ctx.replies[-1]["embed"]
        self.assertTrue(any("AI Engineer" in f.name for f in embed.fields), [f.name for f in embed.fields])

    def test_scout_reports_a_failed_skill(self):
        self.patch("fetch_live_remote_jobs", None)
        ctx = RecordingCtx()
        asyncio.run(self.bot.cmd_scout.callback(ctx, query="ai"))
        self.assertIn("could not be imported", ctx.edited_text())


class EunchaeCommandsTest(unittest.TestCase):
    """ADR-001 R1: !vitals and !test go through the skill registry."""

    def setUp(self):
        import bot_eunchae
        import skills
        self.bot, self.skills = bot_eunchae, skills

    def patch(self, name, value):
        p = mock.patch.object(self.skills, name, value)
        p.start()
        self.addCleanup(p.stop)

    def test_vitals_samples_off_the_event_loop(self):
        # A 0.5 s psutil sample on the shared loop stalled all five bots.
        import threading
        threads = []

        def vitals():
            threads.append(threading.current_thread())
            return {"cpu_pct": 12.0, "cpu_count": 12, "ram_pct": 50.0, "ram_used_gb": 8.0, "ram_total_gb": 15.8,
                    "disk_free_gb": 100.0, "disk_pct": 50.0, "uptime_str": "1h 2m", "healthy": True}
        self.patch("get_system_vitals", vitals)
        ctx = FakeCtx()
        asyncio.run(self.bot.vitals_command.callback(ctx))
        self.assertEqual(len(threads), 1)
        self.assertIsNot(threads[0], threading.main_thread())
        self.assertIn("Healthy", ctx.replies[-1]["embed"].title)

    def test_vitals_reports_a_failed_probe(self):
        self.patch("get_system_vitals", None)
        ctx = FakeCtx()
        asyncio.run(self.bot.vitals_command.callback(ctx))
        self.assertIn("could not be imported", ctx.replies[-1]["content"])

    def test_test_command_refuses_a_path_outside_the_workspace(self):
        calls = []
        self.patch("test_code_file", lambda p: calls.append(p) or {"success": True})
        ctx = FakeCtx()
        asyncio.run(self.bot.test_command.callback(ctx, filepath="C:/Windows/System32/x.py"))
        self.assertEqual(calls, [])
        self.assertTrue(any("relative" in (r["content"] or "") for r in ctx.replies), ctx.replies)


class ReminderDeliveryTest(unittest.TestCase):
    """Assistant B6: Sakura sends each due reminder once, as a DM, with a channel fallback."""

    def setUp(self):
        import reminders
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.enterContext(mock.patch.object(reminders, "STORE", Path(tmp.name) / "reminders.json"))
        self.reminders = reminders
        self.now = real_datetime.datetime(2026, 10, 26, 17, 0)

    def deliver(self, user, channel=None):
        async def owner():
            if isinstance(user, Exception):
                raise user
            return user
        with mock.patch.object(bot_sakura, "reminder_owner", owner), \
                mock.patch.object(bot_sakura, "first_channel", return_value=channel):
            asyncio.run(bot_sakura.deliver_due_reminders(self.now))

    def test_a_due_reminder_is_one_dm(self):
        self.reminders.add("call the bank", real_datetime.datetime(2026, 10, 26, 16, 58))
        self.reminders.add("later", real_datetime.datetime(2026, 10, 26, 18, 0))
        user = mock.MagicMock(mention="<@7>")
        user.send = mock.AsyncMock()
        self.deliver(user)
        self.deliver(user)  # the next minute's tick
        user.send.assert_awaited_once_with("⏰ **Reminder:** call the bank")

    def test_a_late_reminder_says_so(self):
        self.reminders.add("stretch", real_datetime.datetime(2026, 10, 26, 9, 0))
        user = mock.MagicMock()
        user.send = mock.AsyncMock()
        self.deliver(user)
        self.assertIn("sent late because the bots were not running", user.send.await_args.args[0])

    def test_a_refused_dm_goes_to_the_channel_and_a_total_failure_is_recorded(self):
        self.reminders.add("call the bank", real_datetime.datetime(2026, 10, 26, 16, 58))
        user = mock.MagicMock(mention="<@7>")
        user.send = mock.AsyncMock(side_effect=RuntimeError("Cannot send messages to this user"))
        channel = FakeChannel()
        self.deliver(user, channel)
        self.assertEqual(channel.sent, ["<@7> ⏰ **Reminder:** call the bank"])
        self.reminders.add("stretch", real_datetime.datetime(2026, 10, 26, 16, 59))
        self.deliver(RuntimeError("no app info"), None)
        import json
        saved = {i["text"]: i for i in json.loads(self.reminders.STORE.read_text(encoding="utf-8"))}
        self.assertEqual((saved["call the bank"]["status"], saved["stretch"]["status"]), ("sent", "failed"))
        self.assertIn("command-center", saved["stretch"]["reason"])


if __name__ == "__main__":
    unittest.main()
