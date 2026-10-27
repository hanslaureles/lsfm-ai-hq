import datetime
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent
MEMORY_DIR = BASE_DIR / "memory"

from llm_client import query_llm

def load_file(path: Path) -> str:
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""

def consult_career(question: str) -> str:
    """Answers career, resume, and application strategy questions with Chaewon's persona."""
    resume = load_file(MEMORY_DIR / "master_resume.md")
    portfolio = load_file(MEMORY_DIR / "portfolio_spec.md")
    
    prompt = f"""
You are "Chaewon", the sharp, tactical Career & Job Application Copilot for Hans Aaron Laureles.
Hans is chatting with you in the #job-tailoring channel.

### CANDIDATE PROFILE:
{resume}

### PORTFOLIO CONTEXT:
{portfolio}

### HANS'S QUESTION / MESSAGE:
"{question}"

---

### INSTRUCTIONS:
- Answer with actionable, tactical career advice.
- Emphasize his dual-competency advantage (BS Computer Science + high-craft UI/UX Design).
- Keep the tone confident, encouraging, direct, and professional with a touch of Chaewon's charismatic energy.
- Format cleanly for Discord markdown.
"""
    return query_llm(prompt, temperature=0.4, agent="chaewon")


def extract_job_meta(job_text: str) -> tuple[str, str]:
    """Uses LLM to identify the Job Title and Company Name from raw or scraped job text."""
    prompt = f"""
Analyze this job posting or message excerpt and determine:
1. Exact Job Title / Role (e.g. AI Engineer, Product Designer, Frontend Developer)
2. Company or Organization Name (e.g. JWay, Figma, Linear, Stripe)

Guidelines:
- Look for greetings like "Hi [Company] Team", phrases like "applying to [Company]", or email headers.
- Extract the real company name. Avoid generic placeholders like "Target Company" if a specific name is mentioned.
- Return ONLY these two lines:
Role: [Job Title]
Company: [Company Name]

Excerpt:
{job_text[:3000]}
"""
    try:
        res_text = query_llm(prompt, temperature=0.1, agent="chaewon")
        role = "Design / Engineering Role"
        company = "Target Company"
        for line in res_text.splitlines():
            line_s = line.strip()
            if line_s.lower().startswith("role:"):
                role = line_s.split(":", 1)[1].strip()
            elif line_s.lower().startswith("company:"):
                company = line_s.split(":", 1)[1].strip()
        return role, company
    except Exception as _exc:
        print(f"⚠️ [scout.extract_job_meta] suppressed {type(_exc).__name__}: {_exc}", flush=True)
        return "Design / Engineering Role", "Target Company"

def analyze_and_tailor(job_description: str, company: str = "Target Company", role: str = "Design / Engineering Role") -> dict:
    """
    The package for a pasted job (#job-tailoring, !tailor, !apply), built by the job hunt's guarded tailoring
    (job_hunt.tailor_into): skills only reordered from master_resume.md, project text untouched, a second model
    checking the letter's claims. The job gets a job-hunt folder like a hunted one, so the hunt never re-cards it.
    Raises ValueError when no tailoring passes the checks; the folder stays needs_claude and the next hunt retries it.
    """
    import job_hunt
    from pdf_engine import load_master_resume_context
    from web_tools import is_bot_challenge_page
    if is_bot_challenge_page(job_description):
        raise ValueError("This job description is a Cloudflare / anti-bot verification page. Please copy and paste the job description text directly into Discord!")

    if company in ["Target Company", "Job", ""] or role in ["Design / Engineering Role", "Role", "Application", ""]:
        ext_role, ext_company = extract_job_meta(job_description)
        if ext_role and ext_role != "Design / Engineering Role":
            role = ext_role
        if ext_company and ext_company != "Target Company":
            company = ext_company

    ctx = load_master_resume_context()
    if "_error" in ctx:
        raise ValueError(ctx["_error"])
    now = datetime.datetime.now()
    apps = job_hunt.JOB_HUNT_DIR / "applications"
    apps.mkdir(parents=True, exist_ok=True)
    d = apps / job_hunt.folder_name(now.date(), company, role, {p.name for p in apps.iterdir()})
    d.mkdir()
    (d / "posting.txt").write_text(f"{role} at {company}\n(pasted in Discord)\n\n{job_description}", encoding="utf-8")
    meta = {"id": d.name, "company": company, "role": role, "site": "pasted", "url": "", "posted": "",
            "found": now.date().isoformat(), "fit": 0, "why": "", "salary": "", "thin": False, "status": "needs_claude",
            "card_msg_id": 0, "question": "", "reason": "", "built_by": "lsfm-pasted", "updated": now.strftime("%Y-%m-%dT%H:%M")}
    try:
        res = job_hunt.tailor_into(d, {"title": role, "company": company, "site": "pasted"}, job_description, ctx)
    except Exception as e:
        res = {"tailoring": None, "errors": [f"{type(e).__name__}: {e}"]}
    if res["tailoring"]:
        meta.update(job_hunt.tailored_fields(res))
    if res["errors"]:
        meta["reason"] = "; ".join(res["errors"])[:300]
        job_hunt.write_meta(d, meta)
        raise ValueError(f"no tailoring passed the checks ({meta['reason'][:200]}); the next job hunt retries it")
    meta["status"] = "pasted"  # tailored and answered here: never carded again by the hunt
    job_hunt.write_meta(d, meta)

    t = res["tailoring"]
    lines = [f"# Application Package: {role} at {company}", f"> {now:%Y-%m-%d} | Chaewon (guarded tailoring)", "",
             f"## Fit: {res['fit']}%", res["why"]]
    if res["gaps"]:
        lines.append("**Gaps:** " + "; ".join(res["gaps"]))
    if res["warnings"]:
        lines.append("**⚠️ Check before sending:** " + "; ".join(res["warnings"]))
    lines += ["", f"**Resume title:** {t.get('title', '')}",
              "**Project order:** " + ", ".join(t.get("project_order") or []), "", "## Cover letter",
              *[p + "\n" for p in t.get("cover_paragraphs") or []]]
    report = "\n".join(lines)
    saved = d / "package.md"
    saved.write_text(report, encoding="utf-8")

    log_path = MEMORY_DIR / "applications_log.md"
    if log_path.exists():
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"| {now:%Y-%m-%d} | {company} | {role} | Remote | {res['why'][:60].replace('|', '/')} | Ready ({res['fit']}%) | {d.name} |\n")

    return {
        "score": res["fit"],
        "company": company,
        "role": role,
        "report": report,
        "saved_file": str(saved),
        "filename": f"{d.name}.md",
        "resume_pdf": res.get("resume"),
        "cover_pdf": res.get("cover_letter"),
    }


def answer_screening_questions(questions_text: str) -> str:
    """Answers employer screening questionnaire / knockout questions using master profile."""
    resume = load_file(MEMORY_DIR / "master_resume.md")
    portfolio = load_file(MEMORY_DIR / "portfolio_spec.md")

    prompt = f"""
You are "Chaewon", the elite Career Copilot for the candidate.
Your task is to write high-converting, ATS-optimized answers to the employer's screening questions / questionnaire.

### CANDIDATE MASTER PROFILE:
{resume}

### PORTFOLIO & PROJECTS:
{portfolio}

### EMPLOYER SCREENING QUESTION(S):
{questions_text}

### INSTRUCTIONS:
1. Follow the A-P-M (Affirm + Prove + Metric/Stack) formula:
   - Direct Affirmation: Start with a direct, unambiguous positive confirmation of the requirement (e.g. "Yes, I have 3+ years of..."). Never write just "yes" or hedge.
   - Proof of Work: Name the exact tools, languages, frameworks, or skills requested in the question, grounded strictly in the candidate's actual projects from the provided resume and portfolio.
   - Metric & Production Context: Mention responsiveness, performance, users, or workflows.
2. Character Limit: Ensure each answer is between 300 and 650 characters (well within the typical 1,500-character box), punchy, scannable, and confident.
3. Output format for Discord:
   For each question:
   - ❓ **Question:** [Question text]
   - 📋 **Ready-to-Paste Answer:**
   ```text
   [Answer text ready to copy]
   ```
"""
    return query_llm(prompt, temperature=0.3, agent="chaewon")

