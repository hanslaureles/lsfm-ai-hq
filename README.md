<div align="center">

# 🌸 LSFM AI HQ

### Five Discord agents and a local voice copilot built with Python asyncio

[![CI](https://github.com/hanslaureles/lsfm-ai-hq/actions/workflows/ci.yml/badge.svg)](https://github.com/hanslaureles/lsfm-ai-hq/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-lightgrey)](LICENSE)

</div>

**LSFM AI HQ** is a personal operations platform: five Discord bots running on a unified asyncio event loop, each with its own job (briefings and inbox triage, job applications, portfolio audits, code and knowledge, system health), plus **Ciel**, a bilingual voice copilot served via an asynchronous REST and WebSocket server to a local web HUD. Built and run on a single Windows workstation by [Hans Aaron Laureles](https://hanslaureles.vercel.app).

---

## The agents

`run_all.py` starts all five bots on one asyncio event loop and reconnects each one if its gateway connection drops (3 s backoff, growing ×1.5 per retry up to 15 s).

| Agent | File | Role | Example commands |
| :--- | :--- | :--- | :--- |
| 🌸 **Sakura** | `bot_sakura.py` | Chief of staff: briefings, goals, Gmail, routing | `!briefing` `!apply <url>` `!triage` `!inbox` `!mode` |
| ⭐ **Chaewon** | `bot_chaewon.py` | Career copilot: job scouting, tailoring, PDFs | `!scout` `!tailor` `!pdf` `!screen` `!rebuild_resume` |
| 🎨 **Yunjin** | `bot_yunjin.py` | Portfolio auditor and design critic | `!audit` `!critique` |
| 💻 **Kazuha** | `bot_kazuha.py` | Knowledge (RAG) and local Git review | `!ask` `!search` `!git` `!review` `!commit` `!pr` |
| 🛡️ **Eunchae** | `bot_eunchae.py` | System guardian and QA | `!vitals` `!test` `!reflect` `!heartbeat` |

Scheduled work: Sakura posts a briefing once a day after 08:00; Yunjin runs a weekly portfolio audit (its loop checks every 6 h); Eunchae runs a hardware watchdog every 5 minutes and posts a daily vitals card.

### Models

- **Cloud (default):** each agent has its own cloud model, set in `memory/brain_mode.json`: Sakura and Kazuha use Groq `qwen/qwen3.8-27b`, Chaewon `openai/gpt-oss-120b`, Eunchae `openai/gpt-oss-20b`, and Yunjin Gemini `gemini-3.6-flash`. A Groq agent falls back to Gemini `gemini-3.6-flash` when Groq fails; Yunjin runs the reverse, Gemini first and then Groq `qwen/qwen3.8-27b`. On a 429, `call_groq` waits briefly once, then moves to the next Groq model.
- **Local (optional):** Ollama `qwen2.5-coder:7b`, switched on with `!mode local` or `!mode auto`.
- **RAG (`rag_engine.py`):** `gemini-embedding-001` embeddings (3,072 dimensions) and BM25 keyword scores, fused with Reciprocal Rank Fusion, stored in SQLite.

### Tools behind the agents

- **Job pipeline (`scout.py`, `web_tools.py`):** LinkedIn, JobStreet, Indeed and RemoteOK listings; fit analysis against your master resume; tailored resume and cover letter.
- **PDF engine (`pdf_engine.py`):** parses your structured profile from `memory/master_resume.md` and renders a one-page vector PDF with headless Microsoft Edge or Chromium. Rejects compilation if the default template is unchanged or required profile fields (name, summary, experience, education) are missing.
- **Gmail (`gmail_engine.py`):** OAuth2, sorting mail into ten `LSFM/…` labels.

---

## Ciel: the voice copilot

`ciel_server.py` (aiohttp, `localhost:8000`) serves the HUD over REST and WebSocket and runs missions through `ciel_orchestrator.py`.

- **Routing:** keyword fast paths for weather, web search, memory reset and appraisals; otherwise an LLM router returns a JSON plan (general knowledge, Obsidian lookup, or delegation to the five agents).
- **Voice in:** the HUD's WebM/Opus recording is normalized by FFmpeg (16 kHz mono, `volume=1.8,highpass=f=80,lowpass=f=7500`) and transcribed by Groq `whisper-large-v3-turbo`.
- **Voice out:** Edge TTS speaks a Japanese line (`ja-JP-NanamiNeural`), a 350 ms pause, then English (`en-US-AvaNeural`), through a "Thought Acceleration" filter: high-pass 120 Hz, a 3.5 kHz presence boost and a short double echo.
- **Async runtime:** every blocking call (LLMs, weather, web search, psutil, Obsidian, git, Gmail, the PDF build) runs in a worker thread; tool and LLM calls have per-call timeouts, and independent agents run concurrently. At most two missions run at once and up to eight more queue; beyond that a mission is refused (HTTP 503 over REST, a `ciel_error` event over WebSocket).
- **Honest failures:** each agent ends as `done`, `attention` or `failed` with the real error; a timeout reads "timed out after N s", never a quiet success.
- **Local-only server:** a Host allowlist, an Origin check on WebSocket upgrades and non-GET requests, and JSON-only command routes block DNS rebinding and cross-site requests.
- **Proactive sentinel:** every 45 s while a HUD is connected, it checks CPU (> 85 %), RAM (> 88 %), free disk on C: (< 20 GB), uncommitted git changes, the Obsidian daily log, and TODO/FIXME markers in the portfolio.

The HUD front end lives in a separate repository; the server serves it from a sibling `../ciel-hud` folder when present, and the REST/WebSocket API works without it.

---

## Measured performance

From `bench/bench_ciel.py`, **N = 10 runs per stage, 2026-10-01**, on an Intel Core i5-12400F, 16 GB RAM, AMD Radeon RX 6600 XT (8 GB), Windows 11. Network stages are round trips from that machine at that time, not compute times. Full report: [`bench/results/2026-10-01.md`](bench/results/2026-10-01.md).

| Stage | p50 | Max | Where |
| :--- | ---: | ---: | :--- |
| Full voice mission (router → synthesis → bilingual TTS) | 4.43 s | 5.71 s | network |
| ↳ router LLM / synthesis LLM / bilingual TTS | 0.58 / 0.96 / 2.80 s | | network |
| Speech-to-text (Groq Whisper) | 0.50 s | 0.75 s | network |
| FFmpeg inbound / outbound audio | 32 / 81 ms | 44 / 87 ms | local |
| Resume PDF render (headless Edge) | 1.11 s | 7.27 s | local |
| Groq `qwen/qwen3.8-27b`, streamed | first token 303 ms, 459 tokens/s | | network |
| Ollama `qwen2.5-coder:7b` on the RX 6600 XT | 28 tokens/s | | local |

The local model loads fully into VRAM (4.74 GB, cold load 7.7 s). With fewer than 20 runs, the max is also the p95. Groq runs were paced to stay under its 8,000 tokens/minute limit; a mission that hit a rate-limit retry or a model switch would have been excluded (none did, and all stages completed 10 of 10 runs).

---

## Tests and CI

```bash
python -m pytest -q test_ciel_orchestrator.py test_ciel_security.py test_hq_smoke.py test_llm_client.py test_pdf_engine.py bench/test_bench.py
```

The suite is hermetic (LLMs, TTS, Obsidian and engines are stubbed), so it needs no API keys. It covers the server's local-only guard, mission IDs, honest failure states, event-loop responsiveness, timeouts, mission admission and memory clearing. GitHub Actions runs it on every push to `main` and on pull requests. The `.env` and Obsidian-vault checks in `test_hq_smoke.py` skip when those are absent.

---

## Quick start

Requirements: Python 3.11, FFmpeg on `PATH`, Microsoft Edge or Chromium, and a Discord server with five bot applications.

```bash
git clone https://github.com/hanslaureles/lsfm-ai-hq.git
cd lsfm-ai-hq
pip install -r requirements.txt
cp .env.example .env    # bot tokens, Groq and Gemini keys, OWNER_EMAIL, optional Obsidian settings
python -u run_all.py    # the five Discord agents
python ciel_server.py   # Ciel's HUD server on localhost:8000
```

`memory/` ships with templates. Replace `memory/master_resume.md` with your own profile and work history before compiling a resume PDF or running career commands; the compiler rejects default templates and incomplete profile fields.

---

## Repository structure

```
lsfm-ai-hq/
├── bot_*.py               # the five Discord agents
├── run_all.py             # starts all five on one event loop
├── ciel_orchestrator.py   # Ciel: routing, tools, agents, synthesis
├── ciel_server.py         # Ciel: aiohttp REST + WebSocket server
├── voice_engine.py        # Whisper STT, Edge TTS, FFmpeg filters
├── proactive_sentinel.py  # 45 s workspace checks for the HUD
├── *_engine.py            # PDF, RAG, Gmail, per-agent engines
├── llm_client.py          # Groq / Gemini / Ollama routing
├── obsidian_client.py     # Obsidian REST API with filesystem fallback
├── git_sentinel.py        # local Git status, diff and secret checks
├── scout.py, web_tools.py # job scraping and fit analysis
├── bench/                 # benchmark harness, samples, results
├── memory/                # templates for your resume, goals, specs
├── scripts/               # OAuth and Discord setup helpers
└── test_*.py              # hermetic test suite
```

---

## Author

**Hans Aaron Laureles**, Applied AI Engineer and Full-Stack Builder

- Portfolio: [hanslaureles.vercel.app](https://hanslaureles.vercel.app)
- GitHub: [@hanslaureles](https://github.com/hanslaureles)
- LinkedIn: [linkedin.com/in/hanslaureles](https://linkedin.com/in/hanslaureles)
- Email: [hanslaureles92@gmail.com](mailto:hanslaureles92@gmail.com)

Licensed under the [MIT License](LICENSE).
