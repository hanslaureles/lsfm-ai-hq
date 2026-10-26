"""
Assistant B5: local keyword search (BM25) over Hans's notes. Hermetic: temp folders only.
Run: python -m pytest -q test_file_search.py
"""

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import file_search


class FileSearchTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.reports, self.vault = Path(tmp.name) / "reports", Path(tmp.name) / "vault"
        self.reports.mkdir()
        (self.vault / ".obsidian").mkdir(parents=True)
        self.write(self.reports / "GMAIL-2026-10-19.md", "# Gmail OAuth\n\nHans keeps Gmail OAuth in Testing; "
                                                         "it lapses every 7 days and is re-signed by hand.\n")
        self.write(self.vault / "Planning" / "GMAIL-2026-10-19.md",  # the daily job's mirror: same bytes
                   (self.reports / "GMAIL-2026-10-19.md").read_text(encoding="utf-8"))
        self.write(self.vault / "Ideas.md", "# Ideas\n\n## Coffee\n\nA pour-over timer app.\n\n## Voice\n\n"
                                            "Ciel should answer the weather in Silang.\n")
        self.write(self.vault / ".obsidian" / "workspace.md", "gmail oauth gmail oauth gmail oauth")
        self.write(self.vault / "Long.md", "# Log\n\n" + "\n\n".join(f"Entry {i}: nothing to see." for i in range(400)))
        self.index = file_search.Index([self.reports, self.vault])

    @staticmethod
    def write(path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_finds_the_passage_and_names_its_file_and_heading(self):
        hits = self.index.search("when does the Gmail OAuth lapse?")
        self.assertEqual(hits[0]["file"], "reports/GMAIL-2026-10-19.md")  # the source, not the vault mirror
        self.assertEqual(hits[0]["heading"], "Gmail OAuth")
        self.assertIn("lapses every 7 days", hits[0]["snippet"])
        self.assertRegex(hits[0]["modified"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertEqual([h["file"] for h in hits].count("vault/Planning/GMAIL-2026-10-19.md"), 0)
        self.assertNotIn("vault/.obsidian/workspace.md", [h["file"] for h in hits])

    def test_a_section_is_its_own_passage(self):
        hits = self.index.search("weather Silang")
        self.assertEqual((hits[0]["file"], hits[0]["heading"]), ("vault/Ideas.md", "Voice"))
        self.assertNotIn("pour-over", hits[0]["snippet"])

    def test_rare_words_outweigh_common_ones(self):  # IDF: "entry" is everywhere in Long.md, "pour" is not
        self.assertEqual(self.index.search("entry pour-over")[0]["heading"], "Coffee")

    def test_no_match_is_empty_and_long_files_are_split(self):
        self.assertEqual(self.index.search("kubernetes helm"), [])
        self.assertGreater(sum(1 for c in self.index.chunks() if c["file"] == "vault/Long.md"), 3)

    def test_an_edited_or_deleted_note_is_seen_on_the_next_search(self):
        self.assertEqual(self.index.search("sourdough"), [])
        note = self.vault / "Ideas.md"
        note.write_text("# Ideas\n\nBake sourdough on Sunday.\n", encoding="utf-8")
        os.utime(note, (time.time() + 5, time.time() + 5))  # a new mtime even on a coarse clock
        self.assertEqual(self.index.search("sourdough")[0]["file"], "vault/Ideas.md")
        note.unlink()
        self.assertEqual(self.index.search("sourdough"), [])

    def test_missing_roots(self):
        self.assertTrue(file_search.Index([self.vault, self.vault / "nope"]).available())
        self.assertFalse(file_search.Index([self.vault / "nope"]).available())

    def test_roots_come_from_env_or_default_to_reports_then_vault(self):
        with mock.patch.dict(os.environ, {"CIEL_FILE_ROOTS": os.pathsep.join([str(self.vault), str(self.reports)])}):
            self.assertEqual(file_search.default_roots(), [self.vault, self.reports])
        with mock.patch.dict(os.environ, {"CIEL_FILE_ROOTS": ""}):
            names = [p.name for p in file_search.default_roots()]
        self.assertEqual(names[0], "reports")
        self.assertEqual(len(names), 2)


if __name__ == "__main__":
    unittest.main()
