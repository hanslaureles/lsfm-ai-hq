"""
Only Hans commands the bots. Before this, anyone in the server could run !commit, !pr, !clean, !mode
and !hunt, chat with the bots (spending LLM quota) and approve job cards (handoff review, 2026-10-24).

The owner is the Discord application's owner (discord.py's Bot.is_owner, which asks Discord once), or
DISCORD_OWNER_ID from .env when it is set. install(bot) adds a check to every command; on_message and
on_raw_reaction_add ask allowed() before doing anything.
"""

import os

import discord


def install(bot) -> None:
    owner = (os.getenv("DISCORD_OWNER_ID") or "").strip()
    if owner.isdigit():
        bot.owner_id = int(owner)

    async def only_owner(ctx):
        return await bot.is_owner(ctx.author)

    bot.add_check(only_owner)


async def allowed(bot, user) -> bool:
    """True for the owner. `user` is a Member/User or a raw user id (reaction payloads carry only the id)."""
    if isinstance(user, int):
        user = discord.Object(id=user)
    return await bot.is_owner(user)
