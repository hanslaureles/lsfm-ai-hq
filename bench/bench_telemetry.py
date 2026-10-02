"""
LLM telemetry benchmark (Phase 4, 4B-3).

Sends one fixed prompt per agent through llm_client.query_llm_structured, the
same dispatcher the bots use, N times each, and reports per agent and overall:
p50 / p95 total latency, time to first token when --stream is on, how often a
call fell back to another model, and which models answered.

Every call is a network round trip from this machine to Groq or Google; the
numbers move with the connection and the providers' load at the time.

Usage (from the repo root):
    python bench/bench_telemetry.py                 # 10 runs per agent, live
    python bench/bench_telemetry.py --runs 3 --stream
    python bench/bench_telemetry.py --mock          # no keys, no network (CI)

Live runs write bench/results/YYYY-MM-DD-telemetry.json plus a Markdown summary.
--mock answers from fake providers and never writes into bench/results (it uses
the system temp dir unless --out names another place outside it).
"""

import argparse
import copy
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bench_ciel  # noqa: E402  (also puts the repo root on sys.path and chdirs there)
from bench_ciel import RESULTS_DIR, collect_environment, summarize  # noqa: E402

import llm_client  # noqa: E402

# Fixed inputs: one short task per agent's domain, so runs on different days do the same work.
PROMPTS = {
    "sakura": "In two sentences, say what a good morning briefing should list first.",
    "chaewon": "In two sentences, explain what an ATS match score measures.",
    "yunjin": "In two sentences, state the WCAG 2.1 AA contrast rule for normal text.",
    "kazuha": "In two sentences, explain what git rebase does.",
    "eunchae": "In two sentences, explain why a watchdog samples CPU and RAM on a schedule.",
}
PACE_S = 3.0  # untimed wait before each call: 40 Groq calls stay under the 8,000 tokens/minute limit


def run_agent(agent, prompt, runs, *, stream=False, pace_s=PACE_S, query=None, sleep=time.sleep):
    """Calls query(prompt, agent=..., on_delta=...) `runs` times; returns the raw runs and their stats."""
    query = query or llm_client.query_llm_structured
    calls = []
    for _ in range(runs):
        if pace_s:
            sleep(pace_s)
        started = time.perf_counter()
        try:
            r = query(prompt, agent=agent, on_delta=(lambda piece: None) if stream else None)
            calls.append({"ok": True, "total_ms": r.total_ms, "ttft_ms": r.ttft_ms, "provider": r.provider,
                          "model": r.model, "fallback_chain": list(r.fallback_chain)})
        except Exception as _exc:
            calls.append({"ok": False, "total_ms": round((time.perf_counter() - started) * 1000, 1),
                          "error": f"{type(_exc).__name__}: {_exc}"[:300]})
    ok = [c for c in calls if c["ok"]]
    answered = {}
    for c in ok:
        key = f"{c['provider']}/{c['model']}"
        answered[key] = answered.get(key, 0) + 1
    return {
        "prompt": prompt,
        "n_ok": len(ok),
        "n_failed": len(calls) - len(ok),
        "total_ms": summarize([c["total_ms"] for c in ok]),
        "ttft_ms": summarize([c["ttft_ms"] for c in ok if c["ttft_ms"] is not None]),
        # A call fell back when more than one model was tried before the answer.
        "fallback_rate": round(sum(len(c["fallback_chain"]) > 1 for c in ok) / len(ok), 2) if ok else None,
        "answered_by": answered,
        "errors": sorted({c["error"] for c in calls if not c["ok"]}),
        "calls": calls,
    }


def run(runs, *, stream=False, pace_s=PACE_S, query=None, sleep=time.sleep):
    agents = {}
    for agent, prompt in PROMPTS.items():
        print(f"  {agent:<8}", end="", flush=True)
        agents[agent] = run_agent(agent, prompt, runs, stream=stream, pace_s=pace_s, query=query, sleep=sleep)
        s = agents[agent]["total_ms"]
        print(f" p50 {s['p50']} ms ({agents[agent]['n_ok']} ok)" if s else " all calls failed", flush=True)
    every_ok = [c["total_ms"] for a in agents.values() for c in a["calls"] if c["ok"]]
    return agents, {"n_ok": len(every_ok), "total_ms": summarize(every_ok)}


# ---------------------------------------------------------------- mock providers

def _mock_provider(provider, model_kw):
    """A deterministic stand-in for call_groq / call_gemini / call_local_ollama."""
    def fake(prompt, **kw):
        model = kw.get(model_kw) or "mock-model"
        llm_client._note_attempt(kw.get("meta"), provider, model)
        if kw.get("on_delta"):
            kw["on_delta"]("mock")
        llm_client._note_answer(kw.get("meta"), provider, model, 0.0 if kw.get("on_delta") else None)
        return "mock reply"
    return fake


def install_mocks():
    """Swaps the providers and config for fakes; returns a function that restores them."""
    saved = {n: getattr(llm_client, n) for n in ("call_groq", "call_gemini", "call_local_ollama", "load_brain_config")}
    llm_client.call_groq = _mock_provider("groq", "model")
    llm_client.call_gemini = _mock_provider("gemini", "model_name")
    llm_client.call_local_ollama = _mock_provider("ollama", "model")
    llm_client.load_brain_config = lambda: copy.deepcopy(llm_client.DEFAULT_CONFIG)  # cloud mode, default models
    old_key = os.environ.get("GEMINI_API_KEY")
    os.environ["GEMINI_API_KEY"] = old_key or "mock"  # so Yunjin routes to (fake) Gemini first, as configured

    def restore():
        for n, f in saved.items():
            setattr(llm_client, n, f)
        if old_key is None:
            os.environ.pop("GEMINI_API_KEY", None)
    return restore


# ---------------------------------------------------------------- report

def write_markdown(report, path):
    env = report["environment"]
    hw, sw, git = env["hardware"], env["software"], env["git"]
    lines = [
        f"# LLM telemetry benchmark, {report['date']}" + (" (MOCK: fake providers, not a measurement)"
                                                         if report["mock"] else ""),
        "",
        f"- **Hardware:** {hw['cpu']} ({hw['cpu_logical_cores']} threads), {hw['ram_gb']} GB RAM, {hw['gpu']}",
        f"- **Software:** {sw['os']}, Python {sw['python']}",
        f"- **Code:** `{git['sha'][:7]}`{' (uncommitted changes)' if git['dirty'] else ''}",
        f"- **Brain mode:** {report['brain_mode']}. Each agent's fixed prompt is sent {report['runs']} times "
        f"through `query_llm_structured` ({'streamed' if report['stream'] else 'not streamed, as the bots call it'}), "
        f"with an untimed {report['pace_s']} s wait before each call.",
        "- Percentiles are nearest-rank; with fewer than 20 calls, p95 is the max. Every call is a network round "
        "trip from this machine to the provider, not compute time.",
        "",
        "| Agent | Answered by | n ok / failed | p50 ms | p95 ms | max ms | TTFT p50 ms | Fell back |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for agent, a in report["agents"].items():
        ms = a["total_ms"] or {}
        ttft = (a["ttft_ms"] or {}).get("p50", "–")
        by = ", ".join(f"`{k}` ×{v}" for k, v in a["answered_by"].items()) or "–"
        rate = "–" if a["fallback_rate"] is None else f"{a['fallback_rate']:.0%}"
        lines.append(f"| {agent} | {by} | {a['n_ok']} / {a['n_failed']} | {ms.get('p50', '–')} | "
                     f"{ms.get('p95', '–')} | {ms.get('max', '–')} | {ttft} | {rate} |")
    o = report["overall"]
    oms = o["total_ms"] or {}
    lines += ["", f"**All agents:** {o['n_ok']} successful calls, p50 {oms.get('p50', '–')} ms, "
                  f"p95 {oms.get('p95', '–')} ms."]
    failed = {n: a["errors"] for n, a in report["agents"].items() if a["errors"]}
    if failed:
        lines += ["", "## Failures", ""] + [f"- `{n}`: {e}" for n, errs in failed.items() for e in errs]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--stream", action="store_true", help="stream replies to measure time to first token")
    parser.add_argument("--pace", type=float, default=PACE_S, help="untimed seconds before each call")
    parser.add_argument("--mock", action="store_true", help="fake providers: no keys, no network (CI)")
    parser.add_argument("--out", help="output file stem (default: bench/results/YYYY-MM-DD-telemetry)")
    args = parser.parse_args(argv)

    started = datetime.now(timezone.utc)
    date = started.astimezone().strftime("%Y-%m-%d")
    if args.mock:
        stem = Path(args.out) if args.out else Path(tempfile.gettempdir()) / f"{date}-telemetry-mock"
        if RESULTS_DIR.resolve() in stem.resolve().parents:
            parser.error("--mock results are not measurements; write them outside bench/results")
    else:
        stem = Path(args.out) if args.out else RESULTS_DIR / f"{date}-telemetry"

    environment = collect_environment()  # before the calls, so the git SHA is the code that ran
    restore = install_mocks() if args.mock else None
    try:
        brain_mode = llm_client.load_brain_config().get("mode", "cloud")
        print(f"LLM telemetry benchmark: {len(PROMPTS)} agents x {args.runs} run(s)"
              f"{' (MOCK)' if args.mock else ''}, brain mode {brain_mode}")
        agents, overall = run(args.runs, stream=args.stream, pace_s=0 if args.mock else args.pace)
    finally:
        if restore:
            restore()
    report = {
        "date": date,
        "started_at": started.isoformat(timespec="seconds"),
        "duration_s": round((datetime.now(timezone.utc) - started).total_seconds(), 1),
        "mock": args.mock,
        "runs": args.runs,
        "stream": args.stream,
        "pace_s": 0 if args.mock else args.pace,
        "brain_mode": brain_mode,
        "environment": environment,
        "agents": agents,
        "overall": overall,
    }
    stem.parent.mkdir(parents=True, exist_ok=True)
    json_path, md_path = Path(f"{stem}.json"), Path(f"{stem}.md")
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    write_markdown(report, md_path)
    print(f"Wrote {json_path} and {md_path}")
    return report


if __name__ == "__main__":
    main()
