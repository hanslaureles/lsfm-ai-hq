"""
ADR-001 R0: the routing eval's case file and scoring are sound, so the R5 planner
switch is judged against a fair baseline. Hermetic: no LLM, no network.
Run: python -m pytest -q test_routing_eval.py
"""

import asyncio
import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("run_routing_eval", ROOT / "eval" / "run_routing_eval.py")
ev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ev)

AGENT_SKILLS = {label for label in ev.KNOWN_LABELS if not label.startswith("ciel.")}


def load_cases():
    lines = (ROOT / "eval" / "routing_cases.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


class TestCases(unittest.TestCase):

    def test_cases_file_is_valid(self):
        cases = load_cases()
        self.assertEqual(len(cases), 40)
        self.assertGreaterEqual(sum(c["lang"] == "ja" for c in cases), 10)
        self.assertGreaterEqual(sum(bool(c.get("history")) for c in cases), 4)
        used = set()
        for c in cases:
            with self.subTest(prompt=c["prompt"]):
                self.assertIn(c["lang"], ("en", "ja"))
                self.assertTrue(c["prompt"].strip())
                self.assertTrue(c["expect"])
                self.assertLessEqual(set(c["expect"]), ev.KNOWN_LABELS)
                for turn in c.get("history", []):
                    self.assertTrue({"user_prompt", "agents_called", "ciel_reply"} <= set(turn))
                used |= set(c["expect"])
        self.assertEqual(AGENT_SKILLS - used, set(), "every agent skill needs at least one case")

    def test_prompts_are_unique(self):
        prompts = [c["prompt"] for c in load_cases()]
        self.assertEqual(len(prompts), len(set(prompts)))


class TestLabels(unittest.TestCase):

    def test_labels_from_old_plan(self):
        cases = [
            ({"intent_type": "live_weather", "agents_needed": [], "tool_targets": ["live_weather"]},
             {"ciel.weather"}),
            ({"intent_type": "obsidian_brain", "agents_needed": [], "tool_targets": ["ciel_search_vault"],
              "direct_obsidian": "ciel_search_vault"}, {"sakura.vault_search"}),
            ({"intent_type": "agent_delegation", "agents_needed": ["eunchae"], "tool_targets": ["check_vitals"]},
             {"eunchae.vitals"}),
            ({"intent_type": "agent_delegation", "agents_needed": ["kazuha"], "tool_targets": ["whatever"]},
             {"kazuha"}),  # an unmapped target keeps the agent only
            ({"intent_type": "general_knowledge", "agents_needed": [], "tool_targets": ["direct_knowledge"]},
             {"ciel.direct"}),
            ({"intent_type": "reset_memory", "agents_needed": [], "tool_targets": ["reset_memory"]},
             {"ciel.reset"}),
            ({"intent_type": "agent_delegation", "calls": [["yunjin.audit", {}]], "agents_needed": ["yunjin"]},
             {"yunjin.audit"}),
        ]
        for plan, expected in cases:
            with self.subTest(plan=plan):
                self.assertEqual(ev.labels_from_plan(plan), expected)

    def test_labels_follow_what_run_mission_executes(self):
        # Codex R0 fix 1: weather/search run when a tool_target names them (not only the
        # intent), and reset / appraisal return before any agent runs.
        cases = [
            ({"intent_type": "agent_delegation", "agents_needed": ["sakura"],
              "tool_targets": ["check_inbox", "live_weather"]}, {"sakura.inbox", "ciel.weather"}),
            ({"intent_type": "obsidian_brain", "agents_needed": [], "tool_targets": ["ciel_read_profile", "web_search"],
              "direct_obsidian": "ciel_read_profile"}, {"ciel.read_profile", "ciel.web_search"}),
            ({"intent_type": "agent_delegation", "agents_needed": ["kazuha"],
              "tool_targets": ["reset_memory", "git_status"]}, {"ciel.reset"}),
            ({"intent_type": "agent_delegation", "agents_needed": ["kazuha"],
              "tool_targets": ["proactive_appraisal", "git_status"]}, {"ciel.appraisal"}),
            ({"intent_type": "obsidian_brain", "agents_needed": [], "tool_targets": [],
              "direct_obsidian": "proactive_appraisal"}, {"ciel.appraisal"}),
            ({"intent_type": "agent_delegation", "agents_needed": ["eunchae"], "tool_targets": ["check_vitals"],
              "direct_obsidian": "ciel_read_rules"}, {"eunchae.vitals", "ciel.read_rules"}),
        ]
        for plan, expected in cases:
            with self.subTest(plan=plan):
                self.assertEqual(ev.labels_from_plan(plan), expected)

    def test_calls_are_authoritative_for_agents(self):
        # Codex R0 fix 2: an explicit (even empty) calls list wins over legacy agent fields;
        # Ciel's own actions still come from intent / targets.
        self.assertEqual(ev.labels_from_plan({"intent_type": "agent_delegation", "calls": [["kazuha.review", {}]],
                                              "agents_needed": ["yunjin"], "tool_targets": ["scan_links"]}),
                         {"kazuha.review"})  # legacy agents_needed / tool_targets ignored once calls exist
        self.assertEqual(ev.labels_from_plan({"intent_type": "live_weather", "calls": [["sakura.inbox", {}]],
                                              "tool_targets": ["live_weather"]}),
                         {"sakura.inbox", "ciel.weather"})

    def test_empty_or_malformed_plans_are_rejected(self):
        # Codex R0 fix 2: no plan is not a direct answer.
        for bad in ({}, {"calls": []}, None, "general_knowledge", {"intent_type": ""},
                    {"intent_type": "agent_delegation", "calls": [[123, {}]]},
                    {"intent_type": "agent_delegation", "calls": ["kazuha.review"]},
                    # Codex R0 round 2: an empty delegation is not a direct answer ...
                    {"intent_type": "agent_delegation", "calls": [], "agents_needed": ["yunjin"],
                     "tool_targets": ["scan_links"]},
                    {"intent_type": "agent_delegation", "agents_needed": [], "tool_targets": []},
                    # ... and every predicted field has the type Ciel would use.
                    {"intent_type": "agent_delegation", "agents_needed": [123]},
                    {"intent_type": "agent_delegation", "agents_needed": "kazuha"},
                    {"intent_type": "general_knowledge", "calls": [["ciel.direct", 123]]},
                    {"intent_type": "general_knowledge", "tool_targets": [["live_weather"]]},
                    {"intent_type": "obsidian_brain", "direct_obsidian": 5}):
            with self.subTest(plan=bad), self.assertRaises(ValueError):
                ev.labels_from_plan(bad)

    def test_ciel_actions_survive_an_empty_calls_list(self):
        # Codex R0 round 2: keep valid Ciel actions that come with no agent calls.
        self.assertEqual(ev.labels_from_plan({"intent_type": "web_search", "calls": [],
                                              "tool_targets": ["web_search"]}), {"ciel.web_search"})
        self.assertEqual(ev.labels_from_plan({"intent_type": "agent_delegation", "calls": [],
                                              "tool_targets": ["live_weather"]}), {"ciel.weather"})
        self.assertEqual(ev.labels_from_plan({"intent_type": "general_knowledge", "calls": []}), {"ciel.direct"})

    def test_route_keeps_ciel_labels_and_cuts_skills_to_their_agent(self):
        self.assertEqual(ev.route("ciel.weather"), "ciel.weather")
        self.assertEqual(ev.route("kazuha.review"), "kazuha")
        self.assertEqual(ev.route("kazuha"), "kazuha")


class TestScore(unittest.TestCase):

    def test_score_counts_route_and_exact(self):
        cases = [
            {"prompt": "check vitals", "lang": "en", "expect": ["eunchae.vitals"]},
            {"prompt": "review my diff", "lang": "en", "expect": ["kazuha.review"]},
            {"prompt": "天気は？", "lang": "ja", "expect": ["ciel.weather"]},
        ]
        plans = {
            "check vitals": {"intent_type": "agent_delegation", "agents_needed": ["eunchae"],
                             "tool_targets": ["check_vitals"]},                        # exact hit
            "review my diff": {"intent_type": "agent_delegation", "agents_needed": ["kazuha"],
                               "tool_targets": ["git_status"]},                        # route-only hit
            "天気は？": {"intent_type": "general_knowledge", "agents_needed": [],
                        "tool_targets": ["direct_knowledge"]},                          # miss
        }

        async def plan_fn(prompt, history):
            return plans[prompt]
        s = asyncio.run(ev.score(cases, plan_fn))
        self.assertEqual((s["n"], s["n_ja"]), (3, 1))
        self.assertAlmostEqual(s["route"], 2 / 3)
        self.assertAlmostEqual(s["exact"], 1 / 3)
        self.assertEqual(s["route_ja"], 0.0)
        self.assertEqual([m["prompt"] for m in s["misses"]], ["天気は？"])

    def test_score_records_which_model_decided(self):
        # A rate-limited run silently moves to backup models; the baseline must say so.
        cases = [{"prompt": p, "lang": "en", "expect": ["ciel.direct"]} for p in ("a", "b", "c")]
        models = {"a": "qwen/qwen3.8-27b", "b": "llama-3.1-8b-instant", "c": None}

        async def plan_fn(prompt, history):
            source = "keyword" if models[prompt] is None else "llm"
            return {"intent_type": "general_knowledge", "planner": {"source": source, "model": models[prompt]}}
        s = asyncio.run(ev.score(cases, plan_fn))
        self.assertEqual(s["models"], {"qwen/qwen3.8-27b": 1, "llama-3.1-8b-instant": 1, "(none)": 1})

    def test_a_malformed_plan_is_a_miss_not_a_crash(self):
        # Codex R0 fix 2: one bad prediction must not abort the whole eval.
        cases = [{"prompt": "a", "lang": "en", "expect": ["kazuha.review"]},
                 {"prompt": "b", "lang": "en", "expect": ["ciel.direct"]}]

        async def plan_fn(prompt, history):
            if prompt == "a":
                return {"intent_type": "agent_delegation", "calls": [[123, {}]]}
            return {"intent_type": "general_knowledge"}
        s = asyncio.run(ev.score(cases, plan_fn))
        self.assertEqual((s["n"], s["route"]), (2, 0.5))
        self.assertIn("ValueError", s["misses"][0]["got"])
        self.assertEqual(s["sources"], {"error": 1, "keyword": 1})

    def test_bad_planner_metadata_is_a_miss_not_a_crash(self):
        # Codex R0 round 2: route/count processing sits inside the per-case boundary.
        bad = {"a": {"planner": {"source": []}},
               "b": {"planner": {"source": "llm", "model": {"x": 1}}},
               "c": {"intent_type": "agent_delegation", "agents_needed": [123]},
               # Codex R0 round 3: empty containers must not slip past validation via defaults
               "e": {"planner": []},
               "f": {"planner": {"source": "llm", "model": []}},
               "g": {"planner": {"source": "llm", "model": {}}},
               "h": {"planner": {"source": ""}}}
        good = {"d": {},                                            # no planner metadata: keyword
                "i": {"planner": None},                              # null planner: keyword
                "j": {"planner": {"source": "llm", "model": None}}}  # null model: "(none)"
        plans = {k: {"intent_type": "general_knowledge", **v} for k, v in {**bad, **good}.items()}
        plans["c"] = bad["c"]
        cases = [{"prompt": p, "lang": "en", "expect": ["ciel.direct"]} for p in sorted(plans)]

        async def plan_fn(prompt, history):
            return plans[prompt]
        s = asyncio.run(ev.score(cases, plan_fn))
        self.assertEqual(s["n"], 10)
        self.assertEqual(s["route"], 0.3)  # only d, i and j score
        self.assertEqual(s["sources"], {"error": 7, "keyword": 2, "llm": 1})
        self.assertEqual(s["models"], {"(none)": 10})
        self.assertEqual(sorted(m["prompt"] for m in s["misses"]), sorted(bad))

    def test_score_keeps_every_case_for_the_report(self):
        # Codex R0 Q5: exact cannot be rebuilt from aggregates; keep each prediction.
        cases = [{"prompt": "check vitals", "lang": "en", "expect": ["eunchae.vitals"]}]

        async def plan_fn(prompt, history):
            return {"intent_type": "agent_delegation", "agents_needed": ["eunchae"], "tool_targets": ["check_vitals"],
                    "planner": {"source": "llm", "model": "qwen/qwen3.8-27b"}}
        s = asyncio.run(ev.score(cases, plan_fn))
        self.assertEqual(s["cases"], [{"prompt": "check vitals", "lang": "en", "expect": ["eunchae.vitals"],
                                       "got": ["eunchae.vitals"], "route": True, "exact": True,
                                       "source": "llm", "model": "qwen/qwen3.8-27b"}])
        text = ev.report(s, "test", sha="abc1234", pause_s=12.0)
        self.assertIn("| check vitals | en | eunchae.vitals | eunchae.vitals | yes | yes | llm | qwen/qwen3.8-27b |", text)
        self.assertIn("pause 12 s", text)

    def test_each_live_run_gets_its_own_file(self):
        # Codex R0 Q4/Q5: a second run the same day must not overwrite the first.
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            first = ev.next_results_path(d, "2026-10-12")
            self.assertEqual(first.name, "2026-10-12-routing-run1.md")
            first.write_text("x", encoding="utf-8")
            self.assertEqual(ev.next_results_path(d, "2026-10-12").name, "2026-10-12-routing-run2.md")

    def test_r5_gate_needs_one_run_that_clears_both_thresholds(self):
        # Codex R0 fix 3: same-run thresholds, best single baseline run, no componentwise mixing.
        base = [{"route": 0.900, "route_ja": 0.700}, {"route": 0.900, "route_ja": 0.800}]
        self.assertEqual(ev.baseline(base), {"route": 0.900, "route_ja": 0.800})
        bar = ev.baseline(base)
        self.assertFalse(ev.passes_gate([{"route": 0.925, "route_ja": 0.700},
                                         {"route": 0.875, "route_ja": 0.800}], bar))  # complementary runs
        self.assertTrue(ev.passes_gate([{"route": 0.850, "route_ja": 0.600},
                                        {"route": 0.900, "route_ja": 0.800}], bar))
        with self.assertRaises(ValueError):
            ev.passes_gate([{"route": 1.0, "route_ja": 1.0}], bar)  # exactly two runs, no more, no fewer

    def test_a_planner_error_counts_as_a_miss(self):
        cases = [{"prompt": "check vitals", "lang": "en", "expect": ["eunchae.vitals"]}]

        async def broken(prompt, history):
            raise RuntimeError("router exploded")
        s = asyncio.run(ev.score(cases, broken))
        self.assertEqual(s["route"], 0.0)
        self.assertIn("router exploded", s["misses"][0]["got"])


if __name__ == "__main__":
    unittest.main()
