# Ciel pipeline benchmark, 2026-10-26

- **Hardware:** 12th Gen Intel(R) Core(TM) i5-12400F (12 threads), 15.8 GB RAM, AMD Radeon RX 6600 XT
- **Software:** Microsoft Windows 11 Pro (build 10.0.26200), Python 3.11.9, ffmpeg version 9.0.2-full_build-www.gyan.dev, Ollama unreachable (URLError)
- **Code:** `89bd25e`
- **Runs per stage:** 10. Percentiles are nearest-rank; with fewer than 20 runs, p95 is the max.
- **Network** stages are round trips from this machine to the provider and depend on the connection at the time of the run. They are not compute times.
- **Groq stages are paced** (untimed waits: `llm_stream_groq` 5 s, `e2e_mission` 45 s) to stay under the 8,000 tokens/minute limit. An e2e run in which `call_groq` hit a rate-limit sleep or switched model is excluded and listed under Failures.

| Stage | Where | n ok / failed | p50 ms | p95 ms | max ms | Notes |
|---|---|---|---|---|---|---|
| `e2e_mission` | network | 7 / 3 | 3078.0 | 3294.9 | 3294.9 | Full execute_mission for a general-knowledge prompt: router LLM (which writes a direct answer itself since 2026-10-26; synthesis LLM only after a tool), TTS in the voice languages set. Sub-stages are timed inside it, including time_to_first_audio (mission start until the first playable audio URL is broadcast). |
| `e2e.router_llm` | network | 7 / 0 | 1008.3 | 1282.3 | 1282.3 | Measured inside e2e_mission runs. |
| `e2e.tts_clip` | network | 16 / 0 | 1469.5 | 2572.6 | 2572.6 | Measured inside e2e_mission runs. |
| `e2e.time_to_first_audio` | network | 7 / 0 | 1519.9 | 3089.4 | 3089.4 | Measured inside e2e_mission runs. |

## Failures

- `e2e_mission`: RuntimeError: excluded, Groq retry/fallback during run: [Groq 429] qwen/qwen3.8-27b rate limited, switching to next Groq model...
