"""One recipient per gateway message, without tokens, inference, or Discord access."""

import ast
import asyncio
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

import discord_routing as routing


class RoutingTest(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(routing._identities, {}, clear=True))
        self.users = {name: NS(id=i, name=name, bot=True) for i, name in enumerate(routing.HOME_CHANNELS, 10)}
        self.bots = {name: NS(user=user, process_commands=AsyncMock()) for name, user in self.users.items()}
        for name, user in self.users.items():
            routing.register(name, user)

    def message(self, content, channel="commands-chat", *, mentions=(), parent=None, dm=False):
        return NS(content=content, author=NS(id=7, bot=False), mentions=mentions,
                  channel=NS(name=channel, parent=parent), guild=None if dm else NS(id=1))

    def recipients(self, message):
        return {name: content for name, bot in self.bots.items()
                if (content := routing.content_for(message, name, bot)) is not None}

    def test_plain_shared_chat_has_one_recipient(self):
        self.assertEqual(self.recipients(self.message("hello")), {"sakura": "hello"})
        self.assertEqual(self.recipients(self.message("hello", "unrelated")), {})

    def test_each_home_and_its_threads_select_the_home_agent(self):
        for name, channels in routing.HOME_CHANNELS.items():
            for channel in channels:
                with self.subTest(agent=name, channel=channel):
                    self.assertEqual(self.recipients(self.message("hello", channel)), {name: "hello"})
                    self.assertEqual(self.recipients(self.message("hello", "commands-chat", parent=NS(name=channel))), {name: "hello"})
        self.assertEqual(self.recipients(self.message("hello", "arbitrary-thread", parent=NS(name="commands-chat"))), {"sakura": "hello"})

    def test_duplicate_defaults_and_home_overrides(self):
        for text, name in (("!ping", "sakura"), ("!help", "sakura"), ("!audit", "yunjin"),
                           ("!qa file.py", "eunchae"), ("!lessons", "eunchae")):
            self.assertEqual(self.recipients(self.message(text)), {name: text})
        for text, channel, name in (("!qa question", "job-tailoring", "chaewon"),
                                    ("!audit", "pc-vitals", "eunchae"),
                                    ("!lessons", "frontend-lab", "kazuha"),
                                    ("!help", "portfolio-audits", "yunjin")):
            self.assertEqual(self.recipients(self.message(text, channel)), {name: text})

    def test_every_unique_command_and_alias_routes_to_its_owner(self):
        for name, commands in routing.COMMANDS.items():
            for command in commands:
                if sum(command in names for names in routing.COMMANDS.values()) == 1:
                    text = f"!{command} arguments"
                    with self.subTest(command=command):
                        self.assertEqual(self.recipients(self.message(text)), {name: text})
                        self.assertEqual(self.recipients(self.message(text, "unrelated")), {name: text})

    def test_explicit_target_wins_even_for_another_agents_command(self):
        user = self.users["chaewon"]
        for mention in (f"<@{user.id}>", f"<@!{user.id}>"):
            self.assertEqual(self.recipients(self.message(f"{mention} !clean", "pc-vitals", mentions=[user])),
                             {"chaewon": "!clean"})
            self.assertEqual(self.recipients(self.message(f"{mention} hello", mentions=[user])),
                             {"chaewon": "hello"})

    def test_multiple_bot_mentions_choose_first_in_content_not_metadata_order(self):
        first, second = self.users["kazuha"], self.users["sakura"]
        msg = self.message(f"<@{first.id}> <@{second.id}> !ping", mentions=[second, first])
        self.assertEqual(self.recipients(msg), {"kazuha": "!ping"})

    def test_offline_target_never_falls_through_to_another_mutation(self):
        routing._identities.clear()
        offline = NS(id=99, name="Kazuha", bot=True)
        msg = self.message("<@99> !clean", mentions=[offline])
        online = {name: bot for name, bot in self.bots.items() if name != "kazuha"}
        self.assertTrue(all(routing.content_for(msg, name, bot) is None for name, bot in online.items()))
        unknown = NS(id=98, name="Unknown application", bot=True)
        self.assertEqual(self.recipients(self.message("<@98> !clean", mentions=[unknown])), {})
        self.assertIsNone(routing.content_for(self.message("!commit"), "sakura", self.bots["sakura"]))

    def test_user_mentions_in_arguments_survive(self):
        human = NS(id=7, bot=False)
        sakura = self.users["sakura"]
        msg = self.message(f"<@{sakura.id}> !strategy ask <@7>", mentions=[human, sakura])
        self.assertEqual(self.recipients(msg), {"sakura": "!strategy ask <@7>"})

    def test_unrelated_bot_with_matching_display_name_never_selects_an_agent(self):
        other = NS(id=99, name="Sakura Music", display_name="Sakura", global_name="Sakura", bot=True)
        self.assertEqual(self.recipients(self.message("<@99> !clean", mentions=[other])), {})

    def test_dm_stays_with_the_recipient_client(self):
        for name, bot in self.bots.items():
            for content in ("hello", "!help", "!clean"):
                self.assertEqual(routing.content_for(self.message(content, dm=True), name, bot), content)

    def test_unknown_or_empty_commands_have_one_recipient(self):
        for text in ("!", "! ", "!unknown"):
            self.assertEqual(self.recipients(self.message(text)), {"sakura": text.strip()})

    def test_command_message_is_copied_and_normalized(self):
        message = self.message("<@10> !ping", mentions=[self.users["sakura"]])
        asyncio.run(routing.process_commands(self.bots["sakura"], message, "!ping"))
        sent = self.bots["sakura"].process_commands.await_args.args[0]
        self.assertIsNot(sent, message)
        self.assertEqual(sent.content, "!ping")
        self.assertEqual(message.content, "<@10> !ping")

    def test_normalized_address_parses_with_real_discord_py(self):
        import discord
        from discord.ext import commands

        async def check():
            async with commands.Bot(command_prefix="!", intents=discord.Intents.none(), help_command=None) as bot:
                bot._connection.user = self.users["sakura"]
                seen = []

                @bot.command()
                async def ping(ctx):
                    seen.append(ctx.invoked_with)

                message = self.message("<@!10> !ping", mentions=[self.users["sakura"]])
                message._state, message.attachments = bot._connection, []
                content = routing.content_for(message, "sakura", bot)
                await routing.process_commands(bot, message, content)
                self.assertEqual(seen, ["ping"])
                self.assertEqual(message.content, "<@!10> !ping")

        asyncio.run(check())

    def test_command_map_matches_all_bot_decorators_and_ready_registers(self):
        for name in routing.HOME_CHANNELS:
            tree = ast.parse((Path(__file__).parent / f"bot_{name}.py").read_text(encoding="utf-8"))
            commands = set()
            for node in tree.body:
                for decorator in getattr(node, "decorator_list", ()):
                    if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute) and decorator.func.attr == "command":
                        fields = {kw.arg: ast.literal_eval(kw.value) for kw in decorator.keywords}
                        commands.add(fields.get("name", node.name))
                        commands.update(fields.get("aliases", ()))
            self.assertEqual(commands, set(routing.COMMANDS[name]), name)
            ready = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "on_ready")
            self.assertIn(f"discord_routing.register('{name}', bot.user)", ast.unparse(ready))

    def test_real_handler_command_branches_route_once_and_guard_first(self):
        # Compile only the real handler: no token loading, background loops, or heavy imports.
        for allowed in (False, True):
            for name, bot in self.bots.items():
                tree = ast.parse((Path(__file__).parent / f"bot_{name}.py").read_text(encoding="utf-8"))
                handler = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "on_message")
                handler.decorator_list = []
                guard = NS(allowed=AsyncMock(return_value=allowed))
                namespace = {"bot": bot, "owner_guard": guard, "discord_routing": routing}
                exec(compile(ast.Module(body=[handler], type_ignores=[]), "<on_message>", "exec"), namespace)
                bot.process_commands.reset_mock()
                with patch.object(routing, "content_for", wraps=routing.content_for) as route:
                    asyncio.run(namespace["on_message"](self.message("!ping")))
                self.assertEqual(route.call_count, int(allowed))
                self.assertEqual(bot.process_commands.await_count, int(allowed and name == "sakura"))

    def test_real_natural_language_handlers_reply_once_inside_home_threads(self):
        consultations = {"sakura": "consult_sakura", "chaewon": "consult_career", "kazuha": "consult_kazuha",
                         "yunjin": "consult_yunjin", "eunchae": "chat_eunchae"}
        for home, channels in routing.HOME_CHANNELS.items():
            with self.subTest(home=home):
                message = self.message("hello from the task thread", "arbitrary-task-title", parent=NS(name=channels[0]))
                message.attachments, message.reply = [], AsyncMock()
                message.channel.typing = nullcontext
                deliveries = {}
                for name, bot in self.bots.items():
                    tree = ast.parse((Path(__file__).parent / f"bot_{name}.py").read_text(encoding="utf-8"))
                    handler = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "on_message")
                    handler.decorator_list = []
                    delivery = deliveries[name] = AsyncMock()
                    namespace = {"bot": bot, "owner_guard": NS(allowed=AsyncMock(return_value=True)),
                                 "discord_routing": routing, "asyncio": asyncio, "send_clean_embeds": delivery,
                                 consultations[name]: lambda _: "a local test reply", "find_url_in_text": lambda _: None}
                    exec(compile(ast.Module(body=[handler], type_ignores=[]), "<on_message>", "exec"), namespace)
                    asyncio.run(namespace["on_message"](message))
                self.assertEqual({name: delivery.await_count for name, delivery in deliveries.items()},
                                 {name: int(name == home) for name in routing.HOME_CHANNELS})
                message.reply.assert_not_awaited()  # no swallowed handler error instead of a normal reply


if __name__ == "__main__":
    unittest.main()
