"""Real short process/resource replay checks; no real compiler benchmark."""
import json
import os
from pathlib import Path
import resource
import sys
import tempfile
import unittest
from unittest import mock

import run_speed as speed


class SpeedAdapterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = Path(__file__).resolve().parents[2]
        cls.native, cls.semantic = speed.load_support(cls.repo)

    def compiler(self, root, mib=1, delay=0):
        output = root / "output"
        output.mkdir()
        source = ("import pathlib,time\n"
                  f"storage=bytearray({mib}*1024*1024)\n"
                  f"time.sleep({delay})\n"
                  f"p=pathlib.Path({str(output)!r})\n"
                  "(p/'FireSim-generated.sv').write_bytes(b'module F1Shim; endmodule\\n')\n"
                  "(p/'resource.v').write_bytes(b'// actual resource\\n')\n"
                  "(p/'firrtl_black_box_resource_files.f').write_text(str(p/'resource.v')+'\\n')\n")
        return output, {"argv": [sys.executable, "-c", source], "cwd": str(root),
                        "cpu_affinity": [min(os.sched_getaffinity(0))]}

    def test_launch_includes_resource_append_and_preserves_raw_for_validation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output, command = self.compiler(root, delay=0.05)
            run = speed.run_compiler(command, output, root / "compiler.log", 10)
            self.assertGreaterEqual(run["seconds"], 0.05)
            self.assertGreaterEqual(run["seconds"], run["compiler_process_seconds"])
            self.assertGreater(run["resource_append_seconds"], 0)
            self.assertGreater(run["peak_rss_kib"], 0)
            self.assertGreater(run["max_observed_process_threads"], 0)
            self.assertEqual(run["exit_observation_granularity_seconds"], 0.02)
            self.assertEqual(json.loads((root / "compiler.json").read_text())["seconds"], run["seconds"])
            self.assertFalse((output / "FireSim-generated.raw.sv").exists())
            mappings = speed.archive_resources(output, run)
            resources = self.semantic.finalized_resources(output, {"resources": mappings}, "circt")
            self.assertEqual(len(resources), 1)
            self.assertEqual((output / "FireSim-generated.raw.sv").read_bytes(), b"module F1Shim; endmodule\n")
            self.assertTrue((output / "FireSim-generated.sv").read_bytes().endswith(b"// actual resource\n\n"))

    def test_peak_rss_is_per_launch_not_cumulative_child_high_water_mark(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            launches = []
            # fork/preexec inherits this test process's existing RSS high-water
            # mark, which depends on earlier tests. Allocate beyond that mark
            # so the first launch remains distinguishable in a full suite.
            large_mib = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024 + 96
            for index, mib in enumerate((large_mib, 1)):
                directory = root / str(index)
                directory.mkdir()
                output, command = self.compiler(directory, mib=mib)
                launches.append(speed.run_compiler(command, output, directory / "log", 10))
            self.assertGreater(launches[0]["peak_rss_kib"] - launches[1]["peak_rss_kib"], 64 * 1024)

    def test_snapshot_diagnostics_preserve_source_and_ordered_configuration_changes(self):
        before = {"inputs": {"compiler": "abc", "adapter": "old"},
                  "configuration": {"argv": ["proof", "--jobs", "1"]},
                  "git": {"untracked_sources": {"removed.py": "hash"}}}
        after = {"inputs": {"compiler": "abc", "adapter": "new"},
                 "configuration": {"argv": ["proof", "1", "--jobs"]},
                 "git": {"untracked_sources": {"added.py": "hash"}}}
        changes = speed.snapshot_changes(before, after)
        by_path = {tuple(change["path"]): change for change in changes}
        self.assertEqual(set(by_path), {("inputs", "adapter"), ("configuration", "argv"),
                                      ("git", "untracked_sources", "added.py"),
                                      ("git", "untracked_sources", "removed.py")})
        self.assertEqual(by_path[("inputs", "adapter")]["after"], "new")
        self.assertEqual(by_path[("configuration", "argv")]["before"], before["configuration"]["argv"])
        self.assertFalse(by_path[("git", "untracked_sources", "removed.py")]["after_present"])

    def test_failure_and_timeout_cannot_supply_a_timing_sample(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            command = {"argv": [sys.executable, "-c", "raise SystemExit(7)"], "cwd": str(root),
                       "cpu_affinity": [min(os.sched_getaffinity(0))]}
            with self.assertRaisesRegex(ValueError, "exit=7"):
                speed.run_compiler(command, root, root / "failure", 10)
            self.assertEqual(json.loads((root / "failure.json").read_text())["exit_code"], 7)
            command["argv"] = [sys.executable, "-c", "import time; time.sleep(60)"]
            with self.assertRaisesRegex(ValueError, "timeout=True"):
                speed.run_compiler(command, root, root / "timeout", 0.05)
            self.assertTrue(json.loads((root / "timeout.json").read_text())["timed_out"])

    def test_complete_reproduction_only_normalizes_output_metadata_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            accepted, measured = root / "accepted", root / "measured"
            for output in (accepted, measured):
                output.mkdir()
                (output / "FireSim-generated.sv").write_text("module F1Shim; endmodule\n")
                (output / "annotations.json").write_text(json.dumps(
                    [{"targetDir": str(output), "resource": str(output / "a.v"), "ordered": [1, 2, 2]}]))
                (output / "files.f").write_text(str(output / "a.v") + "\n")
            inventory = self.native.inventory(accepted)
            result = speed.repeated_output(accepted, measured, inventory, self.native)
            self.assertEqual(result["normalized_metadata"], ["annotations.json", "files.f"])
            path = measured / "annotations.json"
            good = path.read_text()
            path.write_text(good.replace("1, 2, 2", "2, 1, 2"))
            with self.assertRaisesRegex(ValueError, "annotations.json"):
                speed.repeated_output(accepted, measured, inventory, self.native)
            path.write_text(good)
            (measured / "FireSim-generated.sv").write_text("module F1Shim; wire omitted; endmodule\n")
            with self.assertRaisesRegex(ValueError, "FireSim-generated.sv"):
                speed.repeated_output(accepted, measured, inventory, self.native)
            (measured / "extra").write_text("untracked output")
            with self.assertRaisesRegex(ValueError, "membership"):
                speed.repeated_output(accepted, measured, inventory, self.native)

    def test_rtl_directory_strings_are_never_normalized(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("accepted", "measured"):
                output = root / name
                output.mkdir()
                (output / "FireSim-generated.sv").write_text('initial $display("' + str(output) + '");')
            with self.assertRaisesRegex(ValueError, "FireSim-generated.sv"):
                speed.repeated_output(root / "accepted", root / "measured",
                                      self.native.inventory(root / "accepted"), self.native)

    def fixture_commands(self, root):
        recipe = {"TARGET_CONFIG": "ExactTarget", "PLATFORM_CONFIG": "BaseF2Config"}
        attempt = root / "golden"
        output = attempt / "sfc"
        java = root / "java"
        jars = {"firesim-main.jar": root / "pinned.jar", "firechip.jar": root / "target.jar"}
        command = self.native.SFC.commands(self.repo, java, jars, recipe, attempt, 24, 1)[1]
        gm = {"commands": [command], "recipe": recipe, "heap_gb": 24, "threads": 1,
              "preMIDAS": {"firrtl": {"path": command["argv"][command["argv"].index("-i") + 1]},
                           "annotations": {"path": command["argv"][command["argv"].index("-faf") + 1]}},
              "provenance": {"java": {"path": str(java)},
                             "execution_jars": {name: {"path": str(path)} for name, path in jars.items()}}}
        profile = {"golden": {"preMIDAS": gm["preMIDAS"]}, "compiler": {"path": str(root / "native")},
                   "command_profile": {"cwd": str(self.repo), "cpu_affinity": [min(os.sched_getaffinity(0))],
                                       "platform_config": "BaseF2Config"}}
        circt = root / "accepted-native"
        cm = {"provenance": profile, "command": self.native.command(profile, circt)}
        design = {"recipe": recipe, "sfc_rtl": output / "FireSim-generated.sv",
                  "circt_rtl": circt / "FireSim-generated.sv"}
        return design, gm, cm

    def test_exact_profiles_use_same_archived_input_and_equal_budget(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            design, gm, cm = self.fixture_commands(root)
            destinations = {"sfc": root / "repeat/sfc", "circt": root / "repeat/circt"}
            commands = speed.commands(self.repo, design, gm, cm, destinations, 1, self.native)
            self.assertEqual(commands["sfc"]["cpu_affinity"], commands["circt"]["cpu_affinity"])
            self.assertIn("-XX:ActiveProcessorCount=1", commands["sfc"]["argv"])
            self.assertIn("-Xmx24G", commands["sfc"]["argv"])
            self.assertIn("--no-dedup", commands["sfc"]["argv"])
            self.assertIn("--compile-baseline", commands["circt"]["argv"])
            for kind, input_option, anno_option in (("sfc", "-i", "-faf"),
                                                  ("circt", None, "--annotation-file")):
                argv = commands[kind]["argv"]
                self.assertEqual(argv[argv.index(input_option) + 1] if input_option else argv[1],
                                 gm["preMIDAS"]["firrtl"]["path"])
                self.assertEqual(argv[argv.index(anno_option) + 1], gm["preMIDAS"]["annotations"]["path"])
            gm["commands"][0]["argv"].remove("--no-dedup")
            with self.assertRaisesRegex(ValueError, "complete SFC profile"):
                speed.commands(self.repo, design, gm, cm, destinations, 1, self.native)

    def test_alternates_initial_and_three_fresh_process_repetitions(self):
        self.assertEqual([speed.sample_order(index) for index in range(4)],
                         [("sfc", "circt"), ("circt", "sfc"), ("sfc", "circt"), ("circt", "sfc")])

    def test_invalid_repetition_count_writes_fail_closed_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            report = Path(temporary) / "report.json"
            with mock.patch.dict(os.environ, CHIA_REPORT_PATH=str(report), CHIA_INPUT_IDENTITY="fixture"), \
                    mock.patch.object(sys, "argv", ["speed", "--repo", str(self.repo), "--target", "rocket",
                                                    "--repetitions", "2"]):
                self.assertEqual(speed.main(), 1)
            result = json.loads(report.read_text())
            self.assertFalse(result["passed"])
            self.assertFalse(result["equivalent_work"])
            self.assertEqual(result["samples"], [])

    def test_accepted_static_contract_mismatch_blocks_measurement(self):
        semantic = mock.Mock()
        semantic.compare.return_value = {"driver_abi": {"match": False}}
        semantic.report.return_value = {"static_semantics": False}
        with self.assertRaisesRegex(ValueError, "static contract"):
            speed.accepted_contracts(semantic, self.repo, "rocket", {"sfc": Path("sfc"),
                                                                    "circt": Path("circt")}, "fixture")

    def test_output_validation_failure_retains_measured_pair_without_speed_credit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            design, gm, cm = self.fixture_commands(root)
            design.update(recipe_name="exact_recipe", config={"semantic": {
                "preparation": {"golden_root": str(root / "golden")},
                "candidate_preparation": {"candidate_root": str(root / "candidate")}}})
            gm["sfc_artifacts"] = {}
            cm["circt_inventory"] = {}
            fake_native, fake_semantic = mock.Mock(), mock.Mock()
            fake_native.load_module.return_value._snapshot.return_value = ("fixture", {})
            fake_semantic.receipt.side_effect = [(design["sfc_rtl"].parent, gm, {}),
                                                (design["circt_rtl"].parent, cm, {})]
            report = root / "report.json"
            with mock.patch.dict(os.environ, CHIA_REPORT_PATH=str(report), CHIA_INPUT_IDENTITY="fixture",
                                 CHIA_CORES="1"), \
                    mock.patch.object(sys, "argv", ["speed", "--repo", str(self.repo), "--target", "rocket"]), \
                    mock.patch.object(speed, "load_support", return_value=(fake_native, fake_semantic)), \
                    mock.patch.object(speed, "inputs", return_value=design), \
                    mock.patch.object(speed, "accepted_contracts", return_value={"fixture": "matches"}), \
                    mock.patch.object(speed, "commands", return_value={"sfc": {"fixture": "SFC"},
                                                                       "circt": {"fixture": "CIRCT"}}), \
                    mock.patch.object(speed, "run_compiler", return_value={"seconds": 1.25, "peak_rss_kib": 123}), \
                    mock.patch.object(speed, "archive_resources", return_value=[]), \
                    mock.patch.object(speed, "repeated_output", side_effect=ValueError("changed RTL")):
                self.assertEqual(speed.main(), 1)
            result = json.loads(report.read_text())
            self.assertFalse(result["passed"])
            self.assertFalse(result["equivalent_work"])
            sample = json.loads((root / "initial/sample.json").read_text())
            self.assertEqual(sample["status"], "validating_output_reproduction")
            self.assertEqual(sample["sfc_seconds"], 1.25)
            self.assertEqual(sample["circt_seconds"], 1.25)
            self.assertIn("planned_commands", sample)

    def test_identity_drift_retains_snapshots_and_validated_samples_without_speed_credit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            design, gm, cm = self.fixture_commands(root)
            design.update(recipe_name="exact_recipe", config={"semantic": {
                "preparation": {"golden_root": str(root / "golden")},
                "candidate_preparation": {"candidate_root": str(root / "candidate")}}})
            gm["sfc_artifacts"], cm["circt_inventory"] = {}, {}
            fake_native, fake_semantic = mock.Mock(), mock.Mock()
            before, after = {"inputs": {"adapter": "old"}}, {"inputs": {"adapter": "new"}}
            fake_native.load_module.return_value._snapshot.side_effect = [("fixture", before),
                                                                          ("changed", after)]
            fake_semantic.receipt.side_effect = [(design["sfc_rtl"].parent, gm, {}),
                                                (design["circt_rtl"].parent, cm, {})]
            fake_semantic.paired_syntax.return_value = {"passed": True}
            report = root / "report.json"
            with mock.patch.dict(os.environ, CHIA_REPORT_PATH=str(report), CHIA_INPUT_IDENTITY="fixture",
                                 CHIA_CORES="1"), \
                    mock.patch.object(sys, "argv", ["speed", "--repo", str(self.repo), "--target", "rocket"]), \
                    mock.patch.object(speed, "load_support", return_value=(fake_native, fake_semantic)), \
                    mock.patch.object(speed, "inputs", return_value=design), \
                    mock.patch.object(speed, "accepted_contracts", return_value={"fixture": "matches"}), \
                    mock.patch.object(speed, "commands", return_value={"sfc": {}, "circt": {}}), \
                    mock.patch.object(speed, "run_compiler", return_value={"seconds": 1.25}), \
                    mock.patch.object(speed, "archive_resources", return_value=[]), \
                    mock.patch.object(speed, "repeated_output", return_value={"exact_files": []}):
                self.assertEqual(speed.main(), 1)
            result = json.loads(report.read_text())
            self.assertFalse(result["passed"])
            self.assertFalse(result["equivalent_work"])
            self.assertIn("identity changed", result["error"])
            self.assertEqual(len(result["samples"]), 3)
            self.assertEqual(result["initial_sample"]["status"], "validated")
            self.assertEqual(json.loads((root / "identity-start.json").read_text()), before)
            self.assertEqual(json.loads((root / "identity-end.json").read_text()), after)
            self.assertEqual(result["identity_check"]["changes"],
                             [{"path": ["inputs", "adapter"], "before": "old", "after": "new"}])


if __name__ == "__main__":
    unittest.main()
