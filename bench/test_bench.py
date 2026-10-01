"""
Unit tests for the benchmark's statistics and report output (no stage is run).
Run: python -m pytest -q bench/test_bench.py
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bench_ciel  # noqa: E402


class TestStats(unittest.TestCase):

    def test_nearest_rank_percentiles(self):
        samples = list(range(1, 101))  # 1..100
        self.assertEqual(bench_ciel.percentile(samples, 50), 50)
        self.assertEqual(bench_ciel.percentile(samples, 95), 95)
        self.assertEqual(bench_ciel.percentile(samples, 100), 100)

    def test_small_samples_p95_is_the_max(self):
        samples = [120.0, 80.0, 100.0, 90.0, 300.0]
        self.assertEqual(bench_ciel.percentile(samples, 95), 300.0)
        self.assertEqual(bench_ciel.percentile(samples, 50), 100.0)

    def test_summary_and_empty(self):
        self.assertIsNone(bench_ciel.summarize([]))
        s = bench_ciel.summarize([10.04, 20.06, 30.0])
        self.assertEqual(s, {"p50": 20.1, "p95": 30.0, "max": 30.0, "min": 10.0, "mean": 20.0})


class TestMarkdown(unittest.TestCase):

    def test_report_labels_network_stages_and_failures(self):
        report = {
            "date": "2026-10-01", "runs": 2,
            "environment": {
                "hardware": {"cpu": "CPU", "cpu_logical_cores": 12, "ram_gb": 15.8, "gpu": "GPU"},
                "software": {"os": "Windows 11", "python": "3.11.9", "ffmpeg": "ffmpeg version 9", "ollama": "0.34"},
                "git": {"sha": "abcdef1234", "dirty": True},
            },
            "stages": {
                "ffmpeg_inbound": {"network": False, "notes": "local", "n_ok": 2, "n_failed": 0, "errors": [],
                                   "ms": bench_ciel.summarize([30, 40]), "extras": []},
                "whisper_groq": {"network": True, "notes": "net", "n_ok": 0, "n_failed": 2,
                                 "errors": ["ValueError: GROQ_API_KEY is not configured"], "ms": None, "extras": []},
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "r.md"
            bench_ciel.write_markdown(report, path)
            text = path.read_text(encoding="utf-8")
            json.dumps(report)  # the JSON report must stay serializable
        self.assertIn("| `ffmpeg_inbound` | local | 2 / 0 | 30 | 40 | 40 |", text)
        self.assertIn("| `whisper_groq` | network | 0 / 2 | – | – | – |", text)
        self.assertIn("`abcdef1` (uncommitted changes)", text)
        self.assertIn("GROQ_API_KEY is not configured", text)
        self.assertIn("p95 is the max", text)


if __name__ == "__main__":
    unittest.main()
