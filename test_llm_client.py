"""
Tests for llm_client's Ollama URL handling. Hermetic: only the environment is patched.
Run: python -m pytest -q test_llm_client.py
"""

import importlib.util
import json
import os
import tempfile
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


class TestTypedRouting(unittest.TestCase):
    """3E-1: query_llm routes by an explicit agent, never by stack frames or prompt text."""

    def setUp(self):
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.config = tmp / "brain_mode.json"
        self.config.write_text(json.dumps({"mode": "cloud"}), encoding="utf-8")
        self.enterContext(mock.patch.object(llm_client, "CONFIG_FILE", self.config))
        self.enterContext(mock.patch.object(llm_client, "_config_cache", (None, None), create=True))
        self.calls = []
        self.enterContext(mock.patch.object(llm_client, "call_groq",
                                            lambda p, **k: self.calls.append(("groq", k["model"])) or "ok"))
        self.enterContext(mock.patch.object(llm_client, "call_gemini",
                                            lambda p, **k: self.calls.append(("gemini", k["model_name"])) or "ok"))
        self.enterContext(mock.patch.dict(os.environ, {"GEMINI_API_KEY": "test"}))

    def test_agent_is_required(self):
        with self.assertRaises(TypeError):
            llm_client.query_llm("hi")  # noqa: the point of the test
        for bad in ("", "ciel", None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                llm_client.query_llm("hi", agent=bad)

    def test_each_agent_gets_its_model_and_prompt_text_does_not_steer(self):
        expected = {"sakura": ("groq", "qwen/qwen3.8-27b"), "chaewon": ("groq", "openai/gpt-oss-120b"),
                    "yunjin": ("gemini", "gemini-3.6-flash"), "kazuha": ("groq", "qwen/qwen3.8-27b"),
                    "eunchae": ("groq", "openai/gpt-oss-20b")}
        for agent, call in expected.items():
            with self.subTest(agent=agent):
                self.calls.clear()
                # The old prompt-text fallback would have routed this to Yunjin.
                llm_client.query_llm("Yunjin, please review this.", agent=agent)
                self.assertEqual(self.calls, [call])

    def test_config_read_once_and_reloaded_on_change(self):
        reads = []
        real_read = Path.read_text

        def counting_read(path, *a, **k):
            if path == self.config:
                reads.append(1)
            return real_read(path, *a, **k)

        with mock.patch.object(Path, "read_text", counting_read):
            for _ in range(5):
                llm_client.load_brain_config()
            self.assertEqual(len(reads), 1)
            cfg = llm_client.load_brain_config()
            cfg["agent_cloud_models"]["chaewon"] = "mutated"  # a caller's copy, not the cache
            self.assertEqual(llm_client.load_brain_config()["agent_cloud_models"]["chaewon"], "openai/gpt-oss-120b")
            self.config.write_text(json.dumps({"mode": "local"}), encoding="utf-8")
            st = self.config.stat()
            os.utime(self.config, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))  # a distinct mtime
            self.assertEqual(llm_client.load_brain_config()["mode"], "local")
            self.assertEqual(len(reads), 2)

    def test_every_call_site_names_a_known_agent(self):
        import ast
        repo = Path(__file__).parent
        found = 0
        for path in sorted({*repo.glob("*.py"), *repo.glob("scripts/*.py"), *repo.glob("bench/*.py")}):
            if path.name.startswith("test_") or path.name == "llm_client.py":
                continue
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", None)) == "query_llm":
                    found += 1
                    agent = next((k.value for k in node.keywords if k.arg == "agent"), None)
                    self.assertIsInstance(agent, ast.Constant, f"{path.name}:{node.lineno} needs agent=\"...\"")
                    self.assertIn(agent.value, llm_client.AGENTS, f"{path.name}:{node.lineno}")
        self.assertGreaterEqual(found, 23)


class FakeGenai:
    """Stands in for google.genai.Client: records calls, answers from a script."""

    def __init__(self, answers):
        self.answers = list(answers)  # each is a return value or an exception to raise
        self.calls = []
        self.models = self

    def __call__(self, api_key):
        self.calls.append(("client", api_key))
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.calls.append(("closed", None))

    def _next(self, kind, **kwargs):
        self.calls.append((kind, kwargs))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def generate_content(self, **kwargs):
        return self._next("generate", **kwargs)

    def embed_content(self, **kwargs):
        return self._next("embed", **kwargs)


def _text(t):
    return mock.Mock(text=t)


def _vectors(*vs):
    return mock.Mock(embeddings=[mock.Mock(values=list(v)) for v in vs])


class TestGeminiSdk(unittest.TestCase):
    """3E-2: Gemini calls go through google-genai; the legacy SDK is gone."""

    def setUp(self):
        self.enterContext(mock.patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}))
        self.enterContext(mock.patch("time.sleep"))
        self.enterContext(mock.patch("builtins.print"))

    def use(self, *answers):
        fake = FakeGenai(answers)
        self.enterContext(mock.patch("google.genai.Client", fake))
        return fake

    def models_tried(self, fake):
        return [kw["model"] for kind, kw in fake.calls if kind == "generate"]

    def test_call_gemini_sends_model_config_and_prompt(self):
        fake = self.use(_text("  answer  "))
        out = llm_client.call_gemini("question", system_instruction="be brief",
                                     model_name="gemini-3.6-flash", temperature=0.2)
        self.assertEqual(out, "answer")
        self.assertEqual(fake.calls[0], ("client", "test-key"))
        sent = fake.calls[1][1]
        self.assertEqual(sent["model"], "gemini-3.6-flash")
        self.assertEqual(sent["contents"], "be brief\n\nquestion")
        self.assertEqual((sent["config"].temperature, sent["config"].top_p, sent["config"].max_output_tokens),
                         (0.2, 0.95, 4096))
        self.assertEqual(fake.calls[-1], ("closed", None))  # closed explicitly, not left to garbage collection

    def test_every_gemini_client_is_closed(self):
        rag = _load_rag()
        for name, call in (("call_gemini failing", lambda: llm_client.call_gemini("q", model_name="gemini-3.6-flash")),
                           ("query embedding", lambda: rag.get_query_embedding("q")),
                           ("batch embedding", lambda: rag.get_batch_embeddings(["a"]))):
            with self.subTest(name), mock.patch("google.genai.Client",
                                                FakeGenai([Exception("boom")] * 10)) as fake:
                try:
                    call()
                except RuntimeError:
                    pass  # call_gemini raises once every model failed
                opened = sum(kind == "client" for kind, _ in fake.calls)
                closed = sum(kind == "closed" for kind, _ in fake.calls)
                self.assertEqual((opened, closed), (opened, opened))
                self.assertGreater(opened, 0)

    def test_quota_error_moves_to_the_next_model(self):
        quota = Exception("429 RESOURCE_EXHAUSTED. Please retry in 2.0s.")
        fake = self.use(quota, quota, _text("ok"))
        self.assertEqual(llm_client.call_gemini("q", model_name="gemini-3.6-flash"), "ok")
        first, backup = llm_client.GEMINI_FALLBACK_MODELS[:2]
        self.assertEqual(self.models_tried(fake), [first, first, backup])  # one short wait, then the backup

    def test_empty_answer_is_a_failure_not_an_empty_string(self):
        # google-genai returns text=None for a blocked or empty answer; that must not
        # pass as a reply (the legacy SDK raised on .text instead).
        self.use(*[_text(None)] * len(llm_client.GEMINI_FALLBACK_MODELS))
        with self.assertRaises(RuntimeError):
            llm_client.call_gemini("q", model_name="gemini-3.6-flash")

    def test_rag_query_embedding(self):
        rag = _load_rag()
        fake = self.use(_vectors([0.1, 0.2]))
        self.assertEqual(rag.get_query_embedding("find this"), [0.1, 0.2])
        sent = fake.calls[1][1]
        self.assertEqual((sent["model"], sent["contents"], sent["config"].task_type),
                         ("gemini-embedding-001", "find this", "RETRIEVAL_QUERY"))

    def test_rag_batch_embeddings_keep_one_vector_per_text(self):
        rag = _load_rag()
        fake = self.use(_vectors([1.0], [2.0]), Exception("boom"))
        out = rag.get_batch_embeddings(["a", "b", "c"], batch_size=2)
        self.assertEqual(out, [[1.0], [2.0], []])  # a failed batch leaves empty slots, never shifts
        sent = [kw for kind, kw in fake.calls if kind == "embed"]
        self.assertEqual([s["contents"] for s in sent], [["a", "b"], ["c"]])
        self.assertEqual({s["config"].task_type for s in sent}, {"RETRIEVAL_DOCUMENT"})

    def test_no_legacy_sdk_left(self):
        repo = Path(__file__).parent
        for path in sorted({*repo.glob("*.py"), *repo.glob("scripts/*.py"), *repo.glob("bench/*.py")}):
            if path.name != Path(__file__).name:
                self.assertFalse("google.generativeai" in path.read_text(encoding="utf-8"), path.name)
        reqs = (repo / "requirements.txt").read_text(encoding="utf-8")
        self.assertNotIn("google-generativeai", reqs)
        self.assertIn("google-genai", reqs)


def _load_rag():
    spec = importlib.util.spec_from_file_location("rag_engine_under_test", Path(__file__).with_name("rag_engine.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


if __name__ == "__main__":
    unittest.main()
