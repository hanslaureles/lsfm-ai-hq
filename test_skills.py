"""
ADR-001 R1: the shared skill registry (skills.py) that the Discord bots and Ciel both
call. Hermetic: throwaway skills only, no engines, no network.
Run: python -m pytest -q test_skills.py
"""

import asyncio
import time
import unittest
from pathlib import Path
from unittest import mock

import skills
from skills import Skill, SkillResult


def _ok(**kwargs):
    return SkillResult("done", f"ok {sorted(kwargs.items())}", {"args": kwargs})


class RegistryTestCase(unittest.TestCase):
    """Registers throwaway skills under the test agent and removes them afterwards."""

    def setUp(self):
        self.added = []

    def tearDown(self):
        for name in self.added:
            skills.SKILLS.pop(name, None)

    def add(self, name="testagent.ping", run=_ok, **fields):
        skill = skills.register(Skill(name=name, agent=name.split(".")[0], desc="test skill", run=run, **fields))
        self.added.append(name)
        return skill


class TestRegister(RegistryTestCase):

    def test_register_rejects_duplicate_and_wrong_prefix(self):
        self.add("testagent.ping")
        with self.assertRaises(ValueError):
            self.add("testagent.ping")
        with self.assertRaises(ValueError):
            skills.register(Skill(name="otheragent.ping", agent="testagent", desc="d", run=_ok))
        self.assertNotIn("otheragent.ping", skills.SKILLS)


class TestValidateCall(RegistryTestCase):

    def test_validate_call_rejects_unknown_skill_arg_and_non_str(self):
        self.add("testagent.echo", args={"path": "a file"})
        skill, args = skills.validate_call("testagent.echo", {"path": "x.py"})
        self.assertEqual((skill.name, args), ("testagent.echo", {"path": "x.py"}))
        self.assertEqual(skills.validate_call("testagent.echo", None)[1], {})
        for name, bad in (("testagent.nope", {}), ("testagent.echo", {"rm": "x"}),
                          ("testagent.echo", {"path": 3}), ("testagent.echo", ["path"])):
            with self.subTest(name=name, args=bad), self.assertRaises(ValueError):
                skills.validate_call(name, bad)


class TestRunSkill(RegistryTestCase):

    def test_run_skill_never_raises(self):
        def boom():
            raise OSError("boom")
        self.add("testagent.boom", run=boom)
        r = skills.run_skill("testagent.boom")
        self.assertEqual(r.status, "failed")
        self.assertIn("boom", r.text)
        self.assertIn("Testagent failed: OSError", r.text)
        self.assertEqual(skills.run_skill("testagent.missing").status, "failed")
        self.add("testagent.echo", args={"path": "a file"})
        self.assertEqual(skills.run_skill("testagent.echo", {"rm": "x"}).status, "failed")

    def test_a_run_that_returns_no_result_is_failed(self):
        self.add("testagent.sloppy", run=lambda: "done")
        r = skills.run_skill("testagent.sloppy")
        self.assertEqual(r.status, "failed")

    def test_changes_things_needs_confirm_and_does_not_run(self):
        calls = []

        def sweep():
            calls.append(1)
            return SkillResult("done", "swept")
        self.add("testagent.sweep", run=sweep, changes_things=True, confirm_text="Sweep the inbox?")
        r = skills.run_skill("testagent.sweep")
        self.assertEqual((r.status, r.text, calls), ("needs_confirm", "Sweep the inbox?", []))
        self.assertEqual(skills.run_skill("testagent.sweep", confirmed=True).status, "done")
        self.assertEqual(calls, [1])


class TestCombine(unittest.TestCase):

    def test_combine_worst_status_wins(self):
        r = skills.combine([SkillResult("done", "a", {"x": 1}), SkillResult("attention", "b"),
                            SkillResult("failed", "c")])
        self.assertEqual((r.status, r.text), ("failed", "a | b | c"))
        self.assertEqual(r.data, {"results": [{"x": 1}, {}, {}]})
        self.assertEqual(skills.combine([SkillResult("done", "a"), SkillResult("needs_confirm", "b")]).status,
                         "needs_confirm")
        self.assertEqual(skills.combine([SkillResult("done", "a"), SkillResult("attention", "b")]).status,
                         "attention")
        self.assertEqual(skills.combine([]).status, "failed")  # nothing ran is not success


class TestRunCalls(RegistryTestCase):

    def test_run_calls_runs_in_order_and_combines(self):
        self.add("testagent.a", run=lambda: SkillResult("done", "first"))
        self.add("testagent.b", run=lambda: SkillResult("attention", "second"))
        r = asyncio.run(skills.run_calls([("testagent.a", {}), ("testagent.b", {})]))
        self.assertEqual((r.status, r.text), ("attention", "first | second"))

    def test_run_calls_timeout_reports_failed(self):
        def slow():
            time.sleep(0.3)
            return SkillResult("done", "late")
        self.add("testagent.slow", run=slow, timeout_s=0.05)
        r = asyncio.run(skills.run_calls([("testagent.slow", {})]))
        self.assertEqual(r.status, "failed")
        self.assertIn("timed out after 0.05 s", r.text)

    def test_malformed_calls_are_failed_results_not_exceptions(self):
        # Codex R1 fix 1: a planned call is untrusted input.
        for name in ([], {}, None, 3, ""):
            with self.subTest(name=name):
                self.assertEqual(skills.run_skill(name, {}).status, "failed")
        self.add("testagent.ok", run=lambda: SkillResult("done", "fine"))
        for calls in (None, "testagent.ok", [("missing",)], [([], {})], [("testagent.ok", {}, "extra")], [None]):
            with self.subTest(calls=calls):
                r = asyncio.run(skills.run_calls(calls))
                self.assertEqual(r.status, "failed")
        r = asyncio.run(skills.run_calls([([], {}), ("testagent.ok", {})]))  # the good call still runs
        self.assertEqual((r.status, r.text.endswith("fine")), ("failed", True))

    def test_a_malformed_result_is_failed_and_aggregation_never_throws(self):
        # Codex R1 fix 2: the SkillResult contract (status, str text, dict data) is checked.
        self.add("testagent.notext", run=lambda: SkillResult("done", None))
        self.add("testagent.nodata", run=lambda: SkillResult("done", "ok", None))
        for name in ("testagent.notext", "testagent.nodata"):
            with self.subTest(name=name):
                self.assertEqual(skills.run_skill(name).status, "failed")
        r = asyncio.run(skills.run_calls([("testagent.notext", {}), ("testagent.nodata", {})]))
        self.assertEqual(r.status, "failed")

    def test_run_calls_passes_confirmed(self):
        self.add("testagent.sweep", run=lambda: SkillResult("done", "swept"), changes_things=True,
                 confirm_text="Sweep?")
        self.assertEqual(asyncio.run(skills.run_calls([("testagent.sweep", {})])).status, "needs_confirm")
        self.assertEqual(asyncio.run(skills.run_calls([("testagent.sweep", {})], confirmed=True)).status, "done")


def _load_real(name):
    # Other test files put stubs in sys.modules at collection time, so load the real file by path.
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(f"{name}_real_skills", Path(__file__).parent / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _vitals(cpu=50.0, ram=50.0, free=100.0):
    return {"cpu_pct": cpu, "cpu_count": 12, "ram_pct": ram, "ram_used_gb": 8.0, "ram_total_gb": 15.8,
            "disk_free_gb": free, "disk_total_gb": 500.0, "disk_pct": 50.0, "uptime_str": "1h 2m"}


class TestLimits(unittest.TestCase):
    """ADR-001 P4: the HUD's background checks and !vitals judge a sample the same way."""

    def setUp(self):
        self.ee = _load_real("eunchae_engine")
        self.ps = _load_real("proactive_sentinel")

    def test_sentinel_and_vitals_agree(self):
        self.assertEqual(self.ee.LIMITS, {"cpu_pct": 85, "ram_pct": 88, "disk_free_gb": 20})
        for field, over, under in (("cpu", 86, 84), ("ram", 89, 87), ("free", 19, 21)):
            for value, healthy in ((over, False), (under, True)):
                sample = _vitals(**{field: value})
                with self.subTest(field=field, value=value):
                    self.assertEqual(self.ee.is_healthy(sample), healthy)
                    with mock.patch.object(self.ps, "get_system_vitals", lambda s=sample: dict(s)):
                        warned = bool(self.ps.check_hardware_sentinel())
                    self.assertEqual(warned, not healthy)

    def test_get_system_vitals_uses_the_shared_rule(self):
        # The engine reads C:\ (the bots run on Windows); CI runs on Linux, so fake the disk.
        disk = mock.Mock(total=500 * 1024 ** 3, free=100 * 1024 ** 3, percent=80.0)
        with mock.patch.object(self.ee, "is_healthy", return_value="from is_healthy"), \
                mock.patch.object(self.ee.psutil, "disk_usage", return_value=disk):
            self.assertEqual(self.ee.get_system_vitals(cpu_interval=None)["healthy"], "from is_healthy")

    def test_urgent_alert_limits_live_beside_the_health_limits(self):
        from pathlib import Path
        self.assertEqual(self.ee.ALERT_LIMITS, {"cpu_pct": 95, "ram_pct": 92})
        bot = (Path(__file__).parent / "bot_eunchae.py").read_text(encoding="utf-8")
        self.assertIn("ALERT_LIMITS", bot)
        self.assertNotIn('v["ram_pct"] > 92', bot)


class TestEunchaeSkills(unittest.TestCase):

    def patch(self, name, value):
        p = mock.patch.object(skills, name, value)
        p.start()
        self.addCleanup(p.stop)

    def test_registered(self):
        for name in ("eunchae.vitals", "eunchae.compile_all", "eunchae.test"):
            self.assertEqual(skills.SKILLS[name].agent, "eunchae")
        self.assertEqual(skills.SKILLS["eunchae.test"].args.keys(), {"path"})

    def test_vitals_status_follows_health(self):
        self.patch("get_system_vitals", lambda: {**_vitals(), "healthy": True})
        r = skills.run_skill("eunchae.vitals")
        self.assertEqual(r.status, "done")
        self.assertIn("CPU 50.0% (12 Cores)", r.text)
        self.assertEqual(r.data["cpu_count"], 12)
        self.patch("get_system_vitals", lambda: {**_vitals(ram=94.4), "healthy": False})
        self.assertEqual(skills.run_skill("eunchae.vitals").status, "attention")
        self.patch("get_system_vitals", None)
        r = skills.run_skill("eunchae.vitals")
        self.assertEqual(r.status, "failed")
        self.assertIn("could not be imported", r.text)

    def test_compile_all_reports_syntax_errors(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "good.py").write_text("x = 1\n", encoding="utf-8")
            self.patch("HQ_DIR", Path(d))
            r = skills.run_skill("eunchae.compile_all")
            self.assertEqual(r.status, "done")
            self.assertIn("1 Python scripts", r.text)
            (Path(d) / "bad.py").write_text("def broken(:\n", encoding="utf-8")
            r = skills.run_skill("eunchae.compile_all")
            self.assertEqual(r.status, "attention")
            self.assertIn("1 compilation error(s): bad.py", r.text)

    def workspace(self):
        """A throwaway workspace: ws/HQ (HQ_DIR), ws/portfolio-site, ws/other/x.py, and outside/x.py beside ws."""
        import tempfile
        from pathlib import Path
        root = Path(self.enterContext_tmp())
        ws, outside = root / "ws", root / "outside"
        for d in (ws / "HQ", ws / "portfolio-site", ws / "other", outside):
            d.mkdir(parents=True)
        (ws / "HQ" / "git_sentinel.py").write_text("x = 1\n", encoding="utf-8")
        (ws / "portfolio-site" / "index.html").write_text("<html></html>\n", encoding="utf-8")
        (ws / "other" / "x.py").write_text("x = 2\n", encoding="utf-8")
        (outside / "x.py").write_text("x = 3\n", encoding="utf-8")
        self.patch("WORKSPACE_DIR", ws)
        self.patch("HQ_DIR", ws / "HQ")
        return ws, outside

    def enterContext_tmp(self):
        import tempfile
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        return tmp.name

    def recording_engine(self):
        calls = []
        self.patch("test_code_file", lambda p: calls.append(p) or {"success": True, "passed": True, "warnings": [],
                                                                  "filename": p, "score": 100, "verdict": "PASS"})
        return calls

    def test_eunchae_test_refuses_unsafe_paths(self):
        # Review Focus 4: the path may come from an LLM plan, and test_code_file can run the file.
        ws, outside = self.workspace()
        calls = self.recording_engine()
        for bad in ("C:/Windows/x.py", "C:\\Windows\\x.py", "/etc/x.py", "../x.py", "eval/../../x.py",
                    "notes.txt", "", "  ", "\\\\server\\share\\x.py", "C:x.py", "../outside/x.py"):
            with self.subTest(path=bad):
                self.assertEqual(skills.run_skill("eunchae.test", {"path": bad}).status, "failed")
        self.assertEqual(calls, [])
        # Allowed: resolved inside the workspace, then HQ; the engine gets the exact absolute file.
        self.assertEqual(skills.run_skill("eunchae.test", {"path": "`git_sentinel.py`"}).status, "done")
        self.assertEqual(skills.run_skill("eunchae.test", {"path": "portfolio-site/index.html"}).status, "done")
        self.assertEqual(calls, [str((ws / "HQ" / "git_sentinel.py").resolve()),
                                 str((ws / "portfolio-site" / "index.html").resolve())])  # web checks kept

    def test_eunchae_test_never_substitutes_another_file(self):
        # Codex R1 fix 3: a missing explicit path must not fall through to some other x.py.
        self.workspace()
        calls = self.recording_engine()
        for missing in ("nonexistent/x.py", "x.py"):
            with self.subTest(path=missing):
                r = skills.run_skill("eunchae.test", {"path": missing})
                self.assertEqual(r.status, "failed")
                self.assertIn("not found", r.text)
        self.assertEqual(calls, [])

    def test_eunchae_test_ignores_the_working_directory(self):
        # Codex R1 fix 3: Ciel may start from any folder; resolution must never use the cwd.
        import os
        ws, outside = self.workspace()
        calls = self.recording_engine()
        old = os.getcwd()
        os.chdir(outside)
        self.addCleanup(os.chdir, old)
        self.assertEqual(skills.run_skill("eunchae.test", {"path": "x.py"}).status, "failed")
        self.assertEqual(calls, [])

    def test_eunchae_test_refuses_a_junction_out_of_the_workspace(self):
        # Codex R1 fix 3: containment is checked on the resolved path, so links can't escape.
        import subprocess
        import sys
        if sys.platform != "win32":
            self.skipTest("directory junctions are Windows-only")
        ws, outside = self.workspace()
        made = subprocess.run(["cmd", "/c", "mklink", "/J", str(ws / "link"), str(outside)],
                              capture_output=True, text=True)
        if made.returncode != 0:
            self.skipTest(f"could not create a junction: {made.stdout} {made.stderr}")
        calls = self.recording_engine()
        r = skills.run_skill("eunchae.test", {"path": "link/x.py"})
        self.assertEqual(r.status, "failed")
        self.assertIn("outside the workspace", r.text)
        self.assertEqual(calls, [])

    def test_eunchae_test_maps_the_engine_result(self):
        self.workspace()
        cases = [({"success": True, "passed": True, "warnings": []}, "done"),
                 ({"success": True, "passed": True, "warnings": ["slow"]}, "attention"),
                 ({"success": True, "passed": False, "warnings": [], "defects": ["x"]}, "attention"),
                 ({"success": False, "error": "File not found"}, "failed")]
        for res, status in cases:
            with self.subTest(res=res):
                self.patch("test_code_file", lambda p, res=res: {"filename": p, "score": 80, "verdict": "V", **res})
                r = skills.run_skill("eunchae.test", {"path": "git_sentinel.py"})
                self.assertEqual(r.status, status)

    def test_legacy_calls_pick_eunchae_actions_like_the_old_ciel_jobs(self):
        # Transitional (deleted in R5): the keyword pick Ciel's old hand-written agent jobs made.
        self.assertEqual(skills.legacy_calls("eunchae", "check vitals", []), [("eunchae.vitals", {})])
        self.assertEqual(skills.legacy_calls("eunchae", "run a syntax check", []), [("eunchae.compile_all", {})])
        self.assertEqual(skills.legacy_calls("eunchae", "how is my pc", ["run_tests"]),
                         [("eunchae.compile_all", {})])
        self.assertEqual(skills.legacy_calls("sakura", "check inbox", ["check_inbox"]), [("sakura.inbox", {})])


class TestChaewonSakuraSkills(unittest.TestCase):
    """ADR-001 R3: Chaewon's resume rebuild and scouting, Sakura's inbox scan, priorities and vault search."""

    def patch(self, name, value):
        self.enterContext(mock.patch.object(skills, name, value))

    def test_resume_lock_blocks_second_build(self):
        import threading
        started, release = threading.Event(), threading.Event()

        def slow_build(**kwargs):
            started.set()
            release.wait(5)
            return {"success": True, "size_kb": 82.9, "pdf_path": Path("Hans_Resume.pdf")}

        self.patch("compile_master_resume", slow_build)
        first = []
        t = threading.Thread(target=lambda: first.append(skills.run_skill("chaewon.rebuild_resume")))
        t.start()
        self.assertTrue(started.wait(5))
        second = skills.run_skill("chaewon.rebuild_resume")
        self.assertEqual(second.status, "failed")
        self.assertIn("still running", second.text)
        release.set()
        t.join(5)
        self.assertEqual(first[0].status, "done")
        self.assertIn("82.9 KB", first[0].text)

    def test_rebuild_reports_engine_failure_and_missing_engine(self):
        self.patch("compile_master_resume", lambda **k: {"success": False, "error": "Edge not found"})
        r = skills.run_skill("chaewon.rebuild_resume")
        self.assertEqual(r.status, "failed")
        self.assertIn("Edge not found", r.text)
        self.patch("compile_master_resume", None)
        self.assertEqual(skills.run_skill("chaewon.rebuild_resume").status, "failed")

    def test_scout_zero_jobs_is_attention(self):
        async def none(**kwargs):
            return []
        self.patch("fetch_live_remote_jobs", none)
        self.assertEqual(skills.run_skill("chaewon.scout").status, "attention")

    def test_scout_lists_up_to_three_roles(self):
        seen = {}

        async def five(query, location, limit):
            seen.update(query=query, location=location, limit=limit)
            return [{"title": f"Role {i}", "company": f"Co {i}", "geo": "Remote", "salary": "Competitive",
                     "url": f"https://x/{i}", "score": 90 - i, "source": "LinkedIn"} for i in range(5)]

        self.patch("fetch_live_remote_jobs", five)
        r = skills.run_skill("chaewon.scout")
        self.assertEqual(r.status, "done")
        self.assertEqual(seen, {"query": "applied AI engineer", "location": "Remote", "limit": 5})
        self.assertIn("Role 0", r.text)
        self.assertIn("Role 2", r.text)
        self.assertNotIn("Role 3", r.text)
        self.assertEqual(len(r.data["jobs"]), 5)

    def test_scout_bad_result_or_missing_engine_is_failed(self):
        async def broken(**kwargs):
            return None
        self.patch("fetch_live_remote_jobs", broken)
        self.assertEqual(skills.run_skill("chaewon.scout").status, "failed")
        self.patch("fetch_live_remote_jobs", None)
        self.assertEqual(skills.run_skill("chaewon.scout").status, "failed")

    def test_inbox_scan(self):
        ok = {"ok": True, "status": "Inbox", "unread_count": 2, "job_alerts": ["x"], "highlights": ["Recruiter: hi"]}
        self.patch("scan_morning_inbox", lambda: ok)
        r = skills.run_skill("sakura.inbox")
        self.assertEqual(r.status, "done")
        self.assertIn("2 unread", r.text)
        self.patch("scan_morning_inbox", lambda: {**ok, "ok": False, "status": "Gmail API credentials not configured",
                                                  "unread_count": 0})
        r = skills.run_skill("sakura.inbox")
        self.assertEqual(r.status, "failed")
        self.assertNotIn("clean", r.text.lower())
        self.patch("scan_morning_inbox", lambda: {"ok": True, "unread_count": "2"})  # incomplete engine result
        self.assertEqual(skills.run_skill("sakura.inbox").status, "failed")

    def vault(self, fail=False, content="", matches=()):
        v = mock.MagicMock()
        if fail:
            v.ensure_daily_log.side_effect = v.search_simple.side_effect = ConnectionError("Obsidian REST API unreachable")
        v.ensure_daily_log.return_value = "05 Daily Logs/today.md"
        v.get_file.return_value = content
        v.search_simple.return_value = list(matches)
        self.patch("_vault", lambda: v)
        return v

    def test_priorities(self):
        self.vault(content="- [ ] Apply to Spreetail\n- [x] done\n- [ ] Re-auth Gmail\n")
        r = skills.run_skill("sakura.priorities")
        self.assertEqual(r.status, "done")
        self.assertIn("Apply to Spreetail; Re-auth Gmail", r.text)
        self.vault(fail=True)
        r = skills.run_skill("sakura.priorities")
        self.assertEqual(r.status, "failed")
        self.assertIn("unreachable", r.text)

    def test_vault_search(self):
        self.vault(matches=[{"filename": "a.md"}, {"filename": "b.md"}])
        r = skills.run_skill("sakura.vault_search", {"query": "gmail oauth"})
        self.assertEqual(r.status, "done")
        self.assertEqual(r.data["matches"], [{"filename": "a.md"}, {"filename": "b.md"}])
        self.vault(fail=True)
        self.assertEqual(skills.run_skill("sakura.vault_search", {"query": "x"}).status, "failed")

    # --- Codex QA R3 (ADR-001-R3-CODEX-QA.md) ---
    def test_search_with_no_vault_access_is_failed_not_empty(self):  # F1, real client contract
        import tempfile
        from pathlib import Path
        import obsidian_client
        missing = Path(tempfile.mkdtemp()) / "no-such-vault"
        client = obsidian_client.ObsidianClient(vault_path=str(missing))
        self.enterContext(mock.patch.object(client, "is_rest_api_online", return_value=False))
        self.patch("_vault", lambda: client)
        r = skills.run_skill("sakura.vault_search", {"query": "oauth"})
        self.assertEqual(r.status, "failed")
        self.assertIn("unavailable", r.text)
        missing.mkdir()  # the disk fallback is back: a genuine empty search is done
        (missing / "note.md").write_text("nothing relevant", encoding="utf-8")
        r = skills.run_skill("sakura.vault_search", {"query": "oauth"})
        self.assertEqual((r.status, r.data["matches"]), ("done", []))

    def test_scout_cards_must_be_complete(self):  # F2
        good = {"title": "AI Engineer", "company": "ECLARO", "url": "https://x/1", "score": 88}
        for bad in ([{}], [{"title": "AI Engineer"}], [good, {**good, "url": ""}], [good, {**good, "score": "88"}]):
            with self.subTest(bad=bad):
                async def jobs(**kwargs):
                    return bad
                self.patch("fetch_live_remote_jobs", jobs)
                self.assertEqual(skills.run_skill("chaewon.scout").status, "failed")

    def test_resume_success_needs_the_pdf_fields(self):  # F2
        self.patch("compile_master_resume", lambda **k: {"success": True})
        r = skills.run_skill("chaewon.rebuild_resume")
        self.assertEqual(r.status, "failed")
        self.assertNotIn("None KB", r.text)
        from pathlib import Path
        self.patch("compile_master_resume", lambda **k: {"success": True, "pdf_path": Path("r.pdf"), "size_kb": 82.9})
        self.assertEqual(skills.run_skill("chaewon.rebuild_resume").status, "done")

    def test_negative_unread_count_is_not_a_clean_inbox(self):  # F2
        self.patch("scan_morning_inbox", lambda: {"ok": True, "unread_count": -1, "job_alerts": [], "highlights": []})
        r = skills.run_skill("sakura.inbox")
        self.assertEqual(r.status, "failed")
        self.assertNotIn("clean", r.text.lower())

    def test_resume_failure_never_promises_the_old_pdf_survived(self):  # F3: the renderer writes in place
        self.patch("compile_master_resume", lambda **k: {"success": False, "error": "Headless browser failed"})
        r = skills.run_skill("chaewon.rebuild_resume")
        self.assertNotIn("unchanged", r.text)
        self.assertIn("may be incomplete", r.text)

    def test_vault_client_is_created_once(self):
        made = []
        self.patch("ObsidianClient", lambda: made.append(1) or object())
        self.patch("_VAULT", None)
        self.assertIs(skills._vault(), skills._vault())
        self.assertEqual(len(made), 1)

    def test_legacy_calls_pick_chaewon_and_sakura_actions(self):
        self.assertEqual(skills.legacy_calls("chaewon", "compile resume", ["compile_resume"]),
                         [("chaewon.rebuild_resume", {})])
        self.assertEqual(skills.legacy_calls("chaewon", "find me remote AI jobs", []), [("chaewon.scout", {})])
        self.assertEqual(skills.legacy_calls("chaewon", "scout new roles", []), [("chaewon.scout", {})])
        self.assertEqual(skills.legacy_calls("sakura", "any recruiter emails?", []), [("sakura.inbox", {})])
        self.assertEqual(skills.legacy_calls("sakura", "search my notes for oauth", []),
                         [("sakura.vault_search", {"query": "search my notes for oauth"})])
        self.assertEqual(skills.legacy_calls("sakura", "what's on today", []), [("sakura.priorities", {})])
        for prompt in ("job hunt now", "hunt for jobs", "find jobs"):  # never the 10-minute card-posting hunt
            self.assertNotIn("chaewon.hunt", [n for n, _ in skills.legacy_calls("chaewon", prompt, [])])


class TestKazuhaYunjinSkills(unittest.TestCase):
    """ADR-001 R2: Kazuha and Yunjin run through the registry from Discord and Ciel."""

    def patch(self, name, value, target=skills):
        p = mock.patch.object(target, name, value)
        p.start()
        self.addCleanup(p.stop)

    def test_registered(self):
        for name, agent, args in (("kazuha.git_status", "kazuha", set()), ("kazuha.review", "kazuha", {"scope"}),
                                  ("kazuha.ask", "kazuha", {"question"}), ("yunjin.audit", "yunjin", set())):
            with self.subTest(name=name):
                self.assertEqual(skills.SKILLS[name].agent, agent)
                self.assertEqual(skills.SKILLS[name].args.keys(), args)

    def test_audit_reads_every_html_page(self):
        import tempfile
        from pathlib import Path
        ye = _load_real("yunjin_engine")
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        portfolio, audits = Path(tmp.name) / "portfolio-site", Path(tmp.name) / "audits"
        portfolio.mkdir()
        audits.mkdir()
        for page in ("index.html", "case-ciel.html", "404.html"):
            (portfolio / page).write_text("<html></html>", encoding="utf-8")
        scanned = []

        def scan(path):
            scanned.append(Path(path).name)
            return {"exists": True, "file": Path(path).name, "title": "t", "has_meta_desc": True,
                    "total_images": 0, "broken_images": []}

        self.patch("PORTFOLIO_DIR", portfolio, ye)
        self.patch("AUDITS_DIR", audits, ye)
        self.patch("scan_html_assets", scan, ye)
        self.patch("query_llm", lambda *a, **k: "PORTFOLIO HEALTH SCORE: 88%", ye)
        res = ye.audit_full_portfolio()
        self.assertEqual(sorted(scanned), ["404.html", "case-ciel.html", "index.html"])
        self.assertEqual(res["score"], 88)
        # No score in the critique: no made-up number (it used to default to 94).
        self.patch("query_llm", lambda *a, **k: "A critique with no score.", ye)
        self.assertIsNone(ye.audit_full_portfolio()["score"])

    def test_yunjin_audit_status(self):
        full = {"success": True, "broken_images": [], "report": "r", "saved_file": "a.md", "filename": "a.md"}
        cases = [({**full, "score": 95, "broken_images_count": 0}, "done"),
                 ({**full, "score": 89, "broken_images_count": 0}, "attention"),
                 ({**full, "score": 95, "broken_images_count": 1}, "attention"),
                 ({**full, "score": None, "broken_images_count": 0}, "attention"),
                 ({"success": False, "error": "Portfolio directory not found"}, "failed")]
        for res, status in cases:
            with self.subTest(res=res):
                self.patch("audit_full_portfolio", lambda res=res: dict(res))
                r = skills.run_skill("yunjin.audit")
                self.assertEqual(r.status, status, r.text)
                self.assertEqual(r.data, res)
        self.patch("audit_full_portfolio", lambda: {**full, "score": 70, "broken_images_count": 2})
        self.assertEqual(skills.run_skill("yunjin.audit").text, "Portfolio audit: score 70/100, 2 broken image(s).")
        self.patch("audit_full_portfolio", None)
        self.assertEqual(skills.run_skill("yunjin.audit").status, "failed")

    def test_kazuha_ask_unindexed_is_attention(self):
        asked = []

        def ask(q, answer):
            asked.append(q)
            return {"answer": answer, "citations": ["`a.md` (h)"]}

        self.patch("ask_knowledge_base", lambda q: ask(q, "⚠️ My knowledge vault is currently empty or unindexed."))
        self.assertEqual(skills.run_skill("kazuha.ask", {"question": "what is ciel?"}).status, "attention")
        self.patch("ask_knowledge_base", lambda q: ask(q, "Ciel is the voice copilot."))
        r = skills.run_skill("kazuha.ask", {"question": "what is ciel?"})
        self.assertEqual(r.status, "done")
        self.assertEqual(r.data, {"answer": "Ciel is the voice copilot.", "citations": ["`a.md` (h)"]})
        self.assertEqual(asked, ["what is ciel?"] * 2)
        self.assertEqual(skills.run_skill("kazuha.ask", {"question": "  "}).status, "failed")
        self.assertEqual(len(asked), 2)  # an empty question never reaches the engine
        self.patch("ask_knowledge_base", None)
        self.assertEqual(skills.run_skill("kazuha.ask", {"question": "q"}).status, "failed")

    def test_kazuha_review_scope_and_status(self):
        calls = []

        def review(staged_only, **res):
            calls.append(staged_only)
            return {"success": True, "score": 90, "verdict": "🟢 READY TO COMMIT", "security_alerts": [],
                    "review": "fine", "info": {"branch": "main"}, **res}

        self.patch("kazuha_review_changes", lambda staged_only=False: review(staged_only))
        self.assertEqual(skills.run_skill("kazuha.review", {"scope": "staged"}).status, "done")
        self.assertEqual(skills.run_skill("kazuha.review").status, "done")
        self.assertEqual(calls, [True, False])
        self.assertEqual(skills.run_skill("kazuha.review", {"scope": "everything"}).status, "failed")
        self.assertEqual(len(calls), 2)  # a bad scope never reaches the engine
        self.patch("kazuha_review_changes", lambda staged_only=False: review(staged_only, security_alerts=["key"]))
        self.assertEqual(skills.run_skill("kazuha.review").status, "attention")
        self.patch("kazuha_review_changes", lambda staged_only=False: {"success": False, "error": "not a repo"})
        self.assertEqual(skills.run_skill("kazuha.review").status, "failed")
        self.patch("kazuha_review_changes", None)
        self.assertEqual(skills.run_skill("kazuha.review").status, "failed")

    def test_kazuha_git_status_texts(self):
        self.patch("get_git_info", lambda: {"is_clean": True, "branch": "main", "total_commits": 7,
                                            "staged": [], "unstaged": [], "untracked": []})
        self.patch("get_git_diff", lambda: "")
        self.patch("scan_security_risks", lambda diff: [])
        r = skills.run_skill("kazuha.git_status")
        self.assertEqual((r.status, r.text), ("done", "Git review: Working tree clean on branch 'main' (7 commits). "
                                                      "0 security risks in diff."))
        self.patch("get_git_info", lambda: {"is_clean": False, "branch": "dev", "staged": ["a"], "unstaged": [],
                                            "untracked": ["b", "c"], "total_commits": 2})
        self.patch("get_git_diff", lambda: "+token")
        self.patch("scan_security_risks", lambda diff: ["secret"])
        r = skills.run_skill("kazuha.git_status")
        self.assertEqual(r.status, "attention")
        self.assertIn("Branch 'dev' | Staged: 1 | Unstaged: 0 | Untracked: 2. ⚠️ 1 security risk(s) in diff!", r.text)

        def broken():
            raise OSError("git not found")
        self.patch("get_git_info", broken)
        r = skills.run_skill("kazuha.git_status")
        self.assertEqual(r.status, "failed")
        self.assertIn("git not found", r.text)
        self.patch("get_git_info", None)
        self.assertEqual(skills.run_skill("kazuha.git_status").status, "failed")

    def test_incomplete_engine_results_fail(self):
        # Codex R2 fix 1: an engine dict that can't back the claim (or the embed) is failed, never done.
        review = {"success": True, "score": 95, "verdict": "🟢 READY TO COMMIT", "security_alerts": [],
                  "review": "fine", "info": {"branch": "main"}}
        audit = {"success": True, "score": 95, "broken_images_count": 0, "broken_images": [], "report": "r",
                 "saved_file": "a.md", "filename": "a.md"}
        info = {"branch": "main", "is_clean": True, "staged": [], "unstaged": [], "untracked": [], "total_commits": 3}
        cases = [
            ("kazuha.ask", {"question": "q"}, "ask_knowledge_base", lambda q: {}),
            ("kazuha.ask", {"question": "q"}, "ask_knowledge_base", lambda q: {"answer": "", "citations": []}),
            ("kazuha.ask", {"question": "q"}, "ask_knowledge_base", lambda q: {"answer": "a", "citations": None}),
            ("kazuha.review", {}, "kazuha_review_changes", lambda staged_only=False: {"success": True}),
            ("kazuha.review", {}, "kazuha_review_changes", lambda staged_only=False: {**review, "security_alerts": None}),
            ("kazuha.review", {}, "kazuha_review_changes", lambda staged_only=False: {**review, "score": True}),
            ("yunjin.audit", {}, "audit_full_portfolio", lambda: {k: v for k, v in audit.items() if k != "broken_images_count"}),
            ("yunjin.audit", {}, "audit_full_portfolio", lambda: {k: v for k, v in audit.items() if k != "score"}),
            ("yunjin.audit", {}, "audit_full_portfolio", lambda: {**audit, "score": 999}),
            ("kazuha.git_status", {}, "get_git_info", lambda: {}),
        ]
        self.patch("get_git_diff", lambda: "")
        self.patch("scan_security_risks", lambda diff: [])
        for name, args, engine, fake in cases:
            with self.subTest(name=name, engine=engine, i=cases.index((name, args, engine, fake))):
                self.patch(engine, fake)
                r = skills.run_skill(name, args)
                self.assertEqual(r.status, "failed", r.text)
                self.assertIn("incomplete", r.text)
        # Complete results still pass, and an explicit score None stays attention with renderable data.
        self.patch("kazuha_review_changes", lambda staged_only=False: dict(review))
        self.assertEqual(skills.run_skill("kazuha.review").status, "done")
        self.patch("audit_full_portfolio", lambda: {**audit, "score": None})
        r = skills.run_skill("yunjin.audit")
        self.assertEqual(r.status, "attention")
        self.assertIsNone(r.data["score"])
        self.patch("get_git_info", lambda: dict(info))
        self.assertEqual(skills.run_skill("kazuha.git_status").status, "done")
        # No scanner: the skill can't claim 0 security risks.
        self.patch("scan_security_risks", None)
        r = skills.run_skill("kazuha.git_status")
        self.assertEqual(r.status, "failed", r.text)
        self.assertNotIn("0 security risks", r.text)

    def test_malformed_citations_and_scan_results_fail(self):
        # Codex R2 round 2: element and return types count too, not only the outer fields.
        self.patch("ask_knowledge_base", lambda q: {"answer": "a", "citations": [123]})
        self.assertEqual(skills.run_skill("kazuha.ask", {"question": "q"}).status, "failed")
        self.patch("ask_knowledge_base", lambda q: {"answer": "a", "citations": []})
        self.assertEqual(skills.run_skill("kazuha.ask", {"question": "q"}).status, "done")
        info = {"branch": "main", "is_clean": True, "staged": [], "unstaged": [], "untracked": [], "total_commits": 3}
        self.patch("get_git_info", lambda: dict(info))
        for diff, risks in ((None, []), ("changed", None), ("changed", False), ("changed", "x")):
            with self.subTest(diff=diff, risks=risks):
                self.patch("get_git_diff", lambda d=diff: d)
                self.patch("scan_security_risks", lambda _d, r=risks: r)
                r = skills.run_skill("kazuha.git_status")
                self.assertEqual(r.status, "failed", r.text)
                self.assertNotIn("0 security risks", r.text)
        for diff, risks in (("", []), ("changed", [])):  # real empty results stay valid
            with self.subTest(diff=diff, risks=risks):
                self.patch("get_git_diff", lambda d=diff: d)
                self.patch("scan_security_risks", lambda _d, r=risks: r)
                self.assertEqual(skills.run_skill("kazuha.git_status").status, "done")

    def test_blocked_review_is_attention(self):
        # Codex R2 fix 2: the engine's own verdict counts, not only security alerts.
        for score, status in ((90, "done"), (89, "attention"), (40, "attention")):
            res = {"success": True, "score": score, "verdict": "V", "security_alerts": [], "review": "r",
                   "info": {"branch": "main"}}
            with self.subTest(score=score):
                self.patch("kazuha_review_changes", lambda staged_only=False, res=res: dict(res))
                self.assertEqual(skills.run_skill("kazuha.review").status, status)

    def test_audit_with_no_pages_fails_before_the_critique(self):
        # Codex R2 fix 3: an empty portfolio folder is not a 95% audit.
        import tempfile
        from pathlib import Path
        ye = _load_real("yunjin_engine")
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        asked = []
        self.patch("PORTFOLIO_DIR", Path(tmp.name), ye)
        self.patch("AUDITS_DIR", Path(tmp.name), ye)
        self.patch("query_llm", lambda *a, **k: asked.append(a) or "95%", ye)
        res = ye.audit_full_portfolio()
        self.assertFalse(res["success"])
        self.assertIn("no .html pages", res["error"])
        self.assertEqual(asked, [])
        self.assertEqual(list(Path(tmp.name).iterdir()), [])  # no report written

    def test_legacy_calls_pick_kazuha_and_yunjin_actions(self):
        self.assertEqual(skills.legacy_calls("kazuha", "git status", ["git_status"]), [("kazuha.git_status", {})])
        self.assertEqual(skills.legacy_calls("kazuha", "review my changes", []), [("kazuha.review", {})])
        self.assertEqual(skills.legacy_calls("kazuha", "show the git diff", []), [("kazuha.review", {})])
        self.assertEqual(skills.legacy_calls("yunjin", "scan portfolio", ["scan_links"]), [("yunjin.audit", {})])


class TestRunBlockingMoved(unittest.TestCase):

    def test_heavy_jobs_owns_run_blocking_and_ciel_reuses_it(self):
        import heavy_jobs
        self.assertTrue(asyncio.iscoroutinefunction(heavy_jobs.run_blocking))
        with self.assertRaises(TimeoutError) as err:
            asyncio.run(heavy_jobs.run_blocking(time.sleep, 0.3, timeout=0.05, label="Nap"))
        self.assertEqual(str(err.exception), "Nap timed out after 0.05 s")
        self.assertEqual(asyncio.run(heavy_jobs.run_blocking(sum, [1, 2], timeout=1, label="Sum")), 3)


class JobHuntSkillTest(unittest.TestCase):
    def test_hunt_skill_registered(self):
        skill = skills.SKILLS["chaewon.hunt"]
        self.assertIs(skill.changes_things, False)  # scheduled runs must never hit the confirm gate
        self.assertIs(skill.heavy, True)
        with mock.patch("job_hunt.hunt", return_value=SkillResult("done", "ok")) as hunt:
            res = skills.run_skill("chaewon.hunt", {"slot": "x"})
        self.assertEqual(res.status, "done")
        hunt.assert_called_once_with(slot="x")


if __name__ == "__main__":
    unittest.main()
