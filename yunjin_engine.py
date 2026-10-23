import re
import datetime
from pathlib import Path
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent
WORKSPACE_DIR = BASE_DIR.parent
PORTFOLIO_DIR = WORKSPACE_DIR / "portfolio-site"  # Next.js since 2026-10-23: pages in app/<route>/page.tsx, assets in public/
AUDITS_DIR = BASE_DIR / "portfolio_audits"
AUDITS_DIR.mkdir(exist_ok=True)
MEMORY_DIR = BASE_DIR / "memory"

from llm_client import query_llm


def scan_html_assets(html_file: Path) -> dict:
    """Scans an HTML file for broken image paths, meta tags, and structure."""
    if not html_file.exists():
        return {"exists": False, "file": html_file.name}

    content = html_file.read_text(encoding="utf-8")
    soup = BeautifulSoup(content, "html.parser")

    # ponytail: a page.tsx that exports `metadata` counts as titled and described; parse lib/cases if that proves too loose
    next_metadata = re.search(r"export\b[^\n]*\bmetadata\b", content) is not None
    if next_metadata:  # checked first: a <title> in a .tsx page is usually an inline SVG's
        title = "Next.js metadata export"
    else:
        title = soup.title.string.strip() if soup.title and soup.title.string else "Missing Title"
    meta_desc = ""
    desc_tag = soup.find("meta", attrs={"name": "description"})
    if desc_tag and desc_tag.get("content"):
        meta_desc = desc_tag["content"].strip()
    elif next_metadata:
        meta_desc = "metadata export"

    h1_tags = [h.get_text().strip() for h in soup.find_all("h1")]
    
    # Check all image tags
    images = []
    broken_images = []
    for img in soup.find_all("img"):
        src = img.get("src")
        alt = img.get("alt", "")
        if src:
            # Clean URL params if any
            clean_src = src.split("?")[0]
            # Next.js serves static files from public/ (src="images/..." or "/images/...")
            img_path = PORTFOLIO_DIR / "public" / clean_src.lstrip("/")
            exists = img_path.exists()
            images.append({"src": src, "alt": alt, "exists": exists})
            if not exists:
                broken_images.append(src)

    return {
        "exists": True,
        "file": html_file.name,
        "title": title,
        "has_meta_desc": bool(meta_desc),
        "h1_count": len(h1_tags),
        "total_images": len(images),
        "broken_images": broken_images,
        "content_length": len(content)
    }

def audit_full_portfolio() -> dict:
    """
    Crawls the entire portfolio-site/ folder, validates all image links,
    and runs a comprehensive UX & technical design health check.
    """
    if not PORTFOLIO_DIR.exists():
        return {
            "success": False,
            "error": f"Portfolio directory not found at: {PORTFOLIO_DIR}"
        }

    # every page, so a new one is never skipped (ADR-001 R2): App Router routes plus the static pages in public/
    target_files = sorted(p.relative_to(PORTFOLIO_DIR).as_posix()
                          for pattern in ("app/**/page.tsx", "public/**/*.html") for p in PORTFOLIO_DIR.glob(pattern))
    if not target_files:  # nothing to audit: no critique, no report (Codex R2 fix 3)
        return {"success": False, "error": f"Portfolio audit: no pages (app/**/page.tsx, public/**/*.html) under {PORTFOLIO_DIR}."}

    scan_results = []
    total_images_checked = 0
    all_broken_images = []

    for fname in target_files:
        fpath = PORTFOLIO_DIR / fname
        res = {**scan_html_assets(fpath), "file": fname}  # every route is page.tsx: name it by its path
        scan_results.append(res)
        if res.get("exists"):
            total_images_checked += res.get("total_images", 0)
            if res.get("broken_images"):
                all_broken_images.extend([f"{fname} -> {b}" for b in res["broken_images"]])

    # Prepare summary for Gemini critique
    tech_summary = f"""
PORTFOLIO ASSET & CODE AUDIT:
- Target Directory: {PORTFOLIO_DIR}
- Files Scanned: {len(scan_results)}
- Total Images Checked: {total_images_checked}
- Total Broken Images Found: {len(all_broken_images)}
Broken Image List: {all_broken_images if all_broken_images else "None! All images resolved successfully."}

FILE INVENTORY:
"""
    for r in scan_results:
        if r["exists"]:
            tech_summary += f"- {r['file']}: Title='{r['title']}', MetaDesc={'Yes' if r['has_meta_desc'] else 'NO'}, Images={r['total_images']} (Broken: {len(r['broken_images'])})\n"
        else:
            tech_summary += f"- {r['file']}: MISSING FILE!\n"

    prompt = f"""
You are "Yunjin", the Lead Portfolio Agent, Design System Librarian, and Senior UX Critic for the candidate.
Your job is to provide an elite, professional, and rigorous design audit of the candidate's portfolio site.

### CONTEXT:
The candidate is a BS Computer Science graduate and Applied AI Engineer & Full-Stack Builder.
His portfolio aesthetic is: "Swiss Editorial Monograph" (dark/light mode, high-craft typographic hierarchy, data-dense technical case studies, live terminal and agent copilot).
Flagship case studies:
1. LSFM AI HQ (Autonomous multi-agent operations platform & hybrid inference router)
2. Cognitive Memory Core (Zero-dependency episodic memory retrieval engine)
3. Aura Coffee & Kitchen (Specialty e-commerce storefront with Philippine payment rails)
4. Lumina Analytics (B2B SaaS enterprise telemetry observability)
5. Vellum OS (Mobile ambient notification triage concept)
6. FinTrack (Fintech micro-budgeting user journey)

### TECHNICAL SCAN REPORT:
{tech_summary}

---

### INSTRUCTIONS:
Author a comprehensive Portfolio Health Audit with the following sections:

1. **PORTFOLIO HEALTH SCORE (1-100%)**:
   - Give an honest overall grade based on technical asset health, completeness, and recruiter impression.

2. **TECHNICAL & ASSET INTEGRITY**:
   - Status of image links, SEO meta tags, and document structures.
   - Flag any broken links or missing files clearly.

3. **UX & RECRUITER CRAFT CRITIQUE**:
   - How well does the current portfolio communicate Hans's "Dual Threat" identity (Designer who ships, developer who designs)?
   - Are the case studies structured with strong problem framing, wireframe evidence, design systems, and measurable outcomes?

4. **IMMEDIATE ACTION PLAN (TOP 3 PRIORITIES)**:
   - What are the 3 most impactful visual or copy improvements Hans should make next to elevate the portfolio to tier-1 tech company standards (Stripe, Linear, Apple, Figma)?

Format in clean, engaging Markdown. Tone: Sophisticated, insightful, encouraging, and razor-sharp design taste.
"""

    critique_text = query_llm(prompt, temperature=0.4, agent="yunjin")


    # Extract score
    score = None  # no score in the critique stays None: never a made-up number
    score_match = re.search(r"(\d{2,3})%", critique_text)
    if score_match:
        try:
            score = int(score_match.group(1))
        except Exception:  # quiet: unparsable score keeps the default
            pass

    today_str = datetime.date.today().strftime("%Y-%m-%d")
    audit_file = AUDITS_DIR / f"{today_str}_portfolio_audit.md"
    audit_file.write_text(critique_text, encoding="utf-8")

    return {
        "success": True,
        "score": score,
        "broken_images_count": len(all_broken_images),
        "broken_images": all_broken_images,
        "report": critique_text,
        "saved_file": str(audit_file),
        "filename": audit_file.name
    }

def critique_case_study(project_slug: str) -> dict:
    """Performs an in-depth heuristic UX critique of a specific case study."""
    slug_clean = project_slug.lower().replace("case-", "").replace(".html", "").strip()
    target_file = PORTFOLIO_DIR / "app" / f"case-{slug_clean}" / "page.tsx"

    if not re.fullmatch(r"[a-z0-9-]+", slug_clean) or not target_file.exists():
        available = ", ".join(sorted(p.parent.name[5:] for p in PORTFOLIO_DIR.glob("app/case-*/page.tsx")))
        return {
            "success": False,
            "error": f"Case study not found: `app/case-{slug_clean}/page.tsx` (Available: {available or 'none'})"
        }

    html_content = target_file.read_text(encoding="utf-8")
    soup = BeautifulSoup(html_content, "html.parser")
    main_text = soup.get_text(separator="\n")
    clean_lines = [l.strip() for l in main_text.splitlines() if l.strip()]
    narrative_text = "\n".join(clean_lines)[:10000]

    prompt = f"""
You are "Yunjin", Senior UX Critic and Design Director for Hans Aaron Laureles.
Evaluate this specific case study: **{slug_clean.upper()}**.

### CASE STUDY CONTENT:
{narrative_text}

---

### INSTRUCTIONS:
Evaluate this case study through the eyes of a Design Director at a top design-led technology company (Linear, Stripe, Apple, Figma):

1. **CASE STUDY GRADE (A+ to C-)**:
   - Executive verdict.
2. **WHAT SHINES (STRONG HEURISTICS)**:
   - Highlighting visual craft, component systems, problem clarity, or technical depth.
3. **WHERE IT FALLS SHORT (BLIND SPOTS)**:
   - What would a skeptical design hiring manager question? (e.g. Lack of user quotes, missing alternative wireframe explorations, unverified metric claims).
4. **RECOMMENDED REVISIONS**:
   - 2-3 specific copy, layout, or evidence tweaks to make this case study undeniable.

Tone: Constructive, sharp, high design aesthetic, professional.
"""

    res_text = query_llm(prompt, temperature=0.4, agent="yunjin")
    
    return {
        "success": True,
        "project": slug_clean.upper(),
        "critique": res_text
    }


def consult_yunjin(question: str) -> str:
    """Answers design, UX, visual craft, or portfolio questions with Yunjin's persona."""
    portfolio = (MEMORY_DIR / "portfolio_spec.md").read_text(encoding="utf-8") if (MEMORY_DIR / "portfolio_spec.md").exists() else ""
    design_system = (MEMORY_DIR / "design_system.md").read_text(encoding="utf-8") if (MEMORY_DIR / "design_system.md").exists() else ""
    
    prompt = f"""
You are "Yunjin", Senior UX Critic and Design Director for Hans Aaron Laureles's portfolio and design work.
Hans is chatting with you in the #portfolio-audits channel.

### PORTFOLIO CONTEXT:
{portfolio}

### DESIGN SYSTEM TOKENS:
{design_system}

### HANS'S MESSAGE / DESIGN QUESTION:
"{question}"

---

### INSTRUCTIONS:
- Give thoughtful, sophisticated, senior-level design critique and UX guidance.
- Focus on visual hierarchy, typography, component craft, storytelling, and hiring manager perception.
- Be honest, inspiring, and direct with Yunjin's vibrant, high-taste creative personality.
- Keep the formatting clean and readable for Discord.
"""
    return query_llm(prompt, temperature=0.4, agent="yunjin")


def select_portfolio_pitch(job_text: str, role: str = "Design / Engineering Role", company: str = "Target Company") -> dict:
    """
    Selects the optimal top 2 case studies from Hans's portfolio spec for a specific job,
    and produces strategic interview talking points.
    """
    portfolio = (MEMORY_DIR / "portfolio_spec.md").read_text(encoding="utf-8") if (MEMORY_DIR / "portfolio_spec.md").exists() else ""

    prompt = f"""
You are "Yunjin", Senior UX Critic and Portfolio Director for Hans Aaron Laureles.
The team is preparing a targeted application package for:
**Role:** {role}
**Company:** {company}

### TARGET JOB POSTING EXCERPT:
{job_text[:3000]}

### HANS'S PORTFOLIO CASE STUDIES:
{portfolio}

---

### INSTRUCTIONS:
As the Senior UX Critic, curate the **top 2 flagship case studies** Hans must feature for {company}.
Structure your pitch strategy with:

1. **FLAGSHIP CASE STUDIES**:
   - Primary: Name of project & 1-2 sentence rationale on why it counters {company}'s specific design or product challenges.
   - Secondary: Name of project & 1-2 sentence rationale showcasing his range.
2. **PORTFOLIO WALKTHROUGH STRATEGY**:
   - 2-3 specific talking points, heuristics, or wireframe decisions to emphasize in the portfolio interview round.

Format in clean Discord Markdown. Tone: Discerning, sophisticated, strategic, and razor-sharp design taste.
"""
    pitch_text = query_llm(prompt, temperature=0.3, agent="yunjin")

    # Detect recommended projects
    candidates = ["LSFM AI HQ", "Lumina Analytics", "Vellum OS", "FinTrack", "Aura Coffee & Kitchen", "Cognitive Memory Core", "Patriot Capstone"]
    recommended = [c for c in candidates if c.lower() in pitch_text.lower()]
    if len(recommended) < 2:
        recommended = ["LSFM AI HQ", "Lumina Analytics"]

    return {
        "recommended_projects": recommended[:2],
        "pitch_text": pitch_text
    }



