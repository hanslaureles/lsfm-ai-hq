"""
ADR-001 R0: scores Ciel's planner on eval/routing_cases.jsonl, so the R5 switch to a
registry-generated planner is judged against a measured baseline.

    python eval/run_routing_eval.py              # live router (keys from .env); writes eval/results/<date>-routing-run<k>.md
    python eval/run_routing_eval.py --fallback   # keyword router only (router call forced to fail); prints, writes nothing

Each case's "expect" lists labels: a skill name (eunchae.vitals, ...) or one of Ciel's
own actions (ciel.weather, ...). A plan is scored by what _run_mission would execute.
Two scores:
  route - the set of agents (and Ciel actions) matches; today's planner picks agents,
          not sub-actions, so this is the baseline R5 must not fall below.
  exact - the set of labels matches; for today's planner only an indication (its agent
          jobs also pick sub-actions from prompt words), meaningful once plans carry skills (R5).

R5 gate (fixed before R5, Codex R0 fix 3): exactly two live runs at the default pause,
each kept as its own results file. The baseline is the best single run of the two
baseline runs (route first, then Japanese). R5 passes only if one of its own two runs
clears both thresholds in that same run. No third run.
"""

import argparse
import asyncio
import json
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

HQ = Path(__file__).resolve().parent.parent
CASES = HQ / "eval" / "routing_cases.jsonl"
RESULTS = HQ / "eval" / "results"
DEFAULT_PAUSE_S = 12.0  # between live cases: slows requests under Groq's per-minute limit (no guarantee)

CIEL_LABELS = {"ciel.direct", "ciel.weather", "ciel.web_search", "ciel.news", "ciel.timer", "ciel.reset",
               "ciel.appraisal",
               "ciel.read_rules", "ciel.read_profile", "ciel.read_preferences", "ciel.save_to_daily"}
SKILL_LABELS = {"eunchae.vitals", "eunchae.compile_all", "eunchae.test",
                "kazuha.git_status", "kazuha.review", "kazuha.ask",
                "yunjin.audit",
                "sakura.inbox", "sakura.priorities", "sakura.vault_search", "sakura.clean", "sakura.cleanse",
                "sakura.remind", "sakura.reminders", "sakura.cancel_reminder",
                "chaewon.rebuild_resume", "chaewon.scout"}
KNOWN_LABELS = frozenset(CIEL_LABELS | SKILL_LABELS)

# Today's planner (before R5): tool_targets the agent jobs understand, and Ciel's own actions.
OLD_TARGETS = {"check_vitals": "eunchae.vitals", "run_tests": "eunchae.compile_all", "scan_links": "yunjin.audit",
               "check_inbox": "sakura.inbox", "compile_resume": "chaewon.rebuild_resume",
               "git_status": "kazuha.git_status"}
OBSIDIAN_LABELS = {"ciel_read_rules": "ciel.read_rules", "ciel_read_profile": "ciel.read_profile",
                   "ciel_read_preferences": "ciel.read_preferences", "ciel_save_to_daily": "ciel.save_to_daily",
                   "ciel_search_vault": "sakura.vault_search"}  # R5 folds Ciel's vault search into Sakura's


def route(label: str) -> str:
    return label if label.startswith("ciel.") else label.split(".")[0]


def labels_from_plan(plan) -> set:
    """
    The labels _run_mission would execute for a plan from either planner (before or
    after R5), in its order: reset, then appraisal, return early; then vault actions,
    weather and web search (intent OR tool_target), then agents. A plan with an
    explicit `calls` list (R5) takes its agents from calls only. Raises ValueError for
    a missing or malformed plan, so score() counts it as a miss.
    """
    if not isinstance(plan, dict) or not isinstance(plan.get("intent_type"), str) or not plan["intent_type"]:
        raise ValueError(f"no plan (needs an intent_type): {plan!r:.80}")
    intent = plan["intent_type"]
    targets = _str_list(plan, "tool_targets")
    direct = plan.get("direct_obsidian")
    if direct is not None and not isinstance(direct, str):
        raise ValueError(f"direct_obsidian must be a string: {direct!r:.80}")
    if "reset_memory" in targets:  # ciel_orchestrator._run_mission: reset returns first
        return {"ciel.reset"}
    if "proactive_appraisal" in targets or direct == "proactive_appraisal":  # then appraisal
        return {"ciel.appraisal"}
    labels = {label for name, label in OBSIDIAN_LABELS.items() if direct == name or name in targets}
    if intent == "live_weather" or "live_weather" in targets:
        labels.add("ciel.weather")
    if intent == "web_search" or "web_search" in targets:
        labels.add("ciel.web_search")
    if intent == "news" or "news_search" in targets:  # assistant B4
        labels.add("ciel.news")
    if intent == "timer" or {"set_timer", "cancel_timers"} & set(targets):  # assistant B6
        labels.add("ciel.timer")
    if "calls" in plan:
        if not isinstance(plan["calls"], (list, type(None))):
            raise ValueError(f"calls must be a list: {plan['calls']!r:.80}")
        for call in plan["calls"] or []:
            if not (isinstance(call, (list, tuple)) and len(call) == 2 and isinstance(call[0], str) and call[0]
                    and isinstance(call[1], dict)):
                raise ValueError(f"malformed call (needs [name, {{args}}]): {call!r:.80}")
            labels.add(call[0])
    else:
        for agent in _str_list(plan, "agents_needed"):
            mapped = {OLD_TARGETS[t] for t in targets if OLD_TARGETS.get(t, "").startswith(f"{agent}.")}
            labels |= mapped or {agent}
    if not labels and intent == "agent_delegation":
        raise ValueError("a delegation that names no agent and no action is not a direct answer")
    return labels or {"ciel.direct"}


def _str_list(plan: dict, key: str) -> list:
    """A plan's list field: absent or null means empty; anything but non-empty strings is a bad plan."""
    value = plan.get(key)
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise ValueError(f"{key} must be a list of non-empty strings: {value!r:.80}")
    return value


async def score(cases: list, plan_fn) -> dict:
    """plan_fn: async (prompt, history) -> plan dict. A planner error or a bad plan counts as a miss."""
    route_hits = exact_hits = ja = ja_hits = 0
    misses, sources, models, rows = [], {}, {}, []
    for case in cases:
        expect = set(case["expect"])
        try:  # everything that depends on the prediction stays inside: a bad plan is a miss, never a crash
            plan = await plan_fn(case["prompt"], case.get("history", []))
            got = labels_from_plan(plan)
            source, model = _planner_metadata(plan.get("planner"))
            route_hit = {route(l) for l in got} == {route(l) for l in expect}
            exact_hit = got == expect
        except Exception as e:
            got, source, model = {f"error: {type(e).__name__}: {e}"}, "error", "(none)"
            route_hit = exact_hit = False
        sources[source] = sources.get(source, 0) + 1
        models[model] = models.get(model, 0) + 1  # rate limits silently move to backup models
        route_hits += route_hit
        exact_hits += exact_hit
        if case["lang"] == "ja":
            ja += 1
            ja_hits += route_hit
        rows.append({"prompt": case["prompt"], "lang": case["lang"], "expect": sorted(expect), "got": sorted(got),
                     "route": route_hit, "exact": exact_hit, "source": source, "model": model})
        if not route_hit:
            misses.append({"prompt": case["prompt"], "lang": case["lang"],
                           "expect": sorted(expect), "got": ", ".join(sorted(got))})
    n = len(cases)
    return {"n": n, "n_ja": ja, "route": route_hits / n if n else 0.0, "exact": exact_hits / n if n else 0.0,
            "route_ja": ja_hits / ja if ja else None, "misses": misses, "sources": sources,
            "models": models, "cases": rows}


def _planner_metadata(planner) -> tuple:
    """
    (source, model) from a plan's planner metadata. Supplied values are checked
    before any default: only an absent or null planner / source / model gets the
    default ("keyword" / "(none)"); an empty or wrong-typed value is a bad plan.
    """
    if planner is None:
        return "keyword", "(none)"
    if not isinstance(planner, dict):
        raise ValueError(f"planner metadata must be a dict or null: {planner!r:.80}")
    source, model = planner.get("source"), planner.get("model")
    if source is not None and not (isinstance(source, str) and source):
        raise ValueError(f"planner source must be a non-empty string or null: {source!r:.40}")
    if model is not None and not (isinstance(model, str) and model):
        raise ValueError(f"planner model must be a non-empty string or null: {model!r:.40}")
    return source or "keyword", model or "(none)"

def baseline(runs: list) -> dict:
    """The best single baseline run: highest route, then highest Japanese route."""
    best = max(runs, key=lambda r: (r["route"], r["route_ja"] or 0.0))
    return {"route": best["route"], "route_ja": best["route_ja"]}


def passes_gate(runs: list, bar: dict) -> bool:
    """R5 passes if one of exactly two runs clears both thresholds in that same run."""
    if len(runs) != 2:
        raise ValueError(f"the R5 gate takes exactly two runs, got {len(runs)}")
    return any(r["route"] >= bar["route"] and (r["route_ja"] or 0.0) >= bar["route_ja"] for r in runs)


def load_cases(path: Path = CASES) -> list:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def next_results_path(results_dir: Path, day: str) -> Path:
    """<day>-routing-run<k>.md with the first unused k, so runs on one day never overwrite each other."""
    k = 1
    while (results_dir / f"{day}-routing-run{k}.md").exists():
        k += 1
    return results_dir / f"{day}-routing-run{k}.md"


def planner(fallback: bool, pause_s: float):
    """Ciel's real analyze_and_plan; --fallback forces the router call to fail first."""
    if str(HQ) not in sys.path:
        sys.path.insert(0, str(HQ))
    import ciel_orchestrator
    if fallback:
        def offline(*args, **kwargs):
            raise RuntimeError("router forced offline (--fallback)")
        ciel_orchestrator.call_llm = offline  # a CLI run is its own process; nothing to restore
    ciel = ciel_orchestrator.CielOrchestrator()

    async def plan(prompt, history):
        result = await ciel.analyze_and_plan(prompt, history=history)
        if pause_s:
            await asyncio.sleep(pause_s)  # MEM-009: stay under the provider's per-minute limit
        return result
    return plan


def _git_sha() -> str:
    return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=HQ, capture_output=True, text=True).stdout.strip()


def report(s: dict, mode: str, sha: str = None, pause_s: float = 0.0) -> str:
    sha = sha or _git_sha()
    ja = "n/a" if s["route_ja"] is None else f"{s['route_ja']:.3f}"
    lines = [f"# Ciel routing eval — {date.today().isoformat()}", "",
             f"- Planner: {mode} · HQ `{sha}` · pause {pause_s:g} s · cases: {s['n']} ({s['n_ja']} Japanese)",
             f"- **route accuracy: {s['route']:.3f}** (Japanese: {ja}) · exact: {s['exact']:.3f}",
             f"- Planner source per case: {json.dumps(s['sources'], sort_keys=True)}"
             " (\"fallback\" = the router call failed and the keyword router decided)",
             f"- Model that decided each case: {json.dumps(s['models'], sort_keys=True)}"
             " (\"(none)\" = a keyword fast path, the keyword router, or an error)", "",
             "## Every case", "",
             "| Prompt | Lang | Expected | Got | Route | Exact | Source | Model |",
             "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |"]
    yes = {True: "yes", False: "no"}
    lines += [f"| {c['prompt']} | {c['lang']} | {', '.join(c['expect'])} | {', '.join(c['got'])} | "
              f"{yes[c['route']]} | {yes[c['exact']]} | {c['source']} | {c['model']} |" for c in s["cases"]]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--fallback", action="store_true", help="keyword router only; prints, writes nothing")
    ap.add_argument("--pause", type=float, default=None,
                    help=f"seconds between live cases (default {DEFAULT_PAUSE_S:g}; the gate assumes the default)")
    args = ap.parse_args(argv)
    pause = 0.0 if args.fallback else (DEFAULT_PAUSE_S if args.pause is None else args.pause)
    started = time.perf_counter()
    s = asyncio.run(score(load_cases(), planner(args.fallback, pause)))
    text = report(s, "keyword fallback (--fallback)" if args.fallback else "live LLM router", pause_s=pause)
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    print(text)
    print(f"({time.perf_counter() - started:.0f} s)")
    if not args.fallback:
        RESULTS.mkdir(parents=True, exist_ok=True)
        out = next_results_path(RESULTS, date.today().isoformat())
        out.write_text(text, encoding="utf-8")
        print(f"wrote {out.relative_to(HQ)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
