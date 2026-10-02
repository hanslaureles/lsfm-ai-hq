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

SYNTHESIS_JSON = json.dumps({
    "japanese_voice": "「告。」完了。",
    "english_voice": "Notice: Done.",
    "display_text": "Done.",
})


def _call_groq(prompt, system_instruction="", **kwargs):
    if "routing core" in system_instruction:
        raise RuntimeError("router offline")  # forces the deterministic keyword router
    return SYNTHESIS_JSON


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
        return "05 - Daily Logs/today.md"

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
    "llm_client": {"query_llm": lambda *a, **k: "", "call_groq": _call_groq,
                   "get_brain_status": lambda: {"mode": "test"}},
    "obsidian_client": {"ObsidianClient": FakeObsidian},
    "voice_engine": {"text_to_speech": _tts_bilingual, "text_to_speech_bilingual": _tts_bilingual,
                     "speak_clip": _speak_clip, "concat_clips": _concat_clips},
    "proactive_sentinel": {"evaluate_proactive_suggestions": lambda: [],
                           "generate_appraisal_summary": lambda s: "No issues flagged."},
    # Optional engines: empty modules make each `from x import y` raise ImportError,
    # so the orchestrator falls back to None and tests patch in what they need.
    "eunchae_engine": {}, "yunjin_engine": {}, "sakura_engine": {}, "pdf_engine": {},
    "git_sentinel": {}, "kazuha_engine": {},
}

def _module(name, attrs):
    mod = types.ModuleType(name)
    for key, val in attrs.items():
        setattr(mod, key, val)
    return mod


_saved = {name: sys.modules.get(name) for name in [*STUBS, "ciel_orchestrator"]}
for _name, _attrs in STUBS.items():
    sys.modules[_name] = _module(_name, _attrs)
sys.modules.pop("ciel_orchestrator", None)
import ciel_orchestrator  # noqa: E402
for _name, _orig in _saved.items():
    if _orig is None:
        sys.modules.pop(_name, None)
    else:
        sys.modules[_name] = _orig


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
        self.enterContext(mock.patch.object(ciel_orchestrator, name, value))

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
            ("scan portfolio", "yunjin", "scan_html_assets"),
            ("check inbox", "sakura", "scan_morning_inbox"),
            ("compile resume", "chaewon", "compile_master_resume"),
            ("git status", "kazuha", "get_git_info"),
        ]
        for prompt, agent, engine in cases:
            with self.subTest(agent=agent):
                self.events.clear()
                self.patch(engine, None)
                result, end = await self.agent_end(prompt, agent)
                self.assert_failed(result, end, agent)

    async def test_obsidian_outage_is_not_reported_as_verified(self):
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
        # Own portfolio dir: the default is the sibling ../portfolio-site, which a CI checkout lacks.
        portfolio = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (portfolio / "index.html").write_text("<html></html>", encoding="utf-8")
        self.patch("PORTFOLIO_DIR", portfolio)
        self.patch("scan_html_assets", lambda page: {"total_images": 2, "broken_images": ["images/x.png"]})
        result, end = await self.agent_end("scan portfolio", "yunjin")
        self.assertEqual(end["status"], "attention")
        self.assertEqual(result["failed_agents"], [])

    async def test_synthesis_prompt_marks_failed_findings(self):
        prompts = []

        def recording_groq(prompt, system_instruction="", **kwargs):
            prompts.append(prompt)
            return _call_groq(prompt, system_instruction, **kwargs)

        self.patch("call_groq", recording_groq)
        self.patch("get_system_vitals", None)
        await self.run_mission("check vitals")
        self.assertIn('"status": "failed"', prompts[-1])
        self.assertIn("Never describe a failed check as verified", prompts[-1])

    async def test_mission_failure_returns_error_and_emits_ciel_error(self):
        def groq_down(prompt, system_instruction="", **kwargs):
            raise ConnectionError("Groq API unreachable")

        self.patch("call_groq", groq_down)
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
        self.enterContext(mock.patch.object(ciel_orchestrator, name, value))

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

        self.patch("call_groq", slow_groq)
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
        async def plan(prompt):
            return {"intent_type": "agent_delegation", "agents_needed": ["eunchae", "kazuha"],
                    "tool_targets": ["check_vitals", "git_status"], "direct_obsidian": None}

        self.ciel.analyze_and_plan = plan
        self.patch("get_system_vitals", self.slow({"healthy": True}))
        self.patch("get_git_info", self.slow({"is_clean": True, "branch": "main"}))
        started = time.perf_counter()
        result = await self.run_mission("check vitals and git status")
        elapsed = time.perf_counter() - started
        self.assertEqual(result["agent_status"], {"eunchae": "done", "kazuha": "done"})
        self.assertEqual(list(result["agent_results"]), ["eunchae", "kazuha"])
        self.assertLess(elapsed, 2 * self.SLOW_S * 0.8, "agents ran one after another")


class TestTimeouts(MissionTestCase):
    """A call that hangs must end as an honest failure that names the timeout."""

    def patch(self, name, value):
        self.enterContext(mock.patch.object(ciel_orchestrator, name, value))

    @staticmethod
    def hang(value=None):
        def fn(*args, **kwargs):
            time.sleep(0.4)
            return value
        return fn

    async def test_agent_timeout_reports_failed(self):
        self.enterContext(mock.patch.dict(ciel_orchestrator.AGENT_TIMEOUTS_S, {"eunchae": 0.1}))
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

        self.patch("call_groq", slow_router)
        self.patch("get_system_vitals", lambda: {"healthy": True})
        result = await self.run_mission("check vitals")
        self.assertEqual(result["agents_called"], ["eunchae"])

    async def test_synthesis_timeout_fails_the_mission(self):
        self.patch("SYNTHESIS_TIMEOUT_S", 0.1)
        self.patch("call_groq", self.hang(SYNTHESIS_JSON))
        result = await self.run_mission("what is a b-tree?")
        self.assertIn("timed out after 0.1 s", result["error"])
        self.assertEqual(len([e for e in self.events if e["type"] == "ciel_error"]), 1)
        self.assertEqual(self.completes(), [])


class TestAsyncSafety(MissionTestCase):
    """Hazards that only exist because missions now overlap with each other and with threads."""

    def patch(self, name, value):
        self.enterContext(mock.patch.object(ciel_orchestrator, name, value))

    async def clear_mid_mission(self, prompt):
        task = asyncio.create_task(self.run_mission(prompt))
        await asyncio.sleep(0.1)  # the mission is now waiting on its slow thread
        self.ciel.reset_memory()
        return await task

    async def test_clear_during_mission_does_not_revive_memory(self):
        def slow_groq(prompt, system_instruction="", **kwargs):
            time.sleep(0.3)
            return _call_groq(prompt, system_instruction, **kwargs)

        self.patch("call_groq", slow_groq)
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
            return {"success": True, "size_kb": 82.9}

        self.enterContext(mock.patch.dict(ciel_orchestrator.AGENT_TIMEOUTS_S, {"chaewon": 0.1}))
        self.patch("compile_master_resume", slow_build)

        first = await self.run_mission("compile resume")
        self.assertEqual(first["agent_status"]["chaewon"], "failed")
        self.assertIn("may still finish in the background", first["agent_results"]["chaewon"])

        # The abandoned build is still running in its thread: a second one must not start.
        second = await self.run_mission("compile resume")
        self.assertEqual(second["agent_status"]["chaewon"], "failed")
        self.assertIn("still running", second["agent_results"]["chaewon"])
        self.assertEqual(len(calls), 1)

        await asyncio.sleep(0.6)  # the first build ends and releases the lock
        self.enterContext(mock.patch.dict(ciel_orchestrator.AGENT_TIMEOUTS_S, {"chaewon": 5}))
        third = await self.run_mission("compile resume")
        self.assertEqual(third["agent_status"]["chaewon"], "done")
        self.assertEqual(len(calls), 2)
        self.assertEqual(peak[0], 1)


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
        self.enterContext(mock.patch.object(ciel_orchestrator, name, value))

    def clip_events(self):
        return [e for e in self.events if e["type"] == "ciel_audio_chunk"]

    async def test_first_clip_is_announced_before_the_llm_finishes(self):
        returned = []
        split = STREAMED_REPLY.index("完") + 1  # one character past 「告。」
        self.patch("call_groq", streaming_groq(STREAMED_REPLY, split, pause_s=0.5, returned=returned))
        await self.run_mission("what is a b-tree?")
        first = self.clip_events()[0]
        self.assertEqual(first["seq"], 0)
        self.assertLess(first["timestamp"], returned[0] - 0.3)  # well inside the 0.5 s the LLM was still busy

    async def test_clips_in_order_then_joined_into_the_mission_mp3(self):
        self.patch("call_groq", streaming_groq(STREAMED_REPLY, 40))
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
        self.patch("call_groq", streaming_groq(STREAMED_REPLY, len(STREAMED_REPLY)))
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
        self.patch("call_groq", streaming_groq(STREAMED_REPLY, 40))
        result = await self.ciel.execute_mission("what is a b-tree?", mission_id="../../evil")
        self.assertTrue(CLIPS)
        for _, _, name, _ in CLIPS:
            self.assertRegex(name, r"^ciel_response_[0-9a-f]{32}_\d{2}\.mp3$")
            self.assertIn(result["mission_id"], name)

    async def test_missing_english_field_is_voiced_from_the_display_text(self):
        reply = json.dumps({"japanese_voice": "「告。」完了。", "display_text": "Report: B-trees stay balanced."},
                           ensure_ascii=False)
        self.patch("call_groq", streaming_groq(reply, 10))
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
        self.patch("call_groq", streaming_groq(STREAMED_REPLY, 40))
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
        self.patch("call_groq", streaming_groq(STREAMED_REPLY, split, pause_s=0.5))
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
        self.patch("call_groq", streaming_groq(STREAMED_REPLY, 40, fail_after_first=True))
        result = await self.run_mission("what is a b-tree?")
        self.assertIn("broke after partial output", result["error"])
        self.assertEqual(self.completes(), [])
        self.assertEqual(JOINED, [])
        await asyncio.sleep(0.05)
        spoken = len(CLIPS)
        await asyncio.sleep(0.1)
        self.assertEqual(len(CLIPS), spoken)  # the clip worker was stopped


if __name__ == "__main__":
    unittest.main()
