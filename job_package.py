"""
Builds one job's tailored resume + cover letter from memory/master_resume.md (job hunt).

<job_dir>/tailoring.json (written by Chaewon's job hunt or by the Claude routine) may only set:
  company, role, title, summary, skills_ai, skills_sys, skills_ui,
  project_order (the 4 resume projects, by name), cover_paragraphs (list)
Everything else (dates, employer, school, contact, project text) comes from
master_resume.md untouched. Skills may be reordered, never added.
build() writes <Name>_Resume.pdf and <Name>_Cover_Letter.pdf into <job_dir>.
"""

import datetime
import html
import json
import re
import unicodedata
from pathlib import Path

import pdf_engine

ALLOWED = {"company", "role", "title", "summary", "skills_ai", "skills_sys", "skills_ui",
           "project_order", "cover_paragraphs"}
SKILL_KEYS = ("skills_ai", "skills_sys", "skills_ui")


def skill_items(line: str) -> list[str]:
    """Comma-separated items, splitting only outside parentheses (any depth): 'Python (asyncio, tools (pip, uv))' is one item."""
    items, depth, cur = [], 0, ""
    for ch in line or "":
        depth += (ch == "(") - (ch == ")")
        if ch == "," and depth == 0:
            items.append(cur)
            cur = ""
        else:
            cur += ch
    items.append(cur)
    return [s.strip().rstrip(".").strip() for s in items if s.strip()]


_DASHES = dict.fromkeys(map(ord, "‐‑‒–—―−"), "-")


def _key(item: str) -> str:
    """Comparison form of a skill: LLMs swap '-' for Unicode dashes and spaces for NBSP; those are not new skills."""
    return " ".join(unicodedata.normalize("NFKC", item).translate(_DASHES).lower().split())


def effective(t: dict, ctx: dict, k: str):
    """The value the PDF will show: the tailored one when non-empty, else the master's."""
    return t[k] if t.get(k) else ctx.get(k, "")


def check(t: dict, ctx: dict) -> list[str]:
    """Reasons to refuse this tailoring; empty list means OK."""
    errors = [f"field not allowed: {k}" for k in sorted(set(t) - ALLOWED)]
    for k in ("company", "role", "title", "summary", *SKILL_KEYS):
        if k in t and not isinstance(t[k], str):
            errors.append(f"{k} must be text")
    for k in ("project_order", "cover_paragraphs"):
        if k in t and not (isinstance(t[k], list) and all(isinstance(x, str) for x in t[k])):
            errors.append(f"{k} must be a list of text")
    if errors:
        return errors
    for k in ("company", "role"):
        if not t.get(k, "").strip():
            errors.append(f"missing {k}")
    known = {_key(s) for k in SKILL_KEYS for s in skill_items(ctx.get(k, ""))}
    for k in SKILL_KEYS:
        added = [s for s in skill_items(t.get(k, "")) if _key(s) not in known]
        if added:
            errors.append(f"{k} adds skills not in master_resume.md: {added}")
    shown = [_key(s) for k in SKILL_KEYS for s in skill_items(effective(t, ctx, k))]
    if len(shown) != len(set(shown)):
        errors.append("a skill appears on two skill lines")
    if t.get("project_order"):
        on_page = [p["name"] for p in ctx.get("projects", [])[:4]]
        if sorted(t["project_order"]) != sorted(on_page):
            errors.append(f"project_order must reorder exactly {on_page}")
    if len(t.get("summary", "")) > 400:
        errors.append("summary over 400 characters")
    return errors


def apply(t: dict, ctx: dict) -> dict:
    """Master context plus the tailoring. Tailored text is HTML-escaped: pdf_engine inserts strings into HTML as-is."""
    out = dict(ctx)
    for k in ("title", "summary", *SKILL_KEYS):
        out[k] = html.escape(t[k]) if t.get(k) else ctx.get(k, "")
    if t.get("project_order"):
        by_name = {p["name"]: p for p in ctx["projects"]}
        out["projects"] = [by_name[n] for n in t["project_order"]]
    url = out.get("portfolio_url", "")
    out.update(company=html.escape(t["company"]), role=html.escape(t["role"]),
               portfolio_display=url.replace("https://", "").replace("http://", "").rstrip("/"),
               date_str=datetime.date.today().strftime("%B %d, %Y"))
    if t.get("cover_paragraphs"):
        out["paragraphs"] = [html.escape(p) for p in t["cover_paragraphs"]]
    return out


def page_count(pdf: Path) -> int:
    return len(re.findall(rb"/Type\s*/Page(?!s)", pdf.read_bytes()))


def build(job_dir: Path) -> dict:
    """{"ok", "errors", "resume", "cover_letter"} for <job_dir>/tailoring.json; never raises on a refusal."""
    job_dir = Path(job_dir)
    ctx = pdf_engine.load_master_resume_context()
    slug = re.sub(r"[^A-Za-z0-9]+", "_", ctx.get("name") or "Candidate").strip("_")
    resume, cover = job_dir / f"{slug}_Resume.pdf", job_dir / f"{slug}_Cover_Letter.pdf"
    result = {"ok": False, "errors": [], "resume": str(resume), "cover_letter": str(cover)}
    if "_error" in ctx:
        result["errors"] = [ctx["_error"]]
        return result
    t = json.loads((job_dir / "tailoring.json").read_text(encoding="utf-8-sig"))  # -sig: PowerShell writes a BOM
    errors = check(t, ctx)
    if not errors:
        data = apply(t, ctx)
        for pdf, page in ((resume, pdf_engine.build_resume_html(data)), (cover, pdf_engine.build_cover_letter_html(data))):
            if not pdf_engine.render_html_to_pdf(page, pdf):
                errors.append(f"render failed: {pdf.name}")
            elif page_count(pdf) != 1:
                # The resume prints no summary, so its length comes from master_resume.md.
                hint = ("shorten the Resume bullets or skills in master_resume.md" if pdf == resume
                        else "shorten cover_paragraphs")
                errors.append(f"{pdf.name} is {page_count(pdf)} pages; {hint}")
                pdf.unlink()
    result.update(ok=not errors, errors=errors)
    return result
