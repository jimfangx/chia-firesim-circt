#!/usr/bin/env python3
"""Exercise the production CHIA graph without starting Ray or external jobs."""
import ast
from pathlib import Path
import unittest


class LoopGraphTest(unittest.TestCase):
    def setUp(self):
        source = Path(__file__).with_name("loop.py")
        tree = ast.parse(source.read_text())
        selected = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name in {"_verification_graph", "_graph_complete"}]
        self.events = []
        self.fail = None
        self.fail_dispatch = None
        self.semantic_pass = True
        self.semantic_report = None
        self.unjoined_feature_worker = False

        def get(task):
            self.events.append(("get", task))
            if task == self.fail:
                raise RuntimeError("worker lost")
            if task == "semantic" and self.semantic_report is not None:
                return self.semantic_report
            if task == 'feature_linux' and self.unjoined_feature_worker:
                return {'node': task, 'ok': False, 'identity': 'candidate', 'workers_joined': False}
            return {"node": task, "ok": self.semantic_pass if task == "semantic" else True,
                    "identity": "candidate", "full_matrix": task == 'feature_linux'}

        class Node:
            def __init__(inner, name):
                inner.name = name

            def chia_remote(inner, *args, **kwargs):
                self.events.append(("dispatch", inner.name))
                if self.fail_dispatch == inner.name:
                    raise RuntimeError("dispatch failed")
                return inner.name

        self.namespace = {"get": get}
        for function, name in (("verify_semantic", "semantic"), ("verify_f2", "firesim"),
                               ("verify_jasper", "jasper"), ("verify_feature_linux", "feature_linux"), ("verify_speed", "speed"),
                               ("verify_features", "features")):
            self.namespace[function] = Node(name)
        exec(compile(ast.Module(body=selected, type_ignores=[]), str(source), "exec"), self.namespace)

    def run_graph(self):
        return self.namespace["_verification_graph"]({}, "run")

    def test_semantic_failure_blocks_long_jobs(self):
        self.semantic_pass = False
        reports = self.run_graph()
        self.assertEqual(self.events, [("dispatch", "semantic"), ("get", "semantic")])
        self.assertFalse(self.namespace["_graph_complete"](reports))

    def test_jasper_and_f2_dispatched_before_join_and_speed_after(self):
        reports = self.run_graph()
        self.assertLess(self.events.index(("dispatch", "jasper")), self.events.index(("get", "firesim")))
        self.assertLess(self.events.index(("dispatch", "firesim")), self.events.index(("get", "jasper")))
        self.assertLess(self.events.index(("get", "firesim")), self.events.index(("dispatch", "speed")))
        self.assertLess(self.events.index(("get", "jasper")), self.events.index(("dispatch", "speed")))
        self.assertLess(self.events.index(("dispatch", "feature_linux")), self.events.index(("get", "firesim")))
        self.assertLess(self.events.index(("get", "feature_linux")), self.events.index(("dispatch", "speed")))
        self.assertTrue(self.namespace["_graph_complete"](reports))

    def test_four_prebuild_comparisons_release_both_branches_without_formal_precondition(self):
        from test_verification_nodes import VerificationNodesTest
        fixture = VerificationNodesTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.semantic_report = fixture.semantic()
        self.assertTrue(self.semantic_report["ok"], self.semantic_report)
        self.assertEqual(len(self.semantic_report["details"]), 4)
        self.run_graph()
        self.assertIn(("dispatch", "jasper"), self.events)
        self.assertIn(("dispatch", "firesim"), self.events)

    def test_one_failed_prebuild_comparison_blocks_both_branches_after_collecting_four(self):
        from test_verification_nodes import VerificationNodesTest, ADAPTER
        fixture = VerificationNodesTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.config["semantic"]["command"]["argv"][-1] = ADAPTER.replace(
            '"semantic": True', '"semantic": os.environ["CHIA_TARGET"] != "rocket"')
        self.semantic_report = fixture.semantic()
        self.assertFalse(self.semantic_report["ok"])
        self.assertEqual(len(self.semantic_report["details"]), 4)
        self.run_graph()
        self.assertEqual(self.events, [("dispatch", "semantic"), ("get", "semantic")])

    def test_worker_failure_still_drains_other_branch(self):
        self.fail = "firesim"
        reports = self.run_graph()
        self.assertIn(("get", "jasper"), self.events)
        self.assertIn(("get", "speed"), self.events)
        self.assertFalse(self.namespace["_graph_complete"](reports))

    def test_dispatch_failure_still_drains_already_started_branch(self):
        self.fail_dispatch = "jasper"
        reports = self.run_graph()
        self.assertIn(("get", "firesim"), self.events)
        self.assertIn(("get", "speed"), self.events)
        self.assertFalse(self.namespace["_graph_complete"](reports))

    def test_only_all_six_nodes_with_same_candidate_can_complete(self):
        reports = self.run_graph()
        complete = self.namespace["_graph_complete"]
        self.assertFalse(complete([]))
        self.assertFalse(complete(reports[:3]))
        self.assertFalse(complete(reports[:4]))
        reports[-1]["identity"] = "other-candidate"
        self.assertFalse(complete(reports))
        reports[-1]["identity"] = "candidate"
        reports[-1]["node"] = "jasper"
        self.assertFalse(complete(reports))

    def test_full_port_feature_gate_cannot_be_skipped(self):
        self.fail = "features"
        reports = self.run_graph()
        self.assertIn(("dispatch", "features"), self.events)
        self.assertLess(self.events.index(("get", "speed")), self.events.index(("dispatch", "features")))
        self.assertFalse(self.namespace["_graph_complete"](reports))

    def test_enabled_linux_worker_failure_is_joined_and_cannot_be_ignored(self):
        self.fail = 'feature_linux'
        reports = self.run_graph()
        self.assertIn(('get', 'firesim'), self.events)
        self.assertIn(('get', 'jasper'), self.events)
        self.assertIn(('get', 'feature_linux'), self.events)
        self.assertIn(('get', 'speed'), self.events)
        self.assertFalse(self.namespace['_graph_complete'](reports))

    def test_unjoined_enabled_workers_block_speed_and_acceptance(self):
        self.unjoined_feature_worker = True
        reports = self.run_graph()
        self.assertIn(('get', 'firesim'), self.events)
        self.assertIn(('get', 'jasper'), self.events)
        self.assertNotIn(('dispatch', 'speed'), self.events)
        self.assertNotIn(('dispatch', 'features'), self.events)
        self.assertFalse(self.namespace['_graph_complete'](reports))

    def test_successful_partial_linux_retry_cannot_complete_full_port(self):
        reports=self.run_graph()
        for report in reports:
            if report['node']=='feature_linux':
                report['full_matrix']=False
        self.assertFalse(self.namespace['_graph_complete'](reports))


if __name__ == "__main__":
    unittest.main()
