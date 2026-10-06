import os
import copy
import json
import threading
import time
import urllib.request
import urllib.error
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent
MEMORY_DIR = BASE_DIR / "memory"
MEMORY_DIR.mkdir(exist_ok=True)
CONFIG_FILE = MEMORY_DIR / "brain_mode.json"

DEFAULT_CONFIG = {
    "mode": "cloud",  # Blueprint C default: 100% High-Craft Cloud Intelligence
    "local_model": "qwen2.5-coder:7b",
    "cloud_model": "qwen/qwen3.8-27b",
    "agent_cloud_models": {
        "sakura": "qwen/qwen3.8-27b",
        "chaewon": "openai/gpt-oss-120b",
        "yunjin": "gemini-3.6-flash",
        "kazuha": "qwen/qwen3.8-27b",
        "eunchae": "openai/gpt-oss-20b"
    }
}

_config_cache = (None, None)  # (brain_mode.json mtime_ns, merged config)


def load_brain_config() -> dict:
    """
    brain_mode.json merged over DEFAULT_CONFIG. Parsed once and re-read only when
    the file's mtime changes (!mode writes it). Returns a copy callers may modify.
    """
    global _config_cache
    try:
        mtime = CONFIG_FILE.stat().st_mtime_ns
    except OSError:
        return copy.deepcopy(DEFAULT_CONFIG)
    if _config_cache[0] != mtime:
        merged = copy.deepcopy(DEFAULT_CONFIG)
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            merged.update(data)
            merged_agents = DEFAULT_CONFIG["agent_cloud_models"].copy()
            if isinstance(data.get("agent_cloud_models"), dict):
                merged_agents.update(data["agent_cloud_models"])
            merged["agent_cloud_models"] = merged_agents
        except (OSError, ValueError, TypeError, AttributeError) as e:
            print(f"⚠️ [llm_client] {CONFIG_FILE.name} unreadable, using defaults: {e}", flush=True)
        _config_cache = (mtime, merged)
    return copy.deepcopy(_config_cache[1])

def save_brain_config(cfg: dict):
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")

def get_brain_mode() -> str:
    return load_brain_config().get("mode", "cloud").lower()

def set_brain_mode(mode: str) -> str:
    mode_clean = mode.lower().strip()
    if mode_clean not in ["auto", "local", "cloud"]:
        raise ValueError("Mode must be 'auto', 'local', or 'cloud'.")
    cfg = load_brain_config()
    cfg["mode"] = mode_clean
    save_brain_config(cfg)
    return mode_clean

def ollama_base_url() -> str:
    """
    Ollama's base URL without the /v1 suffix. A "localhost" host is pinned to
    127.0.0.1: Ollama listens on IPv4 only, and on Windows "localhost" resolves to
    ::1 first, so every new connection waited ~2 s for the IPv6 attempt to fail
    (2,034 ms vs 1 ms per request, measured 2026-10-01).
    """
    url = os.getenv("LOCAL_LLM_URL", "http://127.0.0.1:11434").strip().rstrip("/")
    if url.endswith("/v1"):
        url = url[:-3]
    parts = urllib.parse.urlsplit(url)
    if parts.hostname == "localhost":
        netloc = "127.0.0.1" + (f":{parts.port}" if parts.port else "")
        url = urllib.parse.urlunsplit(parts._replace(netloc=netloc))
    return url

def check_ollama_status() -> tuple[bool, list[str]]:
    """Checks if local Ollama daemon is reachable on 127.0.0.1:11434."""
    req = urllib.request.Request(f"{ollama_base_url()}/api/tags", headers={"User-Agent": "JobCopilot/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=1.2) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            models = [m.get("name", "") for m in data.get("models", [])]
            return True, models
    except Exception:  # quiet: Ollama offline is a normal state, returned to the caller
        return False, []

def get_brain_status(agent: str = None) -> dict:
    """Comprehensive diagnostic of the AI brain state with Blueprint C agent breakdown.
    With agent, the cloud model is that agent's own (what query_llm routes it to)."""
    mode = get_brain_mode()
    ollama_up, local_models = check_ollama_status()
    groq_configured = bool(os.getenv("GROQ_API_KEY", "").strip())
    gemini_configured = bool(os.getenv("GEMINI_API_KEY", "").strip())
    cfg = load_brain_config()
    agent_map = cfg.get("agent_cloud_models", DEFAULT_CONFIG["agent_cloud_models"])
    cloud_model = agent_map.get(agent, cfg.get("cloud_model", "qwen/qwen3.8-27b"))

    if mode == "local":
        active_provider = "Local Ollama (RX 6600 XT)" if ollama_up else "Local (Ollama Offline ⚠️)"
        active_model = cfg.get("local_model", "qwen2.5-coder:7b")
    elif mode == "cloud":
        active_provider = "Multi-Tier Cloud Cluster (Groq LPUs + Gemini)"
        active_model = cloud_model
    else:  # auto
        if ollama_up and local_models:
            active_provider = "Local Ollama (Auto-detected)"
            active_model = cfg.get("local_model", "qwen2.5-coder:7b")
        else:
            active_provider = "Multi-Tier Cloud Cluster (Auto-fallback)"
            active_model = cloud_model

    return {
        "mode": mode,
        "architecture": "Specialized Multi-Model Cloud",
        "blueprint": "Specialized Multi-Model Cloud",
        "active_provider": active_provider,
        "active_model": active_model,
        "local_model": cfg.get("local_model", "qwen2.5-coder:7b"),
        "agent_cloud_models": agent_map,
        "ollama_online": ollama_up,
        "local_models_available": local_models,
        "groq_ready": groq_configured,
        "gemini_ready": gemini_configured,
        "breakers": {name: b.state() for name, b in BREAKERS.items()},
        "hardware_target": "PowerColor RX 6600 XT (8GB VRAM) / i5-12400F"
    }


class CircuitBreaker:
    """
    One per cloud provider, shared by every agent in the process (the bots run in
    one process; they call in from asyncio.to_thread workers, hence the lock).
    After THRESHOLD consecutive failed calls the provider is open for COOLDOWN_S:
    query_llm asks it last instead of first. After the cooldown the next call is
    a trial: success closes the breaker, failure opens it again at once.
    """
    THRESHOLD = 3
    COOLDOWN_S = 60.0

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._failures = 0
        self._opened_at = None

    def state(self) -> str:
        with self._lock:
            if self._opened_at is None:
                return "closed"
            # ponytail: every call after the cooldown is a trial (no single-probe gate);
            # add one if concurrent trials ever matter at bot traffic.
            return "open" if self._clock() - self._opened_at < self.COOLDOWN_S else "half-open"

    def is_open(self) -> bool:
        return self.state() == "open"

    def record(self, ok: bool):
        with self._lock:
            if ok:
                self._failures, self._opened_at = 0, None
            else:
                self._failures += 1
                if self._failures >= self.THRESHOLD:
                    self._opened_at = self._clock()


BREAKERS = {"groq": CircuitBreaker(), "gemini": CircuitBreaker()}

GROQ_FALLBACK_MODELS = [
    "qwen/qwen3.8-27b",
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b"
]

GEMINI_FALLBACK_MODELS = [
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3-flash-preview",
    "gemini-3.7-flash"
]

def _read_groq_stream(resp, on_delta) -> str:
    """Reads an OpenAI-style SSE stream, passing each content piece to on_delta; returns the whole text."""
    parts = []
    for raw in resp:
        line = raw.decode("utf-8").strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        for choice in json.loads(data).get("choices") or []:
            piece = (choice.get("delta") or {}).get("content")
            if piece:
                parts.append(piece)
                on_delta(piece)
    return "".join(parts).strip()


def _note_attempt(meta, provider, model):
    """Adds provider/model to meta["chain"] the first time it is tried (retries on it don't repeat it)."""
    if meta is not None:
        step = f"{provider}/{model}"
        chain = meta.setdefault("chain", [])
        if step not in chain:
            chain.append(step)


def _note_answer(meta, provider, model, ttft_ms=None):
    if meta is not None:
        meta.update(provider=provider, model=model, ttft_ms=ttft_ms)


def call_groq(prompt: str, system_instruction: str = "", model: str = None, temperature: float = 0.4,
              on_delta=None, meta: dict = None) -> str:
    """
    Groq chat completion with model fallback and 429 handling. With on_delta, the
    reply is streamed and on_delta(piece) runs for each text piece as it arrives
    (in this thread). Once a piece has gone out, a failure raises instead of
    retrying, since a retry would replay the reply from the start.
    meta (optional dict) collects telemetry for query_llm_structured: "chain" (every
    model tried), and on success "provider", "model" and "ttft_ms" (first streamed
    piece, measured from this call's start; None without on_delta).
    """
    started = time.perf_counter()
    first_piece_ms = []
    groq_key = (os.getenv("GROQ_API_KEY") or "").strip()
    if not groq_key:
        raise ValueError("GROQ_API_KEY not configured")

    if not model:
        cfg = load_brain_config()
        model = cfg.get("cloud_model", "qwen/qwen3.8-27b")

    candidate_models = [model]
    for m in GROQ_FALLBACK_MODELS:
        if m not in candidate_models:
            candidate_models.append(m)

    messages = []
    if system_instruction:
        messages.append({"role": "system", "content": system_instruction})
    messages.append({"role": "user", "content": prompt})

    delivered = []

    def forward(piece):
        if not first_piece_ms:
            first_piece_ms.append(round((time.perf_counter() - started) * 1000, 1))
        delivered.append(piece)
        on_delta(piece)

    last_error = None
    for cand_model in candidate_models:
        for attempt in range(2):
            _note_attempt(meta, "groq", cand_model)
            body = {"model": cand_model, "messages": messages, "temperature": temperature}
            if on_delta:
                body["stream"] = True
            req = urllib.request.Request(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {groq_key}",
                    "Content-Type": "application/json",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
                },
                data=json.dumps(body).encode("utf-8")
            )
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    if on_delta:
                        text = _read_groq_stream(resp, forward)
                    else:
                        data = json.loads(resp.read().decode("utf-8"))
                        text = data["choices"][0]["message"]["content"].strip()
                _note_answer(meta, "groq", cand_model, first_piece_ms[0] if first_piece_ms else None)
                return text
            except urllib.error.HTTPError as e:
                last_error = e
                if e.code == 429:
                    retry_after = e.headers.get("Retry-After")
                    # Groq sends fractional seconds ("1.5"); isdigit() rejected those.
                    try:
                        wait_time = float(retry_after)
                    except (TypeError, ValueError):
                        wait_time = 2.0 + attempt * 2.0
                    if wait_time <= 4.0 and attempt == 0:
                        print(f"[Groq 429] {cand_model} transient rate limit, waiting {wait_time:.1f}s...")
                        time.sleep(wait_time + 0.2)
                        continue
                    else:
                        print(f"[Groq 429] {cand_model} rate limited, switching to next Groq model...")
                        break
                else:
                    print(f"[Groq Error] {cand_model}: {e}")
                    break
            except Exception as e:
                if delivered:
                    raise RuntimeError(f"Groq stream from {cand_model} broke after partial output: {e}") from e
                last_error = e
                print(f"[Groq Exception] {cand_model}: {e}")
                break

    raise RuntimeError(f"All Groq models failed: {last_error}")

def call_local_ollama(prompt: str, system_instruction: str = "", model: str = None, meta: dict = None) -> str:
    if not model:
        cfg = load_brain_config()
        model = cfg.get("local_model", "qwen2.5-coder:7b")
    _note_attempt(meta, "ollama", model)

    messages = []
    if system_instruction:
        messages.append({"role": "system", "content": system_instruction})
    messages.append({"role": "user", "content": prompt})

    req = urllib.request.Request(
        f"{ollama_base_url()}/v1/chat/completions",
        headers={"Content-Type": "application/json"},
        data=json.dumps({"model": model, "messages": messages}).encode("utf-8")
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        text = data["choices"][0]["message"]["content"].strip()
    _note_answer(meta, "ollama", model)
    return text

def call_gemini(prompt: str, system_instruction: str = "", model_name: str = None, temperature: float = 0.4,
                meta: dict = None) -> str:
    from google import genai
    from google.genai import types
    gemini_key = (os.getenv("GEMINI_API_KEY") or "").strip()
    if not gemini_key:
        raise ValueError("GEMINI_API_KEY not configured")

    generation_config = types.GenerateContentConfig(
        temperature=temperature, top_p=0.95, max_output_tokens=4096,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True))  # no tools; skips a per-call warning

    candidate_models = []
    if model_name:
        candidate_models.append(model_name)
    for m in GEMINI_FALLBACK_MODELS:
        if m not in candidate_models:
            candidate_models.append(m)

    full_prompt = f"{system_instruction}\n\n{prompt}" if system_instruction else prompt
    last_error = None

    with genai.Client(api_key=gemini_key) as client:  # closes its HTTP session on every exit
        for cand_model in candidate_models:
            for attempt in range(2):
                _note_attempt(meta, "gemini", cand_model)
                try:
                    res = client.models.generate_content(model=cand_model, contents=full_prompt, config=generation_config)
                    if not (res.text or "").strip():
                        raise ValueError("empty response (blocked or no text parts)")
                    _note_answer(meta, "gemini", cand_model)
                    return res.text.strip()
                except Exception as e:
                    last_error = e
                    err_str = str(e)
                    if "429" in err_str or "quota" in err_str.lower() or "ResourceExhausted" in err_str:
                        import re
                        m_delay = re.search(r"Please retry in ([\d\.]+)s", err_str)
                        wait_time = float(m_delay.group(1)) if m_delay else 3.0
                        if wait_time <= 4.0 and attempt == 0:
                            print(f"[Gemini 429] {cand_model} transient rate limit, waiting {wait_time:.1f}s...")
                            time.sleep(wait_time + 0.5)
                            continue
                        else:
                            print(f"[Gemini 429] {cand_model} quota reached, switching to backup model...")
                            break
                    else:
                        print(f"[Gemini Error] {cand_model}: {err_str[:80]}")
                        break

    raise RuntimeError(f"All Gemini models failed: {last_error}")

AGENTS = tuple(DEFAULT_CONFIG["agent_cloud_models"])


@dataclass(frozen=True)
class LLMResult:
    """One query_llm_structured answer with the telemetry behind it."""
    text: str
    model: str                       # the model that answered
    provider: str                    # "groq" | "gemini" | "ollama"
    ttft_ms: float | None            # first streamed piece; None unless on_delta was given (Groq only)
    total_ms: float                  # whole call, including failed attempts and fallbacks
    fallback_chain: tuple[str, ...]  # "provider/model" in the order tried; the last one answered


def query_llm(prompt: str, system_instruction: str = "", temperature: float = 0.4, *, agent: str) -> str:
    """query_llm_structured(...).text: the dispatcher every bot calls."""
    return query_llm_structured(prompt, system_instruction, temperature, agent=agent).text


def query_llm_structured(prompt: str, system_instruction: str = "", temperature: float = 0.4, *,
                         agent: str, on_delta=None) -> LLMResult:
    """
    Multi-provider LLM dispatcher with per-agent model specialization. `agent` is
    required and must be one of AGENTS; it picks the cloud model from
    brain_mode.json's agent_cloud_models (defaults below). on_delta streams Groq
    replies (and gives ttft_ms); Gemini and Ollama answer in one piece.
    - 'cloud':
        * Sakura: qwen/qwen3.8-27b (Groq)
        * Chaewon: openai/gpt-oss-120b (Groq)
        * Yunjin: gemini-3.6-flash (Google), Groq fallback
        * Kazuha: qwen/qwen3.8-27b (Groq)
        * Eunchae: openai/gpt-oss-20b (Groq)
    - 'local': local Ollama (local_model) for every agent.
    - 'auto': local Ollama if it is running, otherwise the agent's cloud model.
    A cloud provider whose CircuitBreaker is open (3 failed calls in a row) is
    asked last until its cooldown ends. Once a streamed reply has started, a
    failure raises instead of falling back to the other provider.
    """
    agent_key = agent.lower().strip() if isinstance(agent, str) else ""
    if agent_key not in AGENTS:
        raise ValueError(f"query_llm: agent must be one of {', '.join(AGENTS)}; got {agent!r}")

    cfg = load_brain_config()
    mode = cfg.get("mode", "cloud").lower()
    errors = []
    meta = {"chain": []}  # filled by the call_* functions (see call_groq)
    started = time.perf_counter()

    agent_cloud_map = cfg.get("agent_cloud_models", DEFAULT_CONFIG["agent_cloud_models"])
    target_cloud_model = agent_cloud_map.get(agent_key, cfg.get("cloud_model", "qwen/qwen3.8-27b"))
    target_local_model = cfg.get("local_model", "qwen2.5-coder:7b")

    streamed = []      # set once any piece reached the caller's on_delta
    caller_error = []  # an exception from on_delta itself: the caller's, not the provider's

    def _forward(piece):
        streamed.append(True)
        try:
            on_delta(piece)
        except Exception as e:
            caller_error.append(e)
            raise

    def _groq(model):
        return call_groq(prompt, system_instruction=system_instruction, model=model, temperature=temperature,
                         on_delta=_forward if on_delta else None, meta=meta), "groq", model

    def _gemini(model):
        return call_gemini(prompt, system_instruction=system_instruction, model_name=model,
                           temperature=temperature, meta=meta), "gemini", model

    def _ollama():
        return call_local_ollama(prompt, system_instruction=system_instruction, model=target_local_model,
                                 meta=meta), "ollama", target_local_model

    def _dispatch_cloud():
        # (provider, model, error label): the agent's own provider first, the other as fallback.
        if target_cloud_model.startswith("gemini"):  # Yunjin
            steps = [("gemini", target_cloud_model, f"Gemini ({target_cloud_model})"),
                     ("groq", "qwen/qwen3.8-27b", "Groq Cloud fallback")]
        else:  # Sakura, Chaewon, Kazuha, Eunchae
            steps = [("groq", target_cloud_model, f"Groq Cloud ({target_cloud_model})"),
                     ("gemini", "gemini-3.6-flash", "Gemini fallback")]
        if not os.getenv("GEMINI_API_KEY", "").strip():
            steps = [s for s in steps if s[0] != "gemini"]
        # A provider whose breaker is open goes last, never away (sort is stable).
        steps.sort(key=lambda s: BREAKERS[s[0]].is_open())

        for provider, model, label in steps:
            try:
                answer = _groq(model) if provider == "groq" else _gemini(model)
            except Exception as e:
                if caller_error:  # not this provider's fault: no breaker count, no fallback (Codex 5B B3)
                    raise caller_error[0]
                BREAKERS[provider].record(False)
                errors.append(f"{label}: {e}")
                if streamed:  # the caller already has part of this reply; another provider would restart it
                    raise RuntimeError(f"Cloud reply broke after partial output, not falling back: "
                                       f"{'; '.join(errors)}") from e
                continue
            BREAKERS[provider].record(True)
            return answer

        raise RuntimeError(f"Cloud providers failed: {'; '.join(errors)}")

    def _dispatch():
        # 1. LOCAL MODE
        if mode == "local":
            ollama_up, models = check_ollama_status()
            if not ollama_up:
                raise RuntimeError("⚠️ Brain mode is set to 'local', but Ollama is not running on localhost:11434! Please launch Ollama or type '!mode cloud' in Discord.")
            return _ollama()

        # 2. CLOUD MODE
        if mode == "cloud":
            return _dispatch_cloud()

        # 3. AUTO MODE (Smart detection)
        ollama_up, local_models = check_ollama_status()
        if ollama_up and local_models:
            try:
                return _ollama()
            except Exception as e:
                errors.append(f"Local Ollama: {e}")
        return _dispatch_cloud()

    text, provider, requested = _dispatch()
    # A provider stubbed without meta support reports nothing; fall back to what was asked for.
    return LLMResult(text=text, model=meta.get("model", requested), provider=meta.get("provider", provider),
                     ttft_ms=meta.get("ttft_ms"), total_ms=round((time.perf_counter() - started) * 1000, 1),
                     fallback_chain=tuple(meta["chain"]))
