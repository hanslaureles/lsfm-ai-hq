"""
Proactive Sentinel Engine for Manas: Ciel
Monitors operational reality across hardware, repository git status,
Obsidian Second Brain daily priorities, and portfolio integrity.
Generates ranked, autonomous suggestions for Hans without prompting.
"""

import os
import sys
import subprocess
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent
WORKSPACE_DIR = BASE_DIR.parent
PORTFOLIO_DIR = WORKSPACE_DIR / "portfolio-site"
GIT_REPO_DIR = BASE_DIR  # D6: watch the HQ repo; WORKSPACE_DIR (Projects/) is not a git repo

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

try:
    from eunchae_engine import get_system_vitals
except ImportError:
    get_system_vitals = None

try:
    from obsidian_client import ObsidianClient
except ImportError:
    ObsidianClient = None


def check_failed(check: str, category: str, agent: str, error) -> dict:
    """
    A check that could not run is reported, never dropped: an empty result must
    mean every check ran and found nothing, not that one silently errored.
    """
    print(f"⚠️ [Sentinel] {check} check error: {error}", flush=True)
    return {
        "id": f"{check.lower()}_check_failed",
        "severity": "warning",
        "agent": agent,
        "category": category,
        "title": f"{check} Check Failed",
        "description": f"The {check.lower()} check could not run: {error}",
        "action_label": "Dismiss",
    }


def check_hardware_sentinel() -> list[dict]:
    """Inspects PC vitals and flags resource bottlenecks."""
    suggestions = []
    if not get_system_vitals:
        return [check_failed("Hardware", "HARDWARE VITALS", "eunchae", "eunchae_engine is not importable")]

    try:
        vitals = get_system_vitals()
        cpu_pct = vitals.get("cpu_pct", 0)
        ram_pct = vitals.get("ram_pct", 0)
        ram_used = vitals.get("ram_used_gb", 0)
        ram_total = vitals.get("ram_total_gb", 0)
        disk_free = vitals.get("disk_free_gb", 100)

        if cpu_pct > 85:
            suggestions.append({
                "id": "hw_cpu_spike",
                "severity": "warning",
                "agent": "eunchae",
                "category": "HARDWARE VITALS",
                "title": f"High CPU Load ({cpu_pct}%)",
                "description": f"Sustained processor utilization at {cpu_pct}%. Suggesting background process triage.",
                "action_label": "Triage Vitals",
                "action_prompt": "Eunchae, inspect active processes and hardware vitals."
            })

        if ram_pct > 88:
            suggestions.append({
                "id": "hw_ram_pressure",
                "severity": "warning",
                "agent": "eunchae",
                "category": "MEMORY PRESSURE",
                "title": f"Memory Headroom Critical ({ram_used}/{ram_total} GB)",
                "description": f"System RAM usage at {ram_pct}%. Clearing dormant task caches recommended.",
                "action_label": "Optimize Memory",
                "action_prompt": "Eunchae, sample hardware vitals and prune memory caches."
            })

        if disk_free < 20:
            suggestions.append({
                "id": "hw_disk_low",
                "severity": "warning",
                "agent": "eunchae",
                "category": "STORAGE HEADROOM",
                "title": f"Low Disk Space on C: ({disk_free} GB free)",
                "description": "Drive C: free space is under 20GB. Recommend running cache pruning.",
                "action_label": "Clean Cache",
                "action_prompt": "Eunchae, prune audio and workspace caches to free disk space."
            })
    except Exception as e:
        suggestions.append(check_failed("Hardware", "HARDWARE VITALS", "eunchae", e))

    return suggestions


def check_git_sentinel() -> list[dict]:
    """Inspects git status of the HQ repo (GIT_REPO_DIR)."""
    suggestions = []
    try:
        # Check porcelain status
        res = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(GIT_REPO_DIR),
            capture_output=True,
            text=True,
            check=True
        )
        lines = [l for l in res.stdout.splitlines() if l.strip()]
        
        # Check branch
        branch_res = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=str(GIT_REPO_DIR),
            capture_output=True,
            text=True,
            check=True
        )
        current_branch = branch_res.stdout.strip() or "main"

        if len(lines) >= 3:
            suggestions.append({
                "id": "git_uncommitted_files",
                "severity": "action",
                "agent": "kazuha",
                "category": "REPOSITORY SENTINEL",
                "title": f"{len(lines)} Uncommitted Changes on '{current_branch}'",
                "description": f"The HQ repo has {len(lines)} modified or untracked file(s). Recommend reviewing diff and committing progress.",
                "action_label": "Review with Kazuha",
                "action_prompt": "Kazuha, review git status and summarize uncommitted changes."
            })
    except Exception as e:
        suggestions.append(check_failed("Git", "REPOSITORY SENTINEL", "kazuha", e))

    return suggestions


def check_obsidian_sentinel() -> list[dict]:
    """Inspects Obsidian Second Brain for daily briefing status and queued priorities."""
    suggestions = []
    if not ObsidianClient:
        return [check_failed("Obsidian", "SECOND BRAIN", "sakura", "obsidian_client is not importable")]

    try:
        obs = ObsidianClient()
        today = datetime.now().strftime("%Y-%m-%d")
        daily_rel_path = f"05 - Daily Logs/{today}.md"
        
        # Check if today's log exists
        content = ""
        try:
            content = obs.get_file(daily_rel_path)
        except Exception as _exc:
            print(f"⚠️ [proactive_sentinel.check_obsidian_sentinel] suppressed {type(_exc).__name__}: {_exc}", flush=True)
            content = ""

        if not content or len(content.strip()) < 50:
            suggestions.append({
                "id": "obsidian_daily_pending",
                "severity": "action",
                "agent": "sakura",
                "category": "SECOND BRAIN",
                "title": f"Daily Briefing for {today} Pending",
                "description": "Today's executive briefing has not yet been initialized in your Obsidian Second Brain.",
                "action_label": "Form Briefing",
                "action_prompt": "Sakura, generate today's morning executive briefing."
            })
        else:
            # Check for uncompleted checkboxes
            uncompleted = []
            for line in content.splitlines():
                if "- [ ]" in line:
                    clean = line.strip().replace("- [ ]", "").strip()
                    if clean:
                        uncompleted.append(clean)

            if uncompleted:
                import re
                top_priority = uncompleted[0]
                clean_name = re.sub(r'^\*{0,2}Priority\s*\d+:?\*{0,2}\s*', '', top_priority, flags=re.IGNORECASE).strip('* ')
                # Tailor action depending on content
                prompt = f"Ciel, assist with our daily priority: {clean_name}"
                agent = "sakura"
                if any(w in clean_name.lower() for w in ["outreach", "application", "job", "resume"]):
                    agent = "chaewon"
                    prompt = f"Chaewon, prepare job-tailoring outreach aligned with daily priority: {clean_name}"
                elif any(w in clean_name.lower() for w in ["portfolio", "design", "case", "css"]):
                    agent = "yunjin"
                    prompt = f"Yunjin, review portfolio and case studies aligned with daily priority: {clean_name}"
                elif any(w in clean_name.lower() for w in ["telemetry", "swarm", "test", "hardware", "git"]):
                    agent = "eunchae"
                    prompt = f"Eunchae, run verification and telemetry tests for: {clean_name}"

                suggestions.append({
                    "id": "obsidian_top_priority",
                    "severity": "action",
                    "agent": agent,
                    "category": "DAILY PRIORITY",
                    "title": f"Priority: {clean_name[:40]}",
                    "description": f"Queued in today's daily log: \"{clean_name}\". Ready for dispatch.",
                    "action_label": f"Dispatch {agent.capitalize()}",
                    "action_prompt": prompt
                })
    except Exception as e:
        suggestions.append(check_failed("Obsidian", "SECOND BRAIN", "sakura", e))

    return suggestions


def check_portfolio_sentinel() -> list[dict]:
    """Inspects portfolio site integrity and asset health."""
    suggestions = []
    try:
        index_html = PORTFOLIO_DIR / "index.html"
        if index_html.exists():
            text = index_html.read_text(encoding="utf-8")
            if "TODO" in text or "FIXME" in text:
                suggestions.append({
                    "id": "portfolio_todo_flag",
                    "severity": "info",
                    "agent": "yunjin",
                    "category": "PORTFOLIO SENTINEL",
                    "title": "Unresolved Comments in Portfolio",
                    "description": "Detected TODO or placeholder markers in portfolio-site/index.html.",
                    "action_label": "Audit Portfolio",
                    "action_prompt": "Yunjin, audit portfolio HTML structure and clean up remaining markers."
                })
    except Exception as e:
        suggestions.append(check_failed("Portfolio", "PORTFOLIO SENTINEL", "yunjin", e))

    return suggestions


def evaluate_proactive_suggestions() -> list[dict]:
    """
    Runs an autonomous appraisal pass across all 4 operational sentinels.
    Ranks by severity: warning > action > info, and caps to top 3 suggestions.
    """
    all_suggestions = []
    all_suggestions.extend(check_hardware_sentinel())
    all_suggestions.extend(check_git_sentinel())
    all_suggestions.extend(check_obsidian_sentinel())
    all_suggestions.extend(check_portfolio_sentinel())

    severity_weight = {"warning": 3, "action": 2, "info": 1}
    all_suggestions.sort(key=lambda s: severity_weight.get(s.get("severity", "info"), 0), reverse=True)

    return all_suggestions[:3]


def generate_appraisal_summary(suggestions: list[dict]) -> str:
    """Formats a concise, high-density appraisal summary for Ciel."""
    if not suggestions:
        # Only reachable when all four checks ran (a failed check is a suggestion).
        return "Notice: Operational appraisal complete. The hardware, git, Obsidian and portfolio checks all ran and flagged nothing."

    items = []
    for i, s in enumerate(suggestions, 1):
        items.append(f"{i}. [{s['category']}] {s['title']} — {s['description']}")

    return f"Notice: Operational appraisal complete. I have identified {len(suggestions)} active recommendation(s):\n" + "\n".join(items)


if __name__ == "__main__":
    print("Testing Proactive Sentinel...")
    suggs = evaluate_proactive_suggestions()
    print(f"Generated {len(suggs)} suggestions:")
    for s in suggs:
        print(f" - [{s['severity'].upper()}] {s['title']} ({s['agent']}) -> {s['action_prompt']}")
    print("\nFormatted Summary:\n" + generate_appraisal_summary(suggs))
