"""
ADR-001 R1: the shared skill registry (skills.py) that the Discord bots and Ciel both
call. Hermetic: throwaway skills only, no engines, no network.
Run: python -m pytest -q test_skills.py
"""

import asyncio
import time
import unittest
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
        with mock.patch.object(self.ee, "is_healthy", return_value="from is_healthy"):
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

    def test_legacy_calls_pick_eunchae_actions_like_the_old_agent_job(self):
        # Transitional (deleted in R5): the keyword pick Ciel's _agent_job made.
        self.assertEqual(skills.legacy_calls("eunchae", "check vitals", []), [("eunchae.vitals", {})])
        self.assertEqual(skills.legacy_calls("eunchae", "run a syntax check", []), [("eunchae.compile_all", {})])
        self.assertEqual(skills.legacy_calls("eunchae", "how is my pc", ["run_tests"]),
                         [("eunchae.compile_all", {})])
        self.assertEqual(skills.legacy_calls("kazuha", "git status", ["git_status"]), [])  # not migrated yet


class TestRunBlockingMoved(unittest.TestCase):

    def test_heavy_jobs_owns_run_blocking_and_ciel_reuses_it(self):
        import heavy_jobs
        self.assertTrue(asyncio.iscoroutinefunction(heavy_jobs.run_blocking))
        with self.assertRaises(TimeoutError) as err:
            asyncio.run(heavy_jobs.run_blocking(time.sleep, 0.3, timeout=0.05, label="Nap"))
        self.assertEqual(str(err.exception), "Nap timed out after 0.05 s")
        self.assertEqual(asyncio.run(heavy_jobs.run_blocking(sum, [1, 2], timeout=1, label="Sum")), 3)


if __name__ == "__main__":
    unittest.main()
