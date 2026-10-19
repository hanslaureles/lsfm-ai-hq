"""
Regression tests for CielOrchestrator.execute_mission.
Hermetic: LLM, Obsidian, TTS, and the optional engine modules are stubbed, so no
network, audio, or vault I/O runs. The stubs are removed from sys.modules after
import so other test files can install their own.
Run: python -m unittest test_ciel_orchestrator.py
"""

import asyncio
import json
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

from obsidian_client import VAULT_PATHS  # the real path map; the client itself is stubbed below

SYNTHESIS_JSON = json.dumps({
    "japanese_voice": "「告。」完了。",
    "english_voice": "Notice: Done.",
    "display_text": "Done.",
})


def _call_groq(prompt, system_instruction="", **kwargs):
    if "routing core" in system_instruction:
        raise RuntimeError("router offline")  # forces the deterministic keyword router
    return SYNTHESIS_JSON


def _query_llm_structured(prompt, system_instruction="", temperature=0.4, *, agent, on_delta=None, meta=None):
    """Stands in for llm_client.query_llm_structured, which Ciel's call_llm calls as agent="ciel"."""
    text = _call_groq(prompt, system_instruction)
    return types.SimpleNamespace(text=text, provider="groq", model="qwen/qwen3.8-27b", ttft_ms=None,
                                 total_ms=1.0, fallback_chain=("groq/qwen/qwen3.8-27b",))


class FakeObsidian:
    def __init__(self):
        self.fail = False

    def _maybe_fail(self):
        if self.fail:
            raise ConnectionError("Obsidian REST API unreachable")

    def get_file(self, path):
        self._maybe_fail()
        return ""

    def ensure_daily_log(self):
        self._maybe_fail()
        return f"{VAULT_PATHS['daily_logs']}/today.md"

    def list_dir(self, path):
        self._maybe_fail()
        return ["a.md", "b.md"]

    def search_simple(self, query):
        self._maybe_fail()
        return []

    def append_file(self, path, text):
        self._maybe_fail()
        return True

    def log_session(self, *args, **kwargs):
        self._maybe_fail()


TTS_CALLS = []


async def _tts_bilingual(ja_text="", en_text="", output_filename="ciel_reply.mp3", **kwargs):
    TTS_CALLS.append(output_filename)
    return output_filename


CLIPS = []   # (text, lang, filename, gap_before) per streamed sentence
JOINED = []  # (clip filenames, output filename) per concat


async def _speak_clip(text, lang, output_filename, gap_before=False):
    CLIPS.append((text, lang, output_filename, gap_before))
    return output_filename


async def _concat_clips(clip_filenames, output_filename):
    JOINED.append((list(clip_filenames), output_filename))
    return output_filename


STUBS = {
    "llm_client": {"query_llm": lambda *a, **k: "", "query_llm_structured": _query_llm_structured,
                   "get_brain_status": lambda: {"mode": "test"}},
    "obsidian_client": {"ObsidianClient": FakeObsidian, "VAULT_PATHS": VAULT_PATHS},
    "voice_engine": {"text_to_speech": _tts_bilingual, "text_to_speech_bilingual": _tts_bilingual,
                     "speak_clip": _speak_clip, "concat_clips": _concat_clips},
    "proactive_sentinel": {"evaluate_proactive_suggestions": lambda: [],
                           "generate_appraisal_summary": lambda s: "No issues flagged."},
    # Optional engines: empty modules make each `from x import y` raise ImportError,
    # so the orchestrator falls back to None and tests patch in what they need.
    "eunchae_engine": {}, "yunjin_engine": {}, "sakura_engine": {}, "pdf_engine": {},
    "git_sentinel": {}, "kazuha_engine": {}, "rag_engine": {}, "gmail_engine": {},
}

def _module(name, attrs):
    mod = types.ModuleType(name)
    for key, val in attrs.items():
        setattr(mod, key, val)
    return mod


_saved = {name: sys.modules.get(name) for name in [*STUBS, "ciel_orchestrator", "skills"]}
for _name, _attrs in STUBS.items():
    sys.modules[_name] = _module(_name, _attrs)
sys.modules.pop("ciel_orchestrator", None); sys.modules.pop("skills", None)  # skills re-imported under the same stubs
import ciel_orchestrator  # noqa: E402
for _name, _orig in _saved.items():
    if _orig is None:
        sys.modules.pop(_name, None)
    else:
        sys.modules[_name] = _orig


def _owner(name):
    """Engine functions moved into skills.py (ADR-001 R1); everything else still lives on ciel_orchestrator."""
    skills_mod = ciel_orchestrator.skills
    return skills_mod if hasattr(skills_mod, name) and not hasattr(ciel_orchestrator, name) else ciel_orchestrator


def skill_timeout(name, seconds):
    """A migrated agent's timeout is its skill's timeout_s."""
    import dataclasses
    skills_mod = ciel_orchestrator.skills
    return mock.patch.dict(skills_mod.SKILLS, {name: dataclasses.replace(skills_mod.SKILLS[name], timeout_s=seconds)})


class MissionTestCase(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        # execute_mission imports proactive_sentinel lazily; pin our stub for the
        # duration of each test (another test file may have installed its own).
        self.enterContext(mock.patch.dict(sys.modules, {
            "proactive_sentinel": _module("proactive_sentinel", STUBS["proactive_sentinel"]),
        }))
        TTS_CALLS.clear()
        CLIPS.clear()
        JOINED.clear()
        self.ciel = ciel_orchestrator.CielOrchestrator()
        self.events = []

    async def run_mission(self, prompt):
        async def on_event(ev):
            self.events.append(ev)
        return await self.ciel.execute_mission(prompt, event_callback=on_event)

    async def run_confirmed(self, prompt):
        """A prompt whose action changes things, then "confirm" (ADR-001 R4): the confirmed run's result."""
        held = await self.run_mission(prompt)
        self.assertIn("pending_confirm", held)
        return await self.run_mission("confirm")

    def completes(self):
        return [e for e in self.events if e["type"] == "ciel_complete"]


class TestMissionId(MissionTestCase):

    async def test_result_and_every_event_share_one_mission_id(self):
        result = await self.run_mission("what is a b-tree?")
        mid = result.get("mission_id")
        self.assertTrue(mid)
        self.assertTrue(self.events)
        self.assertEqual({e.get("mission_id") for e in self.events}, {mid})
        self.assertEqual(len(self.completes()), 1)
        self.assertEqual(self.completes()[0]["mission_id"], mid)

    async def test_missions_get_distinct_ids_and_audio_files(self):
        # Back-to-back missions land in the same second; the old int(time.time())
        # filename made the second overwrite the first.
        a = await self.run_mission("what is a b-tree?")
        b = await self.run_mission("what is a trie?")
        self.assertNotEqual(a["mission_id"], b["mission_id"])
        self.assertNotEqual(a["audio_url"], b["audio_url"])
        self.assertIn(a["mission_id"], a["audio_url"])
        self.assertEqual(len(set(TTS_CALLS)), 2)

    async def test_caller_mission_id_is_used(self):
        mid = "fedcba9876543210fedcba9876543210"
        result = await self.ciel.execute_mission("what is a b-tree?", mission_id=mid)
        self.assertEqual(result["mission_id"], mid)
        self.assertEqual(result["audio_url"], f"/audio/ciel_response_{mid}.mp3")

    async def test_unsafe_caller_mission_id_never_reaches_filename(self):
        result = await self.ciel.execute_mission("what is a b-tree?", mission_id="../../evil")
        self.assertRegex(result["mission_id"], r"^[0-9a-f]{32}$")
        self.assertNotIn("..", result["audio_url"])
        self.assertNotIn("evil", TTS_CALLS[-1])

    async def test_fast_paths_carry_mission_id(self):
        for prompt in ["clear memory", "any suggestions?"]:
            self.events.clear()
            result = await self.run_mission(prompt)
            self.assertTrue(result.get("mission_id"), prompt)
            self.assertEqual(self.completes()[0]["mission_id"], result["mission_id"], prompt)


MASKING_WORDS = ["verified", "nominal", "synchronized", "standby", "100%", "840ms", "handled with fallback"]


class TestHonestFailures(MissionTestCase):
    """A tool that could not run must reach the HUD as "failed", never as success text."""

    def patch(self, name, value):
        self.enterContext(mock.patch.object(_owner(name), name, value))

    async def agent_end(self, prompt, agent):
        result = await self.run_mission(prompt)
        ends = [e for e in self.events if e["type"] == "agent_state" and e.get("agent") == agent
                and e.get("status") != "active"]
        self.assertEqual(len(ends), 1, f"{agent} should finish exactly once")
        return result, ends[0]

    def assert_failed(self, result, end, agent):
        self.assertEqual(end["status"], "failed", end["result"])
        self.assertEqual(result["agent_status"][agent], "failed")
        self.assertIn(agent, result["failed_agents"])
        for word in MASKING_WORDS:
            self.assertNotIn(word.lower(), end["result"].lower(), f"{agent}: {end['result']}")

    async def test_missing_engines_report_failed(self):
        # Each engine import fell back to None; the old code printed success text
        # ("Design tokens verified ... 100%", "standby (840ms TTFT benchmark)", ...).
        cases = [
            ("check vitals", "eunchae", "get_system_vitals"),
            ("scan portfolio", "yunjin", "audit_full_portfolio"),
            ("check inbox", "sakura", "scan_morning_inbox"),
            ("compile resume", "chaewon", "compile_master_resume"),
            ("git status", "kazuha", "get_git_info"),
        ]
        for prompt, agent, engine in cases:
            with self.subTest(agent=agent):
                self.events.clear()
                self.patch(engine, None)
                if agent == "chaewon":  # R4: a rebuild waits for "confirm" before it runs (and then fails)
                    pended = await self.run_mission(prompt)
                    self.assertIn("pending_confirm", pended)
                    self.events.clear()
                    prompt = "confirm"
                result, end = await self.agent_end(prompt, agent)
                self.assert_failed(result, end, agent)

    async def test_obsidian_outage_is_not_reported_as_verified(self):
        outage = FakeObsidian()
        outage.fail = True
        self.enterContext(mock.patch.object(ciel_orchestrator.skills, "_vault", lambda: outage))  # R3: skills read the vault
        self.ciel.obsidian.fail = True
        result, end = await self.agent_end("morning briefing", "sakura")
        self.assert_failed(result, end, "sakura")
        self.assertIn("unreachable", end["result"])

    async def test_unconfigured_gmail_is_not_a_clean_inbox(self):
        self.patch("scan_morning_inbox", lambda: {"ok": False, "status": "Gmail API credentials not configured",
                                                  "unread_count": 0, "job_alerts": [], "highlights": []})
        result, end = await self.agent_end("check inbox", "sakura")
        self.assert_failed(result, end, "sakura")
        self.assertNotIn("clean", end["result"].lower())

    async def test_clean_inbox_still_reports_done(self):
        self.patch("scan_morning_inbox", lambda: {"ok": True, "status": "Clean Inbox", "unread_count": 0,
                                                  "job_alerts": [], "highlights": []})
        result, end = await self.agent_end("check inbox", "sakura")
        self.assertEqual(end["status"], "done")
        self.assertEqual(result["failed_agents"], [])

    async def test_tool_exception_reports_failed_with_error(self):
        def boom():
            raise OSError("psutil probe crashed")
        self.patch("get_system_vitals", boom)
        result, end = await self.agent_end("check vitals", "eunchae")
        self.assert_failed(result, end, "eunchae")
        self.assertIn("psutil probe crashed", end["result"])

    async def test_problems_found_report_attention(self):
        self.patch("audit_full_portfolio", lambda: {"success": True, "score": 70, "broken_images_count": 1, "broken_images": ["index.html -> x.png"], "report": "r", "saved_file": "a.md", "filename": "a.md"})
        result, end = await self.agent_end("scan portfolio", "yunjin")
        self.assertEqual(end["status"], "attention")
        self.assertEqual(result["failed_agents"], [])

    async def test_synthesis_prompt_marks_failed_findings(self):
        prompts = []

        def recording_groq(prompt, system_instruction="", **kwargs):
            prompts.append(prompt)
            return _call_groq(prompt, system_instruction, **kwargs)

        self.patch("call_llm", recording_groq)
        self.patch("get_system_vitals", None)
        await self.run_mission("check vitals")
        self.assertIn('"status": "failed"', prompts[-1])
        self.assertIn("Never describe a failed check as verified", prompts[-1])

    async def test_mission_failure_returns_error_and_emits_ciel_error(self):
        def groq_down(prompt, system_instruction="", **kwargs):
            raise ConnectionError("Groq API unreachable")

        self.patch("call_llm", groq_down)
        result = await self.run_mission("what is a b-tree?")  # must not raise
        self.assertIn("Groq API unreachable", result["error"])
        self.assertNotIn("reply", result)
        errors = [e for e in self.events if e["type"] == "ciel_error"]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["mission_id"], result["mission_id"])
        self.assertEqual(self.completes(), [])


class TestEventLoopStaysResponsive(MissionTestCase):
    """
    ciel_server serves every HUD client (other missions, telemetry, WS pings, the
    sentinel) from one event loop, so a blocking tool or LLM call must run off it.
    A ticker coroutine counts how often the loop got control during a mission.
    """

    SLOW_S = 0.5

    def patch(self, name, value):
        self.enterContext(mock.patch.object(_owner(name), name, value))

    def slow(self, value=None):
        def fn(*args, **kwargs):
            time.sleep(self.SLOW_S)
            return value
        return fn

    async def ticks_during(self, coro):
        ticks = 0

        async def ticker():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.02)
                ticks += 1

        task = asyncio.create_task(ticker())
        await asyncio.sleep(0)
        try:
            result = await coro
        finally:
            task.cancel()
        return result, ticks

    def assert_loop_ran(self, ticks):
        # A blocked loop gets ~0 ticks; a free one gets ~SLOW_S / 0.02 = 25.
        self.assertGreaterEqual(ticks, 10, "event loop was blocked during the mission")

    async def test_slow_tool_does_not_block_loop(self):
        self.patch("get_system_vitals", self.slow({"healthy": True}))
        result, ticks = await self.ticks_during(self.run_mission("check vitals"))
        self.assertEqual(result["agent_status"]["eunchae"], "done")
        self.assert_loop_ran(ticks)

    async def test_slow_llm_does_not_block_loop(self):
        def slow_groq(prompt, system_instruction="", **kwargs):
            time.sleep(self.SLOW_S)
            return _call_groq(prompt, system_instruction, **kwargs)

        self.patch("call_llm", slow_groq)
        result, ticks = await self.ticks_during(self.run_mission("what is a b-tree?"))
        self.assertEqual(result["reply"], "Done.")
        self.assert_loop_ran(ticks)

    async def test_slow_web_search_does_not_block_loop(self):
        self.ciel.ciel_web_search = self.slow([{"title": "t", "snippet": "s", "url": "u"}])
        result, ticks = await self.ticks_during(self.run_mission("search the web for raft consensus"))
        self.assertIn("[t](u)", result["agent_results"]["web_search_intelligence"])
        self.assert_loop_ran(ticks)

    async def test_slow_weather_does_not_block_loop(self):
        self.ciel.ciel_get_weather = self.slow({"success": True, "raw_summary": "Clear, 25C"})
        result, ticks = await self.ticks_during(self.run_mission("what's the weather in Tokyo?"))
        self.assertEqual(result["agent_results"]["meteorological_telemetry"], "Clear, 25C")
        self.assert_loop_ran(ticks)

    async def test_independent_agents_run_concurrently(self):
        async def plan(prompt, history=None):
            return {"intent_type": "agent_delegation", "agents_needed": ["eunchae", "kazuha"],
                    "tool_targets": ["eunchae.vitals", "kazuha.git_status"], "direct_obsidian": None,
                    "calls": [["eunchae.vitals", {}], ["kazuha.git_status", {}]]}

        self.ciel.analyze_and_plan = plan
        self.patch("get_system_vitals", self.slow({"healthy": True}))
        self.patch("get_git_info", self.slow({"is_clean": True, "branch": "main", "staged": [], "unstaged": [], "untracked": [], "total_commits": 1}))
        self.patch("get_git_diff", lambda: "")
        self.patch("scan_security_risks", lambda diff: [])
        started = time.perf_counter()
        result = await self.run_mission("check vitals and git status")
        elapsed = time.perf_counter() - started
        self.assertEqual(result["agent_status"], {"eunchae": "done", "kazuha": "done"})
        self.assertEqual(list(result["agent_results"]), ["eunchae", "kazuha"])
        self.assertLess(elapsed, 2 * self.SLOW_S * 0.8, "agents ran one after another")


class TestVaultPaths(MissionTestCase):
    """6A-2: Ciel's profile / preferences / rules reads go to the notes' real home."""

    async def test_brain_reads_use_the_vault_path_map(self):
        reads = []

        def get_file(path):
            reads.append(path)
            return f"note at {path}"

        async def plan(prompt, history=None):
            return {"intent_type": "obsidian_brain", "agents_needed": [], "direct_obsidian": None,
                    "tool_targets": ["ciel_read_rules", "ciel_read_profile", "ciel_read_preferences"]}

        self.ciel.obsidian.get_file = get_file
        self.ciel.analyze_and_plan = plan
        await self.run_mission("read my rules, profile and preferences")
        self.assertEqual(sorted(reads), sorted(VAULT_PATHS[k] for k in ("learned_rules", "profile", "preferences")))


class TestHudSignals(MissionTestCase):
    """5E-4a: measured per-mission values the HUD shows instead of random digits."""

    def patch(self, name, value):
        self.enterContext(mock.patch.object(_owner(name), name, value))

    def agent_end(self, agent):
        ends = [e for e in self.events if e["type"] == "agent_state" and e.get("agent") == agent
                and e.get("status") != "active"]
        self.assertEqual(len(ends), 1)
        return ends[0]

    async def test_a_finished_agent_reports_its_measured_time(self):
        def vitals(*args, **kwargs):
            time.sleep(0.05)
            return {"healthy": True}
        self.patch("get_system_vitals", vitals)
        await self.run_mission("check vitals")
        elapsed = self.agent_end("eunchae")["elapsed_ms"]
        self.assertIsInstance(elapsed, float)
        self.assertGreaterEqual(elapsed, 45)

    async def test_a_timed_out_agent_reports_its_time_too(self):
        self.enterContext(skill_timeout("eunchae.vitals", 0.1))
        self.patch("get_system_vitals", TestTimeouts.hang({"healthy": True}))
        await self.run_mission("check vitals")
        end = self.agent_end("eunchae")
        self.assertEqual(end["status"], "failed")
        self.assertGreaterEqual(end["elapsed_ms"], 90)
        self.assertLess(end["elapsed_ms"], 400)  # the timeout ended it, not the hang

    async def test_the_time_covers_the_work_not_event_delivery(self):
        # Codex 5E-4 S1: a slow HUD callback on the "active" status events must not count.
        self.patch("get_system_vitals", lambda *a, **k: {"healthy": True})

        async def slow_status(ev):
            self.events.append(ev)
            if ev["type"] == "agent_state" and ev.get("status") == "active":
                await asyncio.sleep(0.2)
        await self.ciel.execute_mission("check vitals", event_callback=slow_status)
        self.assertLess(self.agent_end("eunchae")["elapsed_ms"], 100)

    async def test_no_work_started_means_no_time(self):
        async def plan(prompt, history=None):
            return {"intent_type": "agent_delegation", "agents_needed": ["eunchae"], "tool_targets": ["eunchae.vitals"],
                    "direct_obsidian": None, "calls": [["eunchae.vitals", {}]]}
        self.ciel.analyze_and_plan = plan
        self.enterContext(mock.patch.dict(ciel_orchestrator.skills.SKILLS, clear=True))  # the lookup fails first
        await self.run_mission("check vitals")
        end = self.agent_end("eunchae")
        self.assertEqual(end["status"], "failed")
        self.assertIsNone(end["elapsed_ms"])  # nothing ran: no measured time, not a made-up 0

    async def test_complete_carries_the_synthesis_telemetry(self):
        def groq_with_fallback(prompt, system_instruction="", **kwargs):
            if "routing core" in system_instruction:
                raise RuntimeError("router offline")
            meta = kwargs.get("meta")
            self.assertIsNotNone(meta, "synthesis must pass meta= to call_llm")
            meta["chain"] = ["groq/qwen/qwen3.8-27b", "groq/openai/gpt-oss-120b"]
            meta.update(provider="groq", model="openai/gpt-oss-120b", ttft_ms=612.0)
            return SYNTHESIS_JSON
        self.patch("call_llm", groq_with_fallback)
        await self.run_mission("what is a b-tree?")
        self.assertEqual(self.completes()[0]["llm"], {
            "provider": "groq", "model": "openai/gpt-oss-120b",
            "fallback_chain": ["groq/qwen/qwen3.8-27b", "groq/openai/gpt-oss-120b"], "ttft_ms": 612.0})

    async def test_fast_paths_send_no_llm(self):
        await self.run_mission("clear memory")
        self.assertNotIn("llm", self.completes()[0])


class TestTimeouts(MissionTestCase):
    """A call that hangs must end as an honest failure that names the timeout."""

    def patch(self, name, value):
        self.enterContext(mock.patch.object(_owner(name), name, value))

    @staticmethod
    def hang(value=None):
        def fn(*args, **kwargs):
            time.sleep(0.4)
            return value
        return fn

    async def test_agent_timeout_reports_failed(self):
        self.enterContext(skill_timeout("eunchae.vitals", 0.1))
        self.patch("get_system_vitals", self.hang({"healthy": True}))
        result = await self.run_mission("check vitals")
        end = [e for e in self.events if e["type"] == "agent_state" and e.get("status") not in (None, "active")]
        self.assertEqual(len(end), 1)
        self.assertEqual(end[0]["status"], "failed")
        self.assertIn("timed out after 0.1 s", end[0]["result"])
        self.assertEqual(result["failed_agents"], ["eunchae"])

    async def test_web_search_timeout_is_reported(self):
        self.patch("WEB_SEARCH_TIMEOUT_S", 0.1)
        self.ciel.ciel_web_search = self.hang([{"title": "t", "snippet": "s", "url": "u"}])
        result = await self.run_mission("search the web for raft consensus")
        self.assertIn("timed out after 0.1 s", result["agent_results"]["web_search_intelligence"])

    async def test_weather_timeout_is_reported(self):
        self.patch("WEATHER_TIMEOUT_S", 0.1)
        self.ciel.ciel_get_weather = self.hang({"success": True, "raw_summary": "Clear"})
        result = await self.run_mission("what's the weather in Tokyo?")
        self.assertIn("timed out after 0.1 s", result["agent_results"]["meteorological_telemetry"])
        self.assertFalse(result["weather_data"]["success"])

    def test_ddgs_gets_a_socket_deadline_below_the_overall_cap(self):
        created = []

        class FakeDDGS:
            def __init__(self, **kwargs):
                created.append(kwargs)

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def text(self, query, max_results=5):
                return [{"title": "t", "body": "s", "href": "u"}]

        self.enterContext(mock.patch.dict(sys.modules, {"ddgs": _module("ddgs", {"DDGS": FakeDDGS})}))
        results = self.ciel.ciel_web_search("raft consensus")
        self.assertEqual(results, [{"title": "t", "snippet": "s", "url": "u"}])
        timeout = created[0].get("timeout")
        self.assertIsNotNone(timeout)
        self.assertLess(timeout, ciel_orchestrator.WEB_SEARCH_TIMEOUT_S)

    async def test_router_timeout_falls_back_to_keyword_router(self):
        self.patch("ROUTER_TIMEOUT_S", 0.1)

        def slow_router(prompt, system_instruction="", **kwargs):
            if "routing core" in system_instruction:
                time.sleep(0.4)
                return "{}"
            return SYNTHESIS_JSON

        self.patch("call_llm", slow_router)
        self.patch("get_system_vitals", lambda: {"healthy": True})
        result = await self.run_mission("check vitals")
        self.assertEqual(result["agents_called"], ["eunchae"])

    async def test_synthesis_timeout_fails_the_mission(self):
        self.patch("SYNTHESIS_TIMEOUT_S", 0.1)
        self.patch("call_llm", self.hang(SYNTHESIS_JSON))
        result = await self.run_mission("what is a b-tree?")
        self.assertIn("timed out after 0.1 s", result["error"])
        self.assertEqual(len([e for e in self.events if e["type"] == "ciel_error"]), 1)
        self.assertEqual(self.completes(), [])


class TestAsyncSafety(MissionTestCase):
    """Hazards that only exist because missions now overlap with each other and with threads."""

    def patch(self, name, value):
        self.enterContext(mock.patch.object(_owner(name), name, value))

    async def clear_mid_mission(self, prompt):
        task = asyncio.create_task(self.run_mission(prompt))
        await asyncio.sleep(0.1)  # the mission is now waiting on its slow thread
        self.ciel.reset_memory()
        return await task

    async def test_clear_during_mission_does_not_revive_memory(self):
        def slow_groq(prompt, system_instruction="", **kwargs):
            time.sleep(0.3)
            return _call_groq(prompt, system_instruction, **kwargs)

        self.patch("call_llm", slow_groq)
        result = await self.clear_mid_mission("what is a b-tree?")
        self.assertEqual(result["reply"], "Done.")
        self.assertEqual(self.ciel.conversation_history, [])
        # A mission that starts after the clear is remembered as usual.
        await self.run_mission("what is a trie?")
        self.assertEqual(len(self.ciel.conversation_history), 1)

    async def test_clear_during_appraisal_does_not_revive_memory(self):
        def slow_suggestions():
            time.sleep(0.3)
            return []

        sys.modules["proactive_sentinel"].evaluate_proactive_suggestions = slow_suggestions
        await self.clear_mid_mission("any suggestions?")
        self.assertEqual(self.ciel.conversation_history, [])

    async def test_timed_out_resume_build_blocks_a_second_build(self):
        calls, running, peak = [], [0], [0]

        def slow_build(**kwargs):
            calls.append(kwargs)
            running[0] += 1
            peak[0] = max(peak[0], running[0])
            time.sleep(0.5)
            running[0] -= 1
            return {"success": True, "size_kb": 82.9, "pdf_path": Path("Hans_Resume.pdf")}

        self.enterContext(skill_timeout("chaewon.rebuild_resume", 0.1))  # R3: the skill's own timeout
        self.patch("compile_master_resume", slow_build)

        first = await self.run_confirmed("compile resume")
        self.assertEqual(first["agent_status"]["chaewon"], "failed")
        self.assertIn("may still finish in the background", first["agent_results"]["chaewon"])

        # The abandoned build is still running in its thread: a second one must not start.
        second = await self.run_confirmed("compile resume")
        self.assertEqual(second["agent_status"]["chaewon"], "failed")
        self.assertIn("still running", second["agent_results"]["chaewon"])
        self.assertEqual(len(calls), 1)

        await asyncio.sleep(0.6)  # the first build ends and releases the lock
        self.enterContext(skill_timeout("chaewon.rebuild_resume", 5))
        third = await self.run_confirmed("compile resume")
        self.assertEqual(third["agent_status"]["chaewon"], "done")
        self.assertEqual(len(calls), 2)
        self.assertEqual(peak[0], 1)


class TestConfirmGate(MissionTestCase):
    """ADR-001 R4: Ciel holds an action that changes things until this tab says confirm."""

    SESSION_A, SESSION_B = "a" * 32, "b" * 32

    def setUp(self):
        super().setUp()
        self.sweeps = []
        skills_mod = ciel_orchestrator.skills
        self.enterContext(mock.patch.object(skills_mod, "gmail_is_configured", lambda: True))
        self.enterContext(mock.patch.object(skills_mod, "sweep_inbox", lambda n: self.sweeps.append(n) or {
            "success": True, "total_scanned": 40, "promos_swept": 12, "newsletters_swept": 3,
            "total_swept": 15, "retained_in_inbox": 25, "categories": {}}))

    async def say(self, prompt, session=None):
        async def on_event(ev):
            self.events.append(ev)
        return await self.ciel.execute_mission(prompt, event_callback=on_event, session_id=session)

    async def test_clean_inbox_pends_and_does_not_sweep(self):
        result = await self.say("clean my inbox")
        self.assertEqual(self.sweeps, [])
        self.assertEqual(result["pending_confirm"], {"text": "Archive up to 50 promo and newsletter emails from Primary?",
                                                     "skills": ["sakura.clean"], "expires_in_s": 120})
        self.assertEqual(result["agent_status"]["sakura"], "waiting")
        self.assertEqual(result["failed_agents"], [])
        ends = [e for e in self.events if e["type"] == "agent_state" and e.get("agent") == "sakura" and e["status"] != "active"]
        self.assertEqual([e["status"] for e in ends], ["waiting"])

    async def test_confirm_runs_pending_once(self):
        await self.say("clean my inbox")
        result = await self.say("Ciel, confirm.")
        self.assertEqual(self.sweeps, [50])
        self.assertEqual(result["planner"]["source"], "confirm")
        self.assertEqual(result["agent_status"]["sakura"], "done")
        await self.say("confirm")  # nothing pending now: an ordinary prompt
        self.assertEqual(self.sweeps, [50])

    async def test_cancel_clears(self):
        await self.say("clean my inbox")
        result = await self.say("cancel")
        self.assertIn("Nothing was changed", result["reply"])
        await self.say("confirm")
        self.assertEqual(self.sweeps, [])

    async def test_pending_expires_after_ttl(self):
        await self.say("clean my inbox")
        with mock.patch.object(ciel_orchestrator.time, "time",
                               return_value=time.time() + ciel_orchestrator.PENDING_TTL_S + 1):
            await self.say("confirm")
        self.assertEqual(self.sweeps, [])

    async def test_pending_is_per_session(self):  # Review Focus 3
        await self.say("clean my inbox", session=self.SESSION_A)
        await self.say("confirm", session=self.SESSION_B)
        self.assertEqual(self.sweeps, [])
        await self.say("confirm", session=self.SESSION_A)
        self.assertEqual(self.sweeps, [50])

    PAUSE_AT = {  # where a running "clean my inbox" is paused
        "first_event": lambda ev: ev["type"] == "ciel_state",  # before anything else ran
        "agent": lambda ev: ev["type"] == "agent_state" and ev.get("agent") == "sakura" and ev["status"] == "active",
        "synthesis": lambda ev: ev["type"] == "ciel_state" and ev.get("state") == "synthesizing",  # after the hold
    }

    async def paused_clean(self, at="agent"):
        """Starts "clean my inbox" and pauses it at the PAUSE_AT point."""
        reached, release = asyncio.Event(), asyncio.Event()

        async def on_event(ev):
            self.events.append(ev)
            if self.PAUSE_AT[at](ev) and not reached.is_set():
                reached.set()
                await release.wait()

        task = asyncio.create_task(self.ciel.execute_mission("clean my inbox", event_callback=on_event))
        await asyncio.wait_for(reached.wait(), 5)
        return task, release

    async def test_reset_during_a_mission_drops_its_held_action(self):  # Codex R4 F1
        for reset in ("say", "endpoint"):  # both reset-memory entry points
            with self.subTest(reset=reset):
                self.sweeps.clear()
                task, release = await self.paused_clean()
                if reset == "say":
                    await self.say("reset memory")
                else:
                    self.ciel.reset_memory()
                release.set()
                result = await task
                self.assertNotIn("pending_confirm", result)
                self.assertEqual(result["agent_status"]["sakura"], "failed")
                self.assertIn("Nothing was changed", result["agent_results"]["sakura"])
                await self.say("confirm")
                self.assertEqual(self.sweeps, [])

    async def test_reset_or_cancel_at_any_point_of_a_mission_drops_its_held_action(self):  # Codex R4 F1/F2
        for at in ("first_event", "synthesis"):
            for clear in ("reset", "cancel"):
                with self.subTest(at=at, clear=clear):
                    self.ciel.reset_memory()
                    self.sweeps.clear()
                    if clear == "cancel":
                        await self.say("clean my inbox")  # an action is held, so "cancel" is a reply
                    task, release = await self.paused_clean(at)
                    if clear == "reset":
                        self.ciel.reset_memory()
                    else:
                        await self.say("cancel")
                    release.set()
                    result = await task
                    self.assertNotIn("pending_confirm", result)
                    self.assertEqual(result["agent_status"]["sakura"], "failed")
                    self.assertEqual(result["failed_agents"], ["sakura"])
                    ends = [e["status"] for e in self.events if e["type"] == "agent_state"
                            and e.get("mission_id") == result["mission_id"] and e["status"] != "active"]
                    self.assertEqual(ends[-1], "failed")
                    await self.say("confirm")
                    self.assertEqual(self.sweeps, [])

    async def test_a_replaced_held_action_is_not_offered(self):  # Codex R4 F2: only the newest can be confirmed
        task, release = await self.paused_clean("synthesis")
        newer = await self.say("mass cleanse my inbox")
        self.assertEqual(newer["pending_confirm"]["skills"], ["sakura.cleanse"])
        release.set()
        self.assertNotIn("pending_confirm", await task)

    async def test_cancel_during_a_mission_drops_its_held_action(self):  # Codex R4 F1
        await self.say("clean my inbox")  # an action is held, so "cancel" is a reply
        task, release = await self.paused_clean()
        await self.say("cancel")
        release.set()
        self.assertNotIn("pending_confirm", await task)
        await self.say("confirm")
        self.assertEqual(self.sweeps, [])
        # A request made after the cancel is still held and runs on confirm.
        self.assertIn("pending_confirm", await self.say("clean my inbox"))
        await self.say("confirm")
        self.assertEqual(self.sweeps, [50])

    async def test_synthesis_is_told_nothing_ran(self):
        prompts = []

        def recording(prompt, system_instruction="", **kwargs):
            prompts.append(prompt)
            return _call_groq(prompt, system_instruction, **kwargs)

        self.enterContext(mock.patch.object(ciel_orchestrator, "call_llm", recording))
        await self.say("clean my inbox")
        self.assertIn('"status": "needs_confirm"', prompts[-1])
        self.assertIn("Ask Hans to say confirm or cancel. Never say it was done.", prompts[-1])

    def test_reply_word_is_exact(self):  # Review Focus 2
        rw = ciel_orchestrator.reply_word
        for prompt in ("now", "no problem, check vitals", "yes check vitals too", "confirm the vitals", "",
                       "cancellation policy"):
            self.assertIsNone(rw(prompt), prompt)
        for prompt, word in (("Ciel, confirm.", "confirm"), ("はい", "confirm"), ("  YES! ", "confirm"),
                             ("go ahead", "confirm"), ("no", "cancel"), ("ciel cancel", "cancel"), ("キャンセル。", "cancel")):
            self.assertEqual(rw(prompt), word, prompt)


class TestRegistryPlanner(MissionTestCase):
    """ADR-001 R5: the router picks skills from the registry; anything invalid falls back to keywords."""

    def route(self, plan: dict):
        """The router LLM answers `plan`; synthesis answers as usual. Router prompts are kept."""
        self.router_prompts = []

        def call(prompt, system_instruction="", **kwargs):
            if "routing core" in system_instruction:
                self.router_prompts.append(prompt)
                return json.dumps(plan)
            return SYNTHESIS_JSON
        self.enterContext(mock.patch.object(ciel_orchestrator, "call_llm", call))

    def record(self, name):
        """Replaces a skill's run with a recorder; returns the list of kwargs it was called with."""
        import dataclasses
        from skills import SkillResult
        runs = []
        skills_mod = ciel_orchestrator.skills
        fake = dataclasses.replace(skills_mod.SKILLS[name],
                                   run=lambda **kw: runs.append(kw) or SkillResult("done", f"{name} ran"))
        self.enterContext(mock.patch.dict(skills_mod.SKILLS, {name: fake}))
        return runs

    async def test_valid_skill_plan_runs_that_skill(self):
        runs = self.record("kazuha.git_status")
        self.route({"intent_type": "agent_delegation", "skills": [{"name": "kazuha.git_status", "args": {}}]})
        result = await self.run_mission("how is the repo doing?")
        self.assertEqual(result["agents_called"], ["kazuha"])
        self.assertEqual(result["planner"]["source"], "llm")
        self.assertEqual(runs, [{}])
        self.assertIn("- kazuha.git_status (kazuha): ", self.router_prompts[0])  # the catalog is in the prompt
        self.assertNotIn("chaewon.hunt", self.router_prompts[0])
        self.assertNotIn("ciel_search_vault", self.router_prompts[0])

    async def test_args_reach_the_skill_and_query_defaults_to_the_prompt(self):
        tests, searches = self.record("eunchae.test"), self.record("sakura.vault_search")
        self.route({"intent_type": "agent_delegation",
                    "skills": [{"name": "eunchae.test", "args": {"path": "git_sentinel.py"}},
                               {"name": "sakura.vault_search"}]})
        result = await self.run_mission("test git_sentinel.py and find my WHEA notes")
        self.assertEqual(result["agents_called"], ["eunchae", "sakura"])
        self.assertEqual(tests, [{"path": "git_sentinel.py"}])
        self.assertEqual(searches, [{"query": "test git_sentinel.py and find my WHEA notes"}])

    async def test_unknown_skill_falls_back(self):
        self.route({"intent_type": "agent_delegation", "skills": [{"name": "kazuha.rm_rf", "args": {}}]})
        result = await self.run_mission("what is a b-tree?")
        self.assertEqual(result["planner"]["source"], "fallback")
        self.assertEqual(result["agents_called"], [])
        self.assertFalse([e for e in self.events if e["type"] == "agent_state"])

    async def test_bad_arg_falls_back(self):  # Review Focus 4
        runs = self.record("eunchae.test")
        for args in ({"cmd": "rm -rf /"}, {"path": 5}, "git_sentinel.py"):
            with self.subTest(args=args):
                self.route({"intent_type": "agent_delegation", "skills": [{"name": "eunchae.test", "args": args}]})
                result = await self.run_mission("what is a b-tree?")
                self.assertEqual(result["planner"]["source"], "fallback")
                self.assertEqual(runs, [])

    async def test_hidden_skill_falls_back_and_never_runs(self):
        hunts = self.record("chaewon.hunt")  # a 10-minute job that posts Discord cards (R3 ruling)
        self.route({"intent_type": "agent_delegation", "skills": [{"name": "chaewon.hunt", "args": {}}]})
        result = await self.run_mission("what is a b-tree?")
        self.assertEqual(result["planner"]["source"], "fallback")
        self.assertEqual(hunts, [])

    async def test_malformed_plans_fall_back(self):
        for plan in ({"intent_type": "agent_delegation", "skills": []},          # a delegation with nothing to run
                     {"intent_type": "agent_delegation", "skills": "kazuha.git_status"},
                     {"intent_type": "agent_delegation", "skills": [["kazuha.git_status", {}]]},
                     {"intent_type": "obsidian_brain", "direct_obsidian": "ciel_search_vault"}):  # removed in R5
            with self.subTest(plan=plan):
                self.route(plan)
                result = await self.run_mission("what is a b-tree?")
                self.assertEqual(result["planner"]["source"], "fallback")

    async def test_wrong_typed_skills_or_targets_fall_back_without_running(self):  # Codex R5 F1
        weather = []
        self.ciel.ciel_get_weather = lambda location="": weather.append(location) or {"success": True,
                                                                                    "raw_summary": "Clear"}
        for key in ("skills", "tool_targets"):
            for bad in ({}, False, 0, ""):
                with self.subTest(key=key, bad=bad):
                    plan = {"intent_type": "live_weather", "location": "Tokyo", "tool_targets": ["live_weather"]}
                    plan[key] = bad
                    self.route(plan)
                    result = await self.run_mission("東京の天気は？")
                    self.assertEqual(result["planner"]["source"], "fallback")
                    self.assertEqual(weather, [])
        for plan in ({"intent_type": "live_weather", "location": "Tokyo"},  # omitted or null: fine
                     {"intent_type": "live_weather", "location": "Tokyo", "skills": None, "tool_targets": None},
                     {"intent_type": "live_weather", "location": "Tokyo", "skills": [], "tool_targets": []}):
            with self.subTest(plan=plan):
                self.route(plan)
                result = await self.run_mission("東京の天気は？")
                self.assertEqual(result["planner"]["source"], "llm")
        self.assertEqual(weather, ["Tokyo"] * 3)

    async def test_llm_weather_and_web_search_are_kept(self):
        self.ciel.ciel_get_weather = lambda location="": {"success": True, "raw_summary": "Clear, 25C"}
        self.route({"intent_type": "live_weather", "tool_targets": ["live_weather"], "location": "Tokyo"})
        result = await self.run_mission("東京の天気は？")  # no English weather word: the router decides
        self.assertEqual(result["planner"]["source"], "llm")
        self.assertEqual(result["agent_results"]["meteorological_telemetry"], "Clear, 25C")

    async def test_again_reuses_last_calls(self):
        runs = self.record("eunchae.vitals")
        await self.run_mission("check vitals")  # the router is offline: keywords decide
        result = await self.run_mission("run that again")
        self.assertEqual(result["agents_called"], ["eunchae"])
        self.assertEqual(result["planner"]["source"], "fallback")
        self.assertEqual(runs, [{}, {}])
        self.assertEqual(self.ciel.history()[-1]["skills_called"], [["eunchae.vitals", {}]])

    async def test_again_without_a_last_skill_is_a_direct_answer(self):
        result = await self.run_mission("say that again")
        self.assertEqual(result["agents_called"], [])


class TestHeavyAgentsUseHeavyPool(MissionTestCase):
    """3E-4: Chaewon's PDF build runs on heavy_jobs.HEAVY_POOL, not the default executor."""

    async def test_resume_build_thread(self):
        import threading
        threads = []

        def build(**kwargs):
            threads.append(threading.current_thread().name)
            return {"success": True, "size_kb": 82.9, "pdf_path": Path("Hans_Resume.pdf")}

        self.enterContext(mock.patch.object(_owner("compile_master_resume"), "compile_master_resume", build))
        result = await self.run_confirmed("compile resume")
        self.assertEqual(result["agent_status"]["chaewon"], "done")
        self.assertEqual(len(threads), 1)
        self.assertTrue(threads[0].startswith("heavy"), threads)


STREAMED_REPLY = json.dumps({
    "japanese_voice": "「告。」完了しました。",
    "english_voice": "Notice: Done. All clear.",
    "display_text": "Done.",
}, ensure_ascii=False)
CLIP_URL = r"^/audio/ciel_response_[0-9a-f]{32}_\d{2}\.mp3$"


def streaming_groq(reply, split_at, pause_s=0.0, fail_after_first=False, returned=None):
    """Synthesis stub that streams `reply` in two pieces through on_delta, pausing in between."""
    def fake(prompt, system_instruction="", on_delta=None, **kwargs):
        if "routing core" in system_instruction:
            raise RuntimeError("router offline")
        on_delta(reply[:split_at])
        time.sleep(pause_s)
        if fail_after_first:
            raise RuntimeError("Groq stream broke after partial output: reset")
        on_delta(reply[split_at:])
        if returned is not None:
            returned.append(time.time())
        return reply
    return fake


class TestStreamedSpeech(MissionTestCase):
    """3D-2: spoken sentences are voiced while the synthesis LLM is still streaming."""

    def patch(self, name, value):
        self.enterContext(mock.patch.object(_owner(name), name, value))

    def clip_events(self):
        return [e for e in self.events if e["type"] == "ciel_audio_chunk"]

    async def test_first_clip_is_announced_before_the_llm_finishes(self):
        returned = []
        split = STREAMED_REPLY.index("完") + 1  # one character past 「告。」
        self.patch("call_llm", streaming_groq(STREAMED_REPLY, split, pause_s=0.5, returned=returned))
        await self.run_mission("what is a b-tree?")
        first = self.clip_events()[0]
        self.assertEqual(first["seq"], 0)
        self.assertLess(first["timestamp"], returned[0] - 0.3)  # well inside the 0.5 s the LLM was still busy

    async def test_clips_in_order_then_joined_into_the_mission_mp3(self):
        self.patch("call_llm", streaming_groq(STREAMED_REPLY, 40))
        result = await self.run_mission("what is a b-tree?")
        self.assertEqual([(t, lang, gap) for t, lang, _, gap in CLIPS], [
            ("「告。」", "ja", False), ("完了しました。", "ja", False),
            ("Notice: Done.", "en", True), ("All clear.", "en", False),  # the JA->EN pause, once
        ])
        events = self.clip_events()
        self.assertEqual([e["seq"] for e in events], [0, 1, 2, 3])
        for e in events:
            self.assertRegex(e["audio_url"], CLIP_URL)  # invariant 8: only the validated id reaches a filename
            self.assertEqual(e["mission_id"], result["mission_id"])
        self.assertLess(self.events.index(events[-1]), self.events.index(self.completes()[0]))
        self.assertEqual(JOINED, [([f for _, _, f, _ in CLIPS], f"ciel_response_{result['mission_id']}.mp3")])
        self.assertEqual(result["audio_url"], f"/audio/ciel_response_{result['mission_id']}.mp3")
        self.assertEqual(TTS_CALLS, [])  # no second, whole-reply synthesis

    async def test_clips_overlap_but_are_announced_in_speaking_order(self):
        # Earlier clips are slower here, so they finish last; the HUD must still get them in order.
        delays = {"「告。」": 0.3, "完了しました。": 0.2, "Notice: Done.": 0.1, "All clear.": 0.0}

        async def uneven_tts(text, lang, output_filename, gap_before=False):
            await asyncio.sleep(delays[text])
            CLIPS.append((text, lang, output_filename, gap_before))

        self.patch("speak_clip", uneven_tts)
        self.patch("call_llm", streaming_groq(STREAMED_REPLY, len(STREAMED_REPLY)))
        started = time.perf_counter()
        await self.run_mission("what is a b-tree?")
        elapsed = time.perf_counter() - started
        made = [t for t, _, _, _ in CLIPS]
        self.assertNotEqual(made, list(delays))  # finished out of order...
        urls = [e["audio_url"] for e in self.clip_events()]
        self.assertEqual(urls, sorted(urls))  # ...announced in order
        self.assertEqual([e["seq"] for e in self.clip_events()], [0, 1, 2, 3])
        self.assertLess(elapsed, 0.5)  # 0.6 s of clips one by one; in parallel (3 at a time) about 0.3 s

    async def test_unsafe_mission_id_never_reaches_a_clip_name(self):
        self.patch("call_llm", streaming_groq(STREAMED_REPLY, 40))
        result = await self.ciel.execute_mission("what is a b-tree?", mission_id="../../evil")
        self.assertTrue(CLIPS)
        for _, _, name, _ in CLIPS:
            self.assertRegex(name, r"^ciel_response_[0-9a-f]{32}_\d{2}\.mp3$")
            self.assertIn(result["mission_id"], name)

    async def test_missing_english_field_is_voiced_from_the_display_text(self):
        reply = json.dumps({"japanese_voice": "「告。」完了。", "display_text": "Report: B-trees stay balanced."},
                           ensure_ascii=False)
        self.patch("call_llm", streaming_groq(reply, 10))
        await self.run_mission("what is a b-tree?")
        self.assertEqual([(t, lang) for t, lang, _, _ in CLIPS],
                         [("「告。」", "ja"), ("完了。", "ja"), ("Report: B-trees stay balanced.", "en")])

    async def test_reply_without_voice_fields_uses_whole_reply_speech(self):
        # Nothing to stream (e.g. the existing non-streaming stub): the old path, unchanged.
        result = await self.run_mission("what is a b-tree?")
        self.assertEqual(CLIPS, [])
        self.assertEqual(self.clip_events(), [])
        self.assertEqual(TTS_CALLS, [f"ciel_response_{result['mission_id']}.mp3"])

    async def test_clip_failure_fails_the_mission(self):
        async def tts_down(text, lang, output_filename, gap_before=False):
            raise ConnectionError("Edge TTS unreachable")

        self.patch("speak_clip", tts_down)
        self.patch("call_llm", streaming_groq(STREAMED_REPLY, 40))
        result = await self.run_mission("what is a b-tree?")
        self.assertIn("Edge TTS unreachable", result["error"])
        self.assertEqual(self.completes(), [])
        self.assertEqual(len([e for e in self.events if e["type"] == "ciel_error"]), 1)

    async def test_synthesis_timeout_while_clips_are_in_flight(self):
        # Codex 3D NOTE: the first sentence is being voiced when synthesis times out.
        entered = []  # times at which a clip started, so the test can prove one was in flight

        async def slow_tts(text, lang, output_filename, gap_before=False):
            entered.append(time.monotonic())
            await asyncio.sleep(0.3)
            CLIPS.append((text, lang, output_filename, gap_before))

        loop_errors = []
        asyncio.get_running_loop().set_exception_handler(lambda loop, ctx: loop_errors.append(ctx.get("message")))
        self.enterContext(mock.patch.object(ciel_orchestrator, "SYNTHESIS_TIMEOUT_S", 0.1))
        self.patch("speak_clip", slow_tts)
        split = STREAMED_REPLY.index("完") + 1
        self.patch("call_llm", streaming_groq(STREAMED_REPLY, split, pause_s=0.5))
        result = await self.run_mission("what is a b-tree?")
        failed_at = time.monotonic()
        self.assertIn("timed out", result["error"])
        # Codex FIX: prove a clip really was in flight when synthesis timed out.
        self.assertEqual(len(entered), 1)
        self.assertLess(entered[0], failed_at)
        self.assertLess(failed_at - entered[0], 0.3)  # it started, but its 0.3 s had not run out
        self.assertEqual(len([e for e in self.events if e["type"] == "ciel_error"]), 1)
        self.assertEqual(self.completes(), [])
        await asyncio.sleep(0.6)  # past the stub's pause and the clip's 0.3 s
        self.assertEqual(CLIPS, [])           # the in-flight clip was cancelled, not finished
        self.assertEqual(self.clip_events(), [])
        self.assertEqual(JOINED, [])
        import gc; gc.collect()               # surfaces "Task exception was never retrieved"
        await asyncio.sleep(0)
        self.assertEqual(loop_errors, [])

    async def test_stream_break_fails_the_mission_and_stops_speaking(self):
        self.patch("call_llm", streaming_groq(STREAMED_REPLY, 40, fail_after_first=True))
        result = await self.run_mission("what is a b-tree?")
        self.assertIn("broke after partial output", result["error"])
        self.assertEqual(self.completes(), [])
        self.assertEqual(JOINED, [])
        await asyncio.sleep(0.05)
        spoken = len(CLIPS)
        await asyncio.sleep(0.1)
        self.assertEqual(len(CLIPS), spoken)  # the clip worker was stopped


class TestSessions(MissionTestCase):
    """6C: each HUD tab (session id) has its own conversation; the plan says who chose it."""

    A, B = "a" * 32, "b" * 32
    ROUTER_JSON = json.dumps({"intent_type": "general_knowledge", "agents_needed": [], "action_summary": "Answer.",
                              "requires_real_tools": False, "tool_targets": ["direct_knowledge"],
                              "direct_obsidian": None})

    def record_prompts(self):
        prompts = []

        def call_groq(prompt, system_instruction="", **kwargs):
            prompts.append(prompt)
            return _call_groq(prompt, system_instruction, **kwargs)

        self.enterContext(mock.patch.object(ciel_orchestrator, "call_llm", call_groq))
        return prompts

    async def ask(self, prompt, session):
        async def on_event(ev):
            self.events.append(ev)
        return await self.ciel.execute_mission(prompt, event_callback=on_event, session_id=session)

    async def test_a_tab_never_sees_another_tabs_turns(self):
        await self.ask("what is a b-tree?", self.A)
        prompts = self.record_prompts()
        await self.ask("what is a skip list?", self.B)
        self.assertTrue(prompts)  # the router and synthesis prompts of B's mission
        self.assertFalse([p for p in prompts if "b-tree" in p], "A's turn leaked into B's prompts")
        self.assertEqual([len(self.ciel.history(s)) for s in (self.A, self.B, None)], [1, 1, 0])

    async def test_a_follow_up_uses_its_own_tabs_turns(self):
        await self.ask("what is a b-tree?", self.A)
        await self.ask("what is a skip list?", self.B)
        prompts = self.record_prompts()
        await self.ask("tell me more about that", self.A)
        self.assertTrue([p for p in prompts if "b-tree" in p])
        self.assertFalse([p for p in prompts if "skip list" in p])

    async def test_clearing_one_tab_keeps_the_other(self):
        await self.ask("what is a b-tree?", self.A)
        await self.ask("what is a skip list?", self.B)
        self.ciel.reset_memory(self.A)
        self.assertEqual(self.ciel.history(self.A), [])
        self.assertEqual(len(self.ciel.history(self.B)), 1)
        result = await self.ask("clear memory", self.B)  # the spoken reset clears only its own tab
        self.assertEqual(result["memory_turns"], 0)
        self.assertEqual(self.ciel.history(self.B), [])

    async def test_a_bad_or_missing_session_id_is_the_default_conversation(self):
        await self.ask("what is a b-tree?", "../not-an-id")
        await self.ask("what is a skip list?", None)
        self.assertEqual(len(self.ciel.conversation_history), 2)
        self.assertEqual(list(self.ciel.conversations), [ciel_orchestrator.DEFAULT_SESSION])

    def test_the_least_recently_used_conversation_goes_past_the_cap(self):
        cap = ciel_orchestrator.MAX_CONVERSATIONS
        ids = [f"{i:032x}" for i in range(cap + 1)]
        for sid in ids[:cap]:
            self.ciel.conversation(sid).turns.append({"n": sid})
        self.ciel.conversation(ids[0])  # used again: now the newest
        self.ciel.conversation(ids[cap])
        self.assertEqual(len(self.ciel.conversations), cap)
        self.assertNotIn(ids[1], self.ciel.conversations)
        self.assertEqual(self.ciel.history(ids[0]), [{"n": ids[0]}])
        self.assertEqual(self.ciel.history(ids[1]), [])  # reading does not create it again
        self.assertNotIn(ids[1], self.ciel.conversations)

    def planner_events(self):
        return [e["planner"] for e in self.events if e["type"] == "plan_formed"]

    async def test_planner_llm_reports_its_model_and_chain(self):
        def call_groq(prompt, system_instruction="", meta=None, **kwargs):
            if "routing core" in system_instruction:
                meta.setdefault("chain", []).extend(["groq/qwen/qwen3.8-27b", "groq/llama-3.1-8b-instant"])
                meta.update(provider="groq", model="llama-3.1-8b-instant")
                return self.ROUTER_JSON
            return _call_groq(prompt, system_instruction, **kwargs)

        self.enterContext(mock.patch.object(ciel_orchestrator, "call_llm", call_groq))
        result = await self.ask("what is a b-tree?", self.A)
        expected = {"source": "llm", "provider": "groq", "model": "llama-3.1-8b-instant",
                    "fallback_chain": ["groq/qwen/qwen3.8-27b", "groq/llama-3.1-8b-instant"]}
        self.assertEqual(result["planner"], expected)
        self.assertEqual(self.planner_events(), [expected])

    async def test_planner_fallback_when_the_router_call_fails(self):
        def call_groq(prompt, system_instruction="", meta=None, **kwargs):
            if "routing core" in system_instruction:
                meta.setdefault("chain", []).append("groq/qwen/qwen3.8-27b")
                raise RuntimeError("router offline")
            return _call_groq(prompt, system_instruction, **kwargs)

        self.enterContext(mock.patch.object(ciel_orchestrator, "call_llm", call_groq))
        result = await self.ask("what is a b-tree?", self.A)
        self.assertEqual(result["planner"], {"source": "fallback", "provider": None, "model": None,
                                             "fallback_chain": ["groq/qwen/qwen3.8-27b"]})

    async def test_planner_keyword_when_no_model_was_asked(self):
        result = await self.ask("clear memory", self.A)
        self.assertEqual(result["planner"]["source"], "keyword")
        self.assertEqual(self.planner_events(), [])  # the reset path ends before plan_formed


class TestSharedDispatcher(MissionTestCase):
    """ADR-001 R-1: Ciel calls the bots' dispatcher, so a Groq outage falls back to Gemini."""

    async def test_ciel_calls_dispatcher_as_ciel(self):
        agents = []

        def recording(prompt, system_instruction="", temperature=0.4, *, agent, on_delta=None, meta=None):
            agents.append(agent)
            return _query_llm_structured(prompt, system_instruction, agent=agent)
        self.enterContext(mock.patch.object(ciel_orchestrator, "query_llm_structured", recording))
        await self.run_mission("what is a b-tree?")
        self.assertEqual(agents, ["ciel", "ciel"])  # router, then synthesis

    async def test_gemini_answer_reaches_hud_fields(self):
        def groq_down_gemini_answers(prompt, system_instruction="", temperature=0.4, *, agent,
                                     on_delta=None, meta=None):
            if "routing core" in system_instruction:
                raise RuntimeError("router offline")
            meta.setdefault("chain", []).extend(["groq/qwen/qwen3.8-27b", "gemini/gemini-3.6-flash"])
            meta.update(provider="gemini", model="gemini-3.6-flash")
            return types.SimpleNamespace(text=SYNTHESIS_JSON, provider="gemini", model="gemini-3.6-flash",
                                         ttft_ms=None, total_ms=1.0, fallback_chain=tuple(meta["chain"]))
        self.enterContext(mock.patch.object(ciel_orchestrator, "query_llm_structured", groq_down_gemini_answers))
        result = await self.run_mission("what is a b-tree?")
        self.assertEqual(result["llm"]["provider"], "gemini")
        self.assertEqual(result["llm"]["fallback_chain"], ["groq/qwen/qwen3.8-27b", "gemini/gemini-3.6-flash"])
        self.assertEqual([e for e in self.events if e["type"] == "ciel_error"], [])


if __name__ == "__main__":
    unittest.main()
