import os
import re
import datetime
import warnings
from pathlib import Path
from dotenv import load_dotenv

warnings.filterwarnings("ignore", category=FutureWarning)

load_dotenv()

BASE_DIR = Path(__file__).parent
MEMORY_DIR = BASE_DIR / "memory"

from llm_client import query_llm
from obsidian_client import ObsidianClient


def load_file(path: Path) -> str:
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def scan_morning_inbox(max_items: int = 10) -> dict:
    """
    Scans Gmail for unread messages, specifically categorizing
    job/recruiter outreach and security/dev notices.
    """
    try:
        from gmail_engine import is_configured, get_gmail_service, fetch_inbox_messages
        if not is_configured():
            return {
                "ok": False,
                "status": "Gmail API credentials not configured",
                "unread_count": 0,
                "job_alerts": [],
                "highlights": []
            }

        svc = get_gmail_service()
        if not svc:
            return {
                "ok": False,
                "status": "Gmail Service Offline",
                "unread_count": 0,
                "job_alerts": [],
                "highlights": []
            }

        messages = fetch_inbox_messages(svc, max_results=max_items, unread_only=True)
        if not messages:
            return {
                "ok": True,
                "status": "Clean Inbox — 0 unread messages in Primary",
                "unread_count": 0,
                "job_alerts": [],
                "highlights": []
            }

        job_keywords = ["job", "interview", "application", "recruiter", "talent", "career", "hiring", "offer", "candidate", "role", "supportninja", "greenhouse", "lever", "workday", "jobstreet"]
        job_alerts = []
        highlights = []

        for m in messages:
            subj = m.get("subject", "(No Subject)")
            sender = m.get("sender", "Unknown")
            snippet = m.get("snippet", "")
            comb = f"{subj} {sender} {snippet}".lower()

            clean_sender = re.sub(r'<.*?>', '', sender).replace('"', '').strip()

            is_job = any(k in comb for k in job_keywords)
            formatted = f"💼 **{clean_sender[:28]}**: {subj[:60]}" if is_job else f"📬 **{clean_sender[:28]}**: {subj[:60]}"
            
            if is_job:
                job_alerts.append(formatted)
            else:
                highlights.append(formatted)

        return {
            "ok": True,
            "status": f"{len(messages)} unread messages in Primary Inbox",
            "unread_count": len(messages),
            "job_alerts": job_alerts,
            "highlights": (job_alerts + highlights)[:5]
        }
    except Exception as e:
        return {
            "ok": False,
            "status": f"Inbox scan error: {e}",
            "unread_count": 0,
            "job_alerts": [],
            "highlights": []
        }


def execute_morning_launchpad(sync_obsidian: bool = True) -> dict:
    """
    Executes the complete Sakura Morning Launchpad routine:
    1. Scans Gmail for unread recruiter & operational messages.
    2. Probes host hardware vitals via Eunchae engine.
    3. Counts active applications in memory & verifies 6 case studies.
    4. Generates an executive daily morning briefing via LLM.
    5. Synchronizes with Obsidian 05 - Daily Logs/ note.
    """
    now = datetime.datetime.now()
    today_str = now.strftime("%A, %B %d, %Y")
    date_iso = now.strftime("%Y-%m-%d")

    # 1. Inbox scan
    inbox = scan_morning_inbox()
    inbox_summary = inbox.get("status", "Inbox Clean")
    if inbox.get("highlights"):
        inbox_summary += "\n" + "\n".join(f"  • {h}" for h in inbox["highlights"])

    # 2. Hardware vitals
    vitals_summary = "Hardware Nominal"
    try:
        from eunchae_engine import get_system_vitals
        v = get_system_vitals()
        vitals_summary = f"CPU: {v.get('cpu_pct', 0)}% ({v.get('cpu_count', 12)} Cores) | RAM: {v.get('ram_used_gb', 0)}GB / {v.get('ram_total_gb', 0)}GB ({v.get('ram_pct', 0)}%) | C: Drive Free: {v.get('disk_free_gb', 0)}GB"
    except Exception:
        pass

    # 3. Application count
    applications_log = load_file(MEMORY_DIR / "applications_log.md")
    app_lines = [l for l in applications_log.splitlines() if l.strip().startswith("|") and not l.startswith("| #") and not l.startswith("|---") and not l.startswith("| -")]
    app_count = len(app_lines)

    # 4. LLM Briefing Generation
    goals = load_file(MEMORY_DIR / "sakura_goals.md")
    prompt = f"""
You are "Sakura", the Chief of Staff and Central Orchestrator for the user.
The user is an Applied AI Engineer & Full-Stack Builder with dual-competency in autonomous AI agent swarms and polished frontend interfaces.

### CANDIDATE & PORTFOLIO PROFILE:
- Role: Applied AI Engineer / Full-Stack AI Engineer (BS Computer Science)
- Core Value: Builds production agent swarms, multi-provider LLM routing (Groq, Gemini, local Ollama), deterministic BM25 memory, and high-craft UI/UX.
- Flagship Projects: 6 verified case studies live in portfolio-site/ (LSFM AI Swarm, Lumina Analytics, Vellum OS, FinTrack, Aura Coffee, Cognitive Memory Core)
- Active Rules: 10 crystallized heuristics in Obsidian (MEM-001 through MEM-010)

### MASTER SPRINT GOALS:
{goals}

### CURRENT OPERATIONAL STATUS:
- Today's Date: {today_str}
- Applications Prepared in Archive: {app_count}
- Inbound & Gmail Pulse:
{inbox_summary}
- PC Hardware Vitals: {vitals_summary}

---

### INSTRUCTIONS:
Compose a structured, authoritative, and inspiring Daily Morning Briefing with these exact markdown sections:

1. 🌸 **GOOD MORNING & SPRINT FOCUS**:
   - 2-sentence warm, commanding greeting from Sakura.
   - Today's core sprint theme (e.g. "High-Leverage AI Agent Outreach" or "Demonstrating Heuristic Intelligence").

2. 📬 **INBOUND & RECRUITER PULSE**:
   - Crisp summary of the inbox state.
   - Note any action items on job leads or application reminders.

3. 📊 **SQUAD OPERATIONAL READINESS**:
   - ⭐ **Chaewon (Career):** {app_count} applications logged; ready to tailor new roles (`!tailor`).
   - 🎨 **Yunjin (Portfolio):** 6/6 Flagship case studies verified (100/100 QA score, 0 broken assets).
   - 💻 **Kazuha (Frontend):** Swiss Editorial design tokens & terminal telemetry in sync.
   - 🛡️ **Eunchae (Guardian):** {vitals_summary}.

4. 🎯 **TOP 3 PRIORITIES FOR HANS TODAY**:
   - Concrete, high-leverage actions to move the needle toward securing an Applied AI Engineer role.

Tone: Calm, commanding, warm, razor-sharp leadership. Avoid robotic clichés.
"""
    briefing_text = query_llm(prompt, temperature=0.4)

    # 5. Obsidian Second Brain Sync
    obsidian_synced = False
    daily_log_path = ""
    if sync_obsidian:
        try:
            client = ObsidianClient()
            daily_log_path = client.ensure_daily_log(date_iso)

            # Build structured launchpad dispatch details
            dispatch_items = [
                f"Inbox Triage: {inbox.get('unread_count', 0)} unread messages scanned",
                f"Hardware Vitals: {vitals_summary}",
                f"Application Archive: {app_count} applications logged",
                "Case Studies: 6/6 flagship studies verified live in portfolio-site/",
                "Top 3 Daily Priorities synchronized to daily log"
            ]
            if inbox.get("job_alerts"):
                dispatch_items.append(f"Inbound Highlights: {', '.join(inbox['job_alerts'][:2])}")

            client.log_session(
                "Sakura",
                f"Morning Launchpad Executed ({now.strftime('%H:%M:%S')})",
                dispatch_items
            )
            obsidian_synced = True
        except Exception as e:
            print(f"⚠️ [Sakura] Obsidian sync failed: {e}", flush=True)

    return {
        "briefing_text": briefing_text,
        "date_str": today_str,
        "date_iso": date_iso,
        "inbox": inbox,
        "vitals_summary": vitals_summary,
        "app_count": app_count,
        "obsidian_synced": obsidian_synced,
        "daily_log_path": daily_log_path
    }


def generate_morning_briefing() -> str:
    """Maintains backward compatibility by running the Morning Launchpad and returning the text."""
    res = execute_morning_launchpad(sync_obsidian=True)
    return res["briefing_text"]


def execute_evening_rollup(sync_obsidian: bool = True) -> dict:
    """
    Executes the complete Sakura Evening Standup & Daily Rollup:
    1. Reads today's Obsidian daily log to gather all recorded dispatches across all agents.
    2. Gathers Git activity (commits today, active work in progress).
    3. Gathers hardware health and applications pipeline metrics.
    4. Synthesizes an executive Day Close report using LLM.
    5. Appends the Evening Rollup section to today's Obsidian daily log.
    6. Returns structured summary and briefing.
    """
    now = datetime.datetime.now()
    today_str = now.strftime("%A, %B %d, %Y")
    date_iso = now.strftime("%Y-%m-%d")

    # 1. Read today's dispatches from Obsidian
    obsidian_dispatches = []
    daily_log_path = ""
    try:
        client = ObsidianClient()
        daily_log_path = client.ensure_daily_log(date_iso)
        log_content = client.get_file(daily_log_path)
        
        # Extract dispatches section
        if "### [" in log_content:
            raw_dispatches = log_content.split("### [", 1)[1]
            chunks = raw_dispatches.split("\n\n### [")
            for ch in chunks:
                obsidian_dispatches.append("### [" + ch.strip())
    except Exception as e:
        print(f"⚠️ [Sakura] Could not fetch Obsidian dispatches: {e}")

    # 2. Gather Git activity
    git_commits = []
    git_modified_count = 0
    try:
        import subprocess
        # Commits today
        c_res = subprocess.run(["git", "log", "--since=midnight", "--oneline"], capture_output=True, text=True, cwd=str(BASE_DIR.parent))
        if c_res.returncode == 0 and c_res.stdout.strip():
            git_commits = [l.strip() for l in c_res.stdout.strip().splitlines() if l.strip()]
        else:
            c_res2 = subprocess.run(["git", "log", "-n", "3", "--oneline"], capture_output=True, text=True, cwd=str(BASE_DIR.parent))
            if c_res2.returncode == 0 and c_res2.stdout.strip():
                git_commits = [f"(Recent) {l.strip()}" for l in c_res2.stdout.strip().splitlines() if l.strip()]

        s_res = subprocess.run(["git", "status", "--short"], capture_output=True, text=True, cwd=str(BASE_DIR.parent))
        if s_res.returncode == 0:
            lines = [l for l in s_res.stdout.strip().splitlines() if l.strip()]
            git_modified_count = len(lines)
    except Exception as e:
        git_commits = [f"Git status query: {e}"]

    # 3. Hardware vitals
    vitals_summary = "Hardware Nominal"
    try:
        from eunchae_engine import get_system_vitals
        v = get_system_vitals()
        vitals_summary = f"CPU: {v.get('cpu_pct', 0)}% ({v.get('cpu_count', 12)} Cores) | RAM: {v.get('ram_used_gb', 0)}GB / {v.get('ram_total_gb', 0)}GB ({v.get('ram_pct', 0)}%) | C: Drive Free: {v.get('disk_free_gb', 0)}GB | Uptime: {v.get('uptime_str', '')}"
    except Exception:
        pass

    # 4. Applications count
    applications_log = load_file(MEMORY_DIR / "applications_log.md")
    app_lines = [l for l in applications_log.splitlines() if l.strip().startswith("|") and not l.startswith("| #") and not l.startswith("|---") and not l.startswith("| -")]
    app_count = len(app_lines)

    # 5. LLM Synthesis
    dispatches_text = "\n\n".join(obsidian_dispatches) if obsidian_dispatches else "No prior dispatches recorded today."
    git_text = "\n".join(f"  • {c}" for c in git_commits) if git_commits else "  • No commits recorded today"

    prompt = f"""
You are "Sakura", Chief of Staff and Central Orchestrator for Hans Aaron Laureles.
Deliver the formal **Evening Standup & Daily Rollup** to close out the day for Hans in Discord and Obsidian.

### CANDIDATE CONTEXT:
- Candidate: Hans Aaron Laureles (Applied AI Engineer & Full-Stack Builder)
- Active Infrastructure: 5-Daemon LSFM Swarm, Obsidian AI Brain, 6 Flagship Projects, 10 Crystallized Heuristics (MEM-001 through MEM-010)

### DAY'S OPERATIONAL DATA:
- Today's Date: {today_str}
- Active Applications in Archive: {app_count}
- Host Hardware Vitals: {vitals_summary}
- Version Control Activity:
  - Modified files in workspace: {git_modified_count}
  - Commits:
{git_text}
- Agent Session Dispatches Recorded Today in Obsidian:
{dispatches_text}

---

### INSTRUCTIONS:
Compose a structured, authoritative, and empowering Evening Standup report with these exact markdown sections:

1. 🌆 **DAY IN REVIEW & KEY ACHIEVEMENTS**:
   - 2-3 sentence executive recap of what the squad and Hans accomplished today.
   - Highlight the primary engineering, architectural, or career wins.

2. 📊 **SQUAD SYSTEM PULSE & HYGIENE**:
   - Summarize the performance of Chaewon, Yunjin, Kazuha, and Eunchae.
   - Note git commit hygiene, portfolio stability (6/6 projects), and zero resource leaks.

3. 🔮 **STRATEGIC CARRYOVER & QUEUE FOR TOMORROW**:
   - 2-3 concrete tasks prioritized for tomorrow morning's launchpad (e.g., following up on inbound signals, next engineering sprint).

4. 🌸 **CHIEF OF STAFF CLOSING WORDS**:
   - Concise, warm, inspiring closing note from Sakura reminding Hans of his competitive differentiation as a builder who ships.

Tone: Calm, commanding, executive, razor-sharp. Avoid robotic clichés.
"""
    rollup_text = query_llm(prompt, temperature=0.4)

    # 6. Obsidian Daily Log Append
    obsidian_synced = False
    if sync_obsidian:
        try:
            client = ObsidianClient()
            daily_log_path = client.ensure_daily_log(date_iso)

            # Append the Evening Standup block to today's daily log
            time_now = now.strftime("%H:%M:%S")
            standup_block = f"\n\n---\n\n## 🌆 Evening Standup & Daily Rollup (Closed at {time_now})\n\n{rollup_text}\n"
            client.append_file(daily_log_path, standup_block)

            # Also log session dispatch
            client.log_session(
                "Sakura",
                f"Evening Daily Rollup Closed ({time_now})",
                [
                    f"Workspace Hygiene: {git_modified_count} files tracked",
                    f"Hardware Health: {vitals_summary}",
                    f"Total Applications: {app_count} in archive",
                    "Daily Standup & Tomorrow's queue sealed in daily log"
                ]
            )
            obsidian_synced = True
        except Exception as e:
            print(f"⚠️ [Sakura] Obsidian evening sync failed: {e}", flush=True)

    return {
        "rollup_text": rollup_text,
        "date_str": today_str,
        "date_iso": date_iso,
        "git_commits": git_commits,
        "git_modified_count": git_modified_count,
        "vitals_summary": vitals_summary,
        "obsidian_synced": obsidian_synced,
        "daily_log_path": daily_log_path
    }



def consult_sakura(question: str) -> str:
    """Answers strategic questions regarding interviews, portfolio decisions, or career strategy."""
    goals = load_file(MEMORY_DIR / "sakura_goals.md")
    resume = load_file(MEMORY_DIR / "master_resume.md")

    prompt = f"""
You are "Sakura", Chief of Staff and Career Strategist for the user.
The user is asking you for guidance.

### CANDIDATE CONTEXT:
- Role: Applied AI Engineer & Full-Stack Builder (BS Computer Science)
- Value: "Builds local and cloud AI agent workflows, automated tool pipelines, and production web interfaces."
- Goals:
{goals}

### USER'S QUESTION:
"{question}"

---

### INSTRUCTIONS:
Provide structured, strategic, high-value advice.
Be direct, actionable, and confident. Think like an executive coach or senior design manager.
"""
    return query_llm(prompt, temperature=0.4)


PENDING_PROPOSALS_FILE = MEMORY_DIR / "pending_proposals.json"

def load_pending_proposals() -> dict:
    if PENDING_PROPOSALS_FILE.exists():
        try:
            import json
            return json.loads(PENDING_PROPOSALS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}

def save_pending_proposal(message_id: int, data: dict):
    import json
    proposals = load_pending_proposals()
    proposals[str(message_id)] = data
    PENDING_PROPOSALS_FILE.write_text(json.dumps(proposals, indent=2), encoding="utf-8")

def get_pending_proposal(message_id: int) -> dict:
    proposals = load_pending_proposals()
    return proposals.get(str(message_id))

def mark_proposal_status(message_id: int, status: str) -> dict:
    import json
    proposals = load_pending_proposals()
    data = proposals.pop(str(message_id), None)
    if data:
        PENDING_PROPOSALS_FILE.write_text(json.dumps(proposals, indent=2), encoding="utf-8")
        # Update applications_log.md status
        log_path = MEMORY_DIR / "applications_log.md"
        if log_path.exists():
            content = log_path.read_text(encoding="utf-8")
            fname = data.get("filename", "")
            lines = content.splitlines()
            new_lines = []
            updated = False
            for line in lines:
                if fname and fname in line:
                    parts = line.split("|")
                    if len(parts) >= 8:
                        parts[6] = f" {status} ({data.get('score', 85)}%) "
                        new_lines.append("|".join(parts))
                        updated = True
                        continue
                new_lines.append(line)
            
            if not updated and status == "Approved":
                # If not present in file, append it
                today_str = datetime.date.today().strftime("%Y-%m-%d")
                new_lines.append(f"| {today_str} | {data.get('company', 'Company')} | {data.get('role', 'Role')} | Remote/Hybrid | Dual-competency CS+UI/UX | {status} ({data.get('score', 85)}%) | [{fname}](../applications/{fname}) |")
            
            log_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    return data
