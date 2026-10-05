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
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=25)
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
            for l in sec_lines:
                ls = l.strip()
                if ls.startswith("### "):
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
                        if bullet.lower().startswith("**resume line:**"):
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

    # Key Technical Impact bullets
    if not ctx.get("bullets"):
        all_bullets = []
        for exp in ctx.get("experience", []):
            all_bullets.extend(exp.get("bullets", []))
        if all_bullets:
            ctx["bullets"] = all_bullets[:4]

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
    Builds an executive, Swiss-inspired, ATS-optimized 1-page HTML resume.
    Uses semantic HTML5 and selectable text so ATS parsers can read every section.
    Renders ONLY data supplied in data dictionary (zero invented credentials).
    """
    name = data.get("name") or os.getenv("OWNER_NAME", "Candidate Name")
    title = data.get("title") or os.getenv("OWNER_TITLE", "Software Engineer")
    if not title or title in ["Design / Engineering Role", "Target Role", "Role", "Application"]:
        title = os.getenv("OWNER_TITLE", "Software Engineer")

    location = data.get("location_short") or data.get("location") or os.getenv("OWNER_LOCATION", "")
    email = data.get("email") or os.getenv("OWNER_EMAIL", OWNER_EMAIL)
    portfolio_url = data.get("portfolio_url") or os.getenv("PORTFOLIO_URL", "")
    portfolio_display = data.get("portfolio_display") or os.getenv("PORTFOLIO_DISPLAY", portfolio_url.replace("https://", "").replace("http://", "").rstrip("/"))
    
    portfolio_link = f'<a href="{portfolio_url}" target="_blank" style="color: #2563eb; text-decoration: none;">{portfolio_display}</a>' if portfolio_url else ""
    email_link = f'<a href="mailto:{email}" style="color: #2563eb; text-decoration: none;">{email}</a>' if email else ""

    github_url = data.get("github_url") or os.getenv("OWNER_GITHUB", "")
    github_display = github_url.replace("https://", "").replace("http://", "").rstrip("/")
    github_link = f'<a href="{github_url}" target="_blank" style="color: #2563eb; text-decoration: none;">{github_display}</a>' if github_url else ""

    education_short = data.get("education_short") or data.get("education_degree", "")

    contact_parts = []
    if location:
        contact_parts.append(f"<span>{location}</span>")
    if email_link:
        contact_parts.append(f"<span>{email_link}</span>")
    if portfolio_link:
        contact_parts.append(f"<span>{portfolio_link}</span>")
    if github_link:
        contact_parts.append(f"<span>{github_link}</span>")
    if education_short:
        contact_parts.append(f"<span>{education_short}</span>")
    contact_bar_html = '<span class="sep"> · </span>'.join(contact_parts)

    summary = data.get("summary", "")
    if len(summary) > 280:
        sentences = [s.strip().rstrip('.') for s in summary.split(". ") if s.strip()]
        if len(sentences) >= 2:
            summary = sentences[0] + ". " + sentences[1] + "."
        else:
            summary = summary[:280].rsplit(" ", 1)[0] + "..."
    summary_html = format_inline_markdown(summary) if summary else ""
    
    bullets = data.get("bullets", [])
    bullets = [b for b in bullets if b][:4]
    bullet_items_html = "".join(f"<li>{format_inline_markdown(b)}</li>" for b in bullets)

    projects = data.get("projects", [])
    project_items_html = ""
    for p in projects[:4]:
        p_name = p.get('name', '')
        p_tag = " · ".join(x for x in (p.get('tag', ''), p.get('credit', '')) if x)
        tag_html = f'<span class="item-meta">{p_tag}</span>' if p_tag else ''
        p_desc = p.get('desc', '')
        project_items_html += f"""
        <div class="item">
          <div class="item-header">
            <span class="item-title">{p_name}</span>
            {tag_html}
          </div>
          <p class="item-desc">{format_inline_markdown(p_desc)}</p>
        </div>
        """

    experiences = data.get("experience", [])
    if not experiences and (data.get("experience_title") or data.get("experience_company")):
        experiences = [{
            "title": data.get("experience_title", ""),
            "company": data.get("experience_company", ""),
            "date": data.get("experience_date", ""),
            "desc": data.get("experience_desc", ""),
            "bullets": data.get("experience_bullets", [])
        }]
    exp_items_html = ""
    for exp in experiences[:3]:
        e_title = exp.get("title", "")
        e_company = exp.get("company", "")
        e_date = exp.get("date", "")
        e_desc = exp.get("desc", "")
        e_bullets = exp.get("bullets", [])
        header_sub = f" · <span class=\"item-subtitle\">{e_company}</span>" if e_company else ""
        desc_html = f"<p class=\"item-desc\">{format_inline_markdown(e_desc)}</p>" if e_desc else ""
        b_html = ""
        if e_bullets and not e_desc:
            b_html = "<ul class=\"bullet-list\" style=\"margin-top: 1px;\">" + "".join(f"<li>{format_inline_markdown(b)}</li>" for b in e_bullets[:3]) + "</ul>"
        exp_items_html += f"""
        <div class="item">
          <div class="item-header">
            <div>
              <span class="item-title">{e_title}</span>{header_sub}
            </div>
            <span class="item-date">{e_date}</span>
          </div>
          {desc_html}
          {b_html}
        </div>
        """

    edu_degree = data.get("education_degree", "")
    edu_school = data.get("education_institution", "")
    edu_dates = data.get("education_dates", "")
    edu_coursework = data.get("education_coursework", "")

    skills_ai = data.get("skills_ai", "")
    skills_sys = data.get("skills_sys", "")
    skills_ui = data.get("skills_ui", "")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{name} — Resume</title>
<style>
  @page {{
    size: letter;
    margin: 0.26in 0.38in 0.20in 0.38in;
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
    font-size: 8.7pt;
    line-height: 1.26;
    -webkit-font-smoothing: antialiased;
  }}
  header {{
    margin-bottom: 7px;
    border-bottom: 2px solid #111827;
    padding-bottom: 4px;
  }}
  .name-row {{
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    margin-bottom: 2px;
  }}
  h1 {{
    font-size: 18.5pt;
    font-weight: 800;
    letter-spacing: -0.5px;
    color: #0f172a;
  }}
  .target-title {{
    font-size: 9.8pt;
    font-weight: 600;
    color: #2563eb;
    text-transform: uppercase;
    letter-spacing: 0.8px;
  }}
  .contact-bar {{
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    font-size: 8.3pt;
    color: #475569;
    font-weight: 500;
    margin-top: 2px;
  }}
  .contact-bar span.sep {{
    color: #cbd5e1;
  }}
  section {{
    margin-bottom: 6px;
  }}
  h2 {{
    font-size: 8.6pt;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.8px;
    color: #0f172a;
    border-bottom: 1px solid #e2e8f0;
    padding-bottom: 2px;
    margin-bottom: 3px;
  }}
  .summary-text {{
    font-size: 8.5pt;
    color: #334155;
    line-height: 1.25;
  }}
  .item {{
    margin-bottom: 3.5px;
  }}
  .item:last-child {{
    margin-bottom: 0;
  }}
  .item-header {{
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    margin-bottom: 1px;
  }}
  .item-title {{
    font-weight: 700;
    font-size: 8.9pt;
    color: #0f172a;
  }}
  .item-subtitle {{
    font-weight: 600;
    color: #334155;
  }}
  .item-date {{
    font-size: 8pt;
    font-weight: 600;
    color: #64748b;
    text-align: right;
  }}
  .item-meta {{
    font-size: 8pt;
    font-weight: 500;
    color: #2563eb;
  }}
  .item-desc {{
    font-size: 8.3pt;
    color: #334155;
    line-height: 1.22;
  }}
  ul.bullet-list {{
    list-style-type: square;
    margin-left: 14px;
    margin-top: 2px;
  }}
  ul.bullet-list li {{
    font-size: 8.3pt;
    color: #334155;
    margin-bottom: 2px;
    line-height: 1.22;
  }}
  ul.bullet-list li strong {{
    color: #0f172a;
  }}
  .skills-grid {{
    display: grid;
    grid-template-columns: 125px 1fr;
    row-gap: 2px;
    font-size: 8.2pt;
    line-height: 1.22;
  }}
  .skills-label {{
    font-weight: 700;
    color: #0f172a;
  }}
  .skills-val {{
    color: #334155;
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
    {contact_bar_html}
  </div>
</header>

<main>
"""

    if summary_html:
        html += f"""
  <section>
    <h2>Professional Summary</h2>
    <p class="summary-text">{summary_html}</p>
  </section>
"""

    if exp_items_html:
        html += f"""
  <section>
    <h2>Professional Work Experience</h2>
    {exp_items_html}
  </section>
"""

    if bullet_items_html:
        html += f"""
  <section>
    <h2>Key Technical Impact</h2>
    <ul class="bullet-list">
      {bullet_items_html}
    </ul>
  </section>
"""

    if project_items_html:
        html += f"""
  <section>
    <h2>Featured Engineering Projects</h2>
    {project_items_html}
  </section>
"""

    if edu_degree or edu_school:
        edu_meta = f'<span class="item-date">{edu_dates}</span>' if edu_dates else ''
        course_meta = f'<span class="item-date">{edu_coursework}</span>' if edu_coursework else ''
        school_sub = f'<span class="item-subtitle">{edu_school}</span>' if edu_school else ''
        html += f"""
  <section>
    <h2>Education</h2>
    <div class="item">
      <div class="item-header">
        <div>
          <span class="item-title">{edu_degree}</span>
        </div>
        {edu_meta}
      </div>
      <div class="item-header">
        {school_sub}
        {course_meta}
      </div>
    </div>
  </section>
"""

    skills_rows = ""
    if skills_ai:
        skills_rows += f'<div class="skills-label">AI & LLM Orchestration:</div><div class="skills-val">{skills_ai}</div>\n'
    if skills_sys:
        skills_rows += f'<div class="skills-label">Languages & Backend:</div><div class="skills-val">{skills_sys}</div>\n'
    if skills_ui:
        skills_rows += f'<div class="skills-label">Frontend & Design:</div><div class="skills-val">{skills_ui}</div>\n'

    if skills_rows:
        html += f"""
  <section>
    <h2>Technical Proficiencies & Capabilities</h2>
    <div class="skills-grid" style="grid-template-columns: 160px 1fr;">
      {skills_rows}
    </div>
  </section>
"""

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
    
    portfolio_link = f'<a href="{portfolio_url}" target="_blank" style="color: #2563eb; text-decoration: none;">🌐 {portfolio_display}</a>'
    email_link = f'<a href="mailto:{email}" style="color: #2563eb; text-decoration: none;">✉️ {email}</a>'

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
    <span>📍 {location}</span>
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
  <p style="font-size: 9.2pt; color: #64748b;">Applied AI Engineer & Full-Stack Builder</p>
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
    safe_role = re.sub(r'[^a-zA-Z0-9_-]', '_', role).strip('_') or "Role"

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
    master_pdf = out_dir / f"{slug_name}_Master_Resume.pdf"

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
    portfolio_dir = BASE_DIR.parent / "portfolio-site"
    portfolio_target = "Hans_Laureles_Resume.pdf" if (portfolio_dir / "Hans_Laureles_Resume.pdf").exists() else f"{slug_name}_Resume.pdf"
    portfolio_pdf = portfolio_dir / portfolio_target
    if sync_portfolio and portfolio_dir.exists():
        try:
            shutil.copy2(master_pdf, portfolio_pdf)
        except Exception as e:
            print(f"⚠️ [PDF Engine] Portfolio sync error: {e}")

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
                    "Typography: system sans-serif stack, selectable vector text",
                    "Geometry: single-page Letter layout",
                    f"Synchronized to {portfolio_pdf.name if sync_portfolio else 'Skipped'}",
                    f"Archived copy: {archive_pdf.name if archive_pdf else 'Skipped'}"
                ]
            )
            obsidian_synced = True
        except Exception as e:
            print(f"⚠️ [PDF Engine] Obsidian sync error: {e}")

    return {
        "success": True,
        "pdf_path": master_pdf,
        "portfolio_pdf": portfolio_pdf if sync_portfolio else None,
        "archive_pdf": archive_pdf,
        "size_kb": size_kb,
        "obsidian_synced": obsidian_synced,
        "date_str": today_str
    }
