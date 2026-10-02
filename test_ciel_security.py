"""
Security regression tests for the Ciel HUD server's localhost_guard middleware.
Hermetic: heavy engine modules are stubbed, so no LLM, Gmail, Obsidian, or audio I/O runs.
Run: python -m unittest test_ciel_security.py
"""

import asyncio
import os
import sys
import time
import types
import unittest
from unittest import mock

from aiohttp import WSServerHandshakeError
from aiohttp.test_utils import TestClient, TestServer


def _install_stubs():
    """Replace the orchestrator and engine modules with inert fakes before importing ciel_server."""

    class FakeCiel:
        def __init__(self):
            self.conversation_history = []
            self.missions = []
            self.mission_ids = []

        def reset_memory(self):
            self.conversation_history = []

        async def execute_mission(self, prompt, event_callback=None, mission_id=None):
            self.missions.append(prompt)
            self.mission_ids.append(mission_id)
            return {"mission_id": mission_id, "reply": "ok", "audio_url": "/audio/x.mp3", "elapsed_ms": 1}

    stubs = {
        "ciel_orchestrator": {"CielOrchestrator": FakeCiel},
        "voice_engine": {
            "transcribe_audio": lambda audio_bytes, filename="": {"text": "hi"},
            "transcribe_audio_groq": lambda *a, **k: {"text": "hi"},
            "prune_audio_cache": lambda max_files=35: 0,
        },
        "eunchae_engine": {"get_system_vitals": lambda **kwargs: {"cpu_pct": 1}},
        "llm_client": {"get_brain_status": lambda: {"mode": "test"}},
        "proactive_sentinel": {"evaluate_proactive_suggestions": lambda: []},
    }
    for name, attrs in stubs.items():
        mod = types.ModuleType(name)
        for key, val in attrs.items():
            setattr(mod, key, val)
        sys.modules[name] = mod


os.environ["CIEL_PORT"] = "8000"
_install_stubs()
sys.modules.pop("ciel_server", None)
import ciel_server  # noqa: E402

GOOD_HOST = {"Host": "localhost:8000"}
GOOD_ORIGIN = {**GOOD_HOST, "Origin": "http://localhost:8000"}
EVIL_ORIGIN = {**GOOD_HOST, "Origin": "https://evil.example"}


class TestLocalhostGuard(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.client = TestClient(TestServer(ciel_server.create_app()))
        await self.client.start_server()
        ciel_server.ciel.missions.clear()
        ciel_server.ciel.mission_ids.clear()

    async def asyncTearDown(self):
        await self.client.close()

    # --- Host allow-list (DNS rebinding) ---

    async def test_get_with_localhost_host_allowed(self):
        res = await self.client.get("/api/telemetry", headers=GOOD_HOST)
        self.assertEqual(res.status, 200)

    async def test_127_host_allowed(self):
        res = await self.client.get("/api/telemetry", headers={"Host": "127.0.0.1:8000"})
        self.assertEqual(res.status, 200)

    async def test_rebound_host_rejected(self):
        res = await self.client.get("/api/memory", headers={"Host": "evil.example:8000"})
        self.assertEqual(res.status, 403)

    # --- CSRF on POST routes ---

    async def test_same_origin_json_chat_allowed(self):
        res = await self.client.post("/api/chat", json={"prompt": "status"}, headers=GOOD_ORIGIN)
        self.assertEqual(res.status, 200)
        self.assertEqual(ciel_server.ciel.missions, ["status"])

    async def test_failed_mission_returns_500_with_error(self):
        """execute_mission reports failures as {"error": ...}; the REST reply must not be a 200."""
        async def failing(prompt, event_callback=None, mission_id=None):
            return {"mission_id": "m1", "error": "ConnectionError: Groq API unreachable", "elapsed_ms": 3}

        original = ciel_server.ciel.execute_mission
        ciel_server.ciel.execute_mission = failing
        try:
            res = await self.client.post("/api/chat", json={"prompt": "status"}, headers=GOOD_ORIGIN)
        finally:
            ciel_server.ciel.execute_mission = original
        self.assertEqual(res.status, 500)
        body = await res.json()
        self.assertEqual(body["mission_id"], "m1")
        self.assertIn("Groq", body["error"])

    # --- Client-chosen mission ids (they end up in the audio filename) ---

    async def test_client_mission_id_is_used_and_broadcast(self):
        mid = "0123456789abcdef0123456789abcdef"
        ws = await self.client.ws_connect("/ws", headers=GOOD_ORIGIN)
        await ws.receive_json(timeout=2)  # ciel_connected
        res = await self.client.post("/api/chat", json={"prompt": "status", "mission_id": mid}, headers=GOOD_ORIGIN)
        self.assertEqual(res.status, 200)
        self.assertEqual(ciel_server.ciel.mission_ids, [mid])
        received = await ws.receive_json(timeout=2)
        self.assertEqual(received["type"], "chat_received")
        self.assertEqual(received["mission_id"], mid)
        await ws.close()

    async def test_unsafe_mission_ids_are_replaced(self):
        bad_ids = ["../../ciel_server", "0123456789ABCDEF0123456789ABCDEF", "abc", "a" * 64, 12345, None]
        for bad in bad_ids:
            await self.client.post("/api/chat", json={"prompt": "status", "mission_id": bad}, headers=GOOD_ORIGIN)
        self.assertEqual(len(ciel_server.ciel.mission_ids), len(bad_ids))
        for used in ciel_server.ciel.mission_ids:
            self.assertRegex(used, r"^[0-9a-f]{32}$")
        self.assertNotIn("../../ciel_server", ciel_server.ciel.mission_ids)

    async def test_cross_origin_chat_rejected_without_side_effects(self):
        res = await self.client.post("/api/chat", json={"prompt": "compile resume"}, headers=EVIL_ORIGIN)
        self.assertEqual(res.status, 403)
        self.assertEqual(ciel_server.ciel.missions, [])

    async def test_simple_request_text_plain_chat_rejected(self):
        """A CORS 'simple' request (text/plain) must not be parsed as a command."""
        res = await self.client.post(
            "/api/chat",
            data='{"prompt": "triage inbox"}',
            headers={**GOOD_HOST, "Content-Type": "text/plain"},
        )
        self.assertEqual(res.status, 415)
        self.assertEqual(ciel_server.ciel.missions, [])

    async def test_memory_clear_requires_json(self):
        bad = await self.client.post("/api/memory/clear", headers=GOOD_HOST)
        self.assertEqual(bad.status, 415)
        ok = await self.client.post("/api/memory/clear", json={}, headers=GOOD_ORIGIN)
        self.assertEqual(ok.status, 200)

    async def test_cross_origin_multipart_transcribe_rejected(self):
        """Multipart forms are CORS-simple, so the Origin check is the only guard here."""
        res = await self.client.post("/api/transcribe", data={"file": b"RIFF"}, headers=EVIL_ORIGIN)
        self.assertEqual(res.status, 403)

    # --- Cross-site WebSocket hijacking ---

    async def test_cross_origin_websocket_rejected(self):
        with self.assertRaises(WSServerHandshakeError) as ctx:
            await self.client.ws_connect("/ws", headers=EVIL_ORIGIN)
        self.assertEqual(ctx.exception.status, 403)

    async def test_same_origin_websocket_survives_malformed_frames(self):
        ws = await self.client.ws_connect("/ws", headers=GOOD_ORIGIN)
        hello = await ws.receive_json(timeout=2)
        self.assertEqual(hello["type"], "ciel_connected")

        await ws.send_str("not json")
        await ws.send_str("[1, 2, 3]")
        await ws.send_json({"action": "ping"})
        pong = await ws.receive_json(timeout=2)
        self.assertEqual(pong["type"], "pong")
        await ws.close()


class TestServerStaysResponsive(unittest.IsolatedAsyncioTestCase):
    """Slow handlers and missions must not stall other clients on the one event loop."""

    async def asyncSetUp(self):
        self.client = TestClient(TestServer(ciel_server.create_app()))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()

    async def ping_rtt(self, ws):
        started = time.perf_counter()
        await ws.send_json({"action": "ping"})
        while (await ws.receive_json(timeout=2))["type"] != "pong":
            pass
        return time.perf_counter() - started

    async def test_slow_telemetry_does_not_delay_pings(self):
        def slow_vitals():
            time.sleep(0.5)  # psutil.cpu_percent(interval=0.5)
            return {"cpu_pct": 1}

        ws = await self.client.ws_connect("/ws", headers=GOOD_ORIGIN)
        await ws.receive_json(timeout=2)  # ciel_connected
        with mock.patch.object(ciel_server, "get_system_vitals", slow_vitals):
            started = time.perf_counter()
            telemetry = asyncio.create_task(self.client.get("/api/telemetry", headers=GOOD_HOST))
            # Let the request reach the handler. Time from before the request: if
            # the handler blocks the loop, this sleep itself waits it out.
            await asyncio.sleep(0.05)
            await self.ping_rtt(ws)
            pong_after = time.perf_counter() - started
            res = await telemetry
        self.assertEqual(res.status, 200)
        self.assertEqual((await res.json())["vitals"], {"cpu_pct": 1})
        self.assertLess(pong_after, 0.3)
        await ws.close()

    async def test_ws_chat_does_not_block_its_own_socket(self):
        async def slow_mission(prompt, event_callback=None, mission_id=None):
            await asyncio.sleep(0.5)
            return {"mission_id": mission_id, "reply": "ok"}

        ws = await self.client.ws_connect("/ws", headers=GOOD_ORIGIN)
        await ws.receive_json(timeout=2)  # ciel_connected
        with mock.patch.object(ciel_server.ciel, "execute_mission", slow_mission):
            await ws.send_json({"action": "chat", "prompt": "status"})
            self.assertEqual((await ws.receive_json(timeout=2))["type"], "chat_received")
            rtt = await self.ping_rtt(ws)
            self.assertLess(rtt, 0.25)
            await asyncio.gather(*ciel_server.ws_missions)
        await ws.close()


class TestTelemetryPush(unittest.IsolatedAsyncioTestCase):
    """Vitals reach the HUD over the WebSocket, sampled without blocking and only while someone listens."""

    async def asyncSetUp(self):
        self.calls = []

        def vitals(**kwargs):
            self.calls.append(kwargs)
            return {"cpu_pct": 7}

        self.enterContext(mock.patch.object(ciel_server, "TELEMETRY_INTERVAL_S", 0.05))
        self.enterContext(mock.patch.object(ciel_server, "get_system_vitals", vitals))
        self.client = TestClient(TestServer(ciel_server.create_app()))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()

    async def test_connected_hud_receives_telemetry_events(self):
        ws = await self.client.ws_connect("/ws", headers=GOOD_ORIGIN)
        await ws.receive_json(timeout=2)  # ciel_connected
        event = await ws.receive_json(timeout=2)
        self.assertEqual(event["type"], "telemetry")
        self.assertEqual(event["vitals"], {"cpu_pct": 7})
        self.assertIn("memory_turns", event)
        self.assertEqual(self.calls[0], {"cpu_interval": None})  # never psutil's 0.5 s sleep
        await ws.close()

    async def test_no_sampling_without_clients(self):
        await asyncio.sleep(0.3)  # six intervals
        self.assertEqual(self.calls, [])

    async def test_rest_telemetry_still_served(self):
        res = await self.client.get("/api/telemetry", headers=GOOD_HOST)
        self.assertEqual(res.status, 200)
        self.assertEqual((await res.json())["vitals"], {"cpu_pct": 7})
        bad = await self.client.get("/api/telemetry", headers={"Host": "evil.example:8000"})
        self.assertEqual(bad.status, 403)


class TestMissionAdmission(unittest.IsolatedAsyncioTestCase):
    """At most MAX_CONCURRENT_MISSIONS run at once; a bounded number wait; the rest get 503."""

    async def asyncSetUp(self):
        self.client = TestClient(TestServer(ciel_server.create_app()))
        await self.client.start_server()
        self.running = 0
        self.peak = 0

        async def slow_mission(prompt, event_callback=None, mission_id=None):
            self.running += 1
            self.peak = max(self.peak, self.running)
            await asyncio.sleep(0.2)
            self.running -= 1
            return {"mission_id": mission_id, "reply": "ok"}

        self.enterContext(mock.patch.object(ciel_server.ciel, "execute_mission", slow_mission))

    async def asyncTearDown(self):
        await self.client.close()

    async def burst(self, n):
        responses = await asyncio.gather(*(
            self.client.post("/api/chat", json={"prompt": f"p{i}"}, headers=GOOD_ORIGIN) for i in range(n)
        ))
        return [(r.status, await r.json()) for r in responses]

    async def test_at_most_two_missions_run_at_once(self):
        results = await self.burst(5)
        self.assertEqual([status for status, _ in results], [200] * 5)
        self.assertEqual(self.peak, ciel_server.MAX_CONCURRENT_MISSIONS)

    async def test_missions_beyond_the_queue_are_refused(self):
        self.client.app[ciel_server.MISSION_GATE].max_queued = 1
        ws = await self.client.ws_connect("/ws", headers=GOOD_ORIGIN)
        await ws.receive_json(timeout=2)  # ciel_connected

        results = await self.burst(4)  # 2 run, 1 waits, 1 is refused
        statuses = sorted(status for status, _ in results)
        self.assertEqual(statuses, [200, 200, 200, 503])
        refused = next(body for status, body in results if status == 503)
        self.assertIn("busy", refused["error"].lower())
        self.assertRegex(refused["mission_id"], r"^[0-9a-f]{32}$")
        self.assertLessEqual(self.peak, 2)

        events = []
        while True:
            try:
                events.append(await ws.receive_json(timeout=0.2))
            except asyncio.TimeoutError:
                break
        types = [e["type"] for e in events]
        self.assertEqual(types.count("ciel_error"), 1)
        self.assertIn("Queued", " ".join(e.get("message", "") for e in events if e["type"] == "ciel_state"))
        await ws.close()


if __name__ == "__main__":
    unittest.main()
