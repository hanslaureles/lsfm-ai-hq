import os
import re
import subprocess
from pathlib import Path
from typing import Optional, Dict, Any, Tuple, List

BASE_DIR = Path(__file__).parent
APPLICATIONS_DIR = BASE_DIR / "applications"
APPLICATIONS_DIR.mkdir(exist_ok=True)

LIVE_PORTFOLIO_URL = os.getenv("PORTFOLIO_URL", "https://example.com")
LIVE_PORTFOLIO_DISPLAY = os.getenv("PORTFOLIO_DISPLAY", "example.com")
# Contact line on generated resumes and cover letters; set OWNER_EMAIL in .env.
OWNER_EMAIL = os.getenv("OWNER_EMAIL", "you@example.com")
# The serif (Harvard) layout embeds regular, bold and italic font subsets: 107 KB on 2026-10-21.
RESUME_SIZE_TARGET_KB = 125

def find_browser_path() -> Optional[str]:
    """Locates Microsoft Edge or Google Chrome for headless PDF rendering."""
    candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    for c in candidates:
        if Path(c).exists():
            return c
    return None

def format_inline_markdown(text: str) -> str:
    """Converts inline markdown (bold, italic, code) into valid semantic HTML."""
    if not text:
        return ""
    # Bold: **text** -> <strong>text</strong>
    t = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    # Italic: *text* -> <em>text</em>
    t = re.sub(r'(?<!\*)\*([^*]+?)\*(?!\*)', r'<em>\1</em>', t)
    # Code: `code` -> <code>code</code>
    t = re.sub(r'`(.+?)`', r'<code>\1</code>', t)
    return t

def render_html_to_pdf(html_content: str, output_pdf_path: Path) -> bool:
    """Renders an HTML string into an ATS-compliant vector PDF using headless Edge/Chrome."""
    browser = find_browser_path()
    if not browser:
        print("⚠️ [PDF Engine] Error: No compatible headless browser (Edge/Chrome) found on system.")
        return False

    temp_html = output_pdf_path.with_suffix(".temp.html")
    try:
        temp_html.write_text(html_content, encoding="utf-8")
        cmd = [
            browser,
            "--headless",
            "--disable-gpu",
            "--no-pdf-header-footer",
            f"--print-to-pdf={output_pdf_path.resolve()}",
            str(temp_html.resolve())
        ]
        subprocess.run(cmd, capture_output=True, text=True, timeout=25)
        return output_pdf_path.exists() and output_pdf_path.stat().st_size > 1000
    except Exception as e:
        print(f"⚠️ [PDF Engine] Error generating PDF: {e}")
        return False
    finally:
        if temp_html.exists():
            try:
                temp_html.unlink()
            except Exception:  # quiet: best-effort temp file cleanup
                pass

def parse_markdown_resume(content: str) -> Tuple[Dict[str, Any], Optional[str]]:
    """
    Parses candidate profile from Markdown resume content.
    Supports section-style Markdown:
      - # Full Name (or - **Full Name:** ...)
      - Contact bullets: Location, Email, GitHub, Portfolio
      - ## Summary / ## Professional Summary
      - ## Experience / ## Professional Work Experience (### Role, Company (Dates) + bullets)
      - ## Projects / ## Featured Engineering Projects (### Project (Tag) + bullets)
      - ## Skills / ## Technical Skills / ## Technical Proficiencies
      - ## Education (### Degree, Institution (Dates) or - **Degree:** ...)
    Returns (context_dict, error_message).
    Rejects default templates or incomplete profiles.
    """
    if not content or not content.strip():
        return {}, "Resume content in memory/master_resume.md is empty."

    # Check for unedited template markers
    if "> Template. Replace this file" in content or content.strip().startswith("# Your Name"):
        return {}, "memory/master_resume.md contains the default template. Replace it with your actual profile before compiling."

    lines = content.splitlines()
    ctx: Dict[str, Any] = {
        "bullets": [],
        "projects": [],
        "experience": []
    }

    # 1. Parse top-level header / name
    for line in lines:
        line_s = line.strip()
        if line_s.startswith("# ") and not ctx.get("name"):
            raw_title = line_s[2:].strip()
            name_part = re.split(r'\s+[—–-]\s+', raw_title)[0].strip()
            if name_part and name_part.lower() not in ["your name", "candidate name", "candidate"]:
                ctx["name"] = name_part
        elif line_s.startswith("- **Full Name:**") or line_s.startswith("- Full Name:"):
            val = line_s.split(":", 1)[1].strip().strip("*").strip()
            if val and val.lower() not in ["your name", "candidate name"]:
                ctx["name"] = val

    # 2. Parse contact info from header or bullet lines
    for line in lines:
        line_s = line.strip()
        short_m = re.match(r'^[-*]\s+\*{0,2}(Location|Degree) \(short\):\*{0,2}\s*(.+)', line_s, re.IGNORECASE)
        if short_m:  # header-only variants; the full values stay for cover letters
            key = "location_short" if short_m.group(1).lower() == "location" else "education_short"
            ctx[key] = short_m.group(2).strip()
        elif re.match(r'^[-*]\s+\*{0,2}Location:\*{0,2}', line_s, re.IGNORECASE):
            ctx["location"] = re.sub(r'^[-*]\s+\*{0,2}Location:\*{0,2}\s*', '', line_s, flags=re.IGNORECASE).strip()
        elif re.match(r'^[-*]\s+\*{0,2}Email:\*{0,2}', line_s, re.IGNORECASE):
            ctx["email"] = re.sub(r'^[-*]\s+\*{0,2}Email:\*{0,2}\s*', '', line_s, flags=re.IGNORECASE).strip()
        elif re.match(r'^[-*]\s+\*{0,2}GitHub:\*{0,2}', line_s, re.IGNORECASE):
            ctx["github_url"] = re.sub(r'^[-*]\s+\*{0,2}GitHub:\*{0,2}\s*', '', line_s, flags=re.IGNORECASE).strip()
        elif re.match(r'^[-*]\s+\*{0,2}(Portfolio|Portfolio Website|Website):\*{0,2}', line_s, re.IGNORECASE):
            ctx["portfolio_url"] = re.sub(r'^[-*]\s+\*{0,2}(Portfolio|Portfolio Website|Website):\*{0,2}\s*', '', line_s, flags=re.IGNORECASE).strip()
        elif re.match(r'^[-*]\s+\*{0,2}Title:\*{0,2}', line_s, re.IGNORECASE):
            ctx["title"] = re.sub(r'^[-*]\s+\*{0,2}Title:\*{0,2}\s*', '', line_s, flags=re.IGNORECASE).strip()
        elif re.match(r'^[-*]\s+\*{0,2}Target Roles?:\*{0,2}', line_s, re.IGNORECASE):
            ctx["target_roles"] = re.sub(r'^[-*]\s+\*{0,2}Target Roles?:\*{0,2}\s*', '', line_s, flags=re.IGNORECASE).strip()
        elif line_s.startswith("- **Professional Summary:**"):
            ctx["summary"] = line_s.split(":", 1)[1].lstrip("* ").strip()
    if not ctx.get("title") and ctx.get("target_roles"):
        ctx["title"] = ctx["target_roles"]

    # 3. Partition sections by ## headings
    sections: Dict[str, List[str]] = {}
    current_sec = None
    for line in lines:
        if line.strip().startswith("## "):
            sec_name = line.strip()[3:].strip().lower()
            current_sec = sec_name
            sections[current_sec] = []
        elif current_sec and not re.fullmatch(r'\s*([-*_])\1{2,}\s*', line):  # skip --- rules
            sections[current_sec].append(line)

    # 4. Parse sections
    for sec_name, sec_lines in sections.items():
        # Highlights: the Key Technical Impact bullets, chosen by hand across projects
        if "highlight" in sec_name:
            ctx["bullets"] = [l.strip()[2:].strip() for l in sec_lines if l.strip().startswith(("- ", "* "))]

        # A. Summary
        elif "summary" in sec_name:
            if not ctx.get("summary"):
                summary_text = " ".join([l.strip() for l in sec_lines if l.strip() and not l.strip().startswith("#") and not l.strip().startswith("- **")])
                if summary_text:
                    ctx["summary"] = summary_text

        # B. Experience
        elif "experience" in sec_name:
            exp_items = []
            curr_exp: Optional[Dict[str, Any]] = None
            for l in sec_lines:
                ls = l.strip()
                if ls.startswith("### "):
                    if curr_exp:
                        exp_items.append(curr_exp)
                    heading = ls[4:].strip()
                    role = heading
                    company = ""
                    dates = ""
                    date_m = re.search(r'\(([^)]+)\)$', heading)
                    if date_m:
                        dates = date_m.group(1).strip()
                        heading_no_date = heading[:date_m.start()].strip()
                    else:
                        heading_no_date = heading

                    if "," in heading_no_date:
                        parts = heading_no_date.split(",", 1)
                        role = parts[0].strip()
                        company = parts[1].strip()
                    elif "·" in heading_no_date:
                        parts = heading_no_date.split("·", 1)
                        role = parts[0].strip()
                        company = parts[1].strip()
                    elif " at " in heading_no_date:
                        parts = heading_no_date.split(" at ", 1)
                        role = parts[0].strip()
                        company = parts[1].strip()

                    curr_exp = {
                        "title": role,
                        "company": company,
                        "date": dates,
                        "desc": "",
                        "bullets": []
                    }
                elif curr_exp:
                    if re.match(r'^[-*]\s+\*{0,2}Company:\*{0,2}', ls, re.IGNORECASE):
                        curr_exp["company"] = re.sub(r'^[-*]\s+\*{0,2}Company:\*{0,2}\s*', '', ls, flags=re.IGNORECASE).strip()
                    elif re.match(r'^[-*]\s+\*{0,2}(Period|Dates?):\*{0,2}', ls, re.IGNORECASE):
                        curr_exp["date"] = re.sub(r'^[-*]\s+\*{0,2}(Period|Dates?):\*{0,2}\s*', '', ls, flags=re.IGNORECASE).strip()
                    elif ls.startswith("- ") or ls.startswith("* "):
                        bullet = ls[2:].strip()
                        if not bullet.lower().startswith("**key contributions"):
                            curr_exp["bullets"].append(bullet)
                    elif ls and not ls.startswith("#"):
                        if not curr_exp["desc"]:
                            curr_exp["desc"] = ls
                        else:
                            curr_exp["desc"] += " " + ls

            if curr_exp:
                exp_items.append(curr_exp)

            if exp_items:
                ctx["experience"] = exp_items
                first = exp_items[0]
                ctx["experience_title"] = first.get("title", "")
                ctx["experience_company"] = first.get("company", "")
                ctx["experience_date"] = first.get("date", "")
                if not first.get("desc") and first.get("bullets"):
                    ctx["experience_desc"] = " ".join(first["bullets"])
                else:
                    ctx["experience_desc"] = first.get("desc", "")

        # C. Projects
        elif "project" in sec_name:
            proj_items = []
            curr_proj: Optional[Dict[str, Any]] = None
            in_resume_bullets = False  # inside an indented "- **Resume bullets:**" list
            for l in sec_lines:
                ls = l.strip()
                if ls.startswith("### "):
                    in_resume_bullets = False
                    if curr_proj:
                        proj_items.append(curr_proj)
                    heading = ls[4:].strip()
                    heading_clean = re.sub(r'^\d+\.\s*', '', heading)
                    p_name = heading_clean
                    p_tag = ""
                    for sep in [" — ", " – ", " - ", " ("]:
                        if sep in heading_clean:
                            parts = heading_clean.split(sep, 1)
                            p_name = parts[0].strip()
                            p_tag = parts[1].rstrip(")").strip()
                            break
                    curr_proj = {"name": p_name, "tag": p_tag, "desc": "", "bullets": []}
                elif curr_proj:
                    if ls.startswith("- ") or ls.startswith("* "):
                        bullet = ls[2:].strip()
                        if l[:1] not in " \t":  # a top-level field ends the Resume bullets list
                            in_resume_bullets = bullet.lower().startswith("**resume bullets:**")
                        if bullet.lower().startswith("**resume bullets:**"):
                            pass
                        elif in_resume_bullets:  # the bullets the resume prints for this project
                            curr_proj.setdefault("resume_bullets", []).append(bullet)
                        elif bullet.lower().startswith("**period:**"):
                            curr_proj["date"] = bullet.split(":", 1)[1].lstrip("* ").strip()
                        elif bullet.lower().startswith("**resume line:**"):
                            curr_proj["desc"] = bullet.split(":", 1)[1].lstrip("* ").strip()
                        elif bullet.lower().startswith("**role:**"):
                            credit_m = re.search(r'\(([^)]+)\)\s*$', bullet)  # "Developer (Solo Project)"
                            if credit_m:
                                curr_proj["credit"] = credit_m.group(1).strip()
                        elif not any(bullet.lower().startswith(x) for x in ["**role:**", "**period:**", "**discipline:**", "**stack:**", "**key contributions"]):
                            curr_proj["bullets"].append(bullet)
                    elif ls and not ls.startswith("#") and not ls.startswith("- **"):
                        if not curr_proj["desc"]:
                            curr_proj["desc"] = ls
                        else:
                            curr_proj["desc"] += " " + ls
            if curr_proj:
                proj_items.append(curr_proj)

            for p in proj_items:
                if not p.get("desc") and p.get("bullets"):
                    p["desc"] = " ".join(p["bullets"][:2])

            if proj_items:
                ctx["projects"] = proj_items

        # D. Skills
        elif "skill" in sec_name or "proficienc" in sec_name:
            skills_lines = [l.strip() for l in sec_lines if l.strip()]
            for sl in skills_lines:
                sl_clean = sl.lstrip("-* ").strip()
                if ":" in sl_clean:
                    cat, vals = sl_clean.split(":", 1)
                    cat_lower = cat.lower()
                    vals_clean = vals.strip().strip("*").strip().rstrip(".")
                    key = None
                    if any(k in cat_lower for k in ["ai", "llm", "ml"]):
                        key = "skills_ai"
                    elif any(k in cat_lower for k in ["lang", "backend", "system"]):
                        key = "skills_sys"
                    elif any(k in cat_lower for k in ["front", "ui", "mobile", "design"]):
                        key = "skills_ui"
                    elif any(k in cat_lower for k in ["cloud", "data"]):
                        key = "skills_cloud"
                    if key:  # several lines can feed one row; keep them all
                        ctx[key] = f"{ctx[key]}, {vals_clean}" if ctx.get(key) else vals_clean

        # E. Education
        elif "education" in sec_name:
            for l in sec_lines:
                ls = l.strip()
                if ls.startswith("### "):
                    heading = ls[4:].strip()
                    dates = ""
                    date_m = re.search(r'\(([^)]+)\)$', heading)
                    if date_m:
                        dates = date_m.group(1).strip()
                        heading = heading[:date_m.start()].strip()
                    if "," in heading:
                        parts = heading.split(",", 1)
                        ctx["education_degree"] = parts[0].strip()
                        ctx["education_institution"] = parts[1].strip()
                    else:
                        ctx["education_degree"] = heading
                    if dates:
                        ctx["education_dates"] = dates
                elif ls.startswith("- **Degree:**") or ls.startswith("- Degree:"):
                    ctx["education_degree"] = ls.split(":", 1)[1].strip().strip("*").strip()
                elif ls.startswith("- **Institution:**") or ls.startswith("- Institution:"):
                    ctx["education_institution"] = ls.split(":", 1)[1].strip().strip("*").strip()
                elif ls.startswith("- **Years:**") or ls.startswith("- Years:"):
                    ctx["education_dates"] = ls.split(":", 1)[1].strip().strip("*").strip()
                elif ls.startswith("- **Relevant Coursework:**") or "coursework:" in ls.lower():
                    ctx["education_coursework"] = ls.split(":", 1)[1].strip().strip("*").strip()

    # Key Technical Impact bullets come only from a Highlights section: copying the
    # experience bullets here printed the internship twice on one page.

    # Required fields check
    missing = []
    if not ctx.get("name"):
        missing.append("Full Name (# Your Name or - **Full Name:**)")
    if not ctx.get("summary"):
        missing.append("Professional Summary (## Summary)")
    if not (ctx.get("experience") or ctx.get("experience_title")):
        missing.append("Work Experience (## Experience)")
    if not (ctx.get("education_degree") or ctx.get("education_institution")):
        missing.append("Education (## Education)")

    if missing:
        return ctx, f"Incomplete resume profile in memory/master_resume.md: missing required fields ({', '.join(missing)}). See memory/master_resume.md for accepted format."

    return ctx, None


def load_master_resume_context(resume_path: Optional[Path] = None) -> Dict[str, Any]:
    """
    Loads candidate profile data from memory/master_resume.md.
    Returns parsed dictionary. If missing, template, or invalid, includes '_error' key with explanation.
    """
    resume_md = resume_path or (BASE_DIR / "memory" / "master_resume.md")
    if not resume_md.exists():
        return {"_error": f"Master resume file not found at {resume_md}. Create it before compiling."}
    try:
        content = resume_md.read_text(encoding="utf-8")
        ctx, err = parse_markdown_resume(content)
        if err:
            ctx["_error"] = err
        return ctx
    except Exception as e:
        return {"_error": f"Failed to read master resume at {resume_md}: {e}"}


def build_resume_html(data: Dict[str, Any]) -> str:
    """
    Builds a 1-page Harvard-style HTML resume: centered name and contact line, then
    Education, Experience, Projects and Skills; serif, black only, single column.
    Uses semantic HTML5 and selectable text so ATS parsers can read every section.
    Renders ONLY data supplied in data dictionary (zero invented credentials).
    The summary and title stay in the data (cover letters, tailoring) but are not printed.
    """
    name = data.get("name") or os.getenv("OWNER_NAME", "Candidate Name")

    location = data.get("location_short") or data.get("location") or os.getenv("OWNER_LOCATION", "")
    email = data.get("email") or os.getenv("OWNER_EMAIL", OWNER_EMAIL)
    portfolio_url = data.get("portfolio_url") or os.getenv("PORTFOLIO_URL", "")
    portfolio_display = data.get("portfolio_display") or os.getenv("PORTFOLIO_DISPLAY", portfolio_url.replace("https://", "").replace("http://", "").rstrip("/"))
    github_url = data.get("github_url") or os.getenv("OWNER_GITHUB", "")
    github_display = github_url.replace("https://", "").replace("http://", "").rstrip("/")

    contact_parts = []
    if location:
        contact_parts.append(f"<span>{location}</span>")
    if email:
        contact_parts.append(f'<span><a href="mailto:{email}">{email}</a></span>')
    if portfolio_url:
        contact_parts.append(f'<span><a href="{portfolio_url}">{portfolio_display}</a></span>')
    if github_url:
        contact_parts.append(f'<span><a href="{github_url}">{github_display}</a></span>')
    contact_html = " • ".join(contact_parts)

    def entry(left_bold: str, right_1: str, left_italic: str, right_2: str, bullets: List[str]) -> str:
        """One Harvard entry: bold name | place, italic role | dates, then bullets."""
        rows = f'<div class="row"><span class="org">{left_bold}</span><span>{right_1}</span></div>'
        if left_italic or right_2:
            rows += f'<div class="row"><span class="role">{left_italic}</span><span>{right_2}</span></div>'
        items = "".join(f"<li>{format_inline_markdown(b)}</li>" for b in bullets if b)
        return f'<div class="entry">{rows}{f"<ul>{items}</ul>" if items else ""}</div>'

    edu_degree = data.get("education_degree", "")
    edu_school = data.get("education_institution", "")
    edu_dates = data.get("education_dates", "")
    edu_coursework = data.get("education_coursework", "").rstrip(".")
    edu_html = ""
    if edu_degree or edu_school:
        course_html = f'<p>Relevant coursework: {edu_coursework}.</p>' if edu_coursework else ""
        edu_html = (f'<div class="entry"><div class="row"><span class="org">{edu_school or edu_degree}</span><span>{edu_dates}</span></div>'
                    + (f'<div class="row"><span class="role">{edu_degree}</span><span></span></div>' if edu_school else "")
                    + f'{course_html}</div>')

    experiences = data.get("experience", [])
    if not experiences and (data.get("experience_title") or data.get("experience_company")):
        experiences = [{
            "title": data.get("experience_title", ""),
            "company": data.get("experience_company", ""),
            "date": data.get("experience_date", ""),
            "desc": data.get("experience_desc", ""),
            "bullets": data.get("experience_bullets", [])
        }]
    exp_html = ""
    for exp in experiences[:3]:
        org, _, place = exp.get("company", "").partition(" · ")  # "Company · City, Country"
        bullets = exp.get("bullets", [])[:3] or [exp.get("desc", "")]
        exp_html += entry(org or exp.get("title", ""), place, exp.get("title", "") if org else "", exp.get("date", ""), bullets)

    highlights = [b for b in data.get("bullets", []) if b][:4]
    highlights_html = "".join(f"<li>{format_inline_markdown(b)}</li>" for b in highlights)

    proj_html = ""
    for p in data.get("projects", [])[:4]:
        bullets = p.get("resume_bullets") or [p.get("desc", "")]
        proj_html += entry(p.get("name", ""), p.get("credit", ""), p.get("tag", ""), p.get("date", ""), bullets)

    skill_rows = [("AI & LLM", data.get("skills_ai", "")), ("Languages & Backend", data.get("skills_sys", "")),
                  ("Frontend & Design", data.get("skills_ui", "")), ("Cloud & Data", data.get("skills_cloud", ""))]
    skills_html = "".join(f"<p><strong>{label}:</strong> {vals}</p>" for label, vals in skill_rows if vals)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{name} — Resume</title>
<style>
  @page {{ size: letter; margin: 0.5in 0.65in; }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: "Times New Roman", Times, serif; color: #000; background: #fff; font-size: 10.5pt; line-height: 1.22; }}
  a {{ color: #000; text-decoration: none; }}
  header {{ text-align: center; margin-bottom: 10px; }}
  h1 {{ font-size: 18pt; font-weight: 700; }}
  .contact {{ margin-top: 2px; }}
  h2 {{ font-size: 10.5pt; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px;
        border-bottom: 1px solid #000; padding-bottom: 1px; margin: 10px 0 5px; }}
  .entry {{ margin-bottom: 6px; }}
  .row {{ display: flex; justify-content: space-between; gap: 12px; }}
  .org {{ font-weight: 700; }}
  .role {{ font-style: italic; }}
  ul {{ margin: 2px 0 0 18px; }}
  li {{ margin-bottom: 1px; }}
  .skills p {{ margin-bottom: 2px; }}
</style>
</head>
<body>

<header>
  <h1>{name}</h1>
  <p class="contact">{contact_html}</p>
</header>

<main>
"""
    if edu_html:
        html += f"\n  <section>\n    <h2>Education</h2>\n    {edu_html}\n  </section>\n"
    if exp_html:
        html += f"\n  <section>\n    <h2>Experience</h2>\n    {exp_html}\n  </section>\n"
    if highlights_html:
        html += f"\n  <section>\n    <h2>Highlights</h2>\n    <ul>{highlights_html}</ul>\n  </section>\n"
    if proj_html:
        html += f"\n  <section>\n    <h2>Projects</h2>\n    {proj_html}\n  </section>\n"
    if skills_html:
        html += f'\n  <section>\n    <h2>Skills</h2>\n    <div class="skills">{skills_html}</div>\n  </section>\n'

    html += """</main>

</body>
</html>
"""
    return html


def build_cover_letter_html(data: Dict[str, Any]) -> str:
    """Builds a matching executive 1-page Cover Letter PDF."""
    name = data.get("name") or os.getenv("OWNER_NAME", "Candidate Name")
    role = data.get("role", "Applied AI Engineer")
    if not role or role in ["Design / Engineering Role", "Target Role", "Role", "Application"]:
        role = "Applied AI Engineer & Full-Stack Builder"

    company = data.get("company", "Target Company")
    if not company or company in ["Target Company", "Job", ""]:
        company = "The Hiring Team"

    title = data.get("title", f"{role} Candidate")
    location = data.get("location") or os.getenv("OWNER_LOCATION", "Remote / Metro Area")
    email = data.get("email") or os.getenv("OWNER_EMAIL", OWNER_EMAIL)
    portfolio_url = data.get("portfolio_url") or os.getenv("PORTFOLIO_URL", "https://example.com")
    portfolio_display = data.get("portfolio_display") or os.getenv("PORTFOLIO_DISPLAY", "example.com")
    
    portfolio_link = f'<a href="{portfolio_url}" target="_blank" style="color: #2563eb; text-decoration: none;">{portfolio_display}</a>'
    email_link = f'<a href="mailto:{email}" style="color: #2563eb; text-decoration: none;">{email}</a>'

    date_str = data.get("date_str", "October 2026")

    raw_paragraphs = data.get("paragraphs", [
        f"I am writing to express my strong enthusiasm for the {role} position at {company}. As a software engineer, I specialize in bridging systems thinking with production-grade engineering rigor.",
        "In my past work, I have designed and delivered scalable backend architectures, built automated tool pipelines, and developed responsive, accessible interfaces that balance usability with fast performance.",
        f"What excites me most about {company} is the commitment to craftsmanship and thoughtful product execution. I would welcome the opportunity to discuss how my technical skills and proactive execution can support your team's goals.",
        "Thank you for your time and consideration. I look forward to the opportunity to speak with you."
    ])

    clean_paragraphs = []
    for p in raw_paragraphs:
        p_s = p.strip()
        # Filter out redundant Subject: ... lines
        if re.match(r'^\**Subject:\s*', p_s, re.IGNORECASE):
            continue
        # Filter out standalone greeting "Hi ... Team," if present at start of body
        if re.match(r'^(Hi|Dear|Hello)\s+.*?Team,?', p_s, re.IGNORECASE) and len(p_s) < 40:
            continue
        if p_s:
            clean_paragraphs.append(format_inline_markdown(p_s))

    if len(clean_paragraphs) < 2:
        default_paras = [
            f"I am writing to express my strong enthusiasm for the {role} position at {company}. My background spans software engineering, distributed systems, and modern full-stack application development.",
            "In my experience building production systems, I have engineered event-driven backend services, developed API integration pipelines, and built responsive, user-centered web applications.",
            f"What excites me about {company} is the opportunity to contribute to high-impact technical initiatives. I would welcome the opportunity to discuss how my systems engineering background and production experience can support your team.",
            "Thank you for your time and consideration. I look forward to speaking with you."
        ]
        clean_paragraphs = [format_inline_markdown(p) for p in default_paras]

    paragraphs_html = "".join(f"<p>{p}</p>" for p in clean_paragraphs)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{name} — Cover Letter for {company}</title>
<style>
  @page {{
    size: letter;
    margin: 0.55in 0.65in 0.55in 0.65in;
  }}
  * {{
    box-sizing: border-box;
    margin: 0;
    padding: 0;
  }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    color: #1a1a1a;
    background: #ffffff;
    font-size: 10pt;
    line-height: 1.55;
    -webkit-font-smoothing: antialiased;
  }}
  header {{
    margin-bottom: 20px;
    border-bottom: 2px solid #111827;
    padding-bottom: 10px;
  }}
  .name-row {{
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    margin-bottom: 3px;
  }}
  h1 {{
    font-size: 21pt;
    font-weight: 800;
    letter-spacing: -0.5px;
    color: #0f172a;
  }}
  .target-title {{
    font-size: 10.5pt;
    font-weight: 600;
    color: #2563eb;
    text-transform: uppercase;
    letter-spacing: 0.8px;
  }}
  .contact-bar {{
    display: flex;
    flex-wrap: wrap;
    gap: 14px;
    font-size: 9.2pt;
    color: #475569;
    font-weight: 500;
    margin-top: 4px;
  }}
  .letter-meta {{
    margin-bottom: 20px;
    font-size: 9.8pt;
    color: #334155;
    line-height: 1.45;
  }}
  .letter-meta .date {{
    font-weight: 600;
    color: #64748b;
    margin-bottom: 10px;
  }}
  .recipient {{
    font-weight: 700;
    color: #0f172a;
    font-size: 10.5pt;
  }}
  .company-name {{
    font-weight: 700;
    color: #0f172a;
    font-size: 11pt;
    margin-top: 1px;
  }}
  .regarding {{
    margin-top: 4px;
    font-weight: 600;
    color: #2563eb;
  }}
  .body-content p {{
    margin-bottom: 13px;
    color: #334155;
    text-align: justify;
  }}
  .body-content strong {{
    color: #0f172a;
  }}
  .signoff {{
    margin-top: 20px;
  }}
  .signoff p {{
    margin-bottom: 4px;
  }}
  .signature-name {{
    font-size: 11.5pt;
    font-weight: 700;
    color: #0f172a;
  }}
</style>
</head>
<body>

<header>
  <div class="name-row">
    <h1>{name}</h1>
    <div class="target-title">{title}</div>
  </div>
  <div class="contact-bar">
    <span>{location}</span>
    <span>{email_link}</span>
    <span>{portfolio_link}</span>
  </div>
</header>

<div class="letter-meta">
  <div class="date">{date_str}</div>
  <div class="recipient">To the Hiring Team</div>
  <div class="company-name">{company}</div>
  <div class="regarding">Regarding: Application for {role}</div>
</div>

<div class="body-content">
  {paragraphs_html}
</div>

<div class="signoff">
  <p>Sincerely,</p>
  <div class="signature-name">{name}</div>
  <p style="font-size: 9.2pt; color: #64748b;">{title}</p>
</div>

</body>
</html>
"""
    return html

def validate_resume_context(context: Dict[str, Any]) -> Optional[str]:
    """
    Validates that candidate context is non-empty, non-template, and has required profile fields.
    Returns error message string if invalid, or None if valid.
    """
    if not context:
        return "Resume context is empty."
    if "_error" in context:
        return context["_error"]
    return None


def generate_tailored_pdf_package(tailored_dict: Dict[str, Any], company: str, role: str, output_dir: Optional[Path] = None) -> Dict[str, Any]:
    """
    Generates both the Resume PDF and Cover Letter PDF for a given tailored application.
    Refuses generation and returns None paths if master_resume.md contains the default
    template or has an incomplete/unparseable profile.
    """
    context = load_master_resume_context()
    err = validate_resume_context(context)
    if err:
        return {
            "resume_pdf": None,
            "cover_pdf": None,
            "error": err,
        }

    out_dir = output_dir or APPLICATIONS_DIR
    out_dir.mkdir(exist_ok=True)
    
    safe_company = re.sub(r'[^a-zA-Z0-9_-]', '_', company).strip('_') or "TargetCompany"

    raw_name = tailored_dict.get("name") or context.get("name") or os.getenv("OWNER_NAME", "Candidate")
    slug_name = re.sub(r'[^A-Za-z0-9]+', '_', raw_name.strip()).strip('_')
    
    resume_pdf_path = out_dir / f"{slug_name}_Resume_{safe_company}.pdf"
    cover_pdf_path = out_dir / f"{slug_name}_CoverLetter_{safe_company}.pdf"
    
    # Render Resume
    resume_html = build_resume_html(tailored_dict)
    resume_ok = render_html_to_pdf(resume_html, resume_pdf_path)
    
    # Render Cover Letter
    cover_html = build_cover_letter_html(tailored_dict)
    cover_ok = render_html_to_pdf(cover_html, cover_pdf_path)
    
    return {
        "resume_pdf": resume_pdf_path if resume_ok else None,
        "cover_pdf": cover_pdf_path if cover_ok else None,
    }


# Strings that change on every build without the page changing: the browser names its
# version in /Creator and /Producer, and stamps /CreationDate and /ModDate.
_PDF_BUILD_STAMP = re.compile(rb"/(?:Creator|Producer|CreationDate|ModDate) ?\((?:\\.|[^\\)])*\)")

PORTFOLIO_SYNC_TEXT = {
    "copied": "✅ updated",
    "unchanged": "✅ already current (same content, not rewritten)",
    "skipped": "⏭️ skipped",
}


def pdf_content_key(data: bytes) -> bytes:
    """
    The PDF without its build stamps and its cross-reference table (byte offsets that move
    when a stamp changes length). Two builds of the same resume have the same key.
    """
    data = _PDF_BUILD_STAMP.sub(b"", data)
    cut = data.rfind(b"\nxref")
    return data[:cut] if cut != -1 else data[:data.rfind(b"startxref")]


PORTFOLIO_SOURCE_STAMP = APPLICATIONS_DIR / "portfolio_resume_source.sha256"  # gitignored, like applications/
PORTFOLIO_PUBLIC_DIR = BASE_DIR.parent / "portfolio-site" / "public"  # Next.js serves the resume from public/


def sync_pdf_copy(src: Path, dest: Path, source: str = "", stamp: Path = None) -> str:
    """
    Copy src over dest unless it holds the same resume. The Sunday rebuild used to
    rewrite portfolio-site's committed resume every week, leaving that repo dirty with
    a 17-byte Edge-version change (audit 2026-10-12, F4). Build stamps are ignored; and
    when `source` (the HTML the PDF was built from) is given, a dest built from the same
    HTML is current too: Edge re-encodes its font subsets from build to build, so on
    2026-10-18 an identical resume differed in 65,000 bytes. `stamp` keeps that HTML's hash.
    Returns "copied", "unchanged" or "failed: <reason>".
    """
    import hashlib
    import shutil
    key = hashlib.sha256(source.encode("utf-8")).hexdigest() if source and stamp else ""
    try:
        same_source = bool(key) and stamp.is_file() and stamp.read_text(encoding="utf-8").strip() == key
        if dest.is_file() and (same_source or pdf_content_key(dest.read_bytes()) == pdf_content_key(src.read_bytes())):
            status = "unchanged"
        else:
            shutil.copy2(src, dest)
            status = "copied"
        if key:
            stamp.write_text(key, encoding="utf-8")
        return status
    except OSError as e:
        print(f"⚠️ [PDF Engine] Portfolio sync error: {e}", flush=True)
        return f"failed: {type(e).__name__}"


def describe_portfolio_sync(res: Dict[str, Any]) -> str:
    """compile_master_resume's portfolio_sync as a short line for the Discord embeds."""
    status = res.get("portfolio_sync", "skipped")
    return PORTFOLIO_SYNC_TEXT.get(status, f"⚠️ {status}")


def compile_master_resume(archive: bool = True, sync_portfolio: bool = True, sync_obsidian: bool = True) -> Dict[str, Any]:
    """
    Compiles 1-page Master ATS Vector Resume PDF:
    1. Loads profile from memory/master_resume.md (stops compilation if template or incomplete).
    2. Renders clean semantic HTML using build_resume_html(context).
    3. Compiles to vector PDF via headless Edge/Chrome.
    4. Verifies file size & single-page layout.
    5. Optionally synchronizes to portfolio-site if configured.
    6. Archives a timestamped copy to applications/archive/.
    7. Logs a session dispatch into Obsidian daily log if enabled.
    """
    from datetime import datetime
    import shutil

    context = load_master_resume_context()
    err = validate_resume_context(context)
    if err:
        return {
            "success": False,
            "error": err,
            "pdf_path": None,
            "size_kb": 0
        }

    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    
    out_dir = APPLICATIONS_DIR
    out_dir.mkdir(exist_ok=True)

    raw_name = context.get("name") or os.getenv("OWNER_NAME", "Candidate")
    slug_name = re.sub(r'[^A-Za-z0-9]+', '_', raw_name.strip()).strip('_')
    master_pdf = out_dir / f"{slug_name}_Resume.pdf"  # no "Master": recruiters see this name

    html = build_resume_html(context)
    success = render_html_to_pdf(html, master_pdf)
    if not success or not master_pdf.exists():
        return {
            "success": False,
            "error": "Headless browser failed to render PDF",
            "pdf_path": None,
            "size_kb": 0
        }

    size_kb = round(master_pdf.stat().st_size / 1024, 1)

    # 1. Sync to portfolio-site
    portfolio_dir = PORTFOLIO_PUBLIC_DIR
    portfolio_target = "Hans_Laureles_Resume.pdf" if (portfolio_dir / "Hans_Laureles_Resume.pdf").exists() else f"{slug_name}_Resume.pdf"
    portfolio_pdf = portfolio_dir / portfolio_target
    portfolio_sync = "skipped"
    if sync_portfolio:
        portfolio_sync = (sync_pdf_copy(master_pdf, portfolio_pdf, html, PORTFOLIO_SOURCE_STAMP)
                          if portfolio_dir.exists() else "no portfolio-site/public folder")

    # 2. Archive timestamped copy
    archive_pdf = None
    if archive:
        try:
            archive_dir = out_dir / "archive"
            archive_dir.mkdir(exist_ok=True)
            archive_pdf = archive_dir / f"{slug_name}_Resume_{today_str}.pdf"
            shutil.copy2(master_pdf, archive_pdf)
        except Exception as e:
            print(f"⚠️ [PDF Engine] Archive copy error: {e}")

    # 3. Obsidian Sync
    obsidian_synced = False
    if sync_obsidian:
        try:
            from obsidian_client import ObsidianClient
            client = ObsidianClient()
            client.log_session(
                "Chaewon",
                f"Master ATS Vector Resume Rebuilt ({size_kb} KB)",
                [
                    f"Artifact: {master_pdf.name} ({size_kb} KB)",
                    "Typography: Times New Roman serif, selectable vector text",
                    "Geometry: single-page Letter layout (Harvard style)",
                    f"Portfolio copy ({portfolio_pdf.name}): {PORTFOLIO_SYNC_TEXT.get(portfolio_sync, portfolio_sync)}",
                    f"Archived copy: {archive_pdf.name if archive_pdf else 'Skipped'}"
                ]
            )
            obsidian_synced = True
        except Exception as e:
            print(f"⚠️ [PDF Engine] Obsidian sync error: {e}")

    return {
        "success": True,
        "pdf_path": master_pdf,
        "portfolio_pdf": portfolio_pdf if portfolio_sync in ("copied", "unchanged") else None,
        "portfolio_sync": portfolio_sync,
        "archive_pdf": archive_pdf,
        "size_kb": size_kb,
        "obsidian_synced": obsidian_synced,
        "date_str": today_str
    }
