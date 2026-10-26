"""
Chaewon's job hunt (JOB-HUNT-LSFM-SPEC): scout -> filter -> describe -> tailor -> job_package.build.

Each job gets a folder under JOB_HUNT_DIR/applications with a meta.json; the Claude desktop
routine reads those folders and fills the application forms. Rules, profile and seen list
live in JOB_HUNT_DIR (outside the repo) so Hans can edit them.
"""

import json
import os
import re
import shutil
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import job_package
from llm_client import query_llm_structured
from pdf_engine import load_master_resume_context
from web_tools import fetch_jobstreet, fetch_linkedin_description, fetch_linkedin_remote

def default_job_hunt_dir() -> Path:
    """JOB_HUNT_DIR from the environment, else the 'job-hunt' folder beside the projects folder that holds HQ."""
    return Path(os.getenv("JOB_HUNT_DIR") or Path(__file__).resolve().parent.parent.parent / "job-hunt")


JOB_HUNT_DIR = default_job_hunt_dir()

# Title words that rule a job out; the description only rules it out for the ones in DESCRIPTION_SKIPS.
SKIP_WORDS = ["onsite", "on-site", "hybrid", "senior", "sr.", "lead", "principal", "manager", "head of",
              "staff engineer", "loom", "video introduction", "video intro", "must reside", "us only", "us-only",
              "eu only", "virtual assistant", "administrative assistant", "sales", "chief", "architect"]
# A title must name a developer role (first scheduled hunt, 2026-10-15: 8 of 10 cards were accountants,
# recruiters, technicians and admin roles that the broad search terms let in). Matched as whole words.
ROLE_WORDS = ["engineer", "developer", "dev", "programmer", "software", "frontend", "front-end", "front end",
              "full stack", "full-stack", "fullstack", "backend", "back-end", "ai", "ml", "machine learning",
              "llm", "automation", "python", "react", "typescript", "javascript",
              "scientist", "data science"]  # Data / Research Scientist titles were dropped as "not a developer role"
DESCRIPTION_SKIPS = ["loom", "video intro", "video introduction", "must reside", "us only", "eu only"]

# One hunt at a time (the 10:00/20:00 loop and !hunt share it). Every stage checks one budget and CANCEL:
# Sakura sets CANCEL when its 660 s wait gives up, so an abandoned worker stops at its next checkpoint
# instead of writing a run summary nobody will card. A single slow call can still run past the budget;
# it is the checkpoint after it that keeps its result from being written.
HUNT_LOCK = threading.Lock()
HUNT_DEADLINE_S = 480
CANCEL = threading.Event()
_RUN = {"started": None}


class _Stop(Exception):
    """Raised at a checkpoint: the budget is spent or the hunt was cancelled."""


def _checkpoint() -> None:
    if _RUN["started"] is None:  # not inside a hunt (direct calls)
        return
    if CANCEL.is_set():
        raise _Stop("cancelled after Sakura's timeout")
    if time.monotonic() - _RUN["started"] >= HUNT_DEADLINE_S:
        raise _Stop(f"stopped at the {HUNT_DEADLINE_S} s deadline")


def _has_word(text: str, word: str) -> bool:
    """word as a whole word or phrase: 'loom' matches 'a Loom video', not 'Bloomberg'."""
    return re.search(rf"(?<![a-z]){re.escape(word)}(?![a-z])", text.lower()) is not None


def text_skip_reason(text: str) -> str:
    """Skip reason from a posting's own words (run again on the full text once it is read)."""
    for word in DESCRIPTION_SKIPS:
        if _has_word(text or "", word):
            return f"'{word}' in the description"
    return ""


@dataclass
class Rules:
    queries: list = field(default_factory=list)
    skip_words: list = field(default_factory=lambda: list(SKIP_WORDS))
    max_jobs: int = 10
    max_age_days: int = 7


def load_rules(path: Path) -> Rules:
    """rules.md as Rules. Hand edits are fine: unknown lines are ignored, missing numbers keep the defaults."""
    rules, section = Rules(), ""
    for raw in Path(path).read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if line.startswith("#"):
            section = line.lstrip("#").strip().lower()
            continue
        bullet = re.match(r"^(?:[-*+]|\d+[.)])\s+(.*)", line)  # '- x', '* x' or '1. x'
        if section.startswith("roles") and bullet:
            for q in re.split(r"\s+/\s+", bullet.group(1)):  # 'A / B' is two roles; 'AI/ML Engineer' is one
                q = q.strip().lower()
                if q and q not in rules.queries:
                    rules.queries.append(q)
        if section.startswith("limits"):
            m = re.search(r"at most (\d+)", line, re.I)
            if m:
                rules.max_jobs = int(m.group(1))
        m = re.search(r"posted (\d+) days", line, re.I)
        if m:
            rules.max_age_days = int(m.group(1))
    return rules


_UNIT_DAYS = {"minute": 0, "hour": 0, "day": 1, "week": 7, "month": 30}


def parse_posted(raw: str, now: datetime) -> date | None:
    """The posting date from an ISO stamp or 'N days ago' style text; None when it cannot be read."""
    s = (raw or "").strip().lower()
    if not s:
        return None
    try:
        stamp = datetime.fromisoformat(s.upper().replace("Z", "+00:00"))
        # a UTC stamp is read on this PC's day (PHT): 2026-10-15T20:00Z was posted on the 16th here
        return (stamp.astimezone() if stamp.tzinfo else stamp).date()
    except ValueError:
        pass
    if s in ("today", "just now") or s.endswith("just now"):
        return now.date()
    if s == "yesterday":
        return now.date() - timedelta(days=1)
    m = re.search(r"(\d+)\s+(minute|hour|day|week|month)s?\s+ago", s)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        if unit in ("minute", "hour"):
            return (now - timedelta(**{f"{unit}s": n})).date()
        return now.date() - timedelta(days=n * _UNIT_DAYS[unit])
    return None


def keyword_score(job: dict, skills: list) -> int:
    text = f"{job.get('title', '')} {job.get('description', '')}".lower()
    return min(20, sum(1 for s in skills if s.lower() in text))


def _skip_reason(job: dict, rules: Rules, posted: date | None, now: datetime) -> str:
    if posted is None:
        return "no posting date"
    if (now.date() - posted).days > rules.max_age_days:
        return f"older than {rules.max_age_days} days"
    arrangement = (job.get("work_arrangement") or "").strip().lower()
    if arrangement in ("hybrid", "on-site", "onsite"):
        return f"{arrangement} role"
    title = job.get("title") or ""
    for word in rules.skip_words:
        if _has_word(title, word):
            return f"'{word}' in the title"
    if re.search(r"(?<![a-z])mid(?![a-z])(?!\s*-?\s*shift)", title.lower()):  # mid-level, not a mid shift
        return "'mid' in the title"
    if not any(_has_word(title, w) for w in ROLE_WORDS):
        return "not a developer role"
    return text_skip_reason(job.get("description") or "")


def job_pair(company: str, title: str) -> tuple:
    """Company + title as a repost key: a reposted job comes back under a new URL."""
    return " ".join((company or "").lower().split()), " ".join((title or "").lower().split())


def known_pairs(apps: Path) -> set:
    """job_pair of every job that already has a folder in applications/ (any status)."""
    pairs = set()
    for meta in Path(apps).glob("*/meta.json"):
        try:
            m = json.loads(meta.read_text(encoding="utf-8-sig"))
            pairs.add(job_pair(m.get("company", ""), m.get("role", "")))
        except (OSError, ValueError, AttributeError):
            continue
    return pairs


def filter_jobs(jobs: list, rules: Rules, seen: set, now: datetime, skills: list, applied: set = frozenset()) -> tuple:
    """(keep, skipped): skipped jobs gain 'reason', kept jobs gain 'posted' (ISO date). Keeps rules.max_jobs.
    applied: job_pairs that already have a folder, so a repost under a new URL is skipped."""
    keep, skipped, urls, pairs = [], [], set(), set()
    for j in jobs:
        url = j.get("url") or ""
        pair = job_pair(j.get("company"), j.get("title"))
        if url in seen:
            reason = "already seen"
        elif pair in applied:
            reason = "repost of a job already carded"
        elif url in urls or pair in pairs:
            reason = "duplicate of another listing"
        else:
            posted = parse_posted(j.get("posted_raw", ""), now)
            reason = _skip_reason(j, rules, posted, now)
        urls.add(url)
        pairs.add(pair)
        if reason:
            skipped.append({**j, "reason": reason})
        else:
            keep.append({**j, "posted": posted.isoformat(), "_score": keyword_score(j, skills)})
    keep.sort(key=lambda j: ((now.date() - date.fromisoformat(j["posted"])).days > 1, -j["_score"],
                             -date.fromisoformat(j["posted"]).toordinal()))
    for j in keep:
        j.pop("_score")
    return keep[:rules.max_jobs], skipped


def load_seen(path: Path) -> set:
    """URLs already handled: the first '|' field of each non-comment line of seen.txt."""
    try:
        lines = Path(path).read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return set()
    return {ln.split("|")[0].strip() for ln in lines if ln.strip() and not ln.lstrip().startswith("#")}


# ------------------------------------------------------------------------------
# The run: scout -> filter -> describe -> tailor -> build, one folder per job
# ------------------------------------------------------------------------------

def skill_keywords(ctx: dict) -> list:
    """Plain skill names for keyword_score: 'Python 3.11 (asyncio, OOP)' -> 'python', 'React.js' -> 'react'."""
    names = []
    for k in job_package.SKILL_KEYS:
        for item in job_package.skill_items(ctx.get(k, "")):
            name = re.sub(r"\s*\(.*\)", "", item).strip().lower()
            name = re.sub(r"\s+\d[\d.]*$", "", name).removesuffix(".js")
            if name and name not in names:
                names.append(name)
    return names


def folder_name(found: date, company: str, role: str, existing: set) -> str:
    """'<YYYY-MM-DD>_<Company>_<Role>', Windows-safe, at most 80 characters, '_2', '_3'... when taken."""
    base = re.sub(r"_+", "_", re.sub(r"[^A-Za-z0-9]", "_", f"{company}_{role}")).strip("_") or "Job"
    base = f"{found.isoformat()}_{base}"[:80].rstrip("_")
    taken = {e.lower() for e in existing}  # NTFS folder names ignore case
    name, n = base, 2
    while name.lower() in taken:
        suffix = f"_{n}"
        name, n = base[:80 - len(suffix)] + suffix, n + 1
    return name


SOURCES = (("LinkedIn", fetch_linkedin_remote), ("Jobstreet", fetch_jobstreet))
PAUSE_S = {"LinkedIn": 2.0}  # between one site's queries: the guest search throttles bursts


def scout(rules: Rules) -> tuple:
    """(jobs, failed_sources) from LinkedIn (remote filter) and Jobstreet; one source failing never stops the other.
    A source that answers every query with no jobs is listed too: it is usually blocked, not quiet."""
    jobs, failed = [], []
    for site, fetch in SOURCES:
        errors, found = [], 0
        for i, q in enumerate(rules.queries):
            if i and PAUSE_S.get(site):
                CANCEL.wait(PAUSE_S[site])  # a cancel ends the pause at once; the checkpoint then stops the hunt
            _checkpoint()
            try:
                batch = fetch(q)
                found += len(batch)
                jobs.extend(batch)
            except Exception as e:
                errors.append(f"{type(e).__name__} {e}"[:80])
        if errors and len(errors) == len(rules.queries):
            failed.append(f"{site}: {errors[0]}")
        elif rules.queries and not found:
            failed.append(f"{site}: no jobs for any query")
    return jobs, failed


def describe(job: dict) -> tuple:
    """(posting text, thin). LinkedIn's full text comes from the guest posting page; Jobstreet stays thin."""
    if job.get("site") == "LinkedIn":
        try:
            text = fetch_linkedin_description(job["url"])
            if text:
                return text, False
        except Exception as e:
            print(f"⚠️ [job_hunt.describe] suppressed {type(e).__name__}: {e}", flush=True)
        return job.get("description") or f"{job.get('title')} at {job.get('company')}", True
    return job.get("description") or "", bool(job.get("thin", True))


TAILOR_SYSTEM = (
    "You tailor Hans Aaron Laureles's one-page resume and cover letter to one job posting. "
    "Reply with JSON only: {\"fit\": <0-100 int>, \"why\": \"<one sentence>\", \"tailoring\": {...}}. "
    "tailoring may only have these keys: company, role, title, summary, skills_ai, skills_sys, skills_ui, "
    "project_order, cover_paragraphs. Rules: skill lines may only reorder (or leave out) the exact items given, "
    "never add one and never put an item on two lines; project_order is exactly the 4 given project names, most "
    "relevant first; summary is at most 400 characters; cover_paragraphs is 3-4 short paragraphs, the first names "
    "the company and role. Claim nothing that is not in the resume facts given. Hans has a 3-month internship and "
    "no full-time experience: never state years of experience. title is the role family, e.g. Junior AI Engineer."
)


def _tailor_prompt(job: dict, text: str, ctx: dict, errors: list) -> str:
    exp = (ctx.get("experience") or [{}])[0]
    facts = {
        "summary": ctx.get("summary", ""),
        "internship": f"{exp.get('title', '')}, {exp.get('company', '')}, {exp.get('date', '')}",
        "skills_ai": ctx.get("skills_ai", ""), "skills_sys": ctx.get("skills_sys", ""), "skills_ui": ctx.get("skills_ui", ""),
        "projects": [{"name": p.get("name"), "about": p.get("desc", "")} for p in (ctx.get("projects") or [])[:4]],
    }
    prompt = (f"RESUME FACTS (the only facts you may use):\n{json.dumps(facts, ensure_ascii=False, indent=1)}\n\n"
              f"JOB: {job.get('title')} at {job.get('company')} ({job.get('site')})\n{text[:6000]}")
    if errors:
        prompt += "\n\nYour last answer was refused for these reasons; fix them:\n- " + "\n- ".join(errors)
    return prompt


def tailor(job: dict, text: str, ctx: dict) -> tuple:
    """(tailoring, fit, why, errors): one LLM call plus one retry carrying the refusal reasons."""
    errors = []
    for _ in range(2):
        _checkpoint()
        try:
            reply = query_llm_structured(_tailor_prompt(job, text, ctx, errors), TAILOR_SYSTEM, 0.3, agent="chaewon").text
        except Exception as e:
            # llm_client already fell back across Groq and Gemini, so an error here means both are down: no retry.
            return None, 0, "", [f"{type(e).__name__}: {e}"[:200]]
        _checkpoint()  # the call may have outlived the budget or Sakura's wait
        try:
            start, end = reply.find("{"), reply.rfind("}")
            answer = json.loads(reply[start:end + 1])
            tailoring = answer["tailoring"]
            if isinstance(tailoring, dict):
                for k in job_package.SKILL_KEYS:  # models often send a skill line as a list
                    if isinstance(tailoring.get(k), list):
                        tailoring[k] = ", ".join(str(s) for s in tailoring[k])
            errors = job_package.check(tailoring, ctx) if isinstance(tailoring, dict) else ["tailoring is not an object"]
        except (ValueError, KeyError, TypeError) as e:
            errors = [f"reply was not the JSON asked for ({type(e).__name__}: {e})"[:200]]
            continue
        if not errors:
            m = re.search(r"\d+", str(answer.get("fit") or "0"))  # tolerant: 85, "85", "85%"
            fit = max(0, min(100, int(m.group()) if m else 0))
            return tailoring, fit, str(answer.get("why") or "")[:200], []
    return None, 0, "", errors


def write_meta(job_dir: Path, meta: dict) -> None:
    """meta.json written atomically (temp file + os.replace), like daily_once.claim."""
    tmp = Path(job_dir) / "meta.json.tmp"
    tmp.write_text(json.dumps(meta, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, Path(job_dir) / "meta.json")


def _append(path: Path, lines: list) -> None:
    if lines:
        with open(path, "a", encoding="utf-8") as f:
            f.write("".join(f"{ln}\n" for ln in lines))


def uncarded_folders(today: date, days: int = 7) -> list:
    """Job folders still waiting for a Discord card (e.g. finished just before a cancelled run gave up)."""
    folders = []
    for meta_path in sorted((JOB_HUNT_DIR / "applications").glob("*/meta.json")):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            found = date.fromisoformat(meta.get("found", ""))
        except (OSError, ValueError):
            continue
        if meta.get("status") in ("queued", "needs_claude") and not meta.get("card_msg_id") \
                and (today - found).days <= days:
            folders.append(str(meta_path.parent))
    return folders


def hunt(slot: str = ""):
    """One job-hunt run: up to rules.max_jobs folders, a runs/<slot>.json summary, seen.txt updated."""
    from skills import SkillResult  # skills imports this module; importing back lazily avoids the cycle

    if not HUNT_LOCK.acquire(blocking=False):
        # failed, not attention: run_daily must leave the slot unclaimed and retry on the next tick.
        return SkillResult("failed", "Job hunt already running; try again when it finishes.")
    CANCEL.clear()
    _RUN["started"] = time.monotonic()
    try:
        return _hunt(slot, SkillResult)
    finally:
        _RUN["started"] = None
        CANCEL.clear()
        HUNT_LOCK.release()


def _hunt(slot: str, SkillResult):
    now = datetime.now()
    slot = slot or f"manual-{now:%Y%m%d-%H%M}"
    today = now.date()
    try:
        rules = load_rules(JOB_HUNT_DIR / "rules.md")
        if not rules.queries:
            return SkillResult("failed", "Job hunt failed: rules.md lists no roles under '## Roles to search'")
        seen_path = JOB_HUNT_DIR / "seen.txt"
        ctx = load_master_resume_context()
        if "_error" in ctx:
            return SkillResult("failed", f"Job hunt failed: {ctx['_error']}")
        jobs, failed_sources = scout(rules)
        _checkpoint()  # the final query may have outlived the budget or Sakura's wait (Codex re-review F2)
        if len(failed_sources) == len(SOURCES):
            # Nothing reachable (e.g. the PC woke before Wi-Fi): failed, so run_daily retries in 30 min and
            # no runs file tells the Claude run that LSFM covered this slot.
            return SkillResult("failed", f"Job hunt failed: every source failed ({'; '.join(failed_sources)})")
        apps = JOB_HUNT_DIR / "applications"
        apps.mkdir(parents=True, exist_ok=True)
        keep, skipped = filter_jobs(jobs, rules, load_seen(seen_path), now, skill_keywords(ctx), known_pairs(apps))
        existing = {p.name for p in apps.iterdir()}
    except Exception as e:
        return SkillResult("failed", f"Job hunt failed: {type(e).__name__}: {e}")

    folders, stopped, rejected = [], "", set()
    for job in keep:
        d = None
        try:
            _checkpoint()
            text, thin = describe(job)
            _checkpoint()
            reason = text_skip_reason(text)  # the full posting can rule a job out (Loom, must reside, ...)
            if reason:
                skipped.append({**job, "reason": reason})
                rejected.add(job.get("url"))  # remembered in seen.txt, so its posting is not fetched again
                continue
            name = folder_name(today, job.get("company", ""), job.get("title", ""), existing)
            existing.add(name)
            d = apps / name
            meta = {"id": name, "company": job.get("company", ""), "role": job.get("title", ""), "site": job.get("site", ""),
                    "url": job.get("url", ""), "posted": job.get("posted", ""), "found": today.isoformat(), "fit": 0,
                    "why": "", "salary": job.get("salary", ""), "thin": thin, "status": "needs_claude", "card_msg_id": 0,
                    "question": "", "reason": "", "built_by": "lsfm", "updated": now.strftime("%Y-%m-%dT%H:%M")}
            try:
                d.mkdir()
                (d / "posting.txt").write_text(f"{job.get('title')} at {job.get('company')}\n{job.get('url')}\n\n{text}",
                                               encoding="utf-8")
                tailoring, fit, why, errors = tailor(job, text, ctx)
                if tailoring:
                    (d / "tailoring.json").write_text(json.dumps(tailoring, indent=1, ensure_ascii=False), encoding="utf-8")
                    _checkpoint()
                    res = job_package.build(d)
                    errors = [] if res["ok"] else res["errors"]
                    meta.update(fit=fit, why=why)
            except _Stop:
                raise
            except Exception as e:
                # One bad job never stops the run: it goes to Claude with the reason.
                errors = [f"{type(e).__name__}: {e}"]
            _checkpoint()  # nothing is recorded for a job that finished after the budget or a cancel
        except _Stop as stop:
            if d is not None:
                shutil.rmtree(d, ignore_errors=True)  # a half-built job leaves no folder behind
            stopped = str(stop)
            break  # the jobs not done stay out of seen.txt, so the next run picks them up
        if errors:
            meta["reason"] = "; ".join(errors)[:300]
        else:
            meta["status"] = "queued"
        try:
            d.mkdir(exist_ok=True)
            write_meta(d, meta)
        except OSError as e:
            print(f"⚠️ [job_hunt] could not write {name}: {type(e).__name__}: {e}", flush=True)
            continue
        folders.append(str(d))
        _append(seen_path, [f"{job.get('url')} | {today.isoformat()} | {meta['status']}"])  # per job: a crash later loses none

    if CANCEL.is_set() and not stopped:
        # Cancelled after the last checkpoint (e.g. during the final search query, with no job left to tailor).
        stopped = "cancelled after Sakura's timeout"
    if stopped and (CANCEL.is_set() or not folders):
        # Cancelled (Sakura already gave up) or nothing built: failed, no run summary, so the slot retries.
        # Jobs finished before the stop keep their folder; Sakura cards them on the next delivery.
        return SkillResult("failed", f"Job hunt {slot} {stopped}; {len(folders)} job(s) kept for the next delivery")
    # One line per URL; never mark a kept job (built or not reached yet) as skipped.
    keep_urls, skip_lines = {j.get("url") for j in keep} - rejected, {}
    for s in skipped:
        url = s.get("url")
        if s.get("reason") != "already seen" and url not in keep_urls:
            skip_lines.setdefault(url, f"{url} | {today.isoformat()} | skipped")
    _append(seen_path, list(skip_lines.values()))

    skip_rows = [{k: s.get(k, "") for k in ("company", "title", "site", "url", "reason")} for s in skipped]
    runs = JOB_HUNT_DIR / "runs"
    runs.mkdir(exist_ok=True)
    (runs / f"{slot}.json").write_text(json.dumps({"slot": slot, "built": folders, "skipped": skip_rows,
                                                   "failed_sources": failed_sources}, indent=1), encoding="utf-8")
    data = {"jobs": folders, "skipped": skip_rows, "failed_sources": failed_sources, "slot": slot}
    queued = sum(1 for f in folders if json.loads((Path(f) / "meta.json").read_text(encoding="utf-8"))["status"] == "queued")
    text = f"Job hunt {slot}: {queued} queued, {len(folders) - queued} for Claude, {len(skipped)} skipped"
    if failed_sources:
        text += f"; failed: {', '.join(failed_sources)}"
    if stopped:
        text += f"; {stopped}"
    return SkillResult("done" if folders else "attention", text, data)
