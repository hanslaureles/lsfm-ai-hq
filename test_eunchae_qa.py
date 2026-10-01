"""
Regression Test Suite for Eunchae QA Gatekeeper & Honest Failures (Invariant 7)
Guarantees that audit exceptions, missing assets, and unhandled errors are never
reworded or masked as 'QA PASSED'.
"""

import importlib.util
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

# Hermetic module loader: test_ciel_security leaves stubs in sys.modules
# ("llm_client", "eunchae_engine") without __file__. Restore real modules.
for mod_name in ["llm_client", "eunchae_engine"]:
    if mod_name in sys.modules and not hasattr(sys.modules[mod_name], "__file__"):
        spec = importlib.util.spec_from_file_location(mod_name, Path(__file__).with_name(f"{mod_name}.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = mod
        spec.loader.exec_module(mod)

import eunchae_engine
import bot_sakura


class TestEunchaeQAHonestFailures(unittest.TestCase):
    def test_handle_qa_audit_failure_records_honest_failure(self):
        """Invariant 7: An audit exception must NEVER produce 'QA PASSED' or passed=True."""
        exc = RuntimeError("Groq API quota exhausted or connection timed out")
        res = bot_sakura.handle_qa_audit_failure(exc)

        self.assertFalse(res["passed"], "Crashed audit must never be marked as passed")
        self.assertEqual(res["score"], 0, "Crashed audit score must be 0")
        self.assertEqual(res["verdict"], "🔴 QA AUDIT ERROR")
        self.assertIn("RuntimeError", res["review"])
        self.assertIn("Groq API quota exhausted", res["review"])
        self.assertFalse(res["deterministic"]["passed"])
        self.assertGreater(len(res["deterministic"]["defects"]), 0)

    def test_audit_application_package_llm_failure_does_not_mask_as_passed(self):
        """When the LLM review fails inside eunchae_engine, it must not return passed=True."""
        tailor_res = {
            "score": 85,
            "report": "Sample tailored bullets",
            "filename": "tailored_sample.md",
            "resume_pdf": None,
            "cover_pdf": None
        }
        with patch.object(eunchae_engine, "query_llm", side_effect=RuntimeError("Provider outage")):
            res = eunchae_engine.audit_application_package(tailor_res, {}, {}, "Acme Corp", "Staff Engineer")

            self.assertFalse(res["passed"], "LLM failure during audit must not pass")
            self.assertIn("QA", res["verdict"])
            self.assertNotIn("CERTIFIED", res["verdict"])
            self.assertNotIn("PASSED", res["verdict"])
            self.assertIn("error", res)

    def test_check_deterministic_qa_detects_empty_package(self):
        """A package with missing PDFs and low score must fail deterministic QA."""
        det = eunchae_engine.check_deterministic_qa({}, "Acme Corp", "Staff Engineer")
        self.assertFalse(det["passed"])
        self.assertTrue(any("Tailored Resume PDF" in d for d in det["defects"]))

    def test_check_deterministic_qa_detects_error_fallback(self):
        """A package with fallback error.md must be flagged as a critical defect."""
        tailor_res = {
            "score": 80,
            "report": "Error generating full report",
            "filename": "error.md"
        }
        det = eunchae_engine.check_deterministic_qa(tailor_res, "Acme Corp", "Staff Engineer")
        self.assertFalse(det["passed"])
        self.assertTrue(any("Fallback `error.md`" in d for d in det["defects"]))

    def test_format_proposal_qa_status_passes_cleanly(self):
        """When QA audit passes, proposal status uses squad memory note and standard approval."""
        qa_res = {
            "passed": True,
            "score": 92,
            "verdict": "🟢 QA CERTIFIED"
        }
        qa_line, actions, color = bot_sakura.format_proposal_qa_status(qa_res, default_score_color=0x57F287)
        self.assertIn("Verified against squad failure memory", qa_line)
        self.assertIn("Approve & Finalize", actions)
        self.assertNotIn("QA WARNING", actions)
        self.assertEqual(color, 0x57F287)

    def test_format_proposal_qa_status_warns_on_failure(self):
        """When QA audit fails, proposal status must display QA warning and red/amber color."""
        qa_res = {
            "passed": False,
            "score": 0,
            "verdict": "🔴 QA AUDIT ERROR"
        }
        qa_line, actions, color = bot_sakura.format_proposal_qa_status(qa_res, default_score_color=0x57F287)
        self.assertIn("⚠️ QA defects flagged or audit failed", qa_line)
        self.assertIn("⚠️ **QA WARNING**", actions)
        self.assertIn("Force Approve & Finalize", actions)
        self.assertEqual(color, 0xED4245)


if __name__ == "__main__":
    unittest.main()
