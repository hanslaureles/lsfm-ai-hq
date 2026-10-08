"""
Reliability and honesty regressions for the Discord, Obsidian, gateway,
sentinel and LLM-client code (Phases 3A, 3B).
Hermetic: temp vaults, local HTTP stubs or an Obsidian URL nothing listens on,
fake Discord targets, patched urlopen.
"""

import asyncio
import contextlib
import email.message
import http.server
import importlib.util
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

import discord

import bot_kazuha
import llm_client
import run_all
from discord_utils import send_clean_embeds, split_smart_chunks
from obsidian_client import VAULT_PATHS, ObsidianClient

ROOT = Path(__file__).resolve().parent


def _load_real(name):
    # Other test files put stubs in sys.modules at collection time (e.g.
    # test_ciel_security stubs proactive_sentinel), so load the real file by path.
    spec = importlib.util.spec_from_file_location(f"{name}_real", ROOT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


proactive_sentinel = _load_real("proactive_sentinel")

# Port 1 refuses at once, so every call takes the filesystem fallback.
OFFLINE_URL = "https://127.0.0.1:1"


class DailyLogTemplateTest(unittest.TestCase):
    def test_template_states_no_unchecked_status(self):
        with tempfile.TemporaryDirectory() as vault:
            client = ObsidianClient(base_url=OFFLINE_URL, vault_path=vault)
            rel = client.ensure_daily_log("2026-01-02")
            text = (Path(vault) / rel).read_text(encoding="utf-8")
        for claim in ("Nominal", "100/100", "5/5", "6/6", "verified", "standing by"):
            self.assertNotIn(claim, text)


class DiskAppendTest(unittest.TestCase):
    def test_concurrent_appends_all_land(self):
        threads_n, per_thread = 16, 25
        with tempfile.TemporaryDirectory() as vault:
            client = ObsidianClient(base_url=OFFLINE_URL, vault_path=vault)
            client.put_file("log.md", "")
            gate = threading.Barrier(threads_n)

            def worker(t):
                gate.wait()
                for i in range(per_thread):
                    self.assertTrue(client.append_file("log.md", f"entry-{t}-{i}\n"))

            threads = [threading.Thread(target=worker, args=(t,)) for t in range(threads_n)]
            for th in threads:
                th.start()
            for th in threads:
                th.join()
            lines = (Path(vault) / "log.md").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), threads_n * per_thread)
        self.assertEqual(len(set(lines)), threads_n * per_thread)


_HOLD_LOCK = (
    "import sys, time, obsidian_client as o\n"
    "with o._vault_lock(sys.argv[1]):\n"
    "    print('locked', flush=True)\n"
    "    time.sleep(30)\n"
)

_APPEND_MANY = (
    "import sys, obsidian_client as o\n"
    "c = o.ObsidianClient(base_url=sys.argv[2], vault_path=sys.argv[1])\n"
    "for i in range(50):\n"
    "    assert c.append_file('log.md', f'{sys.argv[3]}-{i}\\n')\n"
)


class VaultWriteSafetyTest(unittest.TestCase):
    """6A-1: a vault write never destroys a note and never lands twice."""

    def test_existing_daily_log_survives_a_failed_listing(self):
        # Before 6A-1 an empty listing (REST 404, wrong path) made ensure_daily_log
        # write the template over the day's log.
        with tempfile.TemporaryDirectory() as vault:
            client = ObsidianClient(base_url=OFFLINE_URL, vault_path=vault)
            log = Path(vault) / VAULT_PATHS["daily_logs"] / "2026-01-02.md"
            log.parent.mkdir()
            log.write_text("today's entries", encoding="utf-8")
            with mock.patch.object(client, "list_dir", return_value=[]):
                rel = client.ensure_daily_log("2026-01-02")
            self.assertEqual(rel, f"{VAULT_PATHS['daily_logs']}/2026-01-02.md")
            self.assertEqual(log.read_text(encoding="utf-8"), "today's entries")

    def test_create_file_never_overwrites(self):
        with tempfile.TemporaryDirectory() as vault:
            client = ObsidianClient(base_url=OFFLINE_URL, vault_path=vault)
            self.assertTrue(client.create_file("notes/a.md", "first"))
            self.assertFalse(client.create_file("notes/a.md", "second"))
            self.assertEqual((Path(vault) / "notes" / "a.md").read_text(encoding="utf-8"), "first")
            with self.assertRaises(ValueError):
                client.create_file("../outside.md", "x")

    def test_writes_never_go_through_rest(self):
        # A REST write that times out after it landed used to be retried on disk,
        # so the entry was written twice. Writes now go to disk only.
        seen = []

        class Recorder(_StubHandler):
            mode = "hang"

            def _reply(self):
                seen.append(self.command)
                super()._reply()

            do_GET = do_PUT = do_POST = _reply

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Recorder)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with tempfile.TemporaryDirectory() as vault:
            client = ObsidianClient(base_url=f"http://127.0.0.1:{server.server_address[1]}", vault_path=vault)
            self.assertTrue(client.is_rest_api_online())
            self.assertTrue(client.put_file("a.md", "one\n"))
            self.assertTrue(client.append_file("a.md", "two\n"))
            self.assertEqual((Path(vault) / "a.md").read_text(encoding="utf-8"), "one\ntwo\n")
        self.assertNotIn("PUT", seen)
        self.assertNotIn("POST", seen)

    def test_append_waits_for_another_process_and_gives_up_cleanly(self):
        import obsidian_client
        with tempfile.TemporaryDirectory() as vault:
            (Path(vault) / "log.md").write_text("kept\n", encoding="utf-8")
            holder = subprocess.Popen([sys.executable, "-c", _HOLD_LOCK, vault], cwd=ROOT,
                                      stdout=subprocess.PIPE, text=True)
            try:
                self.assertEqual(holder.stdout.readline().strip(), "locked")
                client = ObsidianClient(base_url=OFFLINE_URL, vault_path=vault)
                with mock.patch.object(obsidian_client, "LOCK_TIMEOUT_S", 0.3):
                    self.assertFalse(client.append_file("log.md", "lost?\n"))
                    self.assertFalse(client.put_file("log.md", "overwrite?\n"))
                self.assertEqual((Path(vault) / "log.md").read_text(encoding="utf-8"), "kept\n")
            finally:
                holder.kill()  # before the temp vault is removed: Windows keeps an open lock file
                holder.wait()
                holder.stdout.close()
            # The lock dies with its holder.
            self.assertTrue(client.append_file("log.md", "after\n"))

    def test_appends_from_separate_processes_all_land(self):
        with tempfile.TemporaryDirectory() as vault:
            procs = [subprocess.Popen([sys.executable, "-c", _APPEND_MANY, vault, OFFLINE_URL, f"p{n}"], cwd=ROOT)
                     for n in range(4)]
            for p in procs:
                self.assertEqual(p.wait(timeout=60), 0)
            lines = (Path(vault) / "log.md").read_text(encoding="utf-8").splitlines()
        self.assertEqual(sorted(lines), sorted(f"p{n}-{i}" for n in range(4) for i in range(50)))

    @contextlib.contextmanager
    def _scout_env(self):
        import obsidian_client
        kazuha = _load_real("kazuha_engine")
        papers = [{"title": "T", "url": "https://example.org", "authors": "A", "upvotes": 1, "summary": "S"}]
        with tempfile.TemporaryDirectory() as vault, \
                mock.patch.object(obsidian_client, "DEFAULT_VAULT_PATH", vault), \
                mock.patch.object(obsidian_client, "OBSIDIAN_BASE_URL", OFFLINE_URL), \
                mock.patch.object(kazuha, "fetch_latest_ai_papers", return_value=papers), \
                mock.patch.object(kazuha, "query_llm", return_value="digest"):
            yield kazuha, Path(vault)

    def test_research_digest_is_not_overwritten(self):
        with self._scout_env() as (kazuha, vault):
            first = kazuha.execute_research_scout()
            self.assertTrue(first["obsidian_synced"])
            note = vault / first["obsidian_note"]
            note.write_text("hand-edited", encoding="utf-8")
            second = kazuha.execute_research_scout()
            self.assertEqual(note.read_text(encoding="utf-8"), "hand-edited")
            self.assertTrue(second["obsidian_synced"])  # the digest is there and the run was logged

    def test_research_sync_reports_failed_writes(self):
        # Codex 6A-1 S5: "synced" only when the digest is on disk and the session was logged.
        import obsidian_client
        for method in ("create_file", "log_session"):  # digest write failed / session log failed
            with self.subTest(method), self._scout_env() as (kazuha, vault), \
                    mock.patch.object(obsidian_client.ObsidianClient, method, return_value=False):
                res = kazuha.execute_research_scout()
                self.assertFalse(res["obsidian_synced"])


class VaultPathMapTest(unittest.TestCase):
    """6A-2: one map names every vault location; the code reads through it."""

    FOLDER = re.compile(r"\b0[0-8] (- )?(Inbox|Projects|Research|Knowledge|Decisions|Daily Logs|"
                        r"Agent Documentation|Meeting Notes|Prompts|User|Agents|Rules|Resources|Hub)\b")

    def test_no_vault_folder_is_named_outside_the_map(self):
        found = []
        for path in [*ROOT.glob("*.py"), *ROOT.glob("scripts/*.py"), *ROOT.glob("bench/*.py")]:
            if path.name.startswith("test_"):
                continue
            text = path.read_text(encoding="utf-8")
            if path.name == "obsidian_client.py":
                text = re.sub(r"VAULT_PATHS = \{.*?\n\}", "", text, flags=re.S)
            found += [f"{path.relative_to(ROOT)}:{n}" for n, line in enumerate(text.splitlines(), 1)
                      if self.FOLDER.search(line)]
        self.assertEqual(found, [])

    def test_helpers_read_from_the_map(self):
        with tempfile.TemporaryDirectory() as vault:
            for key in ("learned_rules", "profile", "preferences"):
                note = Path(vault) / VAULT_PATHS[key]
                note.parent.mkdir(parents=True, exist_ok=True)
                note.write_text(key, encoding="utf-8")
            agent = Path(vault) / VAULT_PATHS["agents"] / "Kazuha.md"
            agent.parent.mkdir(parents=True)
            agent.write_text("kazuha", encoding="utf-8")
            client = ObsidianClient(base_url=OFFLINE_URL, vault_path=vault)
            self.assertEqual(client.get_learned_rules(), "learned_rules")
            self.assertEqual(client.get_agent_profile("kazuha"), "kazuha")
            self.assertEqual(client.ensure_daily_log("2026-01-02"), f"{VAULT_PATHS['daily_logs']}/2026-01-02.md")
            self.assertTrue((Path(vault) / VAULT_PATHS["daily_logs"] / "2026-01-02.md").is_file())


class FakeTarget:
    def __init__(self):
        self.embeds = []

    async def reply(self, embed):
        self.embeds.append(embed)

    async def send(self, embed):
        self.embeds.append(embed)


class EmbedLimitsTest(unittest.TestCase):
    def assert_within_limits(self, embeds):
        for e in embeds:
            self.assertLessEqual(len(e.title or ""), 256)
            self.assertLessEqual(len(e.description or ""), 4096)
            for f in e.fields:
                self.assertLessEqual(len(f.value), 1024)
            self.assertLessEqual(len(e), 6000)

    def test_long_title_and_citations_fit(self):
        target = FakeTarget()
        citations = [f"https://example.com/{'x' * 380}/{n}" for n in range(5)]
        asyncio.run(send_clean_embeds(target, "T" * 300, "word " * 2000, citations=citations))
        self.assertGreater(len(target.embeds), 1)  # multi-part, so the "(Part i/n)" suffix is exercised
        self.assert_within_limits(target.embeds)
        self.assertTrue(target.embeds[0].title.endswith(f"(Part 1/{len(target.embeds)})"))

    def test_short_input_unchanged(self):
        target = FakeTarget()
        asyncio.run(send_clean_embeds(target, "Title", "body", citations=["a", "b"]))
        self.assertEqual(target.embeds[0].title, "Title")
        self.assertEqual(target.embeds[0].fields[0].value, "a · b")


class FakeBot:
    """start() raises each error in turn; a 5th call means the loop never stopped."""

    def __init__(self, errors):
        self.errors = list(errors)
        self.calls = 0
        self.http = types.SimpleNamespace(connector=None)

    async def close(self):
        pass

    def clear(self):
        pass

    async def start(self, token):
        self.calls += 1
        if self.calls > 4:
            raise asyncio.CancelledError("retried past a fatal error")
        raise self.errors.pop(0)


class GatewayRetryTest(unittest.TestCase):
    def run_bot(self, bot):
        sleep = mock.AsyncMock()
        with mock.patch.object(run_all.asyncio, "sleep", sleep), mock.patch("builtins.print"):
            asyncio.run(run_all.run_bot_resilient(bot, "token", "Test"))
        return [c.args[0] for c in sleep.await_args_list]

    def test_bad_token_stops_after_transient_retries(self):
        bot = FakeBot([ConnectionError("a"), ConnectionError("b"),
                       discord.errors.LoginFailure("Improper token has been passed.")])
        delays = self.run_bot(bot)
        self.assertEqual(bot.calls, 3)
        self.assertEqual(len(delays), 2)

    def test_backoff_is_jittered_and_grows(self):
        bot = FakeBot([ConnectionError("a"), ConnectionError("b"),
                       discord.errors.LoginFailure("stop")])
        with mock.patch.object(run_all.random, "uniform", return_value=1.2):
            first, second = self.run_bot(bot)
        self.assertAlmostEqual(first, 3.0 * 1.2)
        self.assertAlmostEqual(second, 4.5 * 1.2)

    def test_healthy_session_resets_backoff(self):
        bot = FakeBot([ConnectionError("a"), ConnectionError("b"), ConnectionError("c"),
                       discord.errors.LoginFailure("stop")])
        # Session 3 ran 120 s before failing, so its retry starts from 3 s again.
        clock = iter([0, 1, 1, 2, 2, 122, 122, 123])
        with mock.patch.object(run_all, "monotonic", lambda: next(clock)):
            delays = self.run_bot(bot)
        self.assertEqual(len(delays), 3)
        self.assertTrue(2.4 <= delays[2] <= 3.6, delays)


class RetrySessionLeakTest(unittest.TestCase):
    """3B-6: a real discord.Client retried by run_bot_resilient leaves no aiohttp session open."""

    def test_every_attempt_gets_a_live_session_and_all_are_closed(self):
        attempts = []  # (session, its connector was already closed when used)
        outcomes = [ConnectionError("gateway reset"), ConnectionError("gateway reset"),
                    discord.errors.LoginFailure("Improper token has been passed.")]

        async def fake_request(http, route, **kwargs):
            session = http._HTTPClient__session
            attempts.append((session, session.connector is None or session.connector.closed))
            raise outcomes[len(attempts) - 1]

        async def run():
            bot = discord.Client(intents=discord.Intents.none())
            sleep = mock.AsyncMock()
            with mock.patch.object(discord.http.HTTPClient, "request", fake_request), \
                    mock.patch.object(run_all.asyncio, "sleep", sleep), mock.patch("builtins.print"):
                await run_all.run_bot_resilient(bot, "token", "Test")

        asyncio.run(run())
        self.assertEqual(len(attempts), 3)                      # it retried, then stopped on the bad token
        self.assertEqual(len({id(s) for s, _ in attempts}), 3)  # one session per login
        self.assertEqual([dead for _, dead in attempts], [False] * 3)  # never built on a closed connector
        self.assertTrue(all(s.closed for s, _ in attempts), "a retried session was left open")

    def test_session_is_closed_even_when_client_close_raises(self):
        # Client.close() awaits state and gateway cleanup before http.close(); if
        # that cleanup raises, reset_client must still close the HTTP session
        # before it drops the connector (Codex 3E round 1).
        sessions = []

        async def fake_request(http, route, **kwargs):
            sessions.append(http._HTTPClient__session)
            raise (ConnectionError("gateway reset") if len(sessions) == 1
                   else discord.errors.LoginFailure("Improper token has been passed."))

        async def run():
            bot = discord.Client(intents=discord.Intents.none())
            with mock.patch.object(discord.http.HTTPClient, "request", fake_request), \
                    mock.patch.object(type(bot._connection), "close", side_effect=RuntimeError("state")), \
                    mock.patch.object(run_all.asyncio, "sleep", mock.AsyncMock()), mock.patch("builtins.print"):
                await run_all.run_bot_resilient(bot, "token", "Test")

        asyncio.run(run())
        self.assertEqual(len(sessions), 2)
        self.assertTrue(all(s.closed for s in sessions), "close() raised and the session was left open")


class VaultConsistencyTest(unittest.TestCase):
    """3E-3: startup verdict on whether REST and OBSIDIAN_VAULT_PATH are the same vault."""

    def verdict(self, disk_entries, rest_files=None, vault=None):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(vault or tmp)
            for name in disk_entries:
                (root / name).mkdir() if name.endswith("/") else (root / name).write_text("x", encoding="utf-8")
            client = ObsidianClient(base_url=OFFLINE_URL, vault_path=str(root))
            online = rest_files is not None
            body = json.dumps({"files": rest_files or []})
            with mock.patch.object(client, "is_rest_api_online", return_value=online), \
                    mock.patch.object(client, "_request", return_value={"status": 200, "body": body}):
                return client.check_vault_consistency()

    def test_same_vault_is_ok(self):
        self.assertTrue(self.verdict(["Home.md", "01 - User/"], ["Home.md", "01 - User/"]).startswith("ok"))

    def test_different_vault_is_a_mismatch(self):
        v = self.verdict(["Home.md"], ["Home.md", "05 - Daily Logs/", "Inbox.md"])
        self.assertTrue(v.startswith("MISMATCH: 2 of 3"), v)
        self.assertIn("05 - Daily Logs", v)

    def test_missing_vault_path_and_offline_rest(self):
        self.assertTrue(self.verdict([], ["a.md"], vault=r"Z:\no\such\vault").startswith("MISMATCH"))
        self.assertTrue(self.verdict(["a.md"], None).startswith("skipped: REST API offline"))


class SuppressedExceptionLintTest(unittest.TestCase):
    """3E-3: every `except Exception:` either logs one line or says why it is quiet."""

    def test_no_silent_broad_excepts(self):
        import ast
        repo = Path(__file__).parent
        silent = []
        for path in sorted({*repo.glob("*.py"), *repo.glob("scripts/*.py"), *repo.glob("bench/*.py"),
                            *repo.glob("legacy/*.py")}):
            # Unit tests are skipped; scripts/test_*.py are live scripts, so they are linted.
            if path.name.startswith("test_") and path.parent.name != "scripts":
                continue
            src = path.read_text(encoding="utf-8")
            lines = src.splitlines()
            for node in ast.walk(ast.parse(src)):
                if not (isinstance(node, ast.ExceptHandler) and isinstance(node.type, ast.Name)
                        and node.type.id == "Exception"):
                    continue
                if "# quiet:" in lines[node.lineno - 1]:
                    continue
                first = node.body[0]
                logs = (isinstance(first, ast.Expr) and isinstance(first.value, ast.Call)
                        and getattr(first.value.func, "id", None) == "print")
                if node.name is None or not logs:
                    # A named handler may also use the exception in other ways (return it, re-raise).
                    used = node.name and any(isinstance(n, ast.Name) and n.id == node.name
                                             for b in node.body for n in ast.walk(b))
                    if not used:
                        silent.append(f"{path.name}:{node.lineno}")
        self.assertEqual(silent, [])


class HeavyPoolTest(unittest.TestCase):
    """3E-4: PDF and Gmail jobs queue on their own pool, so quick calls never wait behind them."""

    def test_quick_calls_are_not_starved_by_heavy_jobs(self):
        import heavy_jobs

        async def scenario():
            from concurrent.futures import ThreadPoolExecutor
            loop = asyncio.get_running_loop()
            loop.set_default_executor(ThreadPoolExecutor(max_workers=2))  # a small default pool, worst case
            heavy = [asyncio.ensure_future(heavy_jobs.run_heavy(time.sleep, 0.4)) for _ in range(4)]
            await asyncio.sleep(0.05)
            started = time.perf_counter()
            await asyncio.to_thread(lambda: None)  # vitals, a chat reply, an Obsidian read...
            quick_wait = time.perf_counter() - started
            await asyncio.gather(*heavy)
            return quick_wait

        self.assertLess(asyncio.run(scenario()), 0.1)

    def test_heavy_jobs_run_on_the_heavy_pool(self):
        import heavy_jobs
        name = asyncio.run(heavy_jobs.run_heavy(lambda: threading.current_thread().name))
        self.assertTrue(name.startswith("heavy"), name)

    def test_no_heavy_job_left_on_the_default_executor(self):
        import ast
        heavy = {"compile_master_resume", "generate_tailored_pdf_package", "triage_inbox", "sweep_inbox",
                 "run_mass_cleanse", "get_recent_finance_summary", "scrape_with_headless_edge"}
        repo = Path(__file__).parent
        found = []
        for path in sorted(repo.glob("*.py")):
            if path.name.startswith("test_"):
                continue
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "run_in_executor" \
                        and len(node.args) >= 2 and getattr(node.args[1], "id", None) in heavy:
                    found.append(f"{path.name}:{node.lineno} {node.args[1].id}")
        self.assertEqual(found, [])


class FixedClaimLintTest(unittest.TestCase):
    """Status the code never computed must not ship in user-facing strings."""
    FIXED_CLAIMS = re.compile(
        r"100/100|6/6|5/5 Agents|100% nominal|Hardware Nominal|vitals_summary', 'Nominal'"
        r"|verified case studies|Brain: \*\*Gemini|\b6 Flagship", re.IGNORECASE)

    def test_no_fixed_status_claims(self):
        hits = []
        for path in [*ROOT.glob("*.py"), *ROOT.glob("scripts/*.py")]:
            if path.name.startswith("test_"):
                continue
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if self.FIXED_CLAIMS.search(line):
                    hits.append(f"{path.relative_to(ROOT)}:{n}: {line.strip()[:100]}")
        self.assertEqual(hits, [])


class SentinelFailureTest(unittest.TestCase):
    def test_failed_hardware_check_is_reported(self):
        with mock.patch.object(proactive_sentinel, "get_system_vitals", side_effect=OSError("psutil gone")), \
                mock.patch("builtins.print"):
            res = proactive_sentinel.check_hardware_sentinel()
        self.assertEqual([s["id"] for s in res], ["hardware_check_failed"])
        self.assertIn("psutil gone", res[0]["description"])

    def test_git_check_outside_a_repo_is_reported(self):
        with tempfile.TemporaryDirectory() as not_a_repo, \
                mock.patch.object(proactive_sentinel, "GIT_REPO_DIR", Path(not_a_repo)), \
                mock.patch("builtins.print"):
            res = proactive_sentinel.check_git_sentinel()
        self.assertEqual([s["id"] for s in res], ["git_check_failed"])

    def test_d6_default_repo_is_hq_and_dirty_repos_are_reported(self):
        import git_sentinel
        hq = Path(proactive_sentinel.__file__).resolve().parent
        self.assertEqual(Path(proactive_sentinel.GIT_REPO_DIR).resolve(), hq)
        self.assertEqual(Path(git_sentinel.GIT_REPO_DIR).resolve(), hq)
        with tempfile.TemporaryDirectory() as repo, mock.patch("builtins.print"):
            subprocess.run(["git", "init", "-q", repo], check=True)
            for name in "abc":
                Path(repo, f"{name}.txt").write_text(name, encoding="utf-8")
            with mock.patch.object(proactive_sentinel, "GIT_REPO_DIR", Path(repo)):
                res = proactive_sentinel.check_git_sentinel()
        self.assertEqual([s["id"] for s in res], ["git_uncommitted_files"])
        self.assertIn("3 Uncommitted", res[0]["title"])

    def test_empty_summary_claims_only_what_ran(self):
        text = proactive_sentinel.generate_appraisal_summary([])
        self.assertNotIn("nominal", text.lower())
        self.assertNotIn("synchronized", text.lower())


class FakeCtx:
    def __init__(self):
        self.embeds = []

    async def reply(self, content=None, embed=None):
        if embed is not None:
            self.embeds.append(embed)
        msg = mock.Mock()
        msg.delete = mock.AsyncMock()
        return msg

    def typing(self):
        return mock.AsyncMock()


class KazuhaEmbedTest(unittest.TestCase):
    def run_git(self, sub, **patches):
        ctx = FakeCtx()
        with mock.patch.multiple(bot_kazuha, **patches):
            asyncio.run(bot_kazuha.git_command.callback(ctx, sub))
        return ctx.embeds

    def assert_fits(self, e):
        self.assertLessEqual(len(e.title or ""), 256)
        self.assertLessEqual(len(e.description or ""), 4096)
        self.assertLessEqual(len(e), 6000)

    def test_pr_draft_with_long_llm_title(self):
        pr = {"success": True, "title": "feat: " + "x" * 400, "body": "b" * 5000}
        (embed,) = self.run_git("pr", kazuha_create_pr=lambda: pr)
        self.assert_fits(embed)

    def test_review_with_long_llm_text(self):
        review = {"score": 80, "verdict": "OK", "info": {"branch": "main"}, "review": "r" * 6000}
        (embed,) = self.run_git("review", kazuha_review_changes=lambda staged: review)
        self.assert_fits(embed)

    def test_commit_with_long_llm_message(self):
        commit = {"success": True, "full_message": "m" * 6000}
        (embed,) = self.run_git("commit", kazuha_create_commit=lambda staged: commit)
        self.assert_fits(embed)


class BrainStatusTest(unittest.TestCase):
    CFG = {"mode": "cloud", "local_model": "local-x", "cloud_model": "cloud-y",
           "agent_cloud_models": {"chaewon": "chaewon-z"}}

    def status(self, agent, mode="cloud", ollama=(False, [])):
        cfg = dict(self.CFG, mode=mode)
        with mock.patch.object(llm_client, "load_brain_config", return_value=cfg), \
                mock.patch.object(llm_client, "check_ollama_status", return_value=ollama):
            return llm_client.get_brain_status(agent)

    def test_cloud_mode_reports_the_agents_own_model(self):
        self.assertEqual(self.status("chaewon")["active_model"], "chaewon-z")
        self.assertEqual(self.status("sakura")["active_model"], "cloud-y")  # no override

    def test_auto_mode_with_ollama_up_reports_local(self):
        self.assertEqual(self.status("chaewon", "auto", (True, ["local-x"]))["active_model"], "local-x")


FENCE = "```"
WORDS = ["agent", "router", "Groq", "Gemini", "vault", "embed", "token", "latency", "x" * 50]


def random_markdown(rng):
    """Paragraphs, long lines, sentences, code fences and the odd giant word."""
    blocks = []
    for _ in range(rng.randint(1, 40)):
        kind = rng.random()
        if kind < 0.15:
            body = "\n".join(" ".join(rng.choices(WORDS, k=rng.randint(1, 30))) for _ in range(rng.randint(1, 60)))
            blocks.append(f"{FENCE}{rng.choice(['', 'python', 'bash'])}\n{body}\n{FENCE}")
        elif kind < 0.2:
            blocks.append("w" * rng.randint(100, 2500))  # a word longer than the chunk size
        else:
            sentences = [" ".join(rng.choices(WORDS, k=rng.randint(1, 25))) + "." for _ in range(rng.randint(1, 80))]
            blocks.append(" ".join(sentences))
    return "\n\n".join(blocks)


class ChunkPropertyTest(unittest.TestCase):
    """split_smart_chunks on 300 seeded random documents (stdlib only, reproducible)."""
    MAX = 600

    def test_properties(self):
        rng = random.Random(20261002)
        for case in range(300):
            text = random_markdown(rng)
            chunks = split_smart_chunks(text, max_chars=self.MAX)
            with self.subTest(case=case):
                for c in chunks:
                    self.assertLessEqual(len(c), self.MAX)          # Discord never sees an oversize part
                    self.assertEqual(c.count(FENCE) % 2, 0)          # every part renders its code blocks
                rejoined = re.sub(r"\s+", "", "".join(chunks).replace(FENCE, "").replace("python", "").replace("bash", ""))
                original = re.sub(r"\s+", "", text.replace(FENCE, "").replace("python", "").replace("bash", ""))
                self.assertEqual(rejoined, original)                # no text dropped or duplicated

    def test_short_text_is_one_chunk(self):
        self.assertEqual(split_smart_chunks("hello", max_chars=100), ["hello"])


class _StubHandler(http.server.BaseHTTPRequestHandler):
    mode = "error"  # "error" → 500, "hang" → reply after the client's 3 s timeout

    def _reply(self):
        if self.mode == "hang":
            time.sleep(3.6)
        self.send_response(500)
        self.end_headers()
        self.wfile.write(b"stub failure")

    do_GET = do_PUT = do_POST = _reply

    def log_message(self, *args):
        pass


class ObsidianHttpStubTest(unittest.TestCase):
    """REST up but failing (500) or hanging: reads fall back to the vault on disk; writes only use the disk."""

    def serve(self, mode):
        handler = type("H", (_StubHandler,), {"mode": mode})
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_address[1]}"

    def run_mode(self, mode):
        with tempfile.TemporaryDirectory() as vault:
            (Path(vault) / "note.md").write_text("from disk", encoding="utf-8")
            client = ObsidianClient(base_url=self.serve(mode), vault_path=vault)
            self.assertTrue(client.is_rest_api_online())          # the stub is listening
            self.assertEqual(client.get_file("note.md"), "from disk")
            self.assertTrue(client.put_file("new.md", "written"))
            self.assertEqual((Path(vault) / "new.md").read_text(encoding="utf-8"), "written")

    def test_rest_500_falls_back_to_disk(self):
        self.run_mode("error")

    def test_rest_timeout_falls_back_to_disk(self):
        self.run_mode("hang")


class VaultPathGuardTest(unittest.TestCase):
    """Ciel's read/append-note tools pass router (LLM) output as the path."""
    # A leading slash is stripped on purpose ("/05 - Daily Logs/x.md" is a vault path),
    # so "/etc/passwd" lands inside the vault and is not an escape. A drive path
    # only escapes on Windows; elsewhere it is an odd filename inside the vault.
    # Backslashes are separators only on Windows, likewise.
    ESCAPES = ["../outside.md", "notes/../../outside.md"] + \
        (["..\\outside.md", "C:\\Windows\\win.ini"] if os.name == "nt" else [])

    def test_paths_outside_the_vault_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            vault.mkdir()
            (Path(tmp) / "outside.md").write_text("secret", encoding="utf-8")
            client = ObsidianClient(base_url=OFFLINE_URL, vault_path=vault)
            for path in self.ESCAPES:
                with self.subTest(path=path):
                    with self.assertRaises(ValueError):
                        client.get_file(path)
                    with self.assertRaises(ValueError):
                        client.put_file(path, "x")
                    with self.assertRaises(ValueError):
                        client.append_file(path, "x")
            self.assertEqual((Path(tmp) / "outside.md").read_text(encoding="utf-8"), "secret")

    def test_normal_vault_paths_still_work(self):
        with tempfile.TemporaryDirectory() as vault:
            client = ObsidianClient(base_url=OFFLINE_URL, vault_path=vault)
            self.assertTrue(client.put_file("05 - Daily Logs/2026-10-02.md", "ok"))
            self.assertEqual(client.get_file("/05 - Daily Logs/2026-10-02.md"), "ok")
            self.assertIn("2026-10-02.md", client.list_dir("05 - Daily Logs"))


def _http_429(retry_after):
    headers = email.message.Message()
    headers["Retry-After"] = retry_after
    return urllib.error.HTTPError("https://api.groq.com", 429, "Too Many Requests", headers, None)


def _ok_response(text="ok"):
    resp = mock.MagicMock()
    resp.__enter__.return_value.read.return_value = json.dumps(
        {"choices": [{"message": {"content": text}}]}).encode()
    return resp


class GroqRetryAfterTest(unittest.TestCase):
    def call(self, retry_after):
        sleeps = []
        with mock.patch.dict(os.environ, {"GROQ_API_KEY": "test-key"}), \
                mock.patch.object(llm_client.urllib.request, "urlopen",
                                  side_effect=[_http_429(retry_after), _ok_response()]), \
                mock.patch.object(llm_client.time, "sleep", side_effect=sleeps.append), \
                mock.patch("builtins.print"):
            self.assertEqual(llm_client.call_groq("hi", model="m"), "ok")
        return sleeps

    def test_decimal_retry_after_is_honoured(self):
        self.assertEqual(self.call("1.5"), [1.5 + 0.2])

    def test_integer_retry_after_is_honoured(self):
        self.assertEqual(self.call("2"), [2.0 + 0.2])

    def test_unparseable_retry_after_uses_default(self):
        self.assertEqual(self.call("soon"), [2.0 + 0.2])


def _day_of_dispatches(n=67, size=690, big_first=17_000):
    """The 2026-10-07 shape: 67 entries, ~46 KB, one early entry far over the per-entry cap."""
    entries = [f"### [{8 + i // 6:02d}:{(i * 7) % 60:02d}] Claude: entry {i}\n" + "x" * size for i in range(n)]
    entries[1] = "### [08:07] Sakura Dispatch\n" + "y" * big_first
    return entries


class TestRollupDispatchCap(unittest.TestCase):
    """8C: Groq refused the 2026-10-07 rollup (46 KB of dispatches, HTTP 413 on every Groq model)."""

    def test_a_busy_day_fits_the_budget_newest_kept_in_time_order(self):
        import sakura_engine
        entries = _day_of_dispatches()
        text = sakura_engine.cap_dispatches(entries)
        kept, note = text.rsplit("\n\n", 1)
        self.assertLessEqual(len(kept.encode("utf-8")), 12_000)
        self.assertRegex(note, r"^\(\d+ earlier entries left out\)$")
        self.assertTrue(kept.endswith(entries[-1]))          # the newest entry is kept, last
        self.assertNotIn("entry 0\n", kept)                    # the oldest one is not
        positions = [kept.index(f"entry {i}\n") for i in range(60, 67)]
        self.assertEqual(positions, sorted(positions))         # time order

    def test_the_bots_own_dispatches_are_kept_first(self):
        # The bots post early; on 2026-10-08 newest-first dropped Sakura's morning dispatch.
        import sakura_engine
        entries = _day_of_dispatches()
        entries.insert(0, "### [10:28:59] Sakura Dispatch\n- Morning Launchpad Executed")
        entries.insert(3, "### [10:34:30] Vault job: reports mirrored\n- copied HANDOFF.md")
        entries.insert(5, "### [10:33:29] Kazuha Dispatch\n- Applied AI Research Scout")
        text = sakura_engine.cap_dispatches(entries)
        for header in ("Sakura Dispatch", "Vault job: reports mirrored", "Kazuha Dispatch"):
            self.assertIn(header, text)
        self.assertLessEqual(len(text.rsplit("\n\n", 1)[0].encode("utf-8")), 12_000)
        self.assertLess(text.index("Sakura Dispatch"), text.index("Kazuha Dispatch"))  # still time order
        self.assertTrue(text.rsplit("\n\n", 1)[0].endswith(entries[-1]))  # newest AI entry still in

    def test_one_huge_entry_is_cut_not_dropped_whole(self):
        import sakura_engine
        text = sakura_engine.cap_dispatches(["### [08:07] Sakura Dispatch\n" + "y" * 17_000])
        self.assertLessEqual(len(text.encode("utf-8")), 1_500)
        self.assertTrue(text.startswith("### [08:07] Sakura Dispatch") and text.endswith("…"))

    def test_a_quiet_day_is_sent_whole(self):
        import sakura_engine
        entries = _day_of_dispatches(n=4, size=200, big_first=200)
        self.assertEqual(sakura_engine.cap_dispatches(entries), "\n\n".join(entries))

    def test_no_dispatches_keeps_the_old_message(self):
        import sakura_engine
        self.assertEqual(sakura_engine.cap_dispatches([]), "No prior dispatches recorded today.")

    def test_the_rollup_prompt_on_the_10_07_log_stays_small(self):
        import sakura_engine
        log = "# Daily Log\n\n## Agent Activity\n\n" + "\n\n".join(_day_of_dispatches())
        client = mock.Mock()
        client.ensure_daily_log.return_value = "05 Daily Logs/2026-10-07.md"
        client.get_file.return_value = log
        prompts = []
        with mock.patch.object(sakura_engine, "ObsidianClient", return_value=client), \
                mock.patch.object(sakura_engine, "query_llm", side_effect=lambda p, **kw: prompts.append(p) or "ok"):
            sakura_engine.execute_evening_rollup(sync_obsidian=False)
        self.assertGreater(len(log.encode("utf-8")), 46_000)
        self.assertLess(len(prompts[0].encode("utf-8")), 16_000)  # 12 KB of dispatches + the template


if __name__ == "__main__":
    unittest.main()
