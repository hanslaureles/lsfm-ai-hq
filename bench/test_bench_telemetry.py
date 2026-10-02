"""
Unit tests for the LLM telemetry benchmark (4B-3). Hermetic: no keys, no network.
Run: python -m pytest -q bench/test_bench_telemetry.py
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bench_telemetry  # noqa: E402

LLMResult = bench_telemetry.llm_client.LLMResult


def scripted(*outcomes):
    """A query function answering from a script of LLMResult or exceptions."""
    outcomes = list(outcomes)

    def query(prompt, agent, on_delta=None):
        o = outcomes.pop(0)
        if isinstance(o, Exception):
            raise o
        return o
    return query


def result(ms, chain=("groq/m1",), ttft=None):
    provider, model = chain[-1].split("/", 1)
    return LLMResult(text="x", model=model, provider=provider, ttft_ms=ttft, total_ms=ms, fallback_chain=chain)


class TestRunAgent(unittest.TestCase):

    def test_stats_fallback_rate_and_failures(self):
        q = scripted(result(100.0), result(300.0, chain=("groq/m1", "groq/m2")), RuntimeError("all down"),
                     result(200.0))
        a = bench_telemetry.run_agent("sakura", "p", 4, pace_s=0, query=q)
        self.assertEqual((a["n_ok"], a["n_failed"]), (3, 1))
        self.assertEqual((a["total_ms"]["p50"], a["total_ms"]["p95"]), (200.0, 300.0))
        self.assertEqual(a["fallback_rate"], 0.33)
        self.assertEqual(a["answered_by"], {"groq/m1": 2, "groq/m2": 1})
        self.assertEqual(a["errors"], ["RuntimeError: all down"])
        self.assertIsNone(a["ttft_ms"])  # nothing streamed

    def test_all_failed_and_small_samples_do_not_divide_by_zero(self):
        a = bench_telemetry.run_agent("eunchae", "p", 2, pace_s=0, query=scripted(ValueError("x"), ValueError("x")))
        self.assertEqual((a["n_ok"], a["total_ms"], a["fallback_rate"]), (0, None, None))
        one = bench_telemetry.run_agent("eunchae", "p", 1, pace_s=0, query=scripted(result(50.0, ttft=20.0)))
        self.assertEqual((one["total_ms"]["p50"], one["total_ms"]["p95"], one["ttft_ms"]["p50"]), (50.0, 50.0, 20.0))

    def test_paces_before_every_call_and_passes_on_delta_only_when_streaming(self):
        seen, sleeps = [], []

        def q(prompt, agent, on_delta=None):
            seen.append(on_delta is not None)
            return result(10.0)
        bench_telemetry.run_agent("kazuha", "p", 2, stream=True, pace_s=3.0, query=q, sleep=sleeps.append)
        bench_telemetry.run_agent("kazuha", "p", 1, stream=False, pace_s=0, query=q, sleep=sleeps.append)
        self.assertEqual((seen, sleeps), ([True, True, False], [3.0, 3.0]))


class TestMockRun(unittest.TestCase):

    def setUp(self):
        self.enterContext(mock.patch.object(bench_telemetry, "collect_environment", lambda: {
            "hardware": {"cpu": "cpu", "cpu_logical_cores": 1, "ram_gb": 1, "gpu": "gpu"},
            "software": {"os": "os", "python": "3"}, "git": {"sha": "abcdef0", "dirty": False}}))
        self.enterContext(mock.patch("builtins.print"))

    def test_mock_run_covers_every_agent_and_stays_out_of_bench_results(self):
        before = sorted(p.name for p in bench_telemetry.RESULTS_DIR.iterdir())
        out = Path(self.enterContext(tempfile.TemporaryDirectory())) / "t"
        report = bench_telemetry.main(["--mock", "--runs", "2", "--out", str(out)])
        self.assertEqual(sorted(p.name for p in bench_telemetry.RESULTS_DIR.iterdir()), before)
        saved = json.loads(Path(f"{out}.json").read_text(encoding="utf-8"))
        self.assertTrue(saved["mock"])
        self.assertEqual(set(saved["agents"]), set(bench_telemetry.PROMPTS))
        self.assertEqual(saved["overall"]["n_ok"], 10)
        # Routing still follows the config: Yunjin answers from Gemini, the rest from Groq.
        self.assertEqual(saved["agents"]["yunjin"]["answered_by"], {"gemini/gemini-3.6-flash": 2})
        self.assertEqual(saved["agents"]["chaewon"]["answered_by"], {"groq/openai/gpt-oss-120b": 2})
        self.assertIn("MOCK", Path(f"{out}.md").read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(report["pace_s"], 0)

    def test_mocks_are_removed_after_the_run(self):
        real = bench_telemetry.llm_client.call_groq
        out = Path(self.enterContext(tempfile.TemporaryDirectory())) / "t"
        bench_telemetry.main(["--mock", "--runs", "1", "--out", str(out)])
        self.assertIs(bench_telemetry.llm_client.call_groq, real)

    def test_mock_refuses_to_write_into_bench_results(self):
        with self.assertRaises(SystemExit), mock.patch("sys.stderr"):
            bench_telemetry.main(["--mock", "--out", str(bench_telemetry.RESULTS_DIR / "fake")])


if __name__ == "__main__":
    unittest.main()
