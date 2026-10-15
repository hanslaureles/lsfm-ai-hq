"""
skills.py - one registry of what each agent can do (ADR-001 R1).

The Discord bots and Ciel both call skills from here, so a fix lands once and both
front doors get it. A skill wraps one engine call and turns its raw result into an
honest SkillResult:
  done          - it ran and the result is clean
  attention     - it ran and found problems
  failed        - it could not run (missing engine, exception, timeout)
  needs_confirm - it changes things and was not confirmed; nothing ran
A failure is never reworded as success.
"""

import asyncio
import importlib
import py_compile
import threading
from dataclasses import dataclass, field
from pathlib import Path, PurePath, PureWindowsPath
from typing import Callable

from heavy_jobs import run_blocking

HQ_DIR = Path(__file__).resolve().parent
WORKSPACE_DIR = HQ_DIR.parent  # the projects folder that holds HQ and its sibling repos (eunchae_engine's WORKSPACE_DIR)


def _optional(module: str, name: str):
    """An engine function, or None when its module can't be imported (the skill then reports failed)."""
    try:
        return getattr(importlib.import_module(module), name)
    except (ImportError, AttributeError):
        return None


get_system_vitals = _optional("eunchae_engine", "get_system_vitals")
test_code_file = _optional("eunchae_engine", "test_code_file")
get_git_info = _optional("git_sentinel", "get_git_info")
get_git_diff = _optional("git_sentinel", "get_git_diff")
scan_security_risks = _optional("git_sentinel", "scan_security_risks")
kazuha_review_changes = _optional("kazuha_engine", "kazuha_review_changes")
ask_knowledge_base = _optional("rag_engine", "ask_knowledge_base")
compile_master_resume = _optional("pdf_engine", "compile_master_resume")
fetch_live_remote_jobs = _optional("web_tools", "fetch_live_remote_jobs")
scan_morning_inbox = _optional("sakura_engine", "scan_morning_inbox")
ObsidianClient = _optional("obsidian_client", "ObsidianClient")
audit_full_portfolio = _optional("yunjin_engine", "audit_full_portfolio")

# Worst first: combine() keeps the worst status of a set of results.
STATUS_ORDER = ("failed", "needs_confirm", "attention", "done")


@dataclass(frozen=True)
class SkillResult:
    status: str
    text: str
    data: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Skill:
    name: str                                  # "<agent>.<action>", e.g. "eunchae.vitals"
    agent: str                                 # one of the five bots
    desc: str                                  # one line, shown to Ciel's planner (R5)
    run: Callable[..., SkillResult]            # blocking; takes the args below as keywords
    args: dict = field(default_factory=dict)   # arg name -> description; every arg is an optional string
    keywords: tuple = ()                       # words for the keyword fallback router (R5)
    changes_things: bool = False               # True: run_skill refuses until confirmed (R4)
    confirm_text: str = ""                     # what the confirmation asks
    timeout_s: float = 30.0
    heavy: bool = False                        # run on heavy_jobs.HEAVY_POOL


SKILLS: dict = {}


def register(skill: Skill) -> Skill:
    if not skill.name.startswith(f"{skill.agent}."):
        raise ValueError(f"skill {skill.name!r} must start with its agent {skill.agent!r}")
    if skill.name in SKILLS:
        raise ValueError(f"skill {skill.name!r} is already registered")
    SKILLS[skill.name] = skill
    return skill


def validate_call(name: str, args) -> tuple:
    """(skill, args) for a call; ValueError for an unknown skill, an unknown arg or a non-string value."""
    if not isinstance(name, str) or not name:
        raise ValueError(f"a skill name must be a non-empty string, got {name!r:.40}")
    skill = SKILLS.get(name)
    if skill is None:
        raise ValueError(f"unknown skill {name!r}")
    if args is None:
        return skill, {}
    if not isinstance(args, dict):
        raise ValueError(f"{name}: args must be a mapping, got {type(args).__name__}")
    for key, value in args.items():
        if key not in skill.args:
            raise ValueError(f"{name}: unknown arg {key!r}")
        if not isinstance(value, str):
            raise ValueError(f"{name}: arg {key!r} must be a string, got {type(value).__name__}")
    return skill, dict(args)


def run_skill(name: str, args: dict = None, *, confirmed: bool = False) -> SkillResult:
    """Runs one skill (blocking). Never raises: every problem is a failed SkillResult."""
    agent = name.split(".")[0].capitalize() if isinstance(name, str) and name else "Skill"
    try:
        skill, args = validate_call(name, args)
    except ValueError as e:
        return SkillResult("failed", f"{agent} failed: {e}")
    if skill.changes_things and not confirmed:
        return SkillResult("needs_confirm", skill.confirm_text or f"Run {name}?")
    try:
        result = skill.run(**args)
    except Exception as e:
        return SkillResult("failed", f"{agent} failed: {type(e).__name__}: {e}")
    if not (isinstance(result, SkillResult) and result.status in STATUS_ORDER
            and isinstance(result.text, str) and isinstance(result.data, dict)):
        # The full contract is checked here, so combine() and the callers never see a bad result.
        return SkillResult("failed", f"{agent} failed: {name} returned no valid result")
    return result


def combine(results: list) -> SkillResult:
    """One result for an agent's calls: the worst status wins; texts are joined in order."""
    if not results:
        return SkillResult("failed", "no skill ran")
    status = min((r.status for r in results), key=STATUS_ORDER.index)
    return SkillResult(status, " | ".join(r.text for r in results), {"results": [r.data for r in results]})


async def run_calls(calls: list, *, confirmed: bool = False) -> SkillResult:
    """
    Runs (name, args) calls one after another off the event loop, each under its
    skill's timeout and pool, and combines them. A timeout is a failed result: the
    thread cannot be stopped, so the work may still finish in the background.
    """
    if not isinstance(calls, (list, tuple)):
        return SkillResult("failed", f"no valid calls: expected a list of (name, args), got {type(calls).__name__}")
    results = []
    for call in calls:  # each call is untrusted (it may come from an LLM plan): a bad one is a failed result
        if not (isinstance(call, (list, tuple)) and len(call) == 2):
            results.append(SkillResult("failed", f"malformed call (expected [name, args]): {call!r:.60}"))
            continue
        name, args = call
        skill = SKILLS.get(name) if isinstance(name, str) else None
        if skill is None:
            results.append(run_skill(name, args))  # the failed "unknown / invalid skill" result
            continue
        try:
            results.append(await run_blocking(run_skill, name, args, confirmed=confirmed,
                                              timeout=skill.timeout_s, label=skill.agent.capitalize(),
                                              heavy=skill.heavy))
        except TimeoutError:
            results.append(SkillResult("failed", f"{skill.agent.capitalize()} failed: timed out after "
                                                 f"{skill.timeout_s:g} s; it may still finish in the background"))
        except Exception as e:  # the pool itself failed (e.g. shut down): still an honest result
            results.append(SkillResult("failed", f"{skill.agent.capitalize()} failed: {type(e).__name__}: {e}"))
    return combine(results)


# ==============================================================================
# Eunchae (System Guardian)
# ==============================================================================

def _eunchae_vitals() -> SkillResult:
    if get_system_vitals is None:
        return SkillResult("failed", "Hardware vitals unavailable: eunchae_engine could not be imported.")
    v = get_system_vitals()
    healthy = v.get("healthy")
    state = "Nominal 🟢" if healthy else "High Load ⚠️"
    text = (f"Hardware Vitals: CPU {v.get('cpu_pct')}% ({v.get('cpu_count')} Cores) | "
            f"RAM {v.get('ram_used_gb')}GB / {v.get('ram_total_gb')}GB ({v.get('ram_pct')}%) | "
            f"Storage {v.get('disk_free_gb')}GB free | Uptime {v.get('uptime_str')}. State: {state}.")
    return SkillResult("done" if healthy else "attention", text, v)


def _eunchae_compile_all() -> SkillResult:
    py_files = sorted(HQ_DIR.glob("*.py"))
    errors = []
    for pf in py_files:
        try:
            py_compile.compile(str(pf), doraise=True)
        except Exception as e:
            errors.append((pf.name, str(e)))
    if errors:
        return SkillResult("attention", f"System QA check: Smoke test flagged {len(errors)} compilation error(s): "
                                        f"{errors[0][0]} - {errors[0][1]}.", {"errors": errors})
    return SkillResult("done", f"System QA check: Compilation verification passed across {len(py_files)} Python "
                               f"scripts. 0 syntax errors detected.", {"files": len(py_files)})


TESTABLE_SUFFIXES = (".py", ".html", ".css", ".js", ".jsx", ".ts", ".tsx")  # what test_code_file checks


def _safe_relative_source(raw: str):
    """
    The path as given if it is a relative path with no `..` to a file type test_code_file checks, else None. It may
    come from an LLM plan, and test_code_file resolves absolute paths and can run the file.
    """
    clean = (raw or "").strip().strip("`'\" ")
    if not clean:
        return None
    for flavour in (PureWindowsPath(clean), PurePath(clean)):
        if flavour.anchor or flavour.is_absolute() or ".." in flavour.parts:
            return None
    return clean if clean.lower().endswith(TESTABLE_SUFFIXES) else None


def _resolve_in_workspace(clean: str):
    """
    The exact file `clean` names, looked up under the workspace and then under HQ (never the
    working directory, never by basename elsewhere). The *resolved* path, after symlinks and
    junctions, must stay inside the workspace. A failed SkillResult otherwise.
    """
    workspace = WORKSPACE_DIR.resolve()
    for base in (WORKSPACE_DIR, HQ_DIR):
        candidate = (base / clean).resolve()
        if not candidate.is_relative_to(workspace):
            return SkillResult("failed", f"Code QA refused: {clean} resolves outside the workspace.")
        if candidate.is_file():
            return candidate
    return SkillResult("failed", f"Code QA: {clean} not found in the workspace or HQ "
                                 f"(give the path from the projects folder, e.g. agent-memory/test_memory.py).")


def _eunchae_test(path: str = "") -> SkillResult:
    clean = _safe_relative_source(path)
    if clean is None:
        return SkillResult("failed", "Code QA refused: give a relative path inside the workspace to a .py/.html/.css/.js/.ts file "
                                     "(for example git_sentinel.py or agent-memory/test_memory.py).")
    if test_code_file is None:
        return SkillResult("failed", "Code QA unavailable: eunchae_engine could not be imported.")
    target = _resolve_in_workspace(clean)
    if isinstance(target, SkillResult):
        return target
    # An absolute, existing, contained path: the engine uses it as is, so its cwd lookup and
    # its basename search (rglob) never run (Codex R1 fix 3).
    res = test_code_file(str(target))
    if not res.get("success"):
        return SkillResult("failed", f"Code QA failed: {res.get('error', 'unknown error')}", res)
    status = "done" if res.get("passed") and not res.get("warnings") else "attention"
    return SkillResult(status, f"Code QA {res.get('filename', clean)}: {res.get('verdict', 'INCONCLUSIVE')} "
                               f"(score {res.get('score', 0)}/100).", res)


register(Skill(name="eunchae.vitals", agent="eunchae", desc="CPU, RAM, free disk and uptime of the host PC",
               run=_eunchae_vitals, keywords=("vitals", "cpu", "ram", "hardware")))
register(Skill(name="eunchae.compile_all", agent="eunchae", desc="Syntax-check every top-level HQ Python file",
               run=_eunchae_compile_all, keywords=("syntax", "compile", "smoke")))
register(Skill(name="eunchae.test", agent="eunchae",
               desc="Code QA on one workspace file (.py: compile, AST, imports, unit tests; web files: static checks)",
               run=_eunchae_test, args={"path": "relative path inside the workspace, e.g. git_sentinel.py"},
               keywords=("test",), timeout_s=60.0))


# ==============================================================================
# Engine results (Codex R2 fix 1): a successful skill claims only what the engine
# dict backs, and its data carries every field the Discord embeds read.
# ==============================================================================

def _bad_fields(res, spec: dict) -> list:
    """The keys of `spec` (key -> type) that `res` lacks or holds with the wrong type (True is not an int here)."""
    if not isinstance(res, dict):
        return ["<not a dict>"]
    return [key for key, typ in spec.items()
            if key not in res or not isinstance(res[key], typ) or (isinstance(res[key], bool) and typ is not bool)]


def _incomplete(label: str, bad: list, res=None) -> SkillResult:
    return SkillResult("failed", f"{label}: the engine returned an incomplete result (bad or missing: "
                                 f"{', '.join(bad)}).", res if isinstance(res, dict) else {})


def _score_ok(score) -> bool:
    return isinstance(score, int) and not isinstance(score, bool) and 0 <= score <= 100


# ==============================================================================
# Kazuha (Code & Knowledge)
# ==============================================================================

GIT_INFO_FIELDS = {"branch": str, "is_clean": bool, "staged": list, "unstaged": list, "untracked": list,
                   "total_commits": int}


def _kazuha_git_status() -> SkillResult:
    if get_git_info is None or get_git_diff is None or scan_security_risks is None:
        return SkillResult("failed", "Git review unavailable: git_sentinel could not be imported.")
    try:
        info = get_git_info()
        bad = _bad_fields(info, GIT_INFO_FIELDS)
        if bad:
            return _incomplete("Git review", bad)
        diff = get_git_diff()
        if not isinstance(diff, str):
            return _incomplete("Git review", ["diff"])
        risks = scan_security_risks(diff) if diff else []
        if not isinstance(risks, list):  # only a real list can back "0 security risks"
            return _incomplete("Git review", ["security scan"])
    except Exception as e:
        return SkillResult("failed", f"Git review: git status failed: {e}")
    status = "attention" if risks else "done"
    risk_notice = f" ⚠️ {len(risks)} security risk(s) in diff!" if risks else " 0 security risks in diff."
    data = {"info": info, "risks": risks}
    if info.get("is_clean"):
        return SkillResult(status, f"Git review: Working tree clean on branch '{info.get('branch', 'main')}' "
                                   f"({info.get('total_commits', 0)} commits).{risk_notice}", data)
    return SkillResult(status, f"Git review: Branch '{info.get('branch', 'main')}' | Staged: {len(info.get('staged', []))} | "
                               f"Unstaged: {len(info.get('unstaged', []))} | Untracked: {len(info.get('untracked', []))}."
                               f"{risk_notice}", data)


REVIEW_FIELDS = {"score": int, "verdict": str, "security_alerts": list, "review": str, "info": dict}
AUDIT_FIELDS = {"broken_images_count": int, "broken_images": list, "report": str, "saved_file": str, "filename": str}


def _kazuha_review(scope: str = "all") -> SkillResult:
    if scope not in ("all", "staged"):
        return SkillResult("failed", f"Code review refused: scope must be 'all' or 'staged', got {scope!r:.40}.")
    if kazuha_review_changes is None:
        return SkillResult("failed", "Code review unavailable: kazuha_engine could not be imported.")
    res = kazuha_review_changes(staged_only=scope == "staged")
    if not isinstance(res, dict):
        return _incomplete("Code review", ["<not a dict>"])
    if not res.get("success"):
        return SkillResult("failed", f"Code review failed: {res.get('error', 'unknown error')}", res)
    bad = _bad_fields(res, REVIEW_FIELDS)
    if not bad:
        bad = [k for k, ok in (("score", _score_ok(res["score"])),
                               ("info.branch", isinstance(res["info"].get("branch"), str))) if not ok]
    if bad:
        return _incomplete("Code review", bad, res)
    alerts = res["security_alerts"]
    text = f"Code review ({scope} changes): {res['verdict']}, score {res['score']}%."
    if alerts:
        text += f" ⚠️ {len(alerts)} security alert(s)."
    # The engine's own bar (git_sentinel): 90+ is READY TO COMMIT; below is CAUTION or BLOCKED (Codex R2 fix 2).
    return SkillResult("attention" if alerts or res["score"] < 90 else "done", text, res)


def _kazuha_ask(question: str = "") -> SkillResult:
    if not question.strip():
        return SkillResult("failed", "Knowledge Q&A refused: no question given.")
    if ask_knowledge_base is None:
        return SkillResult("failed", "Knowledge Q&A unavailable: rag_engine could not be imported.")
    res = ask_knowledge_base(question)
    bad = _bad_fields(res, {"answer": str, "citations": list})
    if not bad:  # the embed joins citations as text
        bad = [k for k, ok in (("answer", res["answer"].strip()),
                               ("citations", all(isinstance(c, str) for c in res["citations"]))) if not ok]
    if bad:
        return _incomplete("Knowledge Q&A", bad)
    answer = res["answer"]
    data = {"answer": answer, "citations": res["citations"]}
    # "⚠️" is the engine's empty or unindexed vault reply: nothing was looked up.
    return SkillResult("attention" if answer.startswith("⚠️") else "done", answer, data)


register(Skill(name="kazuha.git_status", agent="kazuha",
               desc="Git branch, staged/unstaged/untracked counts and a secret scan of the diff",
               run=_kazuha_git_status, keywords=("git status", "repo status", "git branch", "secret leak")))
register(Skill(name="kazuha.review", agent="kazuha", desc="LLM code and security review of the uncommitted git diff",
               run=_kazuha_review, args={"scope": "'all' (default) or 'staged'"},
               keywords=("review", "git diff"), timeout_s=90.0))
register(Skill(name="kazuha.ask", agent="kazuha", desc="Answer a question from the indexed codebase and notes, with citations",
               run=_kazuha_ask, args={"question": "the question, in full"}, keywords=("knowledge base",), timeout_s=60.0))


# ==============================================================================
# Yunjin (Document Architect & Portfolio Critic)
# ==============================================================================

def _yunjin_audit() -> SkillResult:
    if audit_full_portfolio is None:
        return SkillResult("failed", "Portfolio audit unavailable: yunjin_engine could not be imported.")
    res = audit_full_portfolio()
    if not isinstance(res, dict):
        return _incomplete("Portfolio audit", ["<not a dict>"])
    if not res.get("success"):
        return SkillResult("failed", f"Portfolio audit failed: {res.get('error', 'unknown error')}", res)
    bad = _bad_fields(res, AUDIT_FIELDS)
    if not bad and res["broken_images_count"] < 0:
        bad = ["broken_images_count"]
    # An explicit None means the critique had no score (attention below); a missing or out-of-range score is bad.
    if "score" not in res or (res["score"] is not None and not _score_ok(res["score"])):
        bad.append("score")
    if bad:
        return _incomplete("Portfolio audit", bad, res)
    score, broken = res["score"], res["broken_images_count"]
    if score is None:  # the critique gave no score; never treat that as a pass
        return SkillResult("attention", f"Portfolio audit: no score in the critique, {broken} broken image(s).", res)
    status = "attention" if broken > 0 or score < 90 else "done"
    return SkillResult(status, f"Portfolio audit: score {score}/100, {broken} broken image(s).", res)


register(Skill(name="yunjin.audit", agent="yunjin",
               desc="Audit every portfolio page: broken images, meta tags, and an LLM design critique with a score",
               run=_yunjin_audit, keywords=("scan portfolio", "audit portfolio", "broken links", "portfolio audit"),
               timeout_s=90.0))


# ==============================================================================
# Chaewon (Career Strategist), ADR-001 R3
# ==============================================================================

# Serializes resume builds in this process. A threading.Lock held by the build thread itself:
# a build whose await was abandoned on timeout keeps it until it really ends, so no second
# build can overwrite the PDF meanwhile (moved from ciel_orchestrator).
RESUME_BUILD_LOCK = threading.Lock()


def _chaewon_rebuild_resume() -> SkillResult:
    if compile_master_resume is None:
        return SkillResult("failed", "Career & ATS Engine unavailable: pdf_engine could not be imported.")
    if not RESUME_BUILD_LOCK.acquire(blocking=False):
        return SkillResult("failed", "Career & ATS Engine: an earlier resume build is still running; "
                                     "this one was not started.")
    try:
        res = compile_master_resume(archive=True, sync_portfolio=True, sync_obsidian=True)
    except Exception as e:
        return SkillResult("failed", f"Career & ATS Engine: resume compilation raised an error: {e}")
    finally:
        RESUME_BUILD_LOCK.release()
    if not isinstance(res, dict):
        return _incomplete("Career & ATS Engine", ["<not a dict>"])
    if res.get("success"):
        bad = _bad_fields(res, REBUILD_FIELDS)  # what "recompiled (N KB)" and the Discord reply need (Codex R3 F2)
        if bad:
            return _incomplete("Career & ATS Engine", bad, res)
        return SkillResult("done", f"Career & ATS Engine: Master resume PDF recompiled ({res['size_kb']} KB); "
                                   f"portfolio-site copy: {res.get('portfolio_sync', 'skipped')}.", res)
    # The renderer writes into the existing PDF, so nothing guarantees the old one survived (Codex R3 F3).
    return SkillResult("failed", f"Career & ATS Engine: resume compilation failed: {res.get('error', 'unknown error')}. "
                                 f"The resume PDF may be incomplete; rebuild it before sending it anywhere.", res)


REBUILD_FIELDS = {"pdf_path": Path, "size_kb": (int, float)}
SCOUT_CARD_FIELDS = {"title": str, "company": str, "url": str, "score": (int, float)}


def _chaewon_scout(query: str = "applied AI engineer", location: str = "Remote") -> SkillResult:
    if fetch_live_remote_jobs is None:
        return SkillResult("failed", "Job scouting unavailable: web_tools could not be imported.")
    try:
        # Runs in a worker thread (run_calls / run_in_executor), which has no event loop of its own.
        jobs = asyncio.run(fetch_live_remote_jobs(query=query, location=location, limit=5))
    except Exception as e:
        return SkillResult("failed", f"Job scouting failed: {type(e).__name__}: {e}")
    if not isinstance(jobs, list):
        return _incomplete("Job scouting", ["jobs"])
    for i, job in enumerate(jobs):  # every card, not only the three Ciel shows: Discord renders them all
        bad = _bad_fields(job, SCOUT_CARD_FIELDS)
        if not bad:
            bad = [k for k in ("title", "company", "url") if not job[k].strip()]
        if bad:
            return _incomplete("Job scouting", [f"role {i + 1}: {', '.join(bad)}"])
    if not jobs:
        return SkillResult("attention", f"Job scouting: no live roles found for '{query}' ({location}).",
                           {"jobs": [], "query": query, "location": location})
    lines = [f"{j.get('title', '?')} at {j.get('company', '?')} ({j.get('geo', location)}, "
             f"{j.get('salary', 'Competitive')}) {j.get('url', '')}" for j in jobs[:3]]
    return SkillResult("done", f"Job scouting: {len(jobs)} role(s) for '{query}' ({location}). Top: " + "; ".join(lines),
                       {"jobs": jobs, "query": query, "location": location})


register(Skill(name="chaewon.rebuild_resume", agent="chaewon",
               desc="Rebuild the master resume PDF and sync the portfolio and Obsidian copies",
               run=_chaewon_rebuild_resume, keywords=("resume", "cv"), timeout_s=60.0, heavy=True))
register(Skill(name="chaewon.scout", agent="chaewon", desc="Find a few live job postings that fit Hans's profile",
               run=_chaewon_scout, args={"query": "role to search, e.g. applied AI engineer",
                                         "location": "Remote or a place, e.g. Philippines"},
               keywords=("scout", "jobs", "roles"), timeout_s=45.0))


# ==============================================================================
# Sakura (Chief of Staff), ADR-001 R3
# ==============================================================================

INBOX_FIELDS = {"ok": bool, "unread_count": int, "job_alerts": list, "highlights": list}
_VAULT = None


def _vault():
    """The Obsidian client for Sakura's skills, created once (each ObsidianClient re-reads its config)."""
    global _VAULT
    if _VAULT is None:
        if ObsidianClient is None:
            raise ConnectionError("obsidian_client could not be imported")
        _VAULT = ObsidianClient()
    return _VAULT


def _sakura_inbox() -> SkillResult:
    if scan_morning_inbox is None:
        return SkillResult("failed", "Gmail Inbox Triage unavailable: sakura_engine could not be imported.")
    try:
        inbox = scan_morning_inbox()
    except Exception as e:
        return SkillResult("failed", f"Gmail Inbox Triage failed: {type(e).__name__}: {e}")
    if isinstance(inbox, dict) and inbox.get("ok") is False:
        # Not configured / offline / error also report 0 unread; that must not read as a clean inbox.
        return SkillResult("failed", f"Gmail Inbox Triage failed: {inbox.get('status', 'unknown error')}.", inbox)
    bad = _bad_fields(inbox, INBOX_FIELDS)
    if not bad and inbox["unread_count"] < 0:
        bad = ["unread_count"]  # a negative count is not a clean inbox (Codex R3 F2)
    if bad:
        return _incomplete("Gmail Inbox Triage", bad, inbox)
    unread, alerts, highlights = inbox["unread_count"], inbox["job_alerts"], inbox["highlights"]
    if unread > 0:
        alert_info = f" ({len(alerts)} recruiter inquiry)" if alerts else ""
        top = f" Top: {highlights[0]}" if highlights else ""
        return SkillResult("done", f"Gmail Inbox Triage: {unread} unread message(s) in Primary{alert_info}.{top}", inbox)
    return SkillResult("done", "Gmail Inbox Triage: Primary inbox clean, 0 unread messages.", inbox)


def _sakura_priorities() -> SkillResult:
    try:
        vault = _vault()
        path = vault.ensure_daily_log()
        content = vault.get_file(path)
    except Exception as e:
        return SkillResult("failed", f"Obsidian daily log could not be read: {e}")
    open_tasks = [ln.strip().lstrip("- [ ]").strip() for ln in (content or "").splitlines() if "- [ ]" in ln][:3]
    if open_tasks:
        return SkillResult("done", f"Obsidian: today's log '{path}' loaded. Priorities: {'; '.join(open_tasks)}.",
                           {"path": path, "priorities": open_tasks})
    return SkillResult("done", f"Obsidian: today's log '{path}' loaded; no open priorities.",
                       {"path": path, "priorities": []})


def _sakura_vault_search(query: str = "") -> SkillResult:
    if not query.strip():
        return SkillResult("failed", "Obsidian Search: no search words given.")
    try:
        vault = _vault()
        # The client returns [] when REST is offline and the vault folder is missing: that is no search,
        # not "0 matches" (Codex R3 F1). Either access path working means [] is a real empty result.
        vault_dir = getattr(vault, "vault_path", None)
        if not vault.is_rest_api_online() and not (vault_dir is not None and Path(vault_dir).is_dir()):
            return SkillResult("failed", "Obsidian Search unavailable: the REST API is offline and the vault folder "
                                         f"was not found ({vault_dir}).")
        matches = vault.search_simple(query)
    except Exception as e:
        return SkillResult("failed", f"Obsidian Search failed: {e}")
    if not isinstance(matches, list):
        return _incomplete("Obsidian Search", ["matches"])
    if matches:
        names = ", ".join(str(m.get("filename", "")) if isinstance(m, dict) else str(m) for m in matches[:3])
        return SkillResult("done", f"Obsidian Search: {len(matches)} matching note(s): {names}.", {"matches": matches})
    return SkillResult("done", "Obsidian Search: 0 matching notes found.", {"matches": []})


register(Skill(name="sakura.inbox", agent="sakura", desc="Count unread Primary Gmail and flag recruiter mail (read only)",
               run=_sakura_inbox, keywords=("inbox", "email", "gmail", "unread", "mail"), heavy=True))
register(Skill(name="sakura.priorities", agent="sakura", desc="Today's open tasks from the Obsidian daily log",
               run=_sakura_priorities, keywords=("priority", "priorities", "today", "schedule")))
register(Skill(name="sakura.vault_search", agent="sakura", desc="Search the Obsidian vault for matching notes",
               run=_sakura_vault_search, args={"query": "words to search for"},
               keywords=("search", "find", "lookup")))


# ==============================================================================
# Chaewon: job hunt (JOB-HUNT-LSFM-PLAN Task 4)
# ==============================================================================

def _chaewon_hunt(slot: str = "") -> SkillResult:
    try:
        job_hunt = importlib.import_module("job_hunt")
    except ImportError as e:
        return SkillResult("failed", f"Job hunt failed: {e}")
    return job_hunt.hunt(slot=slot)


# changes_things=False: it only writes files under the job-hunt folder and never applies anywhere,
# so the 10:00/20:00 loop never meets the R4 confirm gate.
register(Skill(name="chaewon.hunt", agent="chaewon",
               desc="Find fresh remote junior jobs, tailor a resume and cover letter for each, and queue them for review",
               run=_chaewon_hunt, args={"slot": "run label such as 2026-10-15-am"},
               keywords=("hunt", "job hunt", "find jobs"), timeout_s=600.0, heavy=True))


# ==============================================================================
# Transitional (ADR-001 R1-R4): today's planner names agents, not skills.
# legacy_calls reproduces the keyword pick Ciel's old hand-written agent jobs made (deleted
# in R3). R5's planner returns skills itself and this goes away.
# ==============================================================================

def legacy_calls(agent: str, user_prompt: str, tool_targets: list) -> list:
    prompt = user_prompt.lower()
    if agent == "eunchae":
        wants_tests = (any("test" in str(t).lower() or "compile" in str(t).lower() for t in tool_targets or [])
                       or any(w in prompt for w in ("test", "compile", "syntax", "smoke")))
        return [("eunchae.compile_all", {})] if wants_tests else [("eunchae.vitals", {})]
    if agent == "kazuha":
        return [("kazuha.review", {})] if any(w in prompt for w in ("review", "diff")) else [("kazuha.git_status", {})]
    if agent == "yunjin":
        return [("yunjin.audit", {})]
    if agent == "chaewon":
        # Never chaewon.hunt: a 10-minute job that posts Discord cards (R3 ruling).
        wants_resume = (any("resume" in str(t).lower() for t in tool_targets or [])
                        or any(w in prompt for w in ("resume", " cv", "pdf")))
        if not wants_resume and any(w in prompt for w in ("job", "scout", "role", "hiring", "opening", "vacanc")):
            return [("chaewon.scout", {})]
        return [("chaewon.rebuild_resume", {})]
    if agent == "sakura":
        if any(w in prompt for w in ("inbox", "email", "gmail", "unread", "recruiter", "mail")):
            return [("sakura.inbox", {})]
        if any(w in prompt for w in ("search", "find", "lookup")):
            return [("sakura.vault_search", {"query": user_prompt})]
        return [("sakura.priorities", {})]
    return []