import os
import re
import datetime
import warnings
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent
MEMORY_DIR = BASE_DIR / "memory"
APPLICATIONS_DIR = BASE_DIR / "applications"
APPLICATIONS_DIR.mkdir(exist_ok=True)

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

def is_valid_body_paragraph(text: str) -> bool:
    t = text.strip()
    # Must be of substantive length (not just a name, title, or 1-line contact)
    if len(t) < 50:
        return False
    # Filter out Subject line
    if re.match(r'^\**Subject:\s*', t, re.IGNORECASE):
        return False
    # Filter out greetings
    if re.match(r'^(Dear|Hi|Hello|To\s+the\s+Hiring)\s+', t, re.IGNORECASE) and len(t) < 80:
        return False
    # Filter out sign-offs and signature blocks
    if re.match(r'^(Sincerely|Best|Warm\s+regards|Regards|Respectfully),?', t, re.IGNORECASE) and len(t) < 60:
        return False
    # Filter out candidate address/signature lines
    lower_t = t.lower()
    if "hanslaureles" in lower_t or "hansaaronlaureles" in lower_t or "cavite, philippines" in lower_t or "makati city" in lower_t:
        return False
    if "bs computer science" in lower_t and len(t) < 200:
        return False
    return True

def analyze_and_tailor(job_description: str, company: str = "Target Company", role: str = "Design / Engineering Role") -> dict:
    """
    Analyzes a job description against Hans's master resume and portfolio,
    generating a match score, tailored resume bullets, cover letter, and interview strategy.
    """
    from web_tools import is_bot_challenge_page
    if is_bot_challenge_page(job_description):
        raise ValueError("This job description is a Cloudflare / anti-bot verification page. Please copy and paste the job description text directly into Discord!")

    # Pre-check: If company or role are defaults, attempt immediate extraction
    if company in ["Target Company", "Job", ""] or role in ["Design / Engineering Role", "Role", "Application", ""]:
        ext_role, ext_company = extract_job_meta(job_description)
        if ext_role and ext_role != "Design / Engineering Role":
            role = ext_role
        if ext_company and ext_company != "Target Company":
            company = ext_company

    resume = load_file(MEMORY_DIR / "master_resume.md")
    portfolio = load_file(MEMORY_DIR / "portfolio_spec.md")

    prompt = f"""
You are "Scout", the strategic career and application-tailoring AI agent for the candidate. Ground all analysis, scoring, bullets, and cover letter strictly on the candidate's provided master resume and portfolio specifications below.

### CANDIDATE MASTER RESUME:
{resume}

### CANDIDATE PORTFOLIO SPECIFICATIONS:
{portfolio}

---

### TARGET JOB POSTING:
Role: {role}
Company: {company}

Job Description:
{job_description}

---

### INSTRUCTIONS:
Perform a deep, strategic analysis and produce the following tailored application package:

1. **MATCH SCORE & SUMMARY (1-100%)**:
   - Provide an honest fit percentage.
   - 2-3 sentence executive synthesis of why the candidate is a strong match.
   - Highlight the primary technical advantage grounded in the candidate's resume.

2. **TAILORED RESUME BULLET POINTS (3 to 5 bullets)**:
   - Rewrite/highlight bullets grounded strictly in the candidate's master resume and portfolio specifications, emphasizing the keywords, tools, and outcomes mentioned in this job description.
   - Emphasize multi-agent orchestration, software engineering, API integrations, and measurable results.

3. **CONCISE, COMPELLING COVER LETTER (3 to 4 paragraphs)**:
   - Hook: Genuine enthusiasm for the company and role, opening with the candidate's technical edge.
   - Body Paragraph 1: Concrete proof from the candidate's background and engineering projects demonstrating exact relevance.
   - Body Paragraph 2: Technical depth and tool proficiency matching the job requirements.
   - Close: Confident, professional call to action to discuss portfolio architecture case studies.
   - Tone: Confident, articulate, modern, human — NO generic cliches.

4. **STRATEGIC PORTFOLIO RECOMMENDATIONS**:
   - Which 1 or 2 specific projects from the candidate's portfolio specifications to lead with, and the exact talking point to emphasize.

Format your response cleanly in readable GitHub Markdown.
"""

    output_text = query_llm(prompt, temperature=0.4, agent="chaewon")

    # Post-check fallback: Extract company or role if they were generic but mentioned in the generated letter
    if company in ["Target Company", "Job", ""]:
        # Check for patterns like "Hi JWay Team" or "applying to JWay"
        c_match = re.search(r'(?:Hi|Dear|Welcome|applying to)\s+([A-Z][A-Za-z0-9_\s&]{1,25}?)(?:\s+Team|\s+because|\.|\n)', output_text)
        if c_match:
            extracted_c = c_match.group(1).strip()
            if extracted_c and extracted_c not in ["The", "Hiring"]:
                company = extracted_c

    if role in ["Design / Engineering Role", "Role", "Application", ""]:
        r_match = re.search(r'Application for\s+([A-Za-z0-9_\s\-/]{2,35}?)\s+[—\-]', output_text)
        if r_match:
            extracted_r = r_match.group(1).strip()
            if extracted_r:
                role = extracted_r

    # Extract score if present
    match_score = 85
    score_match = re.search(r"(\d{2,3})%", output_text)
    if score_match:
        try:
            match_score = int(score_match.group(1))
        except Exception:  # quiet: unparsable score keeps the default
            pass

    # Save to applications archive
    safe_company = re.sub(r'[^a-zA-Z0-9_-]', '_', company).strip('_') or "Job"
    safe_role = re.sub(r'[^a-zA-Z0-9_-]', '_', role).strip('_') or "Application"
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    filename = f"{today_str}_{safe_company}_{safe_role}.md"
    file_path = APPLICATIONS_DIR / filename

    saved_content = f"# Application Package: {role} at {company}\n"
    saved_content += f"> Date: {today_str} | Generated by Scout Agent\n\n"
    saved_content += f"## Raw Job Description\n```\n{job_description[:1000]}...\n```\n\n"
    saved_content += f"---\n\n{output_text}\n"

    file_path.write_text(saved_content, encoding="utf-8")

    # Update applications log
    log_path = MEMORY_DIR / "applications_log.md"
    if log_path.exists():
        log_entry = f"| {today_str} | {company} | {role} | Remote/Hybrid | AI Systems & Full-Stack | Ready ({match_score}%) | [{filename}](../applications/{filename}) |\n"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(log_entry)

    # Parse structured components for PDF compilation
    bullets = []
    in_bullets = False
    for line in output_text.splitlines():
        line_s = line.strip()
        if "TAILORED RESUME BULLET" in line_s.upper():
            in_bullets = True
            continue
        if in_bullets:
            if "COVER LETTER" in line_s.upper() or line_s.startswith("### 3") or line_s.startswith("3."):
                in_bullets = False
                continue
            if line_s.startswith(("- ", "* ", "• ")) or (len(line_s) > 2 and line_s[0].isdigit() and line_s[1] in [".", ")"]):
                b_clean = re.sub(r'^\s*[-*•\d.)]+\s+', '', line_s).strip()
                if b_clean:
                    # Repair unbalanced bold tags if LLM formatted as **Title:** or Title:**
                    if b_clean.count("**") % 2 != 0:
                        if b_clean.endswith("**"):
                            b_clean = "**" + b_clean
                        elif ":**" in b_clean and not b_clean.startswith("**"):
                            b_clean = "**" + b_clean
                    bullets.append(b_clean)

    # Parse cover letter paragraphs
    cover_paragraphs = []
    in_cover = False
    cur_para = []
    for line in output_text.splitlines():
        line_s = line.strip()
        if "COVER LETTER" in line_s.upper():
            in_cover = True
            cur_para = []
            continue
        if in_cover:
            # Stop condition: Only when a markdown header starts Section 4 or subsequent sections
            is_next_section = (
                (line_s.startswith("#") and any(k in line_s.upper() for k in ["PORTFOLIO", "RECOMMENDATION", "EMPLOYER", "QUESTION", "ATS"])) or
                line_s.startswith("### 4") or 
                line_s.startswith("### 5") or
                line_s.startswith("4. STRATEGIC") or
                line_s.startswith("4. **STRATEGIC")
            )
            if is_next_section:
                in_cover = False
                break
            if not line_s or line_s == "---":
                if cur_para:
                    joined = " ".join(cur_para).strip()
                    cur_para = []
                    if is_valid_body_paragraph(joined):
                        cover_paragraphs.append(joined)
            else:
                if not line_s.startswith("#") and not line_s.startswith("```"):
                    cur_para.append(line_s)
    if cur_para:
        joined = " ".join(cur_para).strip()
        if is_valid_body_paragraph(joined):
            cover_paragraphs.append(joined)

    # Executive summary extraction from Section 1
    summary_text = (
        "Computer Science graduate and Applied AI Engineer specialized in "
        "multi-agent orchestration, LLM inference pipelines, and full-stack web applications. "
        "Experienced in building automated Python tool pipelines and responsive, accessible interfaces in React and TypeScript."
    )
    in_sum = False
    sum_sentences = []
    for line in output_text.splitlines():
        line_s = line.strip()
        if "MATCH SCORE & SUMMARY" in line_s.upper() or "1. **MATCH" in line_s.upper():
            in_sum = True
            continue
        if in_sum:
            if "TAILORED RESUME BULLET" in line_s.upper() or line_s.startswith("### 2") or line_s.startswith("2."):
                in_sum = False
                break
            if line_s and not line_s.startswith("#") and not line_s.startswith("```") and "%" not in line_s:
                clean_line = re.sub(r'^\**[-*•\s]*', '', line_s)
                clean_line = re.sub(r'\*+', '', clean_line).strip()
                if len(clean_line) > 35 and not clean_line.lower().startswith("engineered intelligent matching"):
                    sum_sentences.append(clean_line)
    if sum_sentences:
        summary_text = " ".join(sum_sentences[:2])
    if len(summary_text) > 280:
        summary_text = summary_text[:280].rsplit(" ", 1)[0] + "..."

    from pdf_engine import load_master_resume_context
    context = load_master_resume_context()

    cand_name = context.get("name") or os.getenv("OWNER_NAME", "Candidate Name")
    cand_loc = context.get("location") or os.getenv("OWNER_LOCATION", "Remote / Metro Area")
    cand_email = context.get("email") or os.getenv("OWNER_EMAIL", "you@example.com")
    cand_portfolio = context.get("portfolio_url") or os.getenv("PORTFOLIO_URL", "https://example.com")
    cand_portfolio_display = os.getenv("PORTFOLIO_DISPLAY", "example.com")

    tailored_dict = {
        "name": cand_name,
        "title": role if role not in ["Design / Engineering Role", "Target Role", "Role", "Application"] else "Applied AI Engineer & Full-Stack Builder",
        "company": company,
        "role": role,
        "location": cand_loc,
        "email": cand_email,
        "portfolio_url": cand_portfolio,
        "portfolio_display": cand_portfolio_display,
        "summary": summary_text,
        "bullets": bullets[:4] if bullets else None,
        "paragraphs": cover_paragraphs[:4] if (cover_paragraphs and len(cover_paragraphs) >= 2) else None
    }

    try:
        from pdf_engine import generate_tailored_pdf_package
        pdf_res = generate_tailored_pdf_package(tailored_dict, company, role)
    except Exception as e:
        print(f"⚠️ [Scout] PDF generation error: {e}", flush=True)
        pdf_res = {"resume_pdf": None, "cover_pdf": None}

    return {
        "score": match_score,
        "company": company,
        "role": role,
        "report": output_text,
        "saved_file": str(file_path),
        "filename": filename,
        "resume_pdf": str(pdf_res["resume_pdf"]) if pdf_res.get("resume_pdf") else None,
        "cover_pdf": str(pdf_res["cover_pdf"]) if pdf_res.get("cover_pdf") else None
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

