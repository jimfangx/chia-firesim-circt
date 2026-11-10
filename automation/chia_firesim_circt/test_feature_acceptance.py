#!/usr/bin/env python3
"""Synthetic full-scope receipts only: never launch Ray, Jasper, or AWS."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml

import feature_acceptance as gate
import run_jasper
from verification_nodes import TARGETS


class FeatureAcceptanceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.scope = self.repo / "scope.yaml"
        self.source = self.repo / "compiler.cpp"
        self.source.write_text("fixture compiler")
        self.registry = {"schema_version": 1, "features": {
            name: {"required": True, "capabilities": [name + "_cap"],
                   "representative_cases": [name + "_linux"], "supporting_cases": [name + "_unit"],
                   "evidence": []} for name in gate.FEATURES}}
        self.publish_scope()
        self.config = {"feature_scope_file": str(self.scope), "identity_files": [str(self.scope)],
                       "feature_evidence_root": str(self.repo / "logs/feature-evidence")}
        self.identity = self.snapshot(self.config, self.repo)[0]
        pairs = {side: {kind: {"passed": True} for kind in ("rtl", "driver")} for side in ("sfc", "circt")}
        self.semantic = {"node": "semantic", "ok": True, "identity": self.identity,
                         "details": [{"target": target, "ok": True, "status": "passed", "result": {
                             "target": target, "input_identity": self.identity, "passed": True,
                             "verification_stage": "prebuild", "syntax": True, "semantic": True,
                             "static_semantics": True, "details": {"paired_syntax": {"passed": True, "pairs": pairs}}}}
                             for target in TARGETS]}
        mocked = patch.object(gate, "_snapshot", side_effect=self.snapshot)
        mocked.start()
        self.addCleanup(mocked.stop)
        self.run_number = 0

    def snapshot(self, config, repo):
        identity = hashlib.sha256(self.scope.read_bytes() + self.source.read_bytes()).hexdigest()
        return identity, {"inputs": {str(self.scope): self.binding(self.scope)["sha256"]}}

    def publish_scope(self):
        self.scope.write_text(yaml.safe_dump(self.registry))

    def binding(self, path):
        path = Path(path)
        return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    def write(self, path, value):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
        return self.binding(path)

    def file(self, path, text="synthetic observation\n"):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return self.binding(path)

    def command(self, directory):
        return self.write(directory / "command.json", {"argv": ["synthetic-fixture"], "returncode": 0,
                          "timed_out": False, "stdout": self.file(directory / "stdout.log"),
                          "stderr": self.file(directory / "stderr.log", "")})

    def proof(self, directory, mode):
        self.write(directory / "config.json", {
            "strategy": "state_matching" if mode == "lec" else "proof", "spec_top": "F1Shim",
            "imp_top": "F1Shim", "spec_bbox": [], "imp_bbox": [], "allow_unmatched_ports": False})
        categories = run_jasper.SIGNOFF_CATEGORIES | ({"Auto cutpoints proof"} if mode == "lec" else set())
        self.file(directory / "reports/signoff_summary.txt",
                  "".join(f"category\t{name}\tComplete\n" for name in sorted(categories)) +
                  "".join(f"targets\t{name}\t{1 if name == 'proven' else 0}\n" for name in sorted(run_jasper.TARGET_STATUSES)))
        self.file(directory / "reports/targets.txt", "boundary\n")
        self.file(directory / "reports/status.txt", "proven\t0\tboundary\n")
        if mode == "lec":
            self.write(directory / "reports/lec_auto_cutpoints.json", {"Status": "Complete", "Properties": [
                {"property_name": "actual_cutpoint", "property_status": "proven"}]})
        return {"mode": mode, "proof_status": "proven", "unproven_obligations": 0,
                "proof_directory": str(directory),
                "proof_files": [self.binding(path) for path in sorted(directory.rglob("*")) if path.is_file()]}

    def fpga(self, directory, case_id, coverage, rtl):
        inputs = {key: self.file(directory / key) for key in ("bootbinary", "rootfs", "workload_json", "linux_preparation", "template")}
        runtime = {"run_farm": {"recipe_arg_overrides": {"run_farm_hosts_to_use": [{"f2.6xlarge": 1}]}},
                   "metasimulation": {"metasimulation_enabled": False}}
        inputs["runtime"] = self.write(directory / "runtime.yaml", runtime)
        uart = ('Linux version synthetic\nCIRCT_LINUX_BOOT: PASS\nCIRCT_LINUX_POWEROFF: REQUESTED\n'
                'reboot: Power down\n*** PASSED *** after 42 cycles\nScript done COMMAND_EXIT_CODE="0"\n')
        receipts = {"build_log": self.file(directory / "build.log"), "manager_log": self.file(directory / "manager.log"),
                    "workload_evidence": self.file(directory / "uart.log", uart),
                    "workload_inputs": self.write(directory / "inputs.json", inputs),
                    "frozen_inputs": self.write(directory / "frozen.json", {"candidate_rtl": rtl, "files": [rtl]}),
                    "build_config": self.write(directory / "build.yaml", {"build_farm": {"recipe_arg_overrides": {"build_farm_hosts": ["localhost"]}}}),
                    "hwdb": self.write(directory / "hwdb.yaml", {"case": {"agfi": "agfi-synthetic"}})}
        check = {"passed": True, "case_id": case_id, "input_identity": self.identity, "capabilities": coverage,
                 "command": self.command(directory / "checker"), "observations": [self.file(directory / "feature-output.csv")]}
        return {"local_bitstream_build": True, "firesim_manager": True, "f2_runfarm": True,
                "workload_passed": True, "linux_booted": True, "poweroff_observed": True,
                "workload_kind": "linux_boot_poweroff", "agfi": "agfi-synthetic", "receipts": receipts,
                "feature_checks": [self.write(directory / "feature-check.json", check)]}

    def case(self, feature):
        directory = Path(self.config["feature_evidence_root"]) / self.identity / feature
        case_id = feature + "_linux"
        coverage = [feature + "_cap"]
        artifacts = [{"role": "config", **self.file(directory / "enabled.yaml", yaml.safe_dump({"enabled_features": [feature]}))},
                     {"role": "rtl", **self.file(directory / "candidate.sv", "module F1Shim; endmodule\n")},
                     {"role": "golden_rtl", **self.file(directory / "sfc.sv", "module F1Shim; endmodule\n")}]
        common = {"passed": True, "case_id": case_id, "target": case_id, "input_identity": self.identity,
                  "enabled_features": [feature], "scope": "synthetic full scope", "artifacts": artifacts}
        stages = {}
        for name in gate.STAGES:
            stage_dir = directory / name
            report = {**copy.deepcopy(common), "command": self.command(stage_dir)}
            if name == "semantic":
                pairs = {side: {kind: {"passed": True, "command": self.command(stage_dir / side / kind)}
                                for kind in ("rtl", "driver")} for side in ("sfc", "circt")}
                report.update(verification_stage="prebuild", syntax=True, semantic=True, static_semantics=True,
                              full_equivalence=False, details={"paired_syntax": {"passed": True, "pairs": pairs}})
            elif name.startswith("jasper_"):
                report.update(self.proof(stage_dir / "proof", name.removeprefix("jasper_")))
            else:
                report.update(self.fpga(stage_dir, case_id, coverage, {key: value for key, value in artifacts[1].items() if key != "role"}))
            stages[name] = self.write(stage_dir / "stage.json", report)
        manifest = {"schema_version": 1, "case_id": case_id, "enabled_features": [feature],
                    "capabilities": coverage, "artifacts": artifacts, "stages": stages}
        self.write(directory / (case_id + ".json"), manifest)
        return directory / (case_id + ".json")

    def all_cases(self):
        return {feature: self.case(feature) for feature in gate.FEATURES}

    def run_gate(self):
        self.run_number += 1
        return gate.run_features(self.config, self.repo / f"results-{self.run_number}", self.repo, self.semantic)

    def change_stage(self, manifest_path, stage, mutation):
        manifest = json.loads(manifest_path.read_text())
        path = Path(manifest["stages"][stage]["path"])
        report = json.loads(path.read_text())
        mutation(report)
        manifest["stages"][stage] = self.write(path, report)
        self.write(manifest_path, manifest)

    def test_missing_feature_evidence_blocks_all_four_without_running_commands(self):
        with patch("subprocess.Popen", side_effect=AssertionError("no commands allowed")):
            result = self.run_gate()
        self.assertFalse(result["ok"])
        self.assertEqual(len(result["details"]), 4)
        self.assertTrue(all(detail["pending_capabilities"] and detail["pending_cases"] for detail in result["details"]))
        self.assertTrue(Path(result["evidence"]).exists())

    def test_complete_hashed_actual_stage_inventories_pass(self):
        self.all_cases()
        result = self.run_gate()
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["identity"], self.identity)
        self.assertEqual(len(result["details"]), 4)
        self.assertGreater(len(result["file_sha256"]), 100)
        self.assertEqual(self.registry, yaml.safe_load(self.scope.read_text()))

    def test_semantic_case_must_bind_the_golden_sfc_rtl(self):
        manifests = self.all_cases()
        path = manifests["fame5"]
        case = json.loads(path.read_text())
        case["artifacts"] = [item for item in case["artifacts"] if item["role"] != "golden_rtl"]
        self.write(path, case)
        result = self.run_gate()
        self.assertFalse(result["ok"])
        self.assertIn("golden SFC RTL", result["reason"])

    def test_baseline_semantic_failure_or_stale_source_never_releases_features(self):
        self.semantic["ok"] = False
        self.assertFalse(self.run_gate()["ok"])
        self.semantic["ok"] = True
        self.source.write_text("new compiler version")
        result = self.run_gate()
        self.assertFalse(result["ok"])
        self.assertIn("current semantic input identity", result["reason"])

    def test_scope_must_be_pinned_and_empty_registry_cannot_pass(self):
        with patch.object(gate, "_snapshot", return_value=(self.identity, {"inputs": {}})):
            result = self.run_gate()
        self.assertIn("identity_files", result["reason"])
        self.registry["features"] = {}
        self.publish_scope()
        self.semantic["identity"] = self.snapshot(self.config, self.repo)[0]
        for detail in self.semantic["details"]:
            detail["result"]["input_identity"] = self.semantic["identity"]
        result = self.run_gate()
        self.assertIn("All four", result["reason"])

    def test_registry_cannot_embed_self_referential_report_bindings(self):
        self.registry["features"]["fame5"]["evidence"] = [{"path": "old-report"}]
        self.publish_scope()
        self.identity = self.snapshot(self.config, self.repo)[0]
        self.semantic["identity"] = self.identity
        for detail in self.semantic["details"]:
            detail["result"]["input_identity"] = self.identity
        result = self.run_gate()
        self.assertFalse(result["ok"])
        self.assertIn("Keep scope immutable", result["reason"])

    def test_missing_case_or_capability_cannot_be_replaced_by_supporting_case(self):
        manifests = self.all_cases()
        manifest = json.loads(manifests["fame5"].read_text())
        manifest["case_id"] = "fame5_unit"
        self.write(manifests["fame5"], manifest)
        result = self.run_gate()
        self.assertFalse(result["ok"])
        self.assertIn("Undeclared representative", result["reason"])

    def test_artifact_tampering_invalidates_existing_stage_receipts(self):
        manifests = self.all_cases()
        manifest = json.loads(manifests["nic"].read_text())
        Path(manifest["artifacts"][1]["path"]).write_text("changed candidate")
        result = self.run_gate()
        self.assertFalse(result["ok"])
        self.assertIn("hash mismatch", result["reason"])

    def test_proof_checkbox_cannot_replace_real_signoff_inventory(self):
        manifests = self.all_cases()
        def mutate(report):
            report["proof_files"] = []
        self.change_stage(manifests["fame5"], "jasper_lec", mutate)
        result = self.run_gate()
        self.assertFalse(result["ok"])
        self.assertIn("authoritative proof inventory", result["reason"])

    def test_incomplete_sec_status_even_with_refreshed_hash_cannot_pass(self):
        manifests = self.all_cases()
        def mutate(report):
            status = Path(report["proof_directory"]) / "reports/status.txt"
            status.write_text("undetermined\t0\tboundary\n")
            report["proof_files"] = [self.binding(Path(binding["path"])) for binding in report["proof_files"]]
        self.change_stage(manifests["fame5"], "jasper_sec", mutate)
        result = self.run_gate()
        self.assertFalse(result["ok"])
        self.assertIn("not proven", result["reason"])

    def test_stage_must_identify_enabled_case_and_same_candidate(self):
        manifests = self.all_cases()
        self.change_stage(manifests["nic"], "semantic", lambda report: report.update(input_identity="old"))
        result = self.run_gate()
        self.assertFalse(result["ok"])
        self.assertIn("stale", result["reason"])

    def test_linux_boolean_cannot_replace_uart_or_feature_observations(self):
        manifests = self.all_cases()
        self.change_stage(manifests["nic"], "firesim", lambda report: report.update(feature_checks=[]))
        result = self.run_gate()
        self.assertFalse(result["ok"])
        self.assertIn("observation/checker", result["reason"])

    def test_linux_failure_or_remote_build_is_rejected_with_refreshed_bindings(self):
        manifests = self.all_cases()
        def mutate(report):
            path = Path(report["receipts"]["build_config"]["path"])
            report["receipts"]["build_config"] = self.write(path, {"build_farm": {"recipe_arg_overrides": {"build_farm_hosts": ["remote"]}}})
        self.change_stage(manifests["nic"], "firesim", mutate)
        result = self.run_gate()
        self.assertFalse(result["ok"])
        self.assertIn("local build host", result["reason"])


if __name__ == "__main__":
    unittest.main()
