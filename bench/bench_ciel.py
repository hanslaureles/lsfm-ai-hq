"""
Ciel pipeline benchmark (Phase 2, Task 2).

Times each stage of a Ciel voice mission with time.perf_counter over N runs and
reports p50 / p95 / max. Stdlib plus the existing HQ modules.

Stages marked network=True include this machine's internet path to the provider
(Groq, Microsoft Edge TTS, DuckDuckGo). They are round-trip times from here, not
compute times, and they move with the connection.

Usage (from the repo root):
    python bench/bench_ciel.py                       # every stage, 5 runs
    python bench/bench_ciel.py --runs 10 --local-only
    python bench/bench_ciel.py --stages ffmpeg_inbound,pdf_build

Writes bench/results/YYYY-MM-DD.json and a Markdown summary next to it.
Side effects: none outside the system temp dir. The PDF is rendered into a temp
dir (the portfolio PDF, the archive and Obsidian are never touched), and the
mission mp3s are deleted after each run.
"""

import argparse
import asyncio
import contextlib
import io
import json
import math
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime
from pathlib import Path

BENCH_DIR = Path(__file__).resolve().parent
REPO_DIR = BENCH_DIR.parent
SAMPLES_DIR = BENCH_DIR / "samples"
RESULTS_DIR = BENCH_DIR / "results"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(REPO_DIR))
os.chdir(REPO_DIR)  # the engines resolve .env and their data dirs from here

# Fixed inputs, so runs on different days measure the same work.
SPOKEN_PROMPT = "Ciel, check our hardware vitals and git status."
MISSION_PROMPT = "Explain the difference between TCP and UDP in two sentences."
LLM_PROMPT = "Explain the difference between TCP and UDP in about 120 words."
LLM_MAX_TOKENS = 256
SEARCH_QUERY = "Raft consensus algorithm"
JA_CLIP_TEXT = "「告。」解析を完了しました。"
EN_CLIP_TEXT = "Notice: Analysis complete. All requested checks have finished."

GROQ_URL = "https://api.groq.com/openai/v1"


# ---------------------------------------------------------------- statistics

def percentile(samples, pct):
    """Nearest-rank percentile. With fewer than 20 samples, p95 is the max."""
    if not samples:
        return None
    ordered = sorted(samples)
    rank = max(1, math.ceil(pct / 100 * len(ordered)))
    return ordered[rank - 1]


def summarize(samples):
    if not samples:
        return None
    return {
        "p50": round(percentile(samples, 50), 1),
        "p95": round(percentile(samples, 95), 1),
        "max": round(max(samples), 1),
        "min": round(min(samples), 1),
        "mean": round(statistics.fmean(samples), 1),
    }


# ---------------------------------------------------------------- environment

def _run(cmd, timeout=15):
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return out.stdout.strip()
    except Exception as e:
        return f"unavailable ({type(e).__name__})"


def _cim(cls, prop):
    if platform.system() != "Windows":
        return "n/a"
    script = f"(Get-CimInstance {cls} | Select-Object -ExpandProperty {prop}) -join '; '"
    return _run(["powershell", "-NoProfile", "-Command", script])


def _http_json(url, headers=None, timeout=5):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def ollama_base():
    # The same URL production uses (loads .env, pins localhost to 127.0.0.1).
    from llm_client import ollama_base_url
    return ollama_base_url()


def collect_environment():
    import psutil
    env = {
        "hardware": {
            "cpu": _cim("Win32_Processor", "Name"),
            "cpu_logical_cores": psutil.cpu_count(logical=True),
            "ram_gb": round(psutil.virtual_memory().total / 1024 ** 3, 1),
            "gpu": _cim("Win32_VideoController", "Name"),
        },
        "software": {
            # platform.platform() says "Windows-10" on Windows 11 builds.
            "os": _cim("Win32_OperatingSystem", "Caption") + f" (build {platform.version()})"
            if platform.system() == "Windows" else platform.platform(),
            "python": platform.python_version(),
            "ffmpeg": " ".join(_run(["ffmpeg", "-version"]).split()[:3]) or "missing",
        },
        "git": {
            "sha": _run(["git", "rev-parse", "HEAD"]),
            "dirty": bool(_run(["git", "status", "--porcelain", "--untracked-files=no"])),
        },
    }
    try:
        import edge_tts
        env["software"]["edge_tts"] = edge_tts.__version__
    except Exception:  # quiet: optional package; its version is just omitted
        pass
    try:
        import ddgs
        env["software"]["ddgs"] = getattr(ddgs, "__version__", "unknown")
    except Exception:  # quiet: optional package; its version is just omitted
        pass
    try:
        env["software"]["ollama"] = _http_json(f"{ollama_base()}/api/version")["version"]
    except Exception as e:
        env["software"]["ollama"] = f"unreachable ({type(e).__name__})"
    return env


# ---------------------------------------------------------------- samples

async def ensure_samples():
    """
    Speech sample in the HUD's upload format (WebM/Opus, 48 kHz) plus one JA and
    one EN clip for the outbound filter. Generated once with Edge TTS, then
    committed, so later runs measure identical input.
    """
    import edge_tts
    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    webm = SAMPLES_DIR / "spoken_prompt.webm"
    ja = SAMPLES_DIR / "clip_ja.mp3"
    en = SAMPLES_DIR / "clip_en.mp3"
    if not ja.exists():
        await edge_tts.Communicate(JA_CLIP_TEXT, voice="ja-JP-NanamiNeural").save(str(ja))
    if not en.exists():
        await edge_tts.Communicate(EN_CLIP_TEXT, voice="en-US-AvaNeural").save(str(en))
    if not webm.exists():
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "prompt.mp3"
            await edge_tts.Communicate(SPOKEN_PROMPT, voice="en-US-AvaNeural").save(str(raw))
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw), "-ar", "48000", "-ac", "1",
                            "-c:a", "libopus", "-b:a", "32k", str(webm)], check=True)
    return {"spoken_prompt": webm, "clip_ja": ja, "clip_en": en}


# ---------------------------------------------------------------- LLM streaming

def stream_chat(url, model, headers, prompt):
    """
    One streamed OpenAI-compatible chat completion. Returns the time to the
    first content token and the token count reported by the server (falls back
    to counting content chunks, which is flagged).
    """
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": LLM_MAX_TOKENS,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    req = urllib.request.Request(f"{url}/chat/completions", data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json", **headers})
    started = time.perf_counter()
    first = None
    chunks = 0
    usage = None
    with urllib.request.urlopen(req, timeout=120) as resp:
        for raw in resp:
            line = raw.decode("utf-8").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            event = json.loads(data)
            for choice in event.get("choices") or []:
                if (choice.get("delta") or {}).get("content"):
                    chunks += 1
                    if first is None:
                        first = time.perf_counter()
            usage = event.get("usage") or (event.get("x_groq") or {}).get("usage") or usage
    ended = time.perf_counter()
    if first is None:
        raise RuntimeError("stream returned no content")
    tokens = (usage or {}).get("completion_tokens")
    gen_s = ended - first
    return {
        "ttft_ms": round((first - started) * 1000, 1),
        "completion_tokens": tokens if tokens is not None else chunks,
        "tokens_counted_from_chunks": tokens is None,
        "decode_tokens_per_s": round(((tokens or chunks) - 1) / gen_s, 1) if gen_s > 0 else None,
    }


def ollama_placement(model):
    """How much of the loaded model sits in VRAM, from Ollama's /api/ps."""
    try:
        for m in _http_json(f"{ollama_base()}/api/ps").get("models", []):
            if m.get("name", "").startswith(model) or m.get("model", "").startswith(model):
                size, vram = m.get("size", 0), m.get("size_vram", 0)
                return {"size_gb": round(size / 1e9, 2), "vram_gb": round(vram / 1e9, 2),
                        "gpu_share": round(vram / size, 3) if size else None}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
    return {"error": "model not loaded"}


# ---------------------------------------------------------------- stages

class Bench:
    def __init__(self, samples):
        self.samples = samples
        self.normalized_wav = None
        self.ollama_cold = None
        self._ciel = None

    def ciel(self):
        if self._ciel is None:
            from ciel_orchestrator import CielOrchestrator
            self._ciel = CielOrchestrator()
        return self._ciel

    # Each stage returns a dict of extras (or None) and raises on failure.
    # prepare() and prepare_<stage>() run untimed before a stage's first run, so
    # imports, model loads and input preparation never land in run 1.

    def prepare(self):
        import ciel_orchestrator, eunchae_engine, llm_client, pdf_engine, voice_engine  # noqa: F401

    def prepare_whisper_groq(self):
        self.ffmpeg_inbound()

    def prepare_tool_web_search(self):
        self.ciel()

    prepare_e2e_mission = prepare_tool_web_search

    def prepare_llm_stream_ollama(self):
        """Loads the model (timed as the cold start) and records Ollama's own breakdown."""
        from llm_client import load_brain_config
        model = load_brain_config().get("local_model", "qwen2.5-coder:7b")
        # Unload first: Ollama keeps a model resident for minutes, which would turn
        # the "cold" load into a warm one.
        unload = urllib.request.Request(f"{ollama_base()}/api/generate",
                                        data=json.dumps({"model": model, "keep_alive": 0}).encode("utf-8"),
                                        headers={"Content-Type": "application/json"})
        urllib.request.urlopen(unload, timeout=30).read()
        started = time.perf_counter()
        stream_chat(f"{ollama_base()}/v1", model, {}, "Say OK.")
        self.ollama_cold = {"cold_load_ms": round((time.perf_counter() - started) * 1000, 1),
                            "placement": ollama_placement(model)}
        try:
            # Native API, same prompt: server-side durations in nanoseconds.
            body = {"model": model, "messages": [{"role": "user", "content": LLM_PROMPT}], "stream": False,
                    "options": {"temperature": 0, "num_predict": LLM_MAX_TOKENS}}
            req = urllib.request.Request(f"{ollama_base()}/api/chat", data=json.dumps(body).encode("utf-8"),
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=120) as resp:
                r = json.loads(resp.read().decode("utf-8"))
            self.ollama_cold["server_breakdown_warm"] = {
                "load_ms": round(r.get("load_duration", 0) / 1e6, 1),
                "prompt_tokens": r.get("prompt_eval_count"),
                "prompt_eval_ms": round(r.get("prompt_eval_duration", 0) / 1e6, 1),
                "eval_tokens": r.get("eval_count"),
                "eval_tokens_per_s": round(r["eval_count"] / (r["eval_duration"] / 1e9), 1)
                if r.get("eval_duration") else None,
            }
        except Exception as e:
            self.ollama_cold["server_breakdown_warm"] = {"error": f"{type(e).__name__}: {e}"}

    def ffmpeg_inbound(self):
        from voice_engine import normalize_audio_for_transcription
        raw = self.samples["spoken_prompt"].read_bytes()
        out = normalize_audio_for_transcription(raw)
        if out is raw or out[:4] != b"RIFF":
            raise RuntimeError("FFmpeg did not produce a WAV (normalize fell back to the input)")
        self.normalized_wav = out
        return {"input_bytes": len(raw), "output_bytes": len(out)}

    def whisper_groq(self):
        from voice_engine import _transcribe_groq
        text = _transcribe_groq(self.normalized_wav, filename="normalized.wav")
        expected = set(SPOKEN_PROMPT.lower().replace(",", "").replace(".", "").split())
        got = set(text.lower().replace(",", "").replace(".", "").split())
        return {"transcript": text, "word_recall": round(len(expected & got) / len(expected), 2)}

    def tool_vitals(self):
        from eunchae_engine import get_system_vitals
        vitals = get_system_vitals()
        return {"cpu_pct": vitals.get("cpu_pct")}

    def tool_web_search(self):
        results = self.ciel().ciel_web_search(SEARCH_QUERY, max_results=5)
        if not results:
            raise RuntimeError("0 results (search failed or offline)")
        return {"results": len(results)}

    async def tts_clip_en(self):
        return await self._tts_clip(EN_CLIP_TEXT, "en-US-AvaNeural")

    async def tts_clip_ja(self):
        return await self._tts_clip(JA_CLIP_TEXT, "ja-JP-NanamiNeural")

    async def _tts_clip(self, text, voice):
        import edge_tts
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "clip.mp3"
            await edge_tts.Communicate(text, voice=voice).save(str(out))
            return {"bytes": out.stat().st_size}

    def ffmpeg_outbound(self):
        # The exact filter graph text_to_speech_bilingual applies.
        graph = ("[0:a]apad=pad_dur=0.35[a0];[a0][1:a]concat=n=2:v=0:a=1,"
                 "highpass=f=120,equalizer=f=3500:t=q:w=1.2:g=3.0,aecho=0.85:0.8:25|45:0.18|0.09,volume=0.30[out]")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out.mp3"
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(self.samples["clip_ja"]),
                            "-i", str(self.samples["clip_en"]), "-filter_complex", graph, "-map", "[out]",
                            "-c:a", "libmp3lame", "-b:a", "192k", str(out)], check=True)
            return {"bytes": out.stat().st_size}

    def pdf_build(self):
        # compile_master_resume would overwrite applications/...Master_Resume.pdf even
        # with every sync flag off, so time the same two steps into a temp dir.
        from pdf_engine import build_resume_html, render_html_to_pdf
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "resume.pdf"
            if not render_html_to_pdf(build_resume_html({}), out) or not out.exists():
                raise RuntimeError("headless browser failed to render the PDF")
            return {"size_kb": round(out.stat().st_size / 1024, 1)}

    def llm_stream_groq(self):
        key = (os.getenv("GROQ_API_KEY") or "").strip()
        if not key:
            raise RuntimeError("GROQ_API_KEY not configured")
        from llm_client import load_brain_config
        model = load_brain_config().get("cloud_model", "qwen/qwen3.8-27b")
        res = stream_chat(GROQ_URL, model, {"Authorization": f"Bearer {key}", "User-Agent": "CielBench/1.0"},
                          LLM_PROMPT)
        return {"model": model, **res}

    def llm_stream_ollama(self):
        from llm_client import load_brain_config
        model = load_brain_config().get("local_model", "qwen2.5-coder:7b")
        res = stream_chat(f"{ollama_base()}/v1", model, {}, LLM_PROMPT)
        return {"model": model, **res}

    async def e2e_mission(self):
        """
        One real execute_mission (Groq router + synthesis, Edge TTS, FFmpeg) with
        the LLM and TTS calls instrumented, so their share is reported too.
        """
        import ciel_orchestrator as co
        calls = []
        real_groq, real_tts, real_clip = co.call_groq, co.text_to_speech_bilingual, co.speak_clip

        def timed_groq(prompt, system_instruction="", **kwargs):
            kind = "router_llm" if "routing core" in system_instruction else "synthesis_llm"
            started = time.perf_counter()
            try:
                reply = real_groq(prompt, system_instruction=system_instruction, **kwargs)
            except Exception as e:
                # The router swallows its own errors and falls back to keywords;
                # record them so that run counts as failed, not as a fast router.
                calls.append((kind, None, f"{kind}: {type(e).__name__}: {e}"))
                raise
            calls.append((kind, (time.perf_counter() - started) * 1000, None))
            return reply

        async def timed_tts(**kwargs):
            started = time.perf_counter()
            path = await real_tts(**kwargs)
            calls.append(("tts_bilingual", (time.perf_counter() - started) * 1000, None))
            return path

        async def timed_clip(*args, **kwargs):
            # Streamed speech: one Edge TTS + filter call per sentence.
            started = time.perf_counter()
            path = await real_clip(*args, **kwargs)
            calls.append(("tts_clip", (time.perf_counter() - started) * 1000, None))
            return path

        # Time to first audio: mission start until the first playable audio URL is
        # broadcast (a streamed clip, or the finished mp3 on ciel_complete). Server
        # side; the HUD's fetch and decode of a local file come on top.
        first_audio = []

        async def on_event(ev):
            if not first_audio and (ev["type"] == "ciel_audio_chunk"
                                    or (ev["type"] == "ciel_complete" and ev.get("audio_url"))):
                first_audio.append((time.perf_counter() - mission_started) * 1000)

        co.call_groq, co.text_to_speech_bilingual, co.speak_clip = timed_groq, timed_tts, timed_clip
        log = io.StringIO()
        try:
            ciel = self.ciel()
            ciel.reset_memory()  # same router prompt every run
            # call_groq prints "[Groq 429] ..." / "[Groq Error] ..." when it sleeps on a
            # rate limit or switches model (worker threads share sys.stdout).
            with contextlib.redirect_stdout(log):
                mission_started = time.perf_counter()
                result = await ciel.execute_mission(MISSION_PROMPT, event_callback=on_event)
        finally:
            co.call_groq, co.text_to_speech_bilingual, co.speak_clip = real_groq, real_tts, real_clip
        if first_audio:
            calls.append(("time_to_first_audio", first_audio[0], None))
        for clip in (REPO_DIR / "audio_cache").glob(f"ciel_response_{result.get('mission_id')}_*.mp3"):
            clip.unlink(missing_ok=True)  # streamed clips, if any
        groq_events = [line.strip() for line in log.getvalue().splitlines() if "[Groq" in line]
        if groq_events:
            # A 3 s rate-limit sleep or a different model would distort the timing.
            raise RuntimeError(f"excluded, Groq retry/fallback during run: {groq_events[0][:120]}")
        if result.get("audio_file"):
            (REPO_DIR / "audio_cache" / result["audio_file"]).unlink(missing_ok=True)
        errors = [err for _, _, err in calls if err]
        if result.get("error") or errors:
            raise RuntimeError(result.get("error") or errors[0])
        if not any(kind == "router_llm" for kind, _, _ in calls):
            raise RuntimeError("router LLM was not called (keyword fast path?)")
        return {"intent": result.get("intent_type"), "_sub": [(k, ms) for k, ms, _ in calls]}


STAGES = [
    # name, network, notes
    ("ffmpeg_inbound", False, "HUD WebM/Opus upload -> 16 kHz mono WAV with the inbound filter chain."),
    ("whisper_groq", True, "Groq whisper-large-v3-turbo on the normalized sample, round trip from this machine."),
    ("tool_vitals", False, "get_system_vitals(); includes psutil's deliberate 0.5 s CPU sampling interval."),
    ("tool_web_search", True, "ciel_web_search via DDGS, 5 results."),
    ("tts_clip_en", True, "One Edge TTS clip (en-US-AvaNeural), Microsoft's service, round trip."),
    ("tts_clip_ja", True, "One Edge TTS clip (ja-JP-NanamiNeural), Microsoft's service, round trip."),
    ("ffmpeg_outbound", False, "JA + 350 ms pad + EN concat with the Thought Acceleration filter, mp3 encode."),
    ("pdf_build", False, "build_resume_html + headless Edge render to a temp dir."),
    ("llm_stream_groq", True, f"Streamed completion, temperature 0, max_tokens {LLM_MAX_TOKENS}."),
    ("llm_stream_ollama", False, "Same prompt on local Ollama. The cold model load is reported separately; the "
                                 "prompt repeats every run, so first-token time reflects Ollama's prompt cache "
                                 "(uncached prompt eval is in the server breakdown)."),
    ("e2e_mission", True, "Full execute_mission for a general-knowledge prompt: router LLM, synthesis LLM, "
                          "bilingual TTS. Sub-stages are timed inside it, including time_to_first_audio "
                          "(mission start until the first playable audio URL is broadcast)."),
]
LOCAL_STAGES = [name for name, network, _ in STAGES if not network]

# Untimed wait between runs. Groq allows 8,000 tokens/minute on qwen/qwen3.8-27b
# (x-ratelimit-limit-tokens, 2026-10-01) and a mission uses ~3.5-4k across router
# and synthesis, so unpaced runs start hitting 429 sleeps and model switches.
PACE_S = {"llm_stream_groq": 5, "e2e_mission": 45}


async def run(stage_names, runs):
    samples = await ensure_samples()
    bench = Bench(samples)
    bench.prepare()
    results = {}
    sub_samples = {}
    for name, network, notes in STAGES:
        if name not in stage_names:
            continue
        fn = getattr(bench, name)
        times, extras, errors = [], [], []
        print(f"  {name:<18}", end="", flush=True)
        runs_this = runs
        try:
            getattr(bench, f"prepare_{name}", lambda: None)()
        except Exception as e:
            # Without its input or model loaded, every run would fail the same way.
            errors = [f"prepare: {type(e).__name__}: {e}"[:300]] * runs
            runs_this = 0
        for _ in range(runs_this):
            if PACE_S.get(name):
                # Before the first run too: the previous stage may share the token budget.
                await asyncio.sleep(PACE_S[name])
            started = time.perf_counter()
            try:
                extra = fn()
                if asyncio.iscoroutine(extra):
                    extra = await extra
                times.append((time.perf_counter() - started) * 1000)
                extra = extra or {}
                for sub, ms in extra.pop("_sub", []):
                    sub_samples.setdefault(sub, []).append(ms)
                extras.append(extra)
                print(".", end="", flush=True)
            except Exception as e:
                errors.append(f"{type(e).__name__}: {e}"[:300])
                print("x", end="", flush=True)
        stats = summarize(times)
        print(f"  p50 {stats['p50']} ms" if stats else "  all runs failed")
        entry = {
            "network": network,
            "notes": notes,
            "n_ok": len(times),
            "n_failed": len(errors),
            "pace_s": PACE_S.get(name, 0),
            "errors": sorted(set(errors)),
            "ms": stats,
            "samples_ms": [round(t, 1) for t in times],
            "extras": extras,
        }
        if name == "llm_stream_ollama" and bench.ollama_cold:
            entry["cold_start"] = bench.ollama_cold
        for key in ("ttft_ms", "decode_tokens_per_s"):
            values = [e[key] for e in extras if e.get(key) is not None]
            if values:
                entry[f"{key}_stats"] = summarize(values)
        results[name] = entry
    for sub, values in sub_samples.items():
        results[f"e2e.{sub}"] = {
            "network": True,
            "notes": "Measured inside e2e_mission runs.",
            "n_ok": len(values), "n_failed": 0, "errors": [],
            "ms": summarize(values), "samples_ms": [round(v, 1) for v in values], "extras": [],
        }
    return results


def write_markdown(report, path):
    hw, sw, git = report["environment"]["hardware"], report["environment"]["software"], report["environment"]["git"]
    lines = [
        f"# Ciel pipeline benchmark, {report['date']}",
        "",
        f"- **Hardware:** {hw['cpu']} ({hw['cpu_logical_cores']} threads), {hw['ram_gb']} GB RAM, {hw['gpu']}",
        f"- **Software:** {sw['os']}, Python {sw['python']}, {sw['ffmpeg']}, Ollama {sw.get('ollama')}",
        f"- **Code:** `{git['sha'][:7]}`{' (uncommitted changes)' if git['dirty'] else ''}",
        f"- **Runs per stage:** {report['runs']}. Percentiles are nearest-rank; with fewer than 20 runs, p95 is the max.",
        "- **Network** stages are round trips from this machine to the provider and depend on the connection "
        "at the time of the run. They are not compute times.",
        "- **Groq stages are paced** (untimed waits: " + ", ".join(f"`{k}` {v} s" for k, v in PACE_S.items()) +
        ") to stay under the 8,000 tokens/minute limit. An e2e run in which `call_groq` hit a rate-limit "
        "sleep or switched model is excluded and listed under Failures.",
        "",
        "| Stage | Where | n ok / failed | p50 ms | p95 ms | max ms | Notes |",
        "|---|---|---|---|---|---|---|",
    ]
    for name, s in report["stages"].items():
        ms = s["ms"] or {}
        lines.append(f"| `{name}` | {'network' if s['network'] else 'local'} | {s['n_ok']} / {s['n_failed']} | "
                     f"{ms.get('p50', '–')} | {ms.get('p95', '–')} | {ms.get('max', '–')} | {s['notes']} |")
    llm = {k: report["stages"][k] for k in ("llm_stream_groq", "llm_stream_ollama") if k in report["stages"]}
    if llm:
        lines += ["", "## Local vs cloud LLM (same prompt, streamed)", "",
                  "| Engine | Model | TTFT p50 ms | Decode tok/s p50 | Notes |", "|---|---|---|---|---|"]
        for name, s in llm.items():
            model = s["extras"][0].get("model") if s["extras"] else "–"
            ttft = (s.get("ttft_ms_stats") or {}).get("p50", "–")
            tps = (s.get("decode_tokens_per_s_stats") or {}).get("p50", "–")
            note = "network round trip" if s["network"] else ""
            if s.get("cold_start"):
                cs = s["cold_start"]
                pl = cs.get("placement", {})
                note = (f"cold load {cs['cold_load_ms']} ms; in VRAM: {pl.get('vram_gb', '?')} of "
                        f"{pl.get('size_gb', '?')} GB (GPU share {pl.get('gpu_share', '?')})")
                sb = cs.get("server_breakdown_warm") or {}
                if "prompt_eval_ms" in sb:
                    note += (f"; Ollama's own timing (warm): prompt eval {sb['prompt_eval_ms']} ms for "
                             f"{sb['prompt_tokens']} tokens, decode {sb['eval_tokens_per_s']} tok/s")
            lines.append(f"| {name.split('_')[-1]} | `{model}` | {ttft} | {tps} | {note} |")
    failed = {n: s["errors"] for n, s in report["stages"].items() if s["errors"]}
    if failed:
        lines += ["", "## Failures", ""]
        for name, errs in failed.items():
            lines += [f"- `{name}`: {e}" for e in errs]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--stages", help="comma-separated stage names (default: all)")
    parser.add_argument("--local-only", action="store_true", help="skip every network-bound stage")
    parser.add_argument("--out", help="output file stem (default: bench/results/YYYY-MM-DD)")
    args = parser.parse_args()

    names = [n for n, _, _ in STAGES]
    if args.stages:
        names = [n.strip() for n in args.stages.split(",")]
        unknown = set(names) - {n for n, _, _ in STAGES}
        if unknown:
            parser.error(f"unknown stage(s): {', '.join(sorted(unknown))}")
    if args.local_only:
        names = [n for n in names if n in LOCAL_STAGES]

    started = datetime.now()
    print(f"Ciel benchmark: {len(names)} stage(s) x {args.runs} run(s)")
    environment = collect_environment()
    stages = asyncio.run(run(names, args.runs))
    report = {
        "date": started.strftime("%Y-%m-%d"),
        "started_at": started.isoformat(timespec="seconds"),
        "duration_s": round((datetime.now() - started).total_seconds(), 1),
        "runs": args.runs,
        "inputs": {"spoken_prompt": SPOKEN_PROMPT, "mission_prompt": MISSION_PROMPT, "llm_prompt": LLM_PROMPT,
                   "llm_max_tokens": LLM_MAX_TOKENS, "search_query": SEARCH_QUERY},
        "environment": environment,
        "stages": stages,
    }
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stem = Path(args.out) if args.out else RESULTS_DIR / report["date"]
    stem.with_suffix(".json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    write_markdown(report, stem.with_suffix(".md"))
    print(f"Wrote {stem.with_suffix('.json')} and {stem.with_suffix('.md')}")


if __name__ == "__main__":
    main()
