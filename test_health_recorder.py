"""
Tests for the per-bot heartbeat files (4B-2). Hermetic: temp dirs and a fake bot.
Run: python -m pytest -q test_health_recorder.py
"""

import ast
import asyncio
import json
import tempfile
import threading
import unittest
from pathlib import Path

import health_recorder

ROOT = Path(__file__).parent


class FakeBot:
    def __init__(self, ready=True):
        self.listeners = {}
        self.ready = ready

    def add_listener(self, func, name):
        self.listeners.setdefault(name, []).append(func)

    def is_ready(self):
        return self.ready

    def is_closed(self):
        return False

    def fire(self, name, *args):
        for func in self.listeners.get(name, []):
            asyncio.run(func(*args))


class TestRecordHeartbeat(unittest.TestCase):

    def setUp(self):
        self.dir = Path(self.enterContext(tempfile.TemporaryDirectory())) / "health"  # not created yet

    def test_writes_the_payload_and_creates_the_directory(self):
        path = health_recorder.record_heartbeat("sakura", metadata={"events_processed": 3}, health_dir=self.dir)
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(path, self.dir / "sakura.json")
        self.assertEqual((data["bot"], data["status"], data["events_processed"]), ("sakura", "online", 3))
        self.assertIsInstance(data["pid"], int)
        self.assertTrue(data["timestamp_utc"].endswith("+00:00"))

    def test_rejects_names_that_are_not_plain_bot_names(self):
        for bad in ("../outside", "Sakura", "sakura.json", ""):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                health_recorder.record_heartbeat(bad, health_dir=self.dir)

    def test_concurrent_writers_and_a_reader_never_see_a_partial_file(self):
        health_recorder.record_heartbeat("eunchae", health_dir=self.dir)
        target = self.dir / "eunchae.json"
        errors, done = [], threading.Event()

        def writer(n):
            try:
                for i in range(40):
                    health_recorder.record_heartbeat("eunchae", metadata={"events_processed": n * 1000 + i},
                                                     health_dir=self.dir)
            except Exception as exc:  # any escaping error fails the test below
                errors.append(f"writer: {exc!r}")

        def reader():
            while not done.is_set():
                try:
                    json.loads(target.read_text(encoding="utf-8"))
                except PermissionError:
                    pass  # Windows: replace in progress; a reader retries, it never sees half a file
                except ValueError as exc:
                    errors.append(f"partial read: {exc!r}")

        r = threading.Thread(target=reader)
        r.start()
        writers = [threading.Thread(target=writer, args=(n,)) for n in range(6)]
        for w in writers:
            w.start()
        for w in writers:
            w.join()
        done.set()
        r.join()
        self.assertEqual(errors, [])
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["eunchae.json"])  # no temp files left


class TestHeartbeat(unittest.TestCase):

    def setUp(self):
        self.dir = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def read(self, name):
        return json.loads((self.dir / f"{name}.json").read_text(encoding="utf-8"))

    def test_counts_gateway_events_and_disconnects(self):
        bot = FakeBot()
        hb = health_recorder.attach(bot, "kazuha", health_dir=self.dir)
        for event in ("MESSAGE_CREATE", "TYPING_START", "MESSAGE_CREATE"):
            bot.fire("on_socket_event_type", event)
        bot.fire("on_disconnect")
        asyncio.run(hb.beat())
        data = self.read("kazuha")
        self.assertEqual((data["status"], data["events_processed"], data["last_event_type"], data["disconnects"]),
                         ("online", 3, "MESSAGE_CREATE", 1))

    def test_a_bot_that_is_not_ready_is_not_reported_online(self):
        bot = FakeBot(ready=False)
        hb = health_recorder.attach(bot, "chaewon", health_dir=self.dir)
        asyncio.run(hb.beat())
        self.assertEqual(self.read("chaewon")["status"], "not_ready")

    def test_on_ready_writes_at_once_and_starts_one_periodic_loop(self):
        bot = FakeBot()
        hb = health_recorder.attach(bot, "yunjin", health_dir=self.dir, interval=0.01)

        async def scenario():
            for listener in bot.listeners["on_ready"]:
                await listener()
            self.assertTrue((self.dir / "yunjin.json").exists())
            first = hb._task
            for listener in bot.listeners["on_ready"]:  # a reconnect fires on_ready again
                await listener()
            self.assertIs(hb._task, first)
            await asyncio.sleep(0.05)
            hb._task.cancel()
            return self.read("yunjin")["timestamp_utc"]

        self.assertTrue(asyncio.run(scenario()))

    def test_a_failed_write_is_logged_not_raised(self):
        hb = health_recorder.attach(FakeBot(), "sakura", health_dir=self.dir / "sakura.json" / "x")
        (self.dir / "sakura.json").write_text("{}", encoding="utf-8")  # a file where the dir should be
        asyncio.run(hb.beat())  # must not raise into the bot's event loop


class TestRealDiscordBot(unittest.TestCase):
    """The listener names must match what discord.py 2.x dispatches (gateway.py: 'socket_event_type')."""

    def test_discord_dispatch_reaches_the_counters(self):
        import discord
        from discord.ext import commands

        async def scenario():
            bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
            hb = health_recorder.attach(bot, "eunchae", health_dir=Path(tempfile.mkdtemp()))
            async with bot:  # sets the client's loop, as start() does; no network
                bot.dispatch("socket_event_type", "GUILD_CREATE")
                bot.dispatch("socket_event_type", "MESSAGE_CREATE")
                bot.dispatch("disconnect")
                bot.clear()  # run_all.reset_client calls this between retries; listeners must survive it
                bot.dispatch("socket_event_type", "READY")
                await asyncio.sleep(0.05)  # dispatch schedules each listener as a task
            return hb.events_processed, hb.last_event_type, hb.disconnects

        self.assertEqual(asyncio.run(scenario()), (3, "READY", 1))


class TestEveryBotIsWired(unittest.TestCase):

    def test_each_bot_module_attaches_its_own_heartbeat(self):
        for name in ("sakura", "chaewon", "yunjin", "kazuha", "eunchae"):
            tree = ast.parse((ROOT / f"bot_{name}.py").read_text(encoding="utf-8"))
            calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                     and getattr(n.func, "attr", None) == "attach"
                     and getattr(n.func.value, "id", None) == "health_recorder"]
            with self.subTest(bot=name):
                self.assertEqual(len(calls), 1)
                self.assertEqual([a.value for a in calls[0].args if isinstance(a, ast.Constant)], [name])


if __name__ == "__main__":
    unittest.main()
