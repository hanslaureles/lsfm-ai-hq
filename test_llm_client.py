"""
Tests for llm_client's Ollama URL handling. Hermetic: only the environment is patched.
Run: python -m pytest -q test_llm_client.py
"""

import importlib.util
import json
import os
import unittest
from pathlib import Path
from unittest import mock

# Load the real module from its file: test_ciel_security.py leaves a stub
# "llm_client" in sys.modules, which a plain import would pick up.
_spec = importlib.util.spec_from_file_location("llm_client_under_test", Path(__file__).with_name("llm_client.py"))
llm_client = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(llm_client)


class TestOllamaBaseUrl(unittest.TestCase):

    def url_for(self, value):
        env = {k: v for k, v in os.environ.items() if k != "LOCAL_LLM_URL"}
        if value is not None:
            env["LOCAL_LLM_URL"] = value
        with mock.patch.dict(os.environ, env, clear=True):
            return llm_client.ollama_base_url()

    def test_default_is_ipv4_loopback(self):
        # "localhost" tries ::1 first on Windows; Ollama listens on IPv4 only.
        self.assertEqual(self.url_for(None), "http://127.0.0.1:11434")

    def test_localhost_is_pinned_to_127(self):
        for value in ("http://localhost:11434/v1", "http://localhost:11434", "http://localhost:11434/v1/"):
            with self.subTest(value=value):
                self.assertEqual(self.url_for(value), "http://127.0.0.1:11434")

    def test_other_hosts_are_left_alone(self):
        self.assertEqual(self.url_for("http://192.168.1.20:11434/v1"), "http://192.168.1.20:11434")
        self.assertEqual(self.url_for("http://gpu-box.local:8080"), "http://gpu-box.local:8080")

    def test_callers_build_their_paths_from_it(self):
        seen = []

        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return b'{"models": [], "choices": [{"message": {"content": "ok"}}]}'

        def fake_urlopen(req, timeout=None):
            seen.append(req.full_url)
            return Resp()

        with mock.patch.dict(os.environ, {"LOCAL_LLM_URL": "http://localhost:11434/v1"}), \
                mock.patch.object(llm_client.urllib.request, "urlopen", fake_urlopen):
            llm_client.check_ollama_status()
            llm_client.call_local_ollama("hi", model="m")
        self.assertEqual(seen, ["http://127.0.0.1:11434/api/tags", "http://127.0.0.1:11434/v1/chat/completions"])


class SSE:
    """A streamed response: iterates SSE lines; optionally dies after `fail_after` lines."""

    def __init__(self, pieces, fail_after=None):
        lines = [b": keep-alive\n"]
        lines += [f"data: {json.dumps({'choices': [{'delta': {'content': p}}]})}\n".encode() for p in pieces]
        lines += [b"data: [DONE]\n"]
        self.lines, self.fail_after = lines, fail_after

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        for i, line in enumerate(self.lines):
            if self.fail_after is not None and i > self.fail_after:
                raise ConnectionResetError("socket closed mid-stream")
            yield line


class TestGroqStreaming(unittest.TestCase):

    def call(self, responses):
        bodies = []

        def fake_urlopen(req, timeout=None):
            bodies.append(json.loads(req.data))
            return responses.pop(0)

        pieces = []
        with mock.patch.dict(os.environ, {"GROQ_API_KEY": "test"}), \
                mock.patch.object(llm_client.urllib.request, "urlopen", fake_urlopen):
            try:
                return llm_client.call_groq("hi", model="m1", on_delta=pieces.append), pieces, bodies
            except RuntimeError as e:
                return e, pieces, bodies

    def test_pieces_arrive_in_order_and_join_to_the_reply(self):
        reply, pieces, bodies = self.call([SSE(['{"japanese_voice": "', "「告。」", '"}'])])
        self.assertEqual(pieces, ['{"japanese_voice": "', "「告。」", '"}'])
        self.assertEqual(reply, '{"japanese_voice": "「告。」"}')
        self.assertIs(bodies[0]["stream"], True)

    def test_failure_before_any_piece_falls_back_to_the_next_model(self):
        reply, pieces, bodies = self.call([SSE(["x"], fail_after=0), SSE(["ok"])])
        self.assertEqual(reply, "ok")
        self.assertEqual(pieces, ["ok"])
        self.assertEqual(bodies[0]["model"], "m1")
        self.assertNotEqual(bodies[1]["model"], "m1")

    def test_failure_after_a_piece_raises_instead_of_replaying(self):
        err, pieces, bodies = self.call([SSE(["Report: one. ", "two."], fail_after=1), SSE(["Report: again."])])
        self.assertIsInstance(err, RuntimeError)
        self.assertIn("partial output", str(err))
        self.assertEqual(pieces, ["Report: one. "])
        self.assertEqual(len(bodies), 1)  # no second request

    def test_without_on_delta_nothing_streams(self):
        class Resp(SSE):
            def read(self):
                return b'{"choices": [{"message": {"content": " plain "}}]}'

        bodies = []

        def fake_urlopen(req, timeout=None):
            bodies.append(json.loads(req.data))
            return Resp([])

        with mock.patch.dict(os.environ, {"GROQ_API_KEY": "test"}), \
                mock.patch.object(llm_client.urllib.request, "urlopen", fake_urlopen):
            self.assertEqual(llm_client.call_groq("hi", model="m1"), "plain")
        self.assertNotIn("stream", bodies[0])


if __name__ == "__main__":
    unittest.main()
