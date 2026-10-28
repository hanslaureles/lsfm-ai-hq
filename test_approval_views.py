"""Approval UX without live Discord, credentials, or job applications."""

import asyncio
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import discord
import approval_views
import bot_sakura
import sakura_engine


def interaction():
    return SimpleNamespace(
        user=SimpleNamespace(id=7), channel_id=3,
        message=SimpleNamespace(id=42),
        response=SimpleNamespace(defer=mock.AsyncMock(), is_done=mock.Mock(return_value=True)),
        followup=SimpleNamespace(send=mock.AsyncMock()))


class ApprovalViewsTest(unittest.IsolatedAsyncioTestCase):
    async def test_buttons_persist_and_restore_after_client_clear(self):
        bot = SimpleNamespace(persistent_views=[])
        bot.add_view = lambda view: bot.persistent_views.append(view)
        handler = mock.AsyncMock(return_value="Recorded")
        approval_views.restore_approval_views(bot, handler)
        approval_views.restore_approval_views(bot, handler)
        self.assertEqual(len(bot.persistent_views), 1)
        view = bot.persistent_views[0]
        self.assertTrue(view.is_persistent())
        self.assertEqual([b.label for b in view.children], ["Approve package", "Skip", "View documents"])
        bot.persistent_views.clear()
        approval_views.restore_approval_views(bot, handler)
        restored = bot.persistent_views[0]
        self.assertEqual([b.custom_id for b in restored.children], [b.custom_id for b in view.children])
        await restored.approve.callback(interaction())
        handler.assert_awaited_once()

    async def test_owner_gate_acknowledges_before_owner_lookup(self):
        i = interaction()

        async def owner(_bot, _user):
            i.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
            return False

        handler = mock.AsyncMock()
        view = approval_views.ApprovalView(object(), handler)
        with mock.patch.object(approval_views.owner_guard, "allowed", side_effect=owner):
            self.assertFalse(await view.interaction_check(i))
        handler.assert_not_awaited()
        self.assertIn("Only Hans", i.followup.send.call_args.args[0])

    async def test_qa_warning_and_defer_then_callback(self):
        i = interaction()
        i.response.is_done.return_value = False

        async def handler(*args, **kwargs):
            i.response.defer.assert_awaited_once()
            return "Recorded. No application was submitted."

        view = approval_views.ApprovalView(object(), handler, qa_warning=True)
        self.assertEqual(view.approve.label, "Override QA & approve")
        self.assertEqual(view.approve.style, discord.ButtonStyle.danger)
        await view.approve.callback(i)
        self.assertIn("No application was submitted", i.followup.send.call_args.args[0])

    async def test_expired_interaction_never_records_a_choice(self):
        i = interaction()
        i.response.is_done.return_value = False
        i.response.defer.side_effect = discord.NotFound(SimpleNamespace(status=404, reason="gone"), "expired")
        handler = mock.AsyncMock()
        await approval_views.ApprovalView(object(), handler).approve.callback(i)
        handler.assert_not_awaited()

    async def test_expired_followup_does_not_repeat_the_decision(self):
        i = interaction()
        i.followup.send.side_effect = discord.NotFound(SimpleNamespace(status=404, reason="gone"), "expired")
        handler = mock.AsyncMock(return_value="Approved")
        await approval_views.ApprovalView(object(), handler).approve.callback(i)
        handler.assert_awaited_once()

    async def test_button_error_hides_paths(self):
        i = interaction()
        handler = mock.AsyncMock(side_effect=OSError(r"C:\private\resume.pdf"))
        await approval_views.ApprovalView(object(), handler).approve.callback(i)
        self.assertNotIn("private", i.followup.send.call_args.args[0])


class SharedApprovalTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.card = SimpleNamespace(id=42, author=SimpleNamespace(id=1), guild=None, embeds=[],
                                    attachments=[], reply=mock.AsyncMock(), edit=mock.AsyncMock())
        self.channel = SimpleNamespace(id=3, fetch_message=mock.AsyncMock(return_value=self.card))
        self.bot = SimpleNamespace(user=SimpleNamespace(id=1), get_channel=lambda _: self.channel,
                                   guilds=[], is_owner=mock.AsyncMock(return_value=True))
        for target, name, value in [(bot_sakura, "bot", self.bot),
                                    (bot_sakura, "APPLICATIONS_DIR", self.root),
                                    (sakura_engine, "PENDING_PROPOSALS_FILE", self.root / "pending.json"),
                                    (sakura_engine, "MEMORY_DIR", self.root)]:
            patch = mock.patch.object(target, name, value)
            patch.start()
            self.addCleanup(patch.stop)

    def save(self, **kwargs):
        sakura_engine.save_pending_proposal(42, {"company": "Acme", "role": "Engineer", "channel_id": 3,
                                                "filename": "", **kwargs})

    async def test_concurrent_reaction_and_button_record_one_approval(self):
        self.save(filename="acme.md")
        log = self.root / "applications_log.md"
        log.write_text("", encoding="utf-8")
        reaction = SimpleNamespace(user_id=7, message_id=42, channel_id=3, emoji="✅")
        await asyncio.gather(bot_sakura.on_raw_reaction_add(reaction),
                             bot_sakura.handle_approval_action(42, 3, "✅", self.card))
        self.card.reply.assert_awaited_once()
        self.assertIsNone(sakura_engine.get_pending_proposal(42))
        self.assertEqual(log.read_text(encoding="utf-8").count("Approved"), 1)

    async def test_double_job_button_changes_status_once(self):
        folder = self.root / "job"
        folder.mkdir()
        (folder / "meta.json").write_text(json.dumps({"status": "queued"}), encoding="utf-8")
        self.save(type="job_hunt", folder=str(folder))
        original = bot_sakura.apply_job_reaction
        with mock.patch.object(bot_sakura, "apply_job_reaction", wraps=original):
            results = await asyncio.gather(*(bot_sakura.handle_approval_action(42, 3, "✅", self.card)
                                             for _ in range(2)))
        self.assertIn("Approved", results[0])
        self.assertIn("current status was kept", results[1])
        self.assertEqual(json.loads((folder / "meta.json").read_text())["status"], "approved")

    async def test_documents_and_skip_use_the_same_pending_card(self):
        self.save()
        self.card.attachments = [SimpleNamespace(filename="Resume.pdf", url="https://cdn.discordapp.com/resume")]
        result = await bot_sakura.handle_approval_action(42, 3, "documents", self.card)
        self.assertIn("[Resume.pdf](https://cdn.discordapp.com/resume)", result)
        self.assertIsNotNone(sakura_engine.get_pending_proposal(42))
        result = await bot_sakura.handle_approval_action(42, 3, "❌", self.card)
        self.assertIn("skipped", result)
        self.assertIsNone(sakura_engine.get_pending_proposal(42))
        self.assertIn("Resume.pdf", await bot_sakura.handle_approval_action(42, 3, "documents", self.card))

    async def test_stale_wrong_channel_and_wrong_author_never_record(self):
        self.assertIn("already been handled", await bot_sakura.handle_approval_action(42, 3, "✅", self.card))
        self.save()
        self.assertIn("does not match", await bot_sakura.handle_approval_action(42, 99, "✅", self.card))
        self.card.author.id = 99
        self.assertIn("not a trusted", await bot_sakura.handle_approval_action(42, 3, "✅", self.card))
        self.assertIsNotNone(sakura_engine.get_pending_proposal(42))
        self.card.reply.assert_not_awaited()

    async def test_missing_message_never_records(self):
        self.save()
        self.channel.fetch_message.side_effect = discord.NotFound(SimpleNamespace(status=404, reason="gone"), "deleted")
        self.assertIn("No decision", await bot_sakura.handle_approval_action(42, 3, "✅"))
        self.assertIsNotNone(sakura_engine.get_pending_proposal(42))

    async def test_chaewon_card_keeps_legacy_reaction_approval(self):
        self.card.author.id = 2
        self.save(author_id=2)
        payload = SimpleNamespace(user_id=7, message_id=42, channel_id=3, emoji="✅")
        await bot_sakura.on_raw_reaction_add(payload)
        self.assertIsNone(sakura_engine.get_pending_proposal(42))
        self.card.reply.assert_awaited_once()

    async def test_old_chaewon_card_without_author_state_uses_registered_identity(self):
        import discord_routing
        self.card.author.id = 2
        self.save()
        with mock.patch.dict(discord_routing._identities, {2: "chaewon"}, clear=True):
            result = await bot_sakura.handle_approval_action(42, 3, "✅", self.card)
        self.assertIn("approved", result)
        self.assertIsNone(sakura_engine.get_pending_proposal(42))

    async def test_approval_never_mirrors_to_another_guild(self):
        self.save()
        self.card.guild = SimpleNamespace(text_channels=[])
        foreign_docs = SimpleNamespace(name="application-docs", id=9, send=mock.AsyncMock())
        self.bot.guilds = [SimpleNamespace(text_channels=[foreign_docs])]
        await bot_sakura.handle_approval_action(42, 3, "✅", self.card)
        foreign_docs.send.assert_not_awaited()

    async def test_mirror_failure_closes_files_and_does_not_double_approve(self):
        pdf = self.root / "resume.pdf"
        pdf.write_bytes(b"%PDF")
        self.save(resume_pdf=str(pdf))
        docs = SimpleNamespace(name="application-docs", id=9, send=mock.AsyncMock(side_effect=OSError("offline")))
        self.card.guild = SimpleNamespace(text_channels=[docs])
        self.bot.guilds = [SimpleNamespace(text_channels=[docs])]
        opened = []
        original = bot_sakura._apply_files

        def files(data):
            result = original(data)
            opened.extend(result)
            return result

        with mock.patch.object(bot_sakura, "_apply_files", side_effect=files):
            await bot_sakura.handle_approval_action(42, 3, "✅", self.card)
            await bot_sakura.handle_approval_action(42, 3, "✅", self.card)
        docs.send.assert_awaited_once()
        self.assertEqual(len(opened), 1)
        self.assertTrue(opened[0].fp.closed)


if __name__ == "__main__":
    unittest.main()
