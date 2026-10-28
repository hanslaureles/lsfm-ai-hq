"""Discord summaries, task threads and status updates without live services."""
import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import discord
import discord_utils as delivery


class DeliveryTest(unittest.IsolatedAsyncioTestCase):
    def target(self):
        thread = mock.Mock(spec=discord.Thread)
        thread.send = mock.AsyncMock()
        message = types.SimpleNamespace(create_thread=mock.AsyncMock(return_value=thread), thread=None)
        channel = types.SimpleNamespace(send=mock.AsyncMock())
        target = types.SimpleNamespace(message=message, guild=object(), channel=channel, reply=mock.AsyncMock())
        return target, thread

    async def test_task_thread_sends_results_inside_and_reuses_existing_thread(self):
        target, thread = self.target()
        routed = await delivery.start_task_thread(target, 'Portfolio review')
        await routed.reply('Result')
        thread.send.assert_awaited_once_with('Result')
        target.reply.assert_not_awaited()
        self.assertIs(routed.guild, target.guild)
        self.assertIs(await delivery.start_task_thread(routed, 'Second task'), routed)
        target.message.create_thread.assert_awaited_once()

    async def test_thread_failure_and_dm_keep_original_target(self):
        target, _ = self.target()
        target.message.create_thread.side_effect = discord.Forbidden(mock.Mock(status=403, reason='Forbidden'), 'No thread permission')
        self.assertIs(await delivery.start_task_thread(target, 'Task'), target)
        target.guild = None
        target.message.create_thread.reset_mock()
        self.assertIs(await delivery.start_task_thread(target, 'Task'), target)
        target.message.create_thread.assert_not_awaited()

    async def test_summary_is_short_and_full_text_is_preserved_in_thread(self):
        target, thread = self.target()
        summary = types.SimpleNamespace(create_thread=target.message.create_thread)
        target.channel.send.return_value = summary
        text = '**Needs your attention**\nReview Acme.\n\n**Details**\n' + ('Evidence. ' * 650)
        await delivery.send_summary(target.channel, 'Morning', text, footer_text='Sakura')
        first = target.channel.send.call_args.kwargs['embed']
        self.assertLessEqual(len(first.description), 1000)
        self.assertIn('Review Acme', first.description)
        self.assertGreaterEqual(thread.send.await_count, 2)
        full = ''.join(c.kwargs['embed'].description for c in thread.send.await_args_list)
        self.assertIn('Evidence.', full)
        self.assertEqual(full.count('Evidence.'), 650)

    async def test_summary_falls_back_to_full_delivery_if_threads_unavailable(self):
        target, _ = self.target()
        target.channel.send.return_value = types.SimpleNamespace()
        text = 'Important evidence. ' * 400
        await delivery.send_summary(target.channel, 'Morning', text)
        full = ''.join(c.kwargs['embed'].description for c in target.channel.send.await_args_list[1:])
        self.assertEqual(full.count('Important evidence.'), 400)

    async def test_deleted_progress_message_does_not_discard_completed_summary(self):
        target, _ = self.target()
        status = types.SimpleNamespace(delete=mock.AsyncMock(side_effect=discord.NotFound(
            mock.Mock(status=404, reason='Not Found'), 'Already removed')))
        await delivery.send_summary(target.channel, 'Finished report', 'Your next action.',
                                    status_msg_to_delete=status)
        self.assertEqual(target.channel.send.call_args.kwargs['embed'].description, 'Your next action.')

    async def test_status_updates_same_card_after_restart_without_new_post(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / 'status.json'
            channel = types.SimpleNamespace(id=10, guild=types.SimpleNamespace(me=types.SimpleNamespace(id=99)),
                                            send=mock.AsyncMock(), fetch_message=mock.AsyncMock())
            old = types.SimpleNamespace(id=22, author=types.SimpleNamespace(id=99), edit=mock.AsyncMock())
            channel.send.return_value = old
            channel.fetch_message.return_value = old
            await delivery.update_status_card(channel, discord.Embed(title='Day one'), state)
            await delivery.update_status_card(channel, discord.Embed(title='Day two'), state)
            channel.send.assert_awaited_once()
            channel.fetch_message.assert_awaited_once_with(22)
            old.edit.assert_awaited_once()
            self.assertEqual(json.loads(state.read_text())[str(channel.id)], 22)

    async def test_deleted_status_replaced_but_read_permission_failure_does_not_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / 'status.json'
            state.write_text('{"10": 22}')
            channel = types.SimpleNamespace(id=10, guild=types.SimpleNamespace(me=types.SimpleNamespace(id=99)),
                                            send=mock.AsyncMock(), fetch_message=mock.AsyncMock())
            channel.fetch_message.side_effect = discord.NotFound(mock.Mock(status=404, reason='Not Found'), 'Deleted')
            channel.send.return_value = types.SimpleNamespace(id=23)
            await delivery.update_status_card(channel, discord.Embed(title='Latest'), state)
            self.assertEqual(json.loads(state.read_text())['10'], 23)
            channel.fetch_message.side_effect = discord.Forbidden(mock.Mock(status=403, reason='Forbidden'), 'Denied')
            channel.send.reset_mock()
            with self.assertRaises(discord.Forbidden):
                await delivery.update_status_card(channel, discord.Embed(title='Latest'), state)
            channel.send.assert_not_awaited()

    async def test_status_never_edits_a_foreign_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / 'status.json'
            state.write_text('{"10": 22}')
            foreign = types.SimpleNamespace(author=types.SimpleNamespace(id=3), edit=mock.AsyncMock())
            channel = types.SimpleNamespace(id=10, guild=types.SimpleNamespace(me=types.SimpleNamespace(id=99)),
                                            send=mock.AsyncMock(return_value=types.SimpleNamespace(id=23)),
                                            fetch_message=mock.AsyncMock(return_value=foreign))
            with self.assertRaises(ValueError):
                await delivery.update_status_card(channel, discord.Embed(title='Latest'), state)
            foreign.edit.assert_not_awaited()
            channel.send.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
