"""
Tests for llm_client's Ollama URL handling. Hermetic: only the environment is patched.
Run: python -m pytest -q test_llm_client.py
"""

import importlib.util
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


if __name__ == "__main__":
    unittest.main()
