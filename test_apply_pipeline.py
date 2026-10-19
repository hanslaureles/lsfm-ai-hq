"""ADR-001 R6: !apply runs Chaewon, Yunjin and Kazuha concurrently and never shows a made-up fit score."""

import asyncio
import itertools
import time
import types
import unittest
from unittest import mock

import bot_sakura

IDS = itertools.count(1)


class FakeMessage:
    def __init__(self, content=None, embed=None, files=None):
        self.id, self.content, self.embed, self.files = next(IDS), content, embed, files or []
        self.reactions, self.edits = [], []

    async def add_reaction(self, emoji):
        self.reactions.append(emoji)

    async def edit(self, content=None, **_):
        self.edits.append(content)

    async def reply(self, content):
        return FakeMessage(content)


class FakeChannel:
    def __init__(self):
        self.id, self.sent = next(IDS), []

    async def send(self, content=None, embed=None, files=None):
        msg = FakeMessage(content, embed, files)
        self.sent.append(msg)
        return msg

    def texts(self):
        return [m.content or "" for m in self.sent] + [
            f"{m.embed.title}\n{m.embed.description}" for m in self.sent if m.embed]


TAILOR = {"score": 80, "report": "Strong Python match.", "saved_file": "", "filename": "acme.md"}
YUNJIN = {"recommended_projects": ["Ciel", "LSFM"], "pitch_text": "Lead with Ciel."}
KAZUHA = {"tech_pitch": "Async Python and typed React."}
QA = {"passed": True, "score": 95, "verdict": "PASS", "review": "Clean.", "heuristics_checked": 3}


def slow(result, seconds=0.0, error=None):
    def step(*_):
        time.sleep(seconds)
        if error:
            raise error
        return result
    return step


class ApplyPipelineTest(unittest.TestCase):
    def run_apply(self, chaewon=slow(TAILOR), yunjin=slow(YUNJIN), kazuha=slow(KAZUHA), timeouts=None):
        handoffs, approvals = FakeChannel(), FakeChannel()
        message = FakeMessage()
        message.guild, message.channel = object(), handoffs
        audit = mock.MagicMock(return_value=QA)
        with mock.patch.object(bot_sakura, "find_channel_by_name",
                               side_effect=lambda _g, name: handoffs if name == "agent-handoffs" else approvals), \
                mock.patch.object(bot_sakura, "find_url_in_text", return_value=None), \
                mock.patch.object(bot_sakura, "analyze_and_tailor", side_effect=chaewon), \
                mock.patch.object(bot_sakura, "select_portfolio_pitch", side_effect=yunjin), \
                mock.patch.object(bot_sakura, "generate_tech_pitch", side_effect=kazuha), \
                mock.patch.object(bot_sakura, "APPLY_STEP_TIMEOUT_S", timeouts or bot_sakura.APPLY_STEP_TIMEOUT_S), \
                mock.patch("eunchae_engine.audit_application_package", audit), \
                mock.patch.object(bot_sakura, "save_pending_proposal") as save:
            started = time.monotonic()
            asyncio.run(bot_sakura.handle_apply_orchestration(message, "AI Engineer at Acme ::: Python, LLM agents"))
            elapsed = time.monotonic() - started
        return types.SimpleNamespace(handoffs=handoffs, approvals=approvals, audit=audit, save=save, elapsed=elapsed,
                                     proposal=approvals.sent[-1])

    def test_chaewon_failure_shows_no_score_and_no_approval(self):
        r = self.run_apply(chaewon=slow(None, error=RuntimeError("Groq and Gemini down")))
        text = "\n".join(r.handoffs.texts() + r.approvals.texts())
        self.assertNotIn("80%", text)
        self.assertNotIn("% Match", text)
        self.assertNotIn("% Fit", text)
        self.assertIn("⚠️ Chaewon could not", text)
        self.assertIn("Groq and Gemini down", text)
        self.assertIn("QA skipped: Chaewon's package is missing", text)
        self.assertIn("Re-run !apply once Chaewon is back.", r.proposal.embed.description)
        self.assertEqual(r.proposal.reactions, [])
        r.save.assert_not_called()
        r.audit.assert_not_called()

    def test_steps_run_concurrently(self):
        r = self.run_apply(chaewon=slow(TAILOR, 0.3), yunjin=slow(YUNJIN, 0.3), kazuha=slow(KAZUHA, 0.3))
        self.assertLess(r.elapsed, 0.6)

    def test_kazuha_timeout_others_continue(self):
        r = self.run_apply(kazuha=slow(KAZUHA, 0.3), timeouts={"chaewon": 5, "yunjin": 5, "kazuha": 0.05})
        handoffs = "\n".join(r.handoffs.texts())
        self.assertIn("⚠️ Kazuha could not", handoffs)
        self.assertIn("timed out", handoffs)
        self.assertIn("80%", handoffs)
        self.assertIn("QA skipped", handoffs)
        self.assertIn("Kazuha", [l for l in handoffs.splitlines() if "QA skipped" in l][0])
        self.assertEqual(r.proposal.reactions, ["✅", "❌"])
        r.save.assert_called_once()
        r.audit.assert_not_called()

    def test_all_steps_ok_matches_today(self):
        r = self.run_apply()
        r.audit.assert_called_once()
        self.assertEqual(r.audit.call_args.args[:3], (TAILOR, YUNJIN, KAZUHA))
        self.assertEqual(r.proposal.reactions, ["✅", "❌"])
        self.assertIn("**80% Match**", r.proposal.embed.description)
        self.assertEqual(r.save.call_args.args[1]["score"], 80)

    def test_a_reply_without_a_score_is_a_failure_not_a_default(self):
        r = self.run_apply(chaewon=slow({"report": "no score"}))
        self.assertEqual(r.proposal.reactions, [])
        self.assertIn("⚠️ Chaewon could not", "\n".join(r.handoffs.texts()))

    def test_run_apply_steps_never_raises(self):
        boom = slow(None, error=ValueError("bad"))
        with mock.patch.object(bot_sakura, "analyze_and_tailor", side_effect=boom), \
                mock.patch.object(bot_sakura, "select_portfolio_pitch", side_effect=boom), \
                mock.patch.object(bot_sakura, "generate_tech_pitch", side_effect=boom):
            res = asyncio.run(bot_sakura.run_apply_steps("text", "Acme", "AI Engineer"))
        self.assertEqual({k: v[0] for k, v in res.items()}, {"chaewon": "failed", "yunjin": "failed", "kazuha": "failed"})
        self.assertIn("ValueError: bad", res["yunjin"][2])


if __name__ == "__main__":
    unittest.main()
