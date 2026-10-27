"""
The Ciel bridge (split plan C1): who may call it, what it runs, and that it never takes the bots down.
Hermetic: a local test server and fake skills, no Discord, no LLM.
Run: python -m pytest -q test_bridge.py
"""

import socket
import types
import unittest
from pathlib import Path
from unittest import mock

from aiohttp.test_utils import TestClient, TestServer

import bridge
import skills

TOKEN = "test-token-123"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def fake_skill(name, **kw):
    return skills.Skill(name=name, agent=name.split(".")[0], desc=f"fake {name}", **kw)


class BridgeTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.ran = []
        fakes = [
            fake_skill("eunchae.bridge_ping", run=lambda: self._ran("ping", skills.SkillResult(
                "done", "pong", {"path": Path("C:/x/report.pdf")}))),
            fake_skill("sakura.bridge_send", run=lambda: self._ran("send", skills.SkillResult("done", "sent")),
                       changes_things=True, confirm_text="Send it?"),
            fake_skill("kazuha.bridge_hidden", run=lambda: self._ran("hidden", skills.SkillResult("done", "x")),
                       in_planner=False),
        ]
        registry = {s.name: s for s in fakes}
        patch = mock.patch.dict(skills.SKILLS, registry, clear=True)
        patch.start()
        self.addCleanup(patch.stop)
        self.port = free_port()
        self.client = TestClient(TestServer(bridge.make_app(TOKEN, self.port), host="127.0.0.1", port=self.port))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)

    def _ran(self, what, result):
        self.ran.append(what)
        return result

    def auth(self, token=TOKEN):
        return {"Authorization": f"Bearer {token}"}

    async def test_token_is_required(self):
        for headers in ({}, self.auth("wrong"), {"Authorization": TOKEN}):
            r = await self.client.get("/health", headers=headers)
            self.assertEqual(r.status, 401, headers)
        r = await self.client.get("/health", headers=self.auth())
        self.assertEqual((r.status, await r.json()), (200, {"ok": True, "skills": 2}))

    async def test_a_foreign_host_header_is_refused(self):  # DNS rebinding from a browser page
        r = await self.client.get("/health", headers={**self.auth(), "Host": f"evil.test:{self.port}"})
        self.assertEqual(r.status, 403)

    def test_a_remote_peer_is_refused(self):
        request = types.SimpleNamespace(
            transport=types.SimpleNamespace(get_extra_info=lambda key: ("10.0.0.5", 50000)),
            host=f"127.0.0.1:{self.port}", headers=self.auth())
        self.assertEqual(bridge.refusal(request, TOKEN, self.port)[0], 403)

    async def test_skills_lists_only_what_the_planner_may_call(self):
        r = await self.client.get("/skills", headers=self.auth())
        names = {s["name"]: s for s in (await r.json())["skills"]}
        self.assertEqual(set(names), {"eunchae.bridge_ping", "sakura.bridge_send"})
        self.assertTrue(names["sakura.bridge_send"]["changes_things"])
        self.assertEqual(names["sakura.bridge_send"]["confirm_text"], "Send it?")

    async def test_a_call_runs_and_its_data_comes_back_as_json(self):
        r = await self.client.post("/skill", json={"calls": [["eunchae.bridge_ping", {}]]}, headers=self.auth())
        body = await r.json()
        self.assertEqual((r.status, body["status"], body["text"]), (200, "done", "pong"))
        self.assertIn("report.pdf", body["data"]["results"][0]["path"])  # a Path, sent as text
        self.assertEqual(self.ran, ["ping"])

    async def test_a_changing_skill_waits_for_confirmation(self):
        call = {"calls": [["sakura.bridge_send", {}]]}
        r = await self.client.post("/skill", json=call, headers=self.auth())
        self.assertEqual((await r.json())["status"], "needs_confirm")
        self.assertEqual(self.ran, [])
        r = await self.client.post("/skill", json={**call, "confirmed": True}, headers=self.auth())
        self.assertEqual(((await r.json())["status"], self.ran), ("done", ["send"]))

    async def test_bad_requests_are_refused_before_anything_runs(self):
        bad = [
            {"calls": [["kazuha.bridge_hidden", {}]]},             # hidden from the planner
            {"calls": [["nobody.nothing", {}]]},                    # unknown
            {"calls": [["eunchae.bridge_ping", {"x": "1"}]]},       # unknown arg
            {"calls": [["eunchae.bridge_ping", {}]] * 9},           # too many
            {"calls": [["eunchae.bridge_ping", {}]], "confirmed": "yes"},
            {"calls": []}, {"calls": "eunchae.bridge_ping"}, [1, 2],
        ]
        for body in bad:
            r = await self.client.post("/skill", json=body, headers=self.auth())
            self.assertEqual(r.status, 400, body)
        r = await self.client.post("/skill", data="{not json", headers=self.auth())
        self.assertEqual(r.status, 400)
        r = await self.client.post("/skill", data="x" * (bridge.MAX_BODY + 1), headers=self.auth())
        self.assertEqual(r.status, 413)
        self.assertEqual(self.ran, [])


class StartTest(unittest.IsolatedAsyncioTestCase):
    async def test_no_token_means_no_bridge(self):
        with mock.patch.dict("os.environ", {"LSFM_BRIDGE_TOKEN": ""}):
            self.assertIsNone(await bridge.start())

    async def test_a_busy_port_leaves_the_bots_running(self):
        with socket.socket() as busy:
            busy.bind(("127.0.0.1", 0))
            busy.listen()
            port = busy.getsockname()[1]
            with mock.patch.dict("os.environ", {"LSFM_BRIDGE_TOKEN": TOKEN, "LSFM_BRIDGE_PORT": str(port)}):
                self.assertIsNone(await bridge.start())  # logged, not raised

    async def test_a_bad_port_setting_turns_the_bridge_off(self):  # Codex QA C1-F1: 65536 raised OverflowError
        for raw in ("65536", "0", "99999999", "abc", "-1"):
            with self.subTest(port=raw), \
                    mock.patch.dict("os.environ", {"LSFM_BRIDGE_TOKEN": TOKEN, "LSFM_BRIDGE_PORT": raw}):
                self.assertIsNone(await bridge.start())

    async def test_the_bots_run_even_if_the_bridge_raises(self):  # Codex QA C1-F1, through run_all.main
        import sys
        import run_all
        started = []

        async def fake_run(bot, token, name):
            started.append(name)

        bots = {m: types.SimpleNamespace(bot=object())
                for m in ("bot_chaewon", "bot_yunjin", "bot_sakura", "bot_kazuha", "bot_eunchae")}
        vault = types.SimpleNamespace(ObsidianClient=lambda: types.SimpleNamespace(check_vault_consistency=lambda: "ok"))
        tokens = {k: "t" for k in ("CHAEWON_BOT_TOKEN", "YUNJIN_BOT_TOKEN", "SAKURA_BOT_TOKEN", "KAZUHA_BOT_TOKEN",
                                   "EUNCHAE_BOT_TOKEN")}
        with mock.patch.dict(sys.modules, {**bots, "obsidian_client": vault}), \
                mock.patch.dict("os.environ", tokens), \
                mock.patch.object(run_all, "load_dotenv"), \
                mock.patch.object(run_all, "run_bot_resilient", fake_run), \
                mock.patch.object(run_all.asyncio, "sleep", mock.AsyncMock()), \
                mock.patch.object(bridge, "start", mock.AsyncMock(side_effect=OverflowError("port must be 0-65535"))), \
                mock.patch("builtins.print"):
            await run_all.main()
        self.assertEqual(len(started), 5)

    async def test_it_starts_and_answers_on_localhost(self):
        port = free_port()
        with mock.patch.dict("os.environ", {"LSFM_BRIDGE_TOKEN": TOKEN, "LSFM_BRIDGE_PORT": str(port)}):
            runner = await bridge.start()
        self.assertIsNotNone(runner)
        self.addAsyncCleanup(runner.cleanup)
        import aiohttp
        async with aiohttp.ClientSession() as s:
            async with s.get(f"http://127.0.0.1:{port}/health", headers={"Authorization": f"Bearer {TOKEN}"}) as r:
                self.assertEqual(r.status, 200)


if __name__ == "__main__":
    unittest.main()
