# Model bake-off, first run (partial), 2026-10-27

`python bench/bakeoff_models.py` on HQ `fa5c930` + the upkeep branch, started 15:47 PHT while the bots were
running on the same Groq key. **Stopped by Claude at 17:28.** Groq rate limits (34 `429` lines) made the forced
models fall back to other Groq models, and Gemini 3.8 Flash took minutes per call. No JSON was written, and the
rows below are the console lines that survived. The gpt-oss-120b rows were lost with the console buffer.

## Chaewon writer rows that finished

| Writer (forced) | Posting | Passed checks | Fit | Writer calls | Seconds |
|---|---|---|---|---|---|
| qwen/qwen3.8-27b | Training and Marketing Professionals (Data Analytics & Reporting) | no | – | 3 | 12.0 |
| qwen/qwen3.8-27b | Training and Marketing Professionals (Data Analytics & ML) | no | – | 1 | 9.2 |
| qwen/qwen3.8-27b | Teamstead AI Automation Integration | no | – | 1 | 5.2 |
| qwen/qwen3.8-27b | Shell DTA Software Engineer | yes | 25 | 1 | 5.5 |
| qwen/qwen3.8-27b | Salenga Law Firm Rust Developer | yes | 20 | 2 | 13.4 |
| qwen/qwen3.8-27b | Royal Caribbean IAM Full Stack | no | – | 1 | 7.8 |
| qwen/qwen3.8-27b | Questronix VueJS Developer | yes | 30 | 2 | 16.8 |
| qwen/qwen3.8-27b | Questronix Java Spring Boot | yes | 15 | 1 | 20.9 |
| gemini-3.8-flash | Training and Marketing Professionals (Data Analytics & Reporting) | yes | 20 | 1 | 204.0 |
| gemini-3.8-flash | Training and Marketing Professionals (Data Analytics & ML) | yes | 22 | 1 | 190.5 |

The qwen rows overlap with the 429s, so some of their answers came from another Groq model. Treat them as indicative only.

## Gemini latency probes (one-word prompt, `call_gemini`, no fallback taken)

| Model | Time | Answered by |
|---|---|---|
| gemini-3.6-flash (Yunjin today) | 2.0 s | gemini-3.6-flash |
| gemini-3.8-flash | 214.7 s | gemini-3.8-flash |
| gemini-3.7-flash | two calls still running after 600 s; stopped | – |

## Reading

- **Yunjin:** keep `gemini-3.6-flash`. On 2026-10-27 3.7 and 3.8 Flash answered in minutes, not seconds, whatever their quality.
- **Chaewon:** keep `openai/gpt-oss-120b`. qwen passed the checks on 4 of 8 postings (gpt-oss-120b passed 11 of 12 in the live hunt the same day, with retries), and Gemini 3.8 is too slow for the hunt's 480 s budget.
- **The real constraint is Groq's per-minute token limit**, shared by the bots, the claim checker and any test. A clean comparison needs `--pace` (30 s before every call, now the default) and a run while the bots are idle.
