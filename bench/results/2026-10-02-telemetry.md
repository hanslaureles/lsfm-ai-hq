# LLM telemetry benchmark, 2026-10-02

- **Hardware:** 12th Gen Intel(R) Core(TM) i5-12400F (12 threads), 15.8 GB RAM, AMD Radeon RX 6600 XT
- **Software:** Microsoft Windows 11 Pro (build 10.0.26200), Python 3.11.9
- **Code:** `922248a` (uncommitted changes)
- **Brain mode:** cloud. Each agent's fixed prompt is sent 10 times through `query_llm_structured` (not streamed, as the bots call it), with an untimed 3.0 s wait before each call.
- Percentiles are nearest-rank; with fewer than 20 calls, p95 is the max. Every call is a network round trip from this machine to the provider, not compute time.

| Agent | Answered by | n ok / failed | p50 ms | p95 ms | max ms | TTFT p50 ms | Fell back |
|---|---|---|---|---|---|---|---|
| sakura | `groq/qwen/qwen3.8-27b` ×10 | 10 / 0 | 312.0 | 466.7 | 466.7 | – | 0% |
| chaewon | `groq/openai/gpt-oss-120b` ×10 | 10 / 0 | 786.3 | 931.0 | 931.0 | – | 0% |
| yunjin | `gemini/gemini-3.6-flash` ×9, `gemini/gemini-3.5-flash` ×1 | 10 / 0 | 4861.1 | 7011.6 | 7011.6 | – | 10% |
| kazuha | `groq/qwen/qwen3.8-27b` ×10 | 10 / 0 | 333.6 | 554.1 | 554.1 | – | 0% |
| eunchae | `groq/openai/gpt-oss-20b` ×10 | 10 / 0 | 747.1 | 918.3 | 918.3 | – | 0% |

**All agents:** 50 successful calls, p50 736.3 ms, p95 6177.1 ms.
