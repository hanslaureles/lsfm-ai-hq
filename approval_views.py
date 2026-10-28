"""Restart-safe approval controls; all decisions use Sakura's reaction handler."""

import asyncio
from weakref import WeakValueDictionary

import discord
import owner_guard


_locks = WeakValueDictionary()


def action_lock(message_id):
    lock = _locks.get(message_id)
    if lock is None:
        lock = _locks[message_id] = asyncio.Lock()
    return lock


class ApprovalView(discord.ui.View):
    def __init__(self, bot, handle_action, qa_warning=False):
        super().__init__(timeout=None)
        self.bot, self.handle_action = bot, handle_action
        if qa_warning:
            self.approve.label = "Override QA & approve"
            self.approve.style = discord.ButtonStyle.danger

    async def interaction_check(self, interaction):
        try:
            await interaction.response.defer(ephemeral=True, thinking=True)
        except discord.NotFound:
            return False
        if await owner_guard.allowed(self.bot, interaction.user):
            return True
        await interaction.followup.send("Only Hans can review these packages.", ephemeral=True)
        return False

    async def decide(self, interaction, action):
        try:
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True, thinking=True)
        except discord.NotFound:
            return  # Discord expired the interaction before acknowledgement; make no decision.
        try:
            result = await self.handle_action(interaction.message.id, interaction.channel_id, action,
                                              target_msg=interaction.message)
        except Exception as exc:
            print(f"[ApprovalView] {type(exc).__name__}: approval action failed", flush=True)
            result = "The action could not finish. Check this card's status before trying again."
        try:
            await interaction.followup.send(result, ephemeral=True)
        except discord.NotFound:
            pass  # The recorded decision survives an expired follow-up token.

    @discord.ui.button(label="Approve package", style=discord.ButtonStyle.success,
                       custom_id="sakura:approval:approve")
    async def approve(self, interaction, button):
        await self.decide(interaction, "✅")

    @discord.ui.button(label="Skip", style=discord.ButtonStyle.secondary,
                       custom_id="sakura:approval:skip")
    async def skip(self, interaction, button):
        await self.decide(interaction, "❌")

    @discord.ui.button(label="View documents", style=discord.ButtonStyle.secondary,
                       custom_id="sakura:approval:documents")
    async def documents(self, interaction, button):
        await self.decide(interaction, "documents")


def restore_approval_views(bot, handle_action):
    """One stateless dispatcher covers every persisted pending message after reconnect/restart."""
    view = getattr(bot, "_approval_view", None)
    if view is None or view not in bot.persistent_views:
        view = ApprovalView(bot, handle_action)
        bot.add_view(view)
        bot._approval_view = view
