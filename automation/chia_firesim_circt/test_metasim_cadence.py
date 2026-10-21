#!/usr/bin/env python3
"""Exercise real regression dispatch with mocked manager commands, without Ray."""
import ast
import contextlib
from dataclasses import asdict, dataclass
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

SOURCE = Path(__file__).with_name("loop.py")


class CadenceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.auto = self.repo / "automation"
        self.deploy = self.repo / "sims/firesim/deploy"
        self.auto.mkdir()
        (self.auto / "logs").mkdir()
        self.deploy.mkdir(parents=True)
        for path in [self.deploy / "config_hwdb.yaml", self.deploy / "firesim",
                     self.repo / "sims/firesim/sim/Makefile", self.repo / "sims/firesim/env.sh",
                     self.repo / "sims/firesim/sourceme-manager.sh", self.auto / "regression_gate.py",
                     self.auto / "loop.py"]:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture")
        (self.deploy / "config_runtime.yaml").write_text(yaml.safe_dump(
            {"metasimulation": {}, "workload": {}}))
        self.generated = self.repo / "sims/firesim/sim/generated-src/xilinx_alveo_u250/xilinx_alveo_u250-firesim-FireSim-FireSimRocketConfig-BaseXilinxAlveoU250Config"
        self.generated.mkdir(parents=True)
        self.rtl = self.generated / "FireSim-generated.sv"
        self.rtl.write_text("module FireSim; endmodule")
        (self.generated / "AbstractClockGate.v").write_text("module AbstractClockGate; endmodule")
        self.inputs = {"version": "v1", "initial_standalones_required": False,
                       "archive": str(self.auto), "portable_reference": "spike.json",
                       "rocket_differential": "pending SFC UART-bearing baseline"}
        tree = ast.parse(SOURCE.read_text())
        # Compile exactly the production dispatcher and its result type, while
        # excluding Chia decorators/imports that would initialize a live worker.
        selected = [node for node in tree.body if isinstance(node, (ast.ClassDef, ast.FunctionDef))
                    and node.name in {"CommandResult", "_run_metasim_regressions"}]
        self.env = dict(globals(), REPO=self.repo, AUTOMATION=self.auto, DEPLOY=self.deploy,
                        __file__=str(self.auto / "loop.py"), REGRESSION_ROOT=self.auto,
                        MANAGER_ENV="fixture", BAREMETAL_WORKLOAD_NAME="circt-metasim-bare",
                        BAREMETAL_PASS_MARKER="PASS", REGRESSION_GROUPS=("integer", "uart"),
                        _safe_filename=lambda name: name)
        exec(compile(ast.Module(body=selected, type_ignores=[]), str(SOURCE), "exec"), self.env)
        self.actions = []
        self.fail_smoke = False

        def command(name, command, log_dir):
            if name == "prepare_rocket_regressions":
                (log_dir / "regression-inputs.json").write_text(json.dumps(self.inputs))
            return self.env["CommandResult"](name, 0, "", "")

        def manager(action, case_dir, runtime_config, name, wall_timeout):
            self.actions.append((case_dir.name, action))
            failed = self.fail_smoke and case_dir.name == "circt-metasim-bare" and action == "runworkload"
            return self.env["CommandResult"](name, int(failed), "", "")

        self.env.update(_run_and_log=command, _run_manager=manager)
        self.serial = 0

    def dispatch(self, force_full=False):
        self.serial += 1
        log = self.auto / f"verification-{self.serial}"
        log.mkdir()
        self.actions.clear()
        with contextlib.redirect_stdout(io.StringIO()), patch.object(shutil, "which", return_value=None):
            results = self.env["_run_metasim_regressions"](log, f"run-{self.serial}", force_full)
        report = json.loads((log / "regression-results.json").read_text())
        return results, report

    def test_unchanged_inputs_reuse_suites_but_execute_smoke(self):
        self.dispatch()
        results, report = self.dispatch()
        self.assertTrue(all(r.ok for r in results))
        self.assertEqual(report["required_workloads"], ["circt-metasim-bare"])
        self.assertTrue(report["suite_evidence"]["reused"])
        self.assertIn(("circt-metasim-bare", "runworkload"), self.actions)
        self.assertIn("verification-1", report["suite_evidence"]["verification"])

    def test_rtl_change_invalidates_evidence(self):
        self.dispatch()
        self.rtl.write_text("module Changed; endmodule")
        _, report = self.dispatch()
        self.assertFalse(report["suite_evidence"]["reused"])
        self.assertEqual(len(report["required_workloads"]), 3)

    def test_driver_change_invalidates_evidence(self):
        self.dispatch()
        driver = self.repo / "generators/firechip/chip/src/main/cc/driver.cc"
        driver.parent.mkdir(parents=True)
        driver.write_text("new driver")
        _, report = self.dispatch()
        self.assertFalse(report["suite_evidence"]["reused"])

    def test_changed_build_environment_invalidates_evidence(self):
        self.dispatch()
        with patch.dict(os.environ, {"CXXFLAGS": "changed-build-flags"}):
            _, report = self.dispatch()
        self.assertFalse(report["suite_evidence"]["reused"])

    def test_pruned_original_evidence_forces_refresh(self):
        self.dispatch()
        (self.auto / "verification-1/regression-results.json").unlink()
        _, report = self.dispatch()
        self.assertFalse(report["suite_evidence"]["reused"])

    def test_workload_version_and_first_standalones(self):
        self.dispatch()
        self.inputs.update(version="v2", initial_standalones_required=True)
        _, report = self.dispatch()
        self.assertFalse(report["suite_evidence"]["reused"])
        self.assertIn("circt-fame-uart", report["required_workloads"])
        self.assertTrue((self.auto / "standalones-passed.json").is_file())

    def test_hardware_attempt_forces_full_suites(self):
        self.dispatch()
        _, report = self.dispatch(force_full=True)
        self.assertFalse(report["suite_evidence"]["reused"])
        self.assertEqual(len(report["required_workloads"]), 3)

    def test_periodic_refresh(self):
        self.dispatch()
        for _ in range(9):
            _, report = self.dispatch()
            self.assertTrue(report["suite_evidence"]["reused"])
        _, report = self.dispatch()
        self.assertFalse(report["suite_evidence"]["reused"])

    def test_failure_invalidates_cache_and_cleans_up(self):
        self.dispatch()
        self.fail_smoke = True
        results, _ = self.dispatch()
        self.assertTrue(any(not r.ok for r in results))
        self.assertIn(("circt-metasim-bare", "terminaterunfarm"), self.actions)
        self.assertFalse((self.auto / "logs/metasim-suite-cache.json").exists())
        self.fail_smoke = False
        _, report = self.dispatch()
        self.assertFalse(report["suite_evidence"]["reused"])


if __name__ == "__main__":
    unittest.main()
