"""
Assistant B6: reminders (Sakura delivers them on Discord) and their time parsing. Hermetic: temp files.
Run: python -m pytest -q test_reminders.py
"""

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import reminders

NOW = datetime(2026, 10, 26, 15, 4)


class ParseWhenTest(unittest.TestCase):
    def test_phrases(self):
        cases = {
            "2026-10-26T17:00": datetime(2026, 10, 26, 17, 0),
            "2026-10-27 08:30": datetime(2026, 10, 27, 8, 30),
            "in 20 minutes": datetime(2026, 10, 26, 15, 24),
            "in 2 hrs": datetime(2026, 10, 26, 17, 4),
            "in 3 days": datetime(2026, 10, 29, 15, 4),
            "at 5pm": datetime(2026, 10, 26, 17, 0),
            "5:30 pm": datetime(2026, 10, 26, 17, 30),
            "at 9am": datetime(2026, 10, 27, 9, 0),        # 9 am today has passed: tomorrow
            "tomorrow at 9:30": datetime(2026, 10, 27, 9, 30),
            "tomorrow at 12am": datetime(2026, 10, 27, 0, 0),
            "tomorrow": datetime(2026, 10, 27, 9, 0),
            "at 18:45": datetime(2026, 10, 26, 18, 45),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(reminders.parse_when(text, NOW), expected)

    def test_relative_times_obey_the_same_limits(self):  # Codex QA F2 (2026-10-26)
        self.assertIsNone(reminders.parse_when("in 0 minutes", NOW))          # not in the future
        self.assertEqual(reminders.parse_when("in 366 days", NOW), NOW + reminders.MAX_AHEAD)  # the limit itself
        self.assertIsNone(reminders.parse_when("in 367 days", NOW))           # past the limit
        self.assertIsNone(reminders.parse_when("in 400 days", NOW))
        self.assertIsNone(reminders.parse_when("in 999999999 days", NOW))     # timedelta overflow: refused, no raise
        self.assertIsNone(reminders.parse_when("in " + "9" * 5000 + " minutes", NOW))  # int() digit limit

    def test_unknown_or_past_is_none(self):
        for text in ("", "soon", "later today", "2026-10-26T09:00", "2028-01-01T09:00", "at 25pm", "at 7"):
            with self.subTest(text=text):
                self.assertIsNone(reminders.parse_when(text, NOW))


class StoreTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = Path(tmp.name) / "memory" / "reminders.json"

    def test_add_list_cancel(self):
        a = reminders.add("call the bank", datetime(2026, 10, 26, 17, 0), self.store)
        reminders.add("stretch", datetime(2026, 10, 26, 16, 0), self.store)
        self.assertEqual([i["text"] for i in reminders.pending(self.store)], ["stretch", "call the bank"])
        self.assertEqual([i["id"] for i in reminders.cancel("BANK", self.store)], [a["id"]])
        self.assertEqual([i["text"] for i in reminders.pending(self.store)], ["stretch"])
        self.assertEqual(reminders.cancel("", self.store), [])
        self.assertEqual(len(reminders.cancel("all", self.store)), 1)
        self.assertEqual(reminders.pending(self.store), [])

    def test_a_due_reminder_is_claimed_once(self):
        reminders.add("stretch", datetime(2026, 10, 26, 15, 0), self.store)
        reminders.add("later", datetime(2026, 10, 26, 18, 0), self.store)
        first = reminders.claim_due(NOW, self.store)
        self.assertEqual([i["text"] for i in first], ["stretch"])
        self.assertEqual(reminders.claim_due(NOW, self.store), [])  # a second loop tick sends nothing
        saved = json.loads(self.store.read_text(encoding="utf-8"))
        self.assertEqual({i["text"]: i["status"] for i in saved}, {"stretch": "sent", "later": "pending"})
        reminders.mark_failed(first[0]["id"], "DMs closed", self.store)
        self.assertEqual(json.loads(self.store.read_text(encoding="utf-8"))[0]["status"], "failed")

    def test_old_entries_are_dropped_and_a_broken_file_starts_over(self):
        reminders.add("old", datetime(2026, 9, 1, 9, 0), self.store)
        reminders.claim_due(datetime(2026, 9, 1, 9, 1), self.store)
        reminders.claim_due(NOW, self.store)  # 55 days later
        self.assertEqual(json.loads(self.store.read_text(encoding="utf-8")), [])
        self.store.write_text("{not json", encoding="utf-8")
        self.assertEqual(reminders.pending(self.store), [])


class GmailExpiryReminderTest(unittest.TestCase):
    """A Gmail sign-in schedules one re-sign reminder 6 days later; signing in again replaces it."""

    def test_one_reminder_six_days_after_the_latest_sign_in(self):
        import gmail_engine
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "reminders.json"
            reminders.add("Call the dentist", datetime(2026, 10, 30, 9, 0), store)
            gmail_engine.remind_before_expiry(datetime(2026, 10, 19, 21, 0), store)
            gmail_engine.remind_before_expiry(NOW, store)  # re-signed early: the old reminder goes
            items = reminders.pending(store)
        gmail = [i for i in items if gmail_engine.EXPIRY_REMINDER in i["text"]]
        self.assertEqual(len(gmail), 1)
        self.assertEqual(gmail[0]["due"], "2026-11-01T15:04")
        self.assertIn("python scripts/authenticate.py", gmail[0]["text"])
        self.assertIn("Call the dentist", [i["text"] for i in items])  # other reminders untouched


if __name__ == "__main__":
    unittest.main()
