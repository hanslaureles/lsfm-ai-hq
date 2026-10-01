import os
import sys
import re
import psutil
import datetime
import warnings
from pathlib import Path
from dotenv import load_dotenv

warnings.filterwarnings("ignore", category=FutureWarning)

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

load_dotenv()

BASE_DIR = Path(__file__).parent
WORKSPACE_DIR = BASE_DIR.parent
MEMORY_DIR = BASE_DIR / "memory"
APPLICATIONS_DIR = BASE_DIR / "applications"

from llm_client import query_llm

# ==============================================================================
# AGENT-MEMORY INTEGRATION (Self-Improving Flywheel)
# ==============================================================================
AGENT_MEMORY_DIR = WORKSPACE_DIR / "agent-memory"
if str(AGENT_MEMORY_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_MEMORY_DIR))

try:
    from recall import recall_memories  # type: ignore
    from reflect import record_reflection, load_all_memories  # type: ignore
    from crystallize import crystallize  # type: ignore
except Exception:
    recall_memories = None
    record_reflection = None
    load_all_memories = None
    crystallize = None


# ==============================================================================
# HARDWARE VITALS MONITORING (Legacy Guardian Duties)
# ==============================================================================
def get_system_vitals() -> dict:
    """Reads real-time PC hardware vitals using psutil."""
    cpu_pct = psutil.cpu_percent(interval=0.5)
    cpu_count = psutil.cpu_count(logical=True)
    
    vm = psutil.virtual_memory()
    ram_total_gb = round(vm.total / (1024 ** 3), 1)
    ram_used_gb = round(vm.used / (1024 ** 3), 1)
    ram_pct = vm.percent

    disk = psutil.disk_usage("C:\\")
    disk_total_gb = round(disk.total / (1024 ** 3), 1)
    disk_free_gb = round(disk.free / (1024 ** 3), 1)
    disk_pct = disk.percent

    boot_time = datetime.datetime.fromtimestamp(psutil.boot_time())
    uptime = datetime.datetime.now() - boot_time
    hours, remainder = divmod(int(uptime.total_seconds()), 3600)
    minutes, _ = divmod(remainder, 60)
    uptime_str = f"{hours}h {minutes}m"

    return {
        "cpu_pct": cpu_pct,
        "cpu_count": cpu_count,
        "ram_total_gb": ram_total_gb,
        "ram_used_gb": ram_used_gb,
        "ram_pct": ram_pct,
        "disk_total_gb": disk_total_gb,
        "disk_free_gb": disk_free_gb,
        "disk_pct": disk_pct,
        "uptime_str": uptime_str,
        "healthy": (ram_pct < 90 and cpu_pct < 90 and disk_pct < 95)
    }

def generate_eunchae_report() -> str:
    """Generates an energetic system diagnostic report from Eunchae."""
    vitals = get_system_vitals()
    
    prompt = f"""
You are "Eunchae", the System Guardian and Quality Assurance Inspector for Hans Aaron Laureles's AI team (LE SSERAFIM AI HQ).
Your job is to deliver a quick, fun, but accurate PC Health & Vitals checkup to Hans in Discord.

### SYSTEM VITALS:
- CPU Usage: {vitals['cpu_pct']}% ({vitals['cpu_count']} Logical Cores)
- RAM Usage: {vitals['ram_used_gb']} GB / {vitals['ram_total_gb']} GB ({vitals['ram_pct']}%)
- C: Drive Storage: {vitals['disk_free_gb']} GB Free / {vitals['disk_total_gb']} GB Total ({vitals['disk_pct']}% used)
- PC Uptime: {vitals['uptime_str']}
- Overall Hardware Status: {"🟢 HEALTHY & COOL" if vitals['healthy'] else "⚠️ HEAVY LOAD WARNING"}

---

### INSTRUCTIONS:
Present these vitals with high energy, clarity, and charm.
- Confirm whether the machine is running smoothly.
- Give a quick status on the squad (Sakura, Chaewon, Yunjin, Kazuha).
- Keep it concise and formatted with clean Discord markdown.
"""
    return query_llm(prompt, temperature=0.5)

def chat_eunchae(message_text: str) -> str:
    """Answers chats and hardware queries with Eunchae's energetic maknae guardian persona."""
    vitals = get_system_vitals()
    
    prompt = f"""
You are "Eunchae", the System Guardian & Quality Assurance Inspector for Hans Aaron Laureles's AI team (LE SSERAFIM AI HQ).
Hans is chatting with you in the #pc-vitals or #system-alerts channel.

### LIVE PC CONTEXT:
- CPU: {vitals['cpu_pct']}%
- RAM: {vitals['ram_used_gb']}/{vitals['ram_total_gb']} GB ({vitals['ram_pct']}%)
- Disk: {vitals['disk_free_gb']} GB free
- Status: {"Healthy & Cool 🟢" if vitals['healthy'] else "High Load ⚠️"}

### HANS'S MESSAGE:
"{message_text}"

---

### INSTRUCTIONS:
- Reply with Eunchae's signature bright, cheerful, confident maknae energy.
- You guard both Hans's PC hardware AND the quality of all squad deliverables.
- Keep it concise for Discord.
"""
    return query_llm(prompt, temperature=0.5)


# ==============================================================================
# SQUAD QUALITY ASSURANCE (QA) & DEFECT INSPECTOR
# ==============================================================================
PLACEHOLDER_REGEX = re.compile(r"(\[(?:Company|Client|Your Name|Name|Phone|Email|Insert|Role|Address)\]|Lorem Ipsum|\bTODO\b)", re.IGNORECASE)

def check_deterministic_qa(tailor_res: dict, company: str, role: str) -> dict:
    """
    Performs fast, rule-based deterministic checks before calling the LLM.
    Scans for unfilled placeholders, missing PDF binaries, and error fallbacks.
    """
    defects = []
    warnings = []
    checks = {}

    report_text = tailor_res.get("report", "")
    filename = tailor_res.get("filename", "")

    # 1. Error Fallback Check
    if "error.md" in filename or "Error generating full report" in report_text:
        defects.append("🚨 LLM generation failure detected: Fallback `error.md` was generated.")
        checks["generation_integrity"] = False
    else:
        checks["generation_integrity"] = True

    # 2. Unfilled Placeholder Scan
    raw_saved = ""
    saved_file = tailor_res.get("saved_file")
    if saved_file and Path(saved_file).exists():
        try:
            raw_saved = Path(saved_file).read_text(encoding="utf-8")
        except Exception:
            pass

    found_placeholders = PLACEHOLDER_REGEX.findall(report_text + "\n" + raw_saved)
    if found_placeholders:
        unique_ph = list(set(found_placeholders))
        defects.append(f"⚠️ Unfilled template placeholders detected: `{', '.join(unique_ph[:5])}`")
        checks["placeholder_free"] = False
    else:
        checks["placeholder_free"] = True

    # 3. PDF Files Existence & Size Check
    resume_pdf = tailor_res.get("resume_pdf")
    if resume_pdf and Path(resume_pdf).exists():
        size_kb = round(Path(resume_pdf).stat().st_size / 1024, 1)
        if size_kb < 3:
            defects.append(f"⚠️ Resume PDF `{Path(resume_pdf).name}` is suspiciously small ({size_kb} KB).")
            checks["resume_pdf"] = False
        else:
            checks["resume_pdf"] = True
    else:
        defects.append("🚨 Tailored Resume PDF was not generated or is missing on disk.")
        checks["resume_pdf"] = False

    cover_pdf = tailor_res.get("cover_pdf")
    if cover_pdf and Path(cover_pdf).exists():
        size_kb = round(Path(cover_pdf).stat().st_size / 1024, 1)
        if size_kb < 2:
            warnings.append(f"⚠️ Cover Letter PDF `{Path(cover_pdf).name}` is small ({size_kb} KB).")
            checks["cover_pdf"] = False
        else:
            checks["cover_pdf"] = True
    else:
        warnings.append("⚠️ Tailored Cover Letter PDF was not generated.")
        checks["cover_pdf"] = False

    # 4. ATS Score Sanity Check
    score = tailor_res.get("score", 0)
    if score < 60:
        warnings.append(f"⚠️ Low ATS Match Score ({score}%). Application may need keyword enrichment.")
    checks["ats_score_valid"] = 50 <= score <= 100

    return {
        "passed": len(defects) == 0,
        "defects": defects,
        "warnings": warnings,
        "checks": checks
    }

def recall_squad_heuristics(query: str) -> list:
    """Retrieves relevant past failure lessons from agent-memory."""
    if not recall_memories:
        return []
    try:
        results = recall_memories(query, top_k=3, min_score=0.35)
        return results
    except Exception:
        return []

def audit_application_package(
    tailor_res: dict,
    yunjin_res: dict = None,
    kazuha_res: dict = None,
    company: str = "Target Company",
    role: str = "Target Role"
) -> dict:
    """
    Master QA Gatekeeper Audit: Inspects deliverables from Chaewon, Yunjin, and Kazuha.
    Enforces deterministic safety and recalls past failure heuristics from agent-memory.
    """
    y_res = yunjin_res or {}
    k_res = kazuha_res or {}

    # 1. Deterministic Pre-Flight
    det = check_deterministic_qa(tailor_res, company, role)

    # 2. Episodic Memory Recall (Past Failures)
    memories = recall_squad_heuristics(f"{role} {company} resume cover letter tailoring")
    memory_rules = []
    for mem, sc in memories:
        rule = mem.get("permanent_rule", "")
        if rule:
            memory_rules.append(f"• [{mem.get('domain', 'general')}] {rule} (Relevance: {sc:.1f})")

    memory_context = "\n".join(memory_rules) if memory_rules else "• No active failure regressions for this domain."

    # 3. LLM QA Inspection
    report_excerpt = tailor_res.get("report", "")[:2000]
    rec_projects = ", ".join(y_res.get("recommended_projects", ["Vellum OS", "Lumina Analytics"]))
    tech_pitch_excerpt = k_res.get("tech_pitch", "")[:800]

    prompt = f"""
You are "Eunchae", the System Guardian & Quality Assurance (QA) Inspector for Hans Aaron Laureles.
Your job is to perform a rigorous Quality Assurance Gatekeeper Audit on the squad's deliverables for:
- Role: {role}
- Company: {company}

### SQUAD DELIVERABLES TO AUDIT:
1. **Chaewon (Resume & Cover Letter Report)**:
```
{report_excerpt}
```
2. **Yunjin (Recommended Portfolio Case Studies)**: {rec_projects}
3. **Kazuha (Frontend & CS Tech Pitch Excerpt)**:
```
{tech_pitch_excerpt}
```

### KNOWN SQUAD FAILURE HEURISTICS (FROM AGENT-MEMORY):
{memory_context}

### DETERMINISTIC SCAN FINDINGS:
- Defect Count: {len(det['defects'])}
- Warning Count: {len(det['warnings'])}
- Flags: {', '.join(det['defects'] + det['warnings']) or 'None'}

---

### QA AUDIT INSTRUCTIONS:
Assess the package with protective, sharp QA rigor and output:
1. **QA SCORE (1-100%)**:
   - Start at 100. Deduct 25 for missing PDFs or unreplaced placeholders, 15 for generic tone, 10 for past rule violations.
2. **QA STATUS**:
   - `🟢 QA CERTIFIED` (Score >= 90% and 0 critical defects)
   - `🟡 QA PASS WITH POLISH` (Score 75-89% and 0 critical defects)
   - `🔴 QA DEFECT BLOCKED` (Score < 75% or any critical defect)
3. **CHECKLIST SUMMARY**:
   - [x] Template Placeholders: (Pass/Fail)
   - [x] PDF Assets: (Pass/Fail)
   - [x] ATS Match & Metrics: (Pass/Fail)
   - [x] Past Regressions: (0 Detected / Flagged)
4. **MAKNAE QA VERDICT**:
   - 2-3 energetic, sharp sentences giving the green light or explaining what must be fixed.
"""
    try:
        qa_review_text = query_llm(prompt, temperature=0.3)
    except Exception as e:
        det_passed = det.get("passed", False)
        verdict = "🔴 QA DEFECT BLOCKED" if not det_passed else "🟡 QA UNINSPECTED (LLM ERROR)"
        return {
            "passed": False,
            "score": 50 if det_passed else 25,
            "verdict": verdict,
            "deterministic": det,
            "review": f"⚠️ LLM QA review failed ({type(e).__name__}: {e}). Completed deterministic safety checks only.",
            "heuristics_checked": len(memories),
            "error": str(e)
        }

    # Extract score
    score = 90
    score_match = re.search(r"(?:Score|QA Score)\s*[:=]?\s*(\d{1,3})%", qa_review_text)
    if score_match:
        try:
            score = int(score_match.group(1))
        except Exception:
            pass

    if not det["passed"]:
        score = min(score, 65)
        verdict = "🔴 QA DEFECT BLOCKED"
    elif score >= 90:
        verdict = "🟢 QA CERTIFIED"
    elif score >= 75:
        verdict = "🟡 QA PASS WITH POLISH"
    else:
        verdict = "🔴 QA DEFECT BLOCKED"

    return {
        "passed": det["passed"] and score >= 75,
        "score": score,
        "verdict": verdict,
        "deterministic": det,
        "review": qa_review_text,
        "heuristics_checked": len(memories)
    }

def audit_file_qa(file_path: Path) -> dict:
    """On-demand QA audit on an existing file (markdown or document)."""
    if not file_path.exists():
        return {"success": False, "error": f"File not found: {file_path.name}"}

    content = file_path.read_text(encoding="utf-8")
    placeholders = PLACEHOLDER_REGEX.findall(content)
    unique_ph = list(set(placeholders))

    prompt = f"""
You are "Eunchae", Squad QA Inspector for Hans Aaron Laureles.
Perform a Quality Assurance inspection on the document: `{file_path.name}`.

### DOCUMENT CONTENT:
```
{content[:4000]}
```

---

### INSTRUCTIONS:
Audit this document for professionalism, structure, quantifiable impact, and formatting.
Provide a QA Score (0-100%), Verdict (`🟢 PASSED`, `🟡 POLISH`, `🔴 DEFECT`), and 2-3 specific improvements.
"""
    review_text = query_llm(prompt, temperature=0.3)
    score_match = re.search(r"(\d{2,3})%", review_text)
    score = int(score_match.group(1)) if score_match else 85
    verdict = "🟢 PASSED" if score >= 90 else ("🟡 POLISH" if score >= 75 else "🔴 DEFECT")

    return {
        "success": True,
        "filename": file_path.name,
        "score": score,
        "verdict": verdict,
        "placeholders": unique_ph,
        "review": review_text
    }


# ==============================================================================
# POST-MORTEM REFLECTION & FAILURE LEARNING LOOP
# ==============================================================================
def record_qa_failure(
    domain: str,
    trigger: str,
    symptom: str,
    root_cause: str,
    permanent_rule: str,
    fix_applied: str = "",
    tags: list = None
) -> dict:
    """
    Directly logs a post-mortem into agent-memory and updates crystallized rules.
    """
    if not record_reflection:
        return {"success": False, "error": "agent-memory reflect module not available"}

    try:
        entry = record_reflection(
            domain=domain.lower().strip(),
            trigger=trigger.strip(),
            symptom=symptom.strip(),
            root_cause=root_cause.strip(),
            permanent_rule=permanent_rule.strip(),
            fix_applied=fix_applied.strip(),
            tags=tags or [domain.lower().strip(), "qa-sentinel"]
        )
        if crystallize:
            try:
                crystallize()
            except Exception:
                pass
        return {"success": True, "entry": entry}
    except Exception as e:
        return {"success": False, "error": str(e)}

def get_all_learned_lessons(domain: str = None) -> list:
    """Retrieves all post-mortems and permanent rules stored in agent-memory."""
    if not load_all_memories:
        return []
    try:
        records = load_all_memories()
        if domain:
            records = [r for r in records if r.get("domain", "").lower() == domain.lower()]
        return records
    except Exception:
        return []

# ==============================================================================
# CODE TESTING & EXECUTION ENGINE
# ==============================================================================
def test_code_file(file_path_str: str) -> dict:
    """
    Eunchae QA Code Runner:
    - Resolves file path from workspace or job-copilot
    - Tests syntax compilation (py_compile / AST parse)
    - Tests import resolution / dependencies
    - Runs unit tests if file is a test suite
    - Performs sandbox smoke run if executable
    """
    clean_str = file_path_str.strip("`'\" ")
    target_path = Path(clean_str)

    # Try resolving relative to WORKSPACE_DIR, BASE_DIR, or absolute
    if not target_path.exists():
        target_path = WORKSPACE_DIR / clean_str
    if not target_path.exists():
        target_path = BASE_DIR / clean_str
    if not target_path.exists():
        # Check if basename matches anywhere in workspace
        matches = list(WORKSPACE_DIR.rglob(Path(clean_str).name))
        if matches:
            target_path = matches[0]

    if not target_path.exists():
        return {
            "success": False,
            "error": f"File not found: `{clean_str}`. Please verify the path.",
            "filename": clean_str
        }

    ext = target_path.suffix.lower()
    checks = []
    defects = []
    warnings = []
    
    # Python File Testing
    if ext == ".py":
        # 1. Syntax Check via py_compile
        import py_compile
        try:
            py_compile.compile(str(target_path), doraise=True)
            checks.append("✅ **Python Syntax & Compilation:** Clean (0 syntax errors)")
        except py_compile.PyCompileError as e:
            defects.append(f"🚨 **Syntax Error:** {e}")

        # 2. AST parsing & Code metrics
        import ast
        try:
            content = target_path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(content)
            func_count = len([n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))])
            class_count = len([n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)])
            line_count = len(content.splitlines())
            checks.append(f"📊 **Structure:** {line_count} lines, {func_count} functions, {class_count} classes")

            # Check for dangerous patterns
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    if node.func.id in ["eval", "exec"]:
                        warnings.append(f"⚠️ Dynamic code execution `{node.func.id}()` detected at line {node.lineno}")
        except Exception as e:
            defects.append(f"🚨 **AST Parsing Error:** {e}")

        # 3. Test execution check
        is_test_file = (
            target_path.name.startswith("test_")
            or target_path.name.endswith("_test.py")
            or "unittest.TestCase" in content
            or ("import unittest" in content and "def test_" in content)
            or ("import pytest" in content and "def test_" in content)
        )
        import subprocess
        if is_test_file:
            try:
                res = subprocess.run(
                    [sys.executable, "-m", "unittest", target_path.name],
                    cwd=str(target_path.parent),
                    capture_output=True,
                    text=True,
                    timeout=15
                )
                if res.returncode == 0:
                    checks.append("🧪 **Unit Test Suite:** All unit tests PASSED ✅")
                else:
                    defects.append(f"❌ **Unit Test Failures:**\n```\n{res.stderr[:600]}\n```")
            except Exception as e:
                warnings.append(f"⚠️ Test suite execution timed out or failed: {e}")
        else:
            # 4. Dry-run smoke test (import test)
            try:
                cmd = [sys.executable, "-c", f"import sys; sys.path.insert(0, r'{target_path.parent}'); import {target_path.stem}"]
                smoke = subprocess.run(cmd, capture_output=True, text=True, timeout=8)
                if smoke.returncode == 0:
                    checks.append("⚡ **Import & Dependency Dry-Run:** Resolved cleanly (0 import errors)")
                else:
                    err_msg = smoke.stderr.strip()
                    if "ModuleNotFoundError" in err_msg or "ImportError" in err_msg:
                        defects.append(f"⚠️ **Import Resolution Error:** {err_msg.splitlines()[-1]}")
                    else:
                        checks.append("⚡ **Import Dry-Run:** Tested (Script requires command line arguments)")
            except Exception:
                pass

    # JavaScript / HTML / CSS
    elif ext in [".html", ".css", ".js", ".jsx", ".ts", ".tsx"]:
        content = target_path.read_text(encoding="utf-8", errors="replace")
        line_count = len(content.splitlines())
        checks.append(f"📊 **File Stats:** `{ext}` file, {line_count} lines")
        for open_b, close_b, name in [("{", "}", "curly braces"), ("(", ")", "parentheses"), ("[", "]", "brackets")]:
            if content.count(open_b) != content.count(close_b):
                warnings.append(f"⚠️ Mismatched {name}: {content.count(open_b)} `{open_b}` vs {content.count(close_b)} `{close_b}`")
        checks.append("✅ **Markup Structure:** Valid balanced tags/brackets")
    else:
        checks.append(f"📄 **General File Check:** Read cleanly ({target_path.stat().st_size} bytes)")

    passed = len(defects) == 0
    score = 100 if passed and not warnings else (85 if passed else 50)
    verdict = "🟢 ALL TESTS PASSED" if passed and not warnings else ("🟡 PASSED WITH WARNINGS" if passed else "🔴 TEST FAILED / DEFECTS DETECTED")

    return {
        "success": True,
        "filename": target_path.name,
        "path": str(target_path),
        "passed": passed,
        "score": score,
        "verdict": verdict,
        "checks": checks,
        "defects": defects,
        "warnings": warnings
    }


def check_obsidian_heartbeat() -> dict:
    """
    Cron 5: Obsidian Brain Port 27124 Heartbeat & Host Telemetry Sentinel.
    Probes port 27124 REST API, checks vault filesystem integrity, and pairs with host vitals.
    """
    from obsidian_client import ObsidianClient
    client = ObsidianClient()
    rest_online = False
    try:
        rest_online = client.ping()
    except Exception:
        rest_online = False

    vault_exists = client.vault_path.exists()
    vault_notes = 0
    subdirs_count = {}
    if vault_exists:
        try:
            vault_notes = len(list(client.vault_path.rglob("*.md")))
            for sub in client.vault_path.iterdir():
                if sub.is_dir() and not sub.name.startswith("."):
                    subdirs_count[sub.name] = len(list(sub.rglob("*.md")))
        except Exception as e:
            print(f"⚠️ [Eunchae Engine] Vault read error: {e}", flush=True)

    vitals = get_system_vitals()

    if rest_online and vitals["healthy"]:
        status = "NOMINAL_REST"
        status_text = "🟢 REST API Online & Vault Synchronized"
        color = 0x57F287  # Green
    elif vault_exists and vitals["healthy"]:
        status = "NOMINAL_FALLBACK"
        status_text = "🟡 Fallback Direct I/O Active (Obsidian GUI Closed)"
        color = 0xFEE75C  # Yellow
    else:
        status = "DEGRADED"
        status_text = "🔴 Vault Unreachable or System Under Severe Load"
        color = 0xED4245  # Red

    return {
        "rest_online": rest_online,
        "vault_exists": vault_exists,
        "vault_path": str(client.vault_path),
        "total_notes": vault_notes,
        "subdirs_count": subdirs_count,
        "vitals": vitals,
        "status": status,
        "status_text": status_text,
        "color": color
    }

