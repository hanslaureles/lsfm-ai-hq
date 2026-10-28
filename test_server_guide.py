"""Hermetic guide updater checks; never read credentials or call Discord."""

import ast
import asyncio
from pathlib import Path
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from scripts import update_server_guide as guide


def channels(guild_id=123):
    return [SimpleNamespace(name=name, id=index, guild=SimpleNamespace(id=guild_id),
                            type=discord.ChannelType.text, mention=f"<#{index}>")
            for index, name in enumerate(guide.CHANNEL_NAMES, 1)]


class Channel:
    def __init__(self, messages=()):
        self.messages = list(messages)
        self.send = AsyncMock(side_effect=self.create)

    async def history(self, *, limit):
        assert limit is None
        for message in self.messages:
            yield message

    async def create(self, *, embed, allowed_mentions):
        message = SimpleNamespace(author=SimpleNamespace(id=7), embeds=[embed], pinned=False)

        async def edit(**kwargs):
            message.embeds = [kwargs["embed"]]

        async def pin(**kwargs):
            message.pinned = True

        message.edit = AsyncMock(side_effect=edit)
        message.pin = AsyncMock(side_effect=pin)
        self.messages.append(message)
        return message


def cards():
    return guide.guide_cards(guide.resolve_channels(channels(), 123))


def test_apply_creates_six_then_keeps_and_edits_only_changed_card():
    async def check():
        channel = Channel()
        first = await guide.update_guide(channel, 7, cards(), apply=True)
        assert first == [(key, "create") for key in cards()]
        assert channel.send.await_count == 6
        channel.messages[0].pin.assert_awaited_once()
        assert all(action == "keep" for _, action in
                   await guide.update_guide(channel, 7, cards(), apply=True))
        channel.messages[1].embeds = [discord.Embed(title="Old").set_footer(text=guide.MARKER + "sakura")]
        actions = dict(await guide.update_guide(channel, 7, cards(), apply=True))
        assert actions["sakura"] == "edit"
        assert sum(message.edit.await_count for message in channel.messages) == 1
        assert channel.send.await_count == 6
        channel.messages[0].pin.assert_awaited_once()
    asyncio.run(check())


def test_dry_run_preserves_history_and_ignores_other_authors_marker():
    async def check():
        messages = [SimpleNamespace(author=SimpleNamespace(id=999), embeds=[cards()["start"]]),
                    SimpleNamespace(author=SimpleNamespace(id=7), embeds=[discord.Embed(title="Unmanaged")])]
        channel = Channel(messages)
        assert all(action == "create" for _, action in await guide.update_guide(channel, 7, cards()))
        assert channel.messages == messages
        channel.send.assert_not_awaited()
        await guide.update_guide(channel, 7, cards(), apply=True)
        assert channel.messages[:2] == messages
        assert len(channel.messages) == 8
    asyncio.run(check())


def test_missing_bot_pin_permission_still_publishes_for_user_to_pin():
    async def check():
        channel = Channel()
        await guide.update_guide(channel, 7, cards(), apply=True, pin_start=False)
        assert channel.send.await_count == 6
        channel.messages[0].pin.assert_not_awaited()
        assert not channel.messages[0].pinned
    asyncio.run(check())


def test_legacy_marker_migrates_in_place_without_duplicate_cards():
    async def check():
        channel = Channel()
        await guide.update_guide(channel, 7, cards(), apply=True)
        for key, message in zip(cards(), channel.messages):
            message.embeds[0].set_footer(text=guide.LEGACY_MARKER + key)
        actions = await guide.update_guide(channel, 7, cards(), apply=True)
        assert all(action == "edit" for _, action in actions)
        assert channel.send.await_count == 6
        assert channel.messages[0].pinned
    asyncio.run(check())


def test_duplicate_managed_cards_abort_before_any_write():
    async def check():
        channel = Channel()
        first = await channel.create(embed=cards()["start"], allowed_mentions=None)
        channel.messages.append(first)
        with pytest.raises(ValueError, match="Duplicate"):
            await guide.update_guide(channel, 7, cards(), apply=True)
        channel.send.assert_not_awaited()
        first.edit.assert_not_awaited()
        first.pin.assert_not_awaited()
    asyncio.run(check())


def test_explicit_guild_and_unique_channels_required():
    with pytest.raises(SystemExit):
        guide.main([])
    with pytest.raises(ValueError):
        guide.resolve_channels(channels(456), 123)
    with pytest.raises(ValueError):
        guide.resolve_channels(channels() + channels()[:1], 123)
    assert set(guide.resolve_channels(channels(), 123)) == set(guide.CHANNEL_NAMES)


def test_permission_failure_stops_before_history_or_writes(monkeypatch):
    target_channels = channels()
    target = target_channels[0]
    target.history = AsyncMock()
    target.send = AsyncMock()
    target.permissions_for = lambda member: SimpleNamespace(view_channel=False, read_message_history=False)
    guild = SimpleNamespace(id=123, fetch_channels=AsyncMock(return_value=target_channels),
                            fetch_member=AsyncMock(return_value=object()))

    class Client:
        def __init__(self, **kwargs):
            self.user = SimpleNamespace(id=7)
            self.login = AsyncMock()
            self.fetch_guild = AsyncMock(return_value=guild)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    monkeypatch.setattr(guide.discord, "Client", Client)
    monkeypatch.setattr(guide, "load_dotenv", lambda path: None)
    monkeypatch.setenv("SAKURA_BOT_TOKEN", "fake-test-token")
    with pytest.raises(ValueError, match="Missing channel permissions"):
        asyncio.run(guide.run(123, apply=True))
    target.history.assert_not_called()
    target.send.assert_not_called()


def test_published_commands_exist_and_sensitive_actions_are_accurate():
    for agent, embed in cards().items():
        if agent == "start":
            continue
        tree = ast.parse((Path(guide.BASE_DIR) / f"bot_{agent}.py").read_text(encoding="utf-8"))
        commands = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "command":
                for keyword in node.keywords:
                    if keyword.arg == "name":
                        commands.add(ast.literal_eval(keyword.value))
                    elif keyword.arg == "aliases":
                        commands.update(ast.literal_eval(keyword.value))
        assert set(re.findall(r"!([a-z_]+)", embed.description)) <= commands
        assert len(embed.description) < 4096
    text = "\n".join(embed.description for embed in cards().values())
    assert "also archives" in text and "after your confirmation" in text
    assert "do not submit job applications" in text
    assert "localhost" not in text and "127.0.0.1" not in text
    assert "!palette" not in text and "!review_code" not in text
