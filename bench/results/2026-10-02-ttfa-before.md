# Ciel pipeline benchmark, 2026-10-02

- **Hardware:** 12th Gen Intel(R) Core(TM) i5-12400F (12 threads), 15.8 GB RAM, AMD Radeon RX 6600 XT
- **Software:** Microsoft Windows 11 Pro (build 10.0.26200), Python 3.11.9, ffmpeg version 9.0.2-full_build-www.gyan.dev, Ollama unreachable (URLError)
- **Code:** `3315b69`
- **Runs per stage:** 10. Percentiles are nearest-rank; with fewer than 20 runs, p95 is the max.
- **Network** stages are round trips from this machine to the provider and depend on the connection at the time of the run. They are not compute times.
- **Groq stages are paced** (untimed waits: `llm_stream_groq` 5 s, `e2e_mission` 45 s) to stay under the 8,000 tokens/minute limit. An e2e run in which `call_groq` hit a rate-limit sleep or switched model is excluded and listed under Failures.

| Stage | Where | n ok / failed | p50 ms | p95 ms | max ms | Notes |
|---|---|---|---|---|---|---|
| `e2e_mission` | network | 10 / 0 | 4596.9 | 5991.7 | 5991.7 | Full execute_mission for a general-knowledge prompt: router LLM, synthesis LLM, bilingual TTS. Sub-stages are timed inside it, including time_to_first_audio (mission start until the first playable audio URL is broadcast). |
| `e2e.router_llm` | network | 10 / 0 | 508.2 | 650.1 | 650.1 | Measured inside e2e_mission runs. |
| `e2e.synthesis_llm` | network | 10 / 0 | 835.5 | 1500.2 | 1500.2 | Measured inside e2e_mission runs. |
| `e2e.tts_bilingual` | network | 10 / 0 | 3214.6 | 4410.1 | 4410.1 | Measured inside e2e_mission runs. |
| `e2e.time_to_first_audio` | network | 10 / 0 | 4596.4 | 5991.1 | 5991.1 | Measured inside e2e_mission runs. |
