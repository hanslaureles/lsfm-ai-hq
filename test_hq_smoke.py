"""
Smoke Test Suite for LE SSERAFIM AI HQ
Tests syntax compilation, configuration integrity, and Obsidian integration.
"""

import os
import unittest
import py_compile
from pathlib import Path
from obsidian_client import ObsidianClient, VAULT_PATHS

ROOT = Path(__file__).resolve().parent


class TestLSFMHQSmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parent

    def test_01_all_python_files_compile(self):
        """Verify that every python file in the project compiles with zero syntax errors."""
        py_files = list(self.root.glob("*.py"))
        self.assertGreater(len(py_files), 10, "Should find at least 10 python scripts")
        for py_path in py_files:
            try:
                py_compile.compile(str(py_path), doraise=True)
            except Exception as e:
                self.fail(f"Syntax error compiling {py_path.name}: {e}")

    # .env holds secrets and is never committed, so CI has none; this checks the
    # workstation's copy wherever one exists.
    @unittest.skipUnless((ROOT / ".env").exists(), "no .env here (CI checkout); the workstation runs this check")
    def test_02_env_file_integrity(self):
        """Verify .env has all 5 bot tokens and required environment variables."""
        env_file = self.root / ".env"

        lines = env_file.read_text(encoding="utf-8").splitlines()
        keys = set()
        for line in lines:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key = line.split("=", 1)[0].strip()
                keys.add(key)

        required_keys = [
            "SAKURA_BOT_TOKEN",
            "CHAEWON_BOT_TOKEN",
            "YUNJIN_BOT_TOKEN",
            "KAZUHA_BOT_TOKEN",
            "EUNCHAE_BOT_TOKEN",
            "OBSIDIAN_VAULT_PATH",
        ]
        for req in required_keys:
            self.assertIn(req, keys, f"Missing required configuration key: {req}")

    def test_03_obsidian_client_filesystem_fallback(self):
        """The real vault has every location in VAULT_PATHS, and the agent profile reads. Read-only."""
        client = ObsidianClient()
        if not client.vault_path.exists():
            self.skipTest(f"Obsidian vault {client.vault_path} is not on this machine (CI checkout)")

        for key, rel in VAULT_PATHS.items():
            self.assertTrue((client.vault_path / rel).exists(), f"VAULT_PATHS[{key!r}] = {rel!r} is not in the vault")

        # Verify agent profile lookup (Kazuha)
        profile = client.get_agent_profile("Kazuha")
        self.assertIn("Kazuha", profile, "Profile should contain agent name")
        self.assertIn("Frontend Architect", profile, "Profile should contain role description")


if __name__ == "__main__":
    unittest.main()
