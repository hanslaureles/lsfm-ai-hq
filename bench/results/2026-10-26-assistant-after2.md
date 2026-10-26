# Ciel pipeline benchmark, 2026-10-26

- **Hardware:** 12th Gen Intel(R) Core(TM) i5-12400F (12 threads), 15.8 GB RAM, AMD Radeon RX 6600 XT
- **Software:** Microsoft Windows 11 Pro (build 10.0.26200), Python 3.11.9, ffmpeg version 9.0.2-full_build-www.gyan.dev, Ollama unreachable (URLError)
- **Code:** `2a28252`
- **Runs per stage:** 10. Percentiles are nearest-rank; with fewer than 20 runs, p95 is the max.
- **Network** stages are round trips from this machine to the provider and depend on the connection at the time of the run. They are not compute times.
- **Groq stages are paced** (untimed waits: `llm_stream_groq` 5 s, `e2e_mission` 45 s) to stay under the 8,000 tokens/minute limit. An e2e run in which `call_groq` hit a rate-limit sleep or switched model is excluded and listed under Failures.

| Stage | Where | n ok / failed | p50 ms | p95 ms | max ms | Notes |
|---|---|---|---|---|---|---|
| `e2e_mission` | network | 10 / 0 | 3639.5 | 5435.8 | 5435.8 | Full execute_mission for a general-knowledge prompt: router LLM (which writes a direct answer itself since 2026-10-26; synthesis LLM only after a tool), TTS in the voice languages set. Sub-stages are timed inside it, including time_to_first_audio (mission start until the first playable audio URL is broadcast). |
| `e2e.router_llm` | network | 10 / 0 | 967.9 | 1244.6 | 1244.6 | Measured inside e2e_mission runs. |
| `e2e.tts_clip` | network | 21 / 0 | 1968.0 | 3297.5 | 4728.1 | Measured inside e2e_mission runs. |
| `e2e.time_to_first_audio` | network | 10 / 0 | 1741.4 | 3087.9 | 3087.9 | Measured inside e2e_mission runs. |
