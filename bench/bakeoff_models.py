"""
Model bake-off (2026-10-27, approved by Hans): which cloud model should Chaewon tailor with, and Yunjin
critique with. Live calls; writes bench/results/YYYY-MM-DD-model-bakeoff.{json,md}; changes no setting.

  python bench/bakeoff_models.py                    # 8 saved postings x 3 writers, 3 case studies x 3 Gemini models
  python bench/bakeoff_models.py --jobs 4 --skip-yunjin

Chaewon: each writer model runs the real job_hunt.tailor (same checks and the same Kazuha claim checker) on
the same saved, full postings. A run whose provider fell back to another model is counted, not hidden.
Yunjin: the real critique_case_study per Gemini model; the texts are saved side by side for Hans to judge.
"""

import argparse
import json
import statistics
import sys
import time
import types
from datetime import date
from pathlib import Path

HQ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HQ))

WRITERS = [("groq", "openai/gpt-oss-120b"), ("groq", "qwen/qwen3.8-27b"), ("gemini", "gemini-3.8-flash")]
CRITICS = ["gemini-3.6-flash", "gemini-3.7-flash", "gemini-3.8-flash"]
CASES = ["ciel", "lsfm", "memory"]
# Wait before every model call (writer and checker). A tailoring prompt is ~4k tokens and Groq's free tier allows
# 8,000 tokens a minute per model, shared with the running bots: the first run (2026-10-27, 15 s between jobs only)
# hit 34 rate limits and its forced models silently fell back. The waits are left out of the reported seconds.
# ponytail: a fixed pace, not a token budget.
PACE_S = 30
RESULTS = HQ / "bench" / "results"
_PACED = [0.0]  # seconds slept on purpose so far; timed() subtracts them


def pace() -> None:
    time.sleep(PACE_S)
    _PACED[0] += PACE_S


def timed(fn):
    """(fn(), seconds it took without the pacing waits inside it)."""
    t0, paced0 = time.perf_counter(), _PACED[0]
    result = fn()
    return result, round(time.perf_counter() - t0 - (_PACED[0] - paced0), 1)


def forced(provider: str, model: str, log: list):
    """A query_llm_structured stand-in that asks one model; log gets the model that really answered."""
    import llm_client

    def call(prompt, system_instruction="", temperature=0.4, **_):
        pace()
        meta = {}
        if provider == "groq":
            text = llm_client.call_groq(prompt, system_instruction, model=model, temperature=temperature, meta=meta)
        else:
            text = llm_client.call_gemini(prompt, system_instruction, model_name=model, temperature=temperature, meta=meta)
        log.append(meta.get("model", model))
        return types.SimpleNamespace(text=text)
    return call


def postings(apps: Path, n: int) -> list:
    """The newest n saved full postings (not thin), newest first: (job, text)."""
    out = []
    for d in sorted((p for p in apps.iterdir() if (p / "posting.txt").exists()), reverse=True):
        try:
            meta = json.loads((d / "meta.json").read_text(encoding="utf-8-sig"))
            text = (d / "posting.txt").read_text(encoding="utf-8").split("\n\n", 1)[1]
        except (OSError, ValueError, IndexError):
            continue
        if meta.get("thin") or len(text) < 400:
            continue
        out.append(({"title": meta.get("role", ""), "company": meta.get("company", ""), "site": meta.get("site", ""),
                     "id": d.name}, text))
        if len(out) == n:
            break
    return out


def summarize(rows: list) -> dict:
    """Per writer: ok count, mean writer calls, runs with claim warnings or a fallback, p50 seconds, mean fit."""
    out = {}
    for model in dict.fromkeys(r["model"] for r in rows):
        mine = [r for r in rows if r["model"] == model]
        ok = [r for r in mine if r["ok"]]
        out[model] = {
            "runs": len(mine), "ok": len(ok),
            "mean_writer_calls": round(statistics.mean(r["writer_calls"] for r in mine), 2),
            "claim_warning_runs": sum(1 for r in ok if r["warnings"]),
            "fallback_runs": sum(1 for r in mine if r["fell_back"]),
            "p50_s": round(statistics.median(r["seconds"] for r in mine), 1),
            "mean_fit": round(statistics.mean(r["fit"] for r in ok), 1) if ok else None,
        }
    return out


def run_chaewon(n: int) -> list:
    import job_hunt
    from pdf_engine import load_master_resume_context
    ctx, rows = load_master_resume_context(), []
    jobs = postings(job_hunt.JOB_HUNT_DIR / "applications", n)
    real, real_check = job_hunt.query_llm_structured, job_hunt.query_llm

    def paced_check(*a, **k):  # the claim checker shares the Groq budget too
        pace()
        return real_check(*a, **k)
    job_hunt.query_llm = paced_check
    try:
        for provider, model in WRITERS:
            for job, text in jobs:
                log = []
                job_hunt.query_llm_structured = forced(provider, model, log)
                res, seconds = timed(lambda: job_hunt.tailor(job, text, ctx))
                rows.append({"model": model, "job": job["id"], "ok": not res["errors"], "fit": res["fit"],
                             "gaps": res["gaps"], "warnings": res["warnings"], "errors": res["errors"],
                             "writer_calls": len(log), "fell_back": any(m != model for m in log),
                             "answered_by": log, "seconds": seconds,
                             "letter": (res["tailoring"] or {}).get("cover_paragraphs", []),
                             "title": (res["tailoring"] or {}).get("title", "")})
                print(f"  {model:22} {job['id'][:48]:48} ok={rows[-1]['ok']} fit={res['fit']} "
                      f"calls={len(log)} {rows[-1]['seconds']}s fell_back={rows[-1]['fell_back']}", flush=True)
    finally:
        job_hunt.query_llm_structured, job_hunt.query_llm = real, real_check
    return rows


def run_yunjin() -> list:
    import yunjin_engine
    real, rows = yunjin_engine.query_llm, []
    try:
        for model in CRITICS:
            log = []
            call = forced("gemini", model, log)
            yunjin_engine.query_llm = lambda prompt, temperature=0.4, agent=None, _c=call: _c(prompt, "", temperature).text
            for slug in CASES:
                res, seconds = timed(lambda: yunjin_engine.critique_case_study(slug))
                rows.append({"model": model, "case": slug, "seconds": seconds,
                             "answered_by": log[-1:], "critique": res.get("critique") or res.get("error", "")})
                print(f"  {model:18} {slug:8} {rows[-1]['seconds']}s answered_by={log[-1:]}", flush=True)
    finally:
        yunjin_engine.query_llm = real
    return rows


def write(chaewon: list, yunjin: list, out: Path) -> None:
    data = {"date": date.today().isoformat(), "writers": WRITERS, "critics": CRITICS,
            "chaewon_summary": summarize(chaewon) if chaewon else {}, "chaewon": chaewon, "yunjin": yunjin}
    out.with_suffix(".json").write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    md = [f"# Model bake-off, {data['date']}", "",
          f"Live runs on this PC; network round trips. Seconds leave out the {PACE_S:g} s pacing wait before each "
          "model call. Chaewon rows use the real `job_hunt.tailor` (same checks and "
          "the same Kazuha claim checker) on the same saved postings. No setting was changed.", ""]
    if chaewon:
        md += ["## Chaewon (tailoring)", "", "| Writer | ok / runs | writer calls (mean) | runs with claim warnings | "
               "runs that fell back | p50 s | mean fit |", "|---|---|---|---|---|---|---|"]
        for model, s in data["chaewon_summary"].items():
            md.append(f"| `{model}` | {s['ok']} / {s['runs']} | {s['mean_writer_calls']} | {s['claim_warning_runs']} | "
                      f"{s['fallback_runs']} | {s['p50_s']} | {s['mean_fit']} |")
        md += ["", "Fits per posting (blank = refused):", "", "| Posting | " + " | ".join(m for _, m in WRITERS) + " |",
               "|---|" + "---|" * len(WRITERS)]
        for job in dict.fromkeys(r["job"] for r in chaewon):
            cells = [next((str(r["fit"]) if r["ok"] else "" for r in chaewon if r["job"] == job and r["model"] == m), "")
                     for _, m in WRITERS]
            md.append(f"| {job[:60]} | " + " | ".join(cells) + " |")
        md += ["", "Cover letters are in the JSON (`chaewon[].letter`) to read side by side.", ""]
    if yunjin:
        md += ["## Yunjin (case-study critiques)", "", "Judge by reading: same case study, same prompt, one model each.", ""]
        for slug in CASES:
            md += [f"### {slug}", ""]
            for r in (r for r in yunjin if r["case"] == slug):
                md += [f"**`{r['model']}`** ({r['seconds']} s, answered by {', '.join(r['answered_by']) or '?'})", "",
                       r["critique"].strip(), ""]
    out.with_suffix(".md").write_text("\n".join(md) + "\n", encoding="utf-8")


def main():
    global PACE_S
    from dotenv import load_dotenv
    load_dotenv(HQ / ".env")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--jobs", type=int, default=8)
    parser.add_argument("--pace", type=float, default=PACE_S, help="seconds to wait before each model call")
    parser.add_argument("--skip-yunjin", action="store_true")
    parser.add_argument("--skip-chaewon", action="store_true")
    parser.add_argument("--out", default=str(RESULTS / f"{date.today().isoformat()}-model-bakeoff"),
                        help="file stem inside bench/results (.json and .md are added)")
    args = parser.parse_args()
    out = results_stem(args.out)
    if out is None:  # checked before any model call or file write
        parser.error(f"--out must name a file stem directly inside {RESULTS}")
    PACE_S = args.pace
    chaewon = [] if args.skip_chaewon else run_chaewon(args.jobs)
    yunjin = [] if args.skip_yunjin else run_yunjin()
    write(chaewon, yunjin, out)
    print(f"Wrote {out}.json and .md", flush=True)


def results_stem(raw: str):
    """The resolved stem when it sits directly in bench/results (so its .json and .md do too), else None."""
    stem = Path(raw).resolve()
    return stem if stem.parent == RESULTS.resolve() and stem.name not in ("", ".", "..") else None


if __name__ == "__main__":
    main()
