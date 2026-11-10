"""Prompt-size regression tests; no model, Ray jobs or external tools."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import loop


class PromptFeedbackTest(unittest.TestCase):
    def test_large_graph_keeps_status_errors_and_evidence_without_payloads(self):
        reports = [{"node": "semantic", "ok": True, "status": "passed", "identity": "accepted",
                    "evidence": "/evidence/semantic/result.json", "details": [
                        {"target": target, "status": "passed", "ok": True,
                         "evidence": f"/evidence/semantic/{target}/target-result.json",
                         "result": {"passed": True, "syntax": True, "semantic": True,
                                    "details": {"rtl_inventory": "x" * 1_000_000}}}
                        for target in ("rocket", "megaboomv3", "shuttle", "saturn_shuttle")]},
                   {"node": "jasper", "ok": False, "status": "failed",
                    "reason": "adapter failed", "evidence": "/evidence/jasper/result.json",
                    "details": [{"target": "rocket", "mode": "lec", "result": {
                        "error": "unmapped output", "proof_status": "incomplete"}}]}]
        feedback = loop._verification_feedback(reports, Path("/evidence/graph.json"))
        self.assertLess(len(feedback), 10_000)
        for value in ("/evidence/graph.json", "saturn_shuttle", "unmapped output", "incomplete"):
            self.assertIn(value, feedback)
        self.assertNotIn("rtl_inventory", feedback)
        self.assertEqual(len(reports[0]["details"][0]["result"]["details"]["rtl_inventory"]), 1_000_000)
        # Persisting the graph remains lossless and independent of prompt size.
        self.assertGreater(len(json.dumps(reports)), 4_000_000)

    def test_every_feedback_field_and_final_prompt_are_bounded(self):
        oversized = "first error " + "x" * 1_100_000 + " last error"
        reports = [{"node": "semantic", "reason": oversized, "evidence": "/evidence/result.json",
                    "details": [{"target": "rocket", "reason": oversized}]}]
        feedback = loop._verification_feedback(reports, Path("/evidence/graph.json"))
        self.assertLessEqual(len(feedback), loop.FEEDBACK_MAX_CHARS)
        self.assertIn("first error", feedback)
        self.assertIn("last error", feedback)
        with patch.object(loop, "_workspace_snapshot", return_value=oversized):
            prompt = loop._agent_prompt(oversized, 1, 10, None)
        self.assertLess(len(prompt), loop.PROMPT_MAX_CHARS)
        self.assertIn("Iteration 1 of 10", prompt)

    def test_many_failed_nodes_cannot_overflow_feedback_budget(self):
        row = {key: "x" * 20_000 for key in (
            "node", "target", "mode", "status", "reason", "error", "evidence", "identity")}
        feedback = loop._verification_feedback([{**row, "details": [row] * 20}] * 10,
                                               Path("/evidence/graph.json"))
        self.assertLessEqual(len(feedback), loop.FEEDBACK_MAX_CHARS)
        self.assertIn("/evidence/graph.json", feedback)


if __name__ == "__main__":
    unittest.main()
