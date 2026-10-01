"""
Unit tests for the PDF Engine and Master Resume Parser.
Verifies that:
1. The shipped default template is rejected and stops compilation.
2. Incomplete profiles with missing required fields are rejected and stop compilation.
3. Section-style Markdown resumes matching the documented template parse completely.
4. The resume compiler never invents credentials or renders preset data.
5. Empty context dict maintains backwards-compatibility for benchmarking.
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pdf_engine import (
    parse_markdown_resume,
    load_master_resume_context,
    build_resume_html,
    compile_master_resume,
    generate_tailored_pdf_package,
)

SAMPLE_SECTION_STYLE_RESUME = """# Alex Morgan

- **Location:** San Francisco, CA
- **Email:** alex@example.com
- **GitHub:** https://github.com/alexm
- **Portfolio:** https://alexm.dev

## Summary
Full-stack software engineer with 5 years of experience building resilient cloud services and web applications.

## Experience
### Senior Software Engineer, Stripe (2022 – Present)
- Architected a distributed webhook ingestion service handling 15k events/sec.
- Reduced p99 latency by 40% through redis caching and connection pooling.

### Software Engineer, Twilio (2020 – 2022)
- Built customer-facing dashboard components in React and TypeScript.
- Shipped automated phone number provisioning API.

## Projects
### FlowMesh (Service Mesh Orchestrator)
- Lightweight distributed service mesh in Go with eBPF data plane.

### NeuralSearch
- Vector search engine using HNSW indexing in Python and Rust.

## Technical Skills
- **Languages & Frameworks:** Go, Python, TypeScript, React, Next.js, FastAPI
- **Cloud & Infrastructure:** AWS, Docker, Kubernetes, Terraform, PostgreSQL
- **AI & Systems:** Vector embeddings, Semantic search, LLM routing

## Education
### BS Computer Science, Stanford University (2016 – 2020)
- Relevant Coursework: Operating Systems, Compilers, Distributed Systems
"""

SAMPLE_PRESET_CREDENTIALS = [
    "FFmpeg 16 kHz",
    "Manas: Ciel",
    "LSFM AI HQ",
    "Digital Services",
    "DLSU-D",
    "De La Salle",
    "ROC.PH",
    "Bilingual",
    "Thought Acceleration",
]


class TestResumeParserAndEngine(unittest.TestCase):

    def test_default_template_stops_compilation(self):
        """Shipped template must be rejected and must stop PDF compilation."""
        template_text = """# Your Name

> Template. Replace this file with your master resume in Markdown before compiling a resume PDF.

## Summary
One or two sentences on what you build and for whom.

## Experience
### Role, Company (Start – End)
- What you shipped, with a number you can back up.

## Projects
### Project name
- What it does, the stack, and one measured result.

## Skills
Languages, frameworks, tools.

## Education
Degree, school, year.
"""
        ctx, err = parse_markdown_resume(template_text)
        self.assertIsNotNone(err)
        self.assertIn("default template", err)

        with tempfile.TemporaryDirectory() as tmp:
            fake_md = Path(tmp) / "master_resume.md"
            fake_md.write_text(template_text, encoding="utf-8")
            loaded = load_master_resume_context(fake_md)
            self.assertIn("_error", loaded)
            self.assertIn("default template", loaded["_error"])

            with mock.patch("pdf_engine.load_master_resume_context", return_value=loaded):
                res = compile_master_resume(archive=False, sync_portfolio=False, sync_obsidian=False)
                self.assertFalse(res["success"])
                self.assertIn("default template", res["error"])
                self.assertIsNone(res["pdf_path"])

    def test_missing_required_fields_stops_compilation(self):
        """Incomplete resume profile missing required sections must stop compilation."""
        incomplete_text = """# Jane Doe

- **Location:** New York, NY
- **Email:** jane@example.com

## Summary
Experienced engineer.
"""
        ctx, err = parse_markdown_resume(incomplete_text)
        self.assertIsNotNone(err)
        self.assertIn("missing required fields", err)
        self.assertIn("Work Experience", err)
        self.assertIn("Education", err)

        with mock.patch("pdf_engine.load_master_resume_context", return_value={"_error": err}):
            res = compile_master_resume(archive=False, sync_portfolio=False, sync_obsidian=False)
            self.assertFalse(res["success"])
            self.assertIn("missing required fields", res["error"])

    def test_section_style_resume_parsed_completely(self):
        """Section-style markdown matching template format must parse all fields."""
        ctx, err = parse_markdown_resume(SAMPLE_SECTION_STYLE_RESUME)
        self.assertIsNone(err)
        self.assertEqual(ctx["name"], "Alex Morgan")
        self.assertEqual(ctx["location"], "San Francisco, CA")
        self.assertEqual(ctx["email"], "alex@example.com")
        self.assertEqual(ctx["github_url"], "https://github.com/alexm")
        self.assertEqual(ctx["portfolio_url"], "https://alexm.dev")
        self.assertIn("5 years of experience", ctx["summary"])
        self.assertEqual(len(ctx["experience"]), 2)
        self.assertEqual(ctx["experience_title"], "Senior Software Engineer")
        self.assertEqual(ctx["experience_company"], "Stripe")
        self.assertEqual(len(ctx["projects"]), 2)
        self.assertEqual(ctx["projects"][0]["name"], "FlowMesh")
        self.assertEqual(ctx["projects"][0]["tag"], "Service Mesh Orchestrator")
        self.assertEqual(ctx["education_degree"], "BS Computer Science")
        self.assertEqual(ctx["education_institution"], "Stanford University")
        self.assertEqual(ctx["education_dates"], "2016 – 2020")
        self.assertGreaterEqual(len(ctx["bullets"]), 2)

    def test_zero_invented_credentials_rendered_in_html(self):
        """build_resume_html must only render supplied data and zero preset credentials."""
        ctx, err = parse_markdown_resume(SAMPLE_SECTION_STYLE_RESUME)
        self.assertIsNone(err)
        html = build_resume_html(ctx)

        # Check candidate's own data is present
        self.assertIn("Alex Morgan", html)
        self.assertIn("Stripe", html)
        self.assertIn("FlowMesh", html)
        self.assertIn("Stanford University", html)

        # Check ZERO preset credentials are present
        for preset in SAMPLE_PRESET_CREDENTIALS:
            self.assertNotIn(preset.lower(), html.lower(), f"Invented credential found: {preset}")

    def test_empty_dict_renders_valid_html_structure(self):
        """build_resume_html({}) must return valid HTML structure without preset credentials."""
        html = build_resume_html({})
        self.assertTrue(html.startswith("<!DOCTYPE html>"))
        self.assertIn("<title>", html)
        self.assertIn("</html>", html)
        for preset in SAMPLE_PRESET_CREDENTIALS:
            self.assertNotIn(preset.lower(), html.lower(), f"Invented credential in empty render: {preset}")

    def test_tailored_pdf_package_refuses_default_template_without_rendering(self):
        """generate_tailored_pdf_package must refuse the default template without creating files."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            out_dir = tmp_path / "apps"
            out_dir.mkdir()

            template_text = """# Your Name

> Template. Replace this file with your master resume in Markdown before compiling a resume PDF.

## Summary
One or two sentences on what you build and for whom.

## Experience
### Role, Company (Start – End)
- What you shipped, with a number you can back up.

## Projects
### Project name
- What it does, the stack, and one measured result.

## Skills
Languages, frameworks, tools.

## Education
Degree, school, year.
"""
            mem_file = tmp_path / "master_resume.md"
            mem_file.write_text(template_text, encoding="utf-8")
            loaded = load_master_resume_context(mem_file)

            with mock.patch("pdf_engine.load_master_resume_context", return_value=loaded):
                res = generate_tailored_pdf_package(
                    tailored_dict={"role": "Software Engineer", "company": "Acme Corp"},
                    company="Acme Corp",
                    role="Software Engineer",
                    output_dir=out_dir,
                )
                self.assertIsNone(res["resume_pdf"])
                self.assertIsNone(res["cover_pdf"])
                self.assertIn("error", res)
                self.assertIn("default template", res["error"])
                # Ensure zero PDF files were created on disk
                created_pdfs = list(out_dir.glob("*.pdf"))
                self.assertEqual(created_pdfs, [])


if __name__ == "__main__":
    unittest.main()
