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

import importlib
import py_compile
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
# Transitional (ADR-001 R1-R4): today's planner names agents, not skills.
# legacy_calls reproduces the keyword pick Ciel's _agent_job made for each migrated
# agent. R5's planner returns skills itself and this goes away.
# ==============================================================================

def legacy_calls(agent: str, user_prompt: str, tool_targets: list) -> list:
    prompt = user_prompt.lower()
    if agent == "eunchae":
        wants_tests = (any("test" in str(t).lower() or "compile" in str(t).lower() for t in tool_targets or [])
                       or any(w in prompt for w in ("test", "compile", "syntax", "smoke")))
        return [("eunchae.compile_all", {})] if wants_tests else [("eunchae.vitals", {})]
    return []  # not migrated yet: Ciel's _agent_job still handles it