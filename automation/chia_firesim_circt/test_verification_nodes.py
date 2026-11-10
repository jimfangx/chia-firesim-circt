#!/usr/bin/env python3
"""Exercise real subprocess adapters and fail-closed gate dependencies, no Ray."""
import copy
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

sys.path.insert(0, str(Path(__file__).parent))
import verification_nodes as nodes


ADAPTER = '''import json, os
from pathlib import Path
r = {"passed": True, "target": os.environ["CHIA_TARGET"],
     "input_identity": os.environ["CHIA_INPUT_IDENTITY"], "scope": "fixture full design",
     "syntax": True, "semantic": True, "static_semantics": True,
     "verification_stage": "prebuild", "full_equivalence": False,
     "deferred_obligations": {"jasper": ["LEC and SEC remain mandatory"], "firesim": ["FPGA workload remains mandatory"]},
     "details": {"paired_syntax": {"passed": True, "pairs": {
         side: {"rtl": {"passed": True}, "driver": {"passed": True}} for side in ("sfc", "circt")}}},
     "mode": os.environ["CHIA_MODE"],
     "proof_status": "proven", "unproven_obligations": 0,
     "sfc_seconds": 2.0, "circt_seconds": 1.0, "equivalent_work": True,
     "timing_scope": "firesim_compiler",
     "local_bitstream_build": True, "firesim_manager": True,
     "f2_runfarm": True, "workload_passed": True, "agfi": "agfi-fixture",
     "workload_kind": "linux_boot_poweroff", "linux_booted": True, "poweroff_observed": True,
     "build_log": "build.log", "manager_log": "manager.log", "workload_evidence": "uart.log",
     "workload_inputs": "runtime-inputs.json"}
for key in ("build_log", "manager_log", "workload_evidence", "workload_inputs"):
    receipt = Path(os.environ["CHIA_REPORT_PATH"]).parent / (key + ".log")
    receipt.write_text("fixture receipt")
    r[key] = str(receipt)
r.update(samples=[{"sfc_seconds": r["sfc_seconds"], "circt_seconds": r["circt_seconds"]} for _ in range(3)],
         sfc_command=["fixture-sfc"], circt_command=["fixture-circt"], cache_policy="matched cold",
         cores=int(os.environ["CHIA_CORES"]))
Path(os.environ["CHIA_REPORT_PATH"]).write_text(json.dumps(r))
'''

# A small receipt-producing fixture exercises preparation scheduling and hash
# validation without invoking Scala, target generation, or the real manager.
PREPARER = '''import hashlib, json, sys
from pathlib import Path
import yaml
root = Path(sys.argv[1])
requested = sys.argv[2:]
targets = requested[1::2]
assert requested[::2] == ["--target"] * len(targets)
invocations = Path("generation-invocations.json")
history = json.loads(invocations.read_text()) if invocations.exists() else []
invocations.write_text(json.dumps(history + [targets]))
# FAILURE_INJECTION
recipes = yaml.safe_load(Path("recipes.yaml").read_text())
names = dict(zip(("rocket", "megaboomv3", "shuttle", "saturn_shuttle"), recipes))
def rec(path):
    return {"path": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size}
def inventory(directory):
    return {str(p.relative_to(directory)): {k: v for k, v in rec(p).items() if k != "path"}
            for p in directory.rglob("*") if p.is_file()}
toolchain = root / "toolchain"
toolchain.mkdir(parents=True, exist_ok=True)
for name in ["firechip.jar", "firesim-main.jar"]:
    (toolchain / name).write_text("fixture compiler jar")
for target in targets:
    attempt = root / target / "attempt-fixture"
    for name in ["inputs", "sfc", "preMIDAS-resources"]:
        (attempt / name).mkdir(parents=True)
    recipe = recipes[names[target]]
    long_name = "firechip.chip.FireSim." + recipe["TARGET_CONFIG"]
    fir = attempt / "inputs" / (long_name + ".sfc.fir")
    annos = attempt / "inputs" / (long_name + ".anno.json")
    fir.write_text("circuit FireSim :\\n")
    annos.write_text("[]")
    for suffix in [".sv", ".raw.sv", ".const.h", ".const.vh", ".defines.vh", ".synthesis.xdc", ".implementation.xdc", ".partition.const.h"]:
        (attempt / "sfc" / ("FireSim-generated" + suffix)).write_text("fixture generated output")
    (attempt / "sfc/FireSim-generated.peer2peer.const.yaml").write_text("")
    for name in ["post-bridge-extraction.json", "post-fame5-transform.json", "post-sim-mapping.fir"]:
        (attempt / "sfc" / name).write_text("[]" if name.endswith(".json") else "circuit FireSim :")
    for index in range(3):
        source = Path("bootrom-source-%d.img" % index)
        source.write_text("fixture bootrom")
        (attempt / "preMIDAS-resources" / ("bootrom-%d.img" % index)).write_bytes(source.read_bytes())
    external = Path("resource.v")
    archived = attempt / "preMIDAS-resources/annotation-0.resource"
    archived.write_bytes(external.read_bytes())
    sfc_source = Path("platform-blackbox.v")
    sfc_source.write_text("module blackbox; endmodule")
    sfc_archived = attempt / "sfc/external-resources/blackbox.v"
    sfc_archived.parent.mkdir()
    sfc_archived.write_bytes(sfc_source.read_bytes())
    (attempt / "sfc/firrtl_black_box_resource_files.f").write_text(str(sfc_source.resolve()))
    manifest = {"status": "complete", "target": target, "recipe_name": names[target], "recipe": recipe,
                "preMIDAS": {"firrtl": rec(fir), "annotations": rec(annos),
                             "annotation_resources": [{"source": rec(external), "archived": rec(archived)}],
                             "bootrom_resources": [{"source": rec(Path("bootrom-source-%d.img" % i)),
                                                   "archived": rec(attempt / "preMIDAS-resources" / ("bootrom-%d.img" % i))} for i in range(3)]},
                "artifacts": inventory(attempt / "inputs"), "sfc_artifacts": inventory(attempt / "sfc"),
                "resource_artifacts": inventory(attempt / "preMIDAS-resources"),
                "sfc_resources": [{"source": rec(sfc_source), "archived": rec(sfc_archived)}],
                "provenance": {"execution_jars": {p.name: rec(p) for p in toolchain.iterdir()}}}
    manifest_path = attempt / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    (attempt.parent / "complete.json").write_text(json.dumps({"status": "complete", "target": target, "manifest": rec(manifest_path)}))
'''


class VerificationNodesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.evidence = self.repo / "evidence"
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        (self.repo / "source.scala").write_text("fixture")
        (self.repo / "recipes.yaml").write_text("fixture")
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)
        subprocess.run(["git", "-c", "user.email=fixture@example.invalid", "-c", "user.name=Fixture",
                        "commit", "-qm", "fixture"], cwd=self.repo, check=True)
        (self.repo / "sfc.v").write_text("module Test; endmodule")
        (self.repo / "circt.v").write_text("module Test; endmodule")
        self.adapter = {"argv": [sys.executable, "-c", ADAPTER], "timeout_seconds": 10}
        self.config = {
            "recipes_file": "recipes.yaml", "cores": 1,
            "targets": {target: {"recipe": nodes.FIXED_TARGETS[target], "sfc_artifacts": ["sfc.v"],
                                  "circt_artifacts": ["circt.v"]} for target in nodes.TARGETS},
            "semantic": {"command": copy.deepcopy(self.adapter)},
            "jasper": {"lec": copy.deepcopy(self.adapter), "sec": copy.deepcopy(self.adapter)},
            "firesim": {"command": copy.deepcopy(self.adapter)},
            "speed": {"command": copy.deepcopy(self.adapter)},
        }

    def semantic(self):
        return nodes.run_semantic(self.config, self.evidence, self.repo)

    def test_runtime_preparation_keeps_generated_script_out_of_source_identity(self):
        import run_f2

        deploy = self.repo / "sims/firesim/deploy"
        (deploy / "workloads").mkdir(parents=True)
        (deploy / "config_runtime.yaml").write_text(yaml.safe_dump({
            "metasimulation": {}, "target_config": {}, "workload": {}}))
        template = deploy / "workloads/br-base-uniform.json"
        template.write_text(json.dumps({"benchmark_name": "br-base-uniform"}))
        evidence = self.repo / "automation/chia_firesim_circt/logs/verification/firesim/rocket"
        evidence.mkdir(parents=True)

        def prepare(repo, directory, selected_template):
            directory.mkdir()
            for name in ("firemarshal.sh", "rootfs.img", "kernel", "linux-workload-inputs.json"):
                (directory / name).write_text("generated fixture")
            return {"template": selected_template, "rootfs": directory / "rootfs.img",
                    "bootbinary": directory / "kernel", "manifest": directory / "linux-workload-inputs.json"}

        before, _ = nodes._snapshot(self.config, self.repo)
        with patch("prepare_f2_linux.prepare_linux_workload", side_effect=prepare):
            name, _ = run_f2.runtime(self.repo, "rocket", evidence,
                                    {"recipe_name": "fixture", "config": {}}, {})
        after, _ = nodes._snapshot(self.config, self.repo)
        self.assertEqual(before, after)
        workload = json.loads((deploy / "workloads" / (name + ".json")).read_text())
        assets = deploy / "workloads" / name
        self.assertEqual((assets / workload["common_rootfs"]).resolve(),
                         evidence / "linux-workload/rootfs.img")
        # Actual source edits still invalidate this same inventory.
        (self.repo / "new-driver.cc").write_text("new implementation")
        self.assertNotEqual(after, nodes._snapshot(self.config, self.repo)[0])

    def preparation(self):
        real_recipes = Path(__file__).with_name("selected_config_build_recipes.yaml")
        (self.repo / "recipes.yaml").write_text(real_recipes.read_text())
        (self.repo / "resource.v").write_text("module resource; endmodule")
        self.config["semantic"]["preparation"] = {
            "golden_root": "goldens",
            "command": {"argv": [sys.executable, "-c", PREPARER, "{golden_root}"], "timeout_seconds": 10},
        }
        for spec in self.config["targets"].values():
            spec["sfc_artifacts"] = []

    def generation_history(self):
        return json.loads((self.repo / "generation-invocations.json").read_text())

    def candidate_preparation(self):
        self.preparation()
        self.config["semantic"]["candidate_preparation"] = {
            "candidate_root": "candidates", "compiler": "fixture-native-compiler",
            "build_command": {"argv": [sys.executable, "-c",
                "from pathlib import Path; Path('candidate-build.log').write_text('built')"],
                "timeout_seconds": 10},
            "command": {"argv": [sys.executable, "-c",
                "from pathlib import Path; import sys; "
                "assert Path('candidate-build.log').is_file(); "
                "Path('candidate-requests.json').write_text(__import__('json').dumps(sys.argv[1:])); "
                "[Path('candidate-' + t + '.json').write_text('complete') for t in sys.argv[2::2]]"],
                "timeout_seconds": 10}}
        def receipt(config, repo, target):
            path = repo / ("candidate-" + target + ".json")
            return ([str(path), str(repo / "circt.v")], set()) if path.exists() else None
        mocked = patch.object(nodes, "_circt_receipt", side_effect=receipt)
        mocked.start()
        self.addCleanup(mocked.stop)

    def test_candidate_preparation_runs_before_identity_but_never_claims_semantics(self):
        self.candidate_preparation()
        self.config["semantic"]["command"] = None
        result = self.semantic()
        self.assertEqual(result["status"], "blocked", result)
        self.assertIn("Unconfigured adapter", result["reason"])
        self.assertIsNotNone(result["identity"])
        prepared = result["candidate_preparation"]
        self.assertEqual(prepared["status"], "complete")
        self.assertEqual(prepared["requested_targets"], list(nodes.TARGETS))
        self.assertEqual(prepared["verification_claim"], "none: candidate generation/inventory only")
        self.assertEqual(json.loads((self.repo / "candidate-requests.json").read_text()),
                         [arg for target in nodes.TARGETS for arg in ("--target", target)])
        self.evidence = self.repo / "second-evidence"
        second = self.semantic()
        self.assertEqual(second["candidate_preparation"]["existing_complete"], list(nodes.TARGETS))
        self.assertEqual(second["candidate_preparation"]["requested_targets"], [])
        self.assertEqual(result["identity"], second["identity"])

    def test_candidate_build_failure_stops_before_generation_and_identity(self):
        self.candidate_preparation()
        self.config["semantic"]["candidate_preparation"]["build_command"]["argv"][2] = "raise SystemExit(9)"
        result = self.semantic()
        self.assertEqual(result["status"], "failed", result)
        self.assertIsNone(result["identity"])
        self.assertFalse((self.repo / "candidate-requests.json").exists())
        command = json.loads((self.evidence / "semantic/candidate-preparation/build/command.json").read_text())
        self.assertEqual(command["returncode"], 9)

    def test_candidate_partial_failure_retains_diagnostics_without_snapshot(self):
        self.candidate_preparation()
        self.config["semantic"]["candidate_preparation"]["command"]["argv"][2] = (
            "from pathlib import Path; Path('candidate-rocket.json').write_text('partial'); raise SystemExit(7)")
        result = self.semantic()
        self.assertEqual(result["status"], "failed", result)
        self.assertIsNone(result["identity"])
        self.assertFalse((self.evidence / "semantic/inputs.json").exists())
        command = json.loads((self.evidence / "semantic/candidate-preparation/generation/command.json").read_text())
        self.assertEqual(command["returncode"], 7)
        self.assertTrue((self.evidence / "semantic/candidate-preparation/result.json").exists())

    def test_candidate_zero_exit_without_complete_inventory_blocks(self):
        self.candidate_preparation()
        self.config["semantic"]["candidate_preparation"]["command"]["argv"][2] = "pass"
        result = self.semantic()
        self.assertEqual(result["status"], "blocked", result)
        self.assertIn("no current complete receipts", result["reason"])
        self.assertIsNone(result["identity"])

    def test_preparation_runs_before_identity_and_candidate_absence_still_blocks(self):
        self.preparation()
        for spec in self.config["targets"].values():
            spec["circt_artifacts"] = []
        self.config["semantic"]["command"] = None
        result = self.semantic()
        self.assertEqual(result["status"], "blocked", result)
        self.assertEqual(result["preparation"]["status"], "complete")
        self.assertIn("rocket.circt_artifacts", result["reason"])
        self.assertEqual(self.generation_history(), [list(nodes.TARGETS)])
        self.assertTrue((self.evidence / "semantic/preparation/generation/stdout.log").is_file())

    def test_complete_preparation_is_reused_and_all_inventories_bound(self):
        self.preparation()
        first = self.semantic()
        self.assertTrue(first["ok"], first)
        snapshot = json.loads((self.evidence / "semantic/inputs.json").read_text())
        paths = snapshot["artifacts"]["rocket.sfc_artifacts"]
        self.assertTrue(any(name.endswith(".raw.sv") for name in paths))
        self.assertTrue(any(name.endswith("annotation-0.resource") for name in paths))
        self.assertTrue(any(name.endswith("peer2peer.const.yaml") for name in paths))
        self.evidence = self.repo / "second-evidence"
        second = self.semantic()
        self.assertTrue(second["ok"], second)
        self.assertEqual(first["identity"], second["identity"])
        self.assertEqual(self.generation_history(), [list(nodes.TARGETS)])
        self.assertEqual(second["preparation"]["requested_targets"], [])

    def test_preparation_only_requests_missing_targets_and_preserves_completed(self):
        self.preparation()
        root = self.repo / "goldens"
        subprocess.run([sys.executable, "-c", PREPARER, str(root), "--target", "rocket"], cwd=self.repo, check=True)
        before = (root / "rocket/complete.json").read_bytes()
        result = self.semantic()
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.generation_history(), [["rocket"], list(nodes.TARGETS[1:])])
        self.assertEqual(before, (root / "rocket/complete.json").read_bytes())

    def test_preparation_failure_logs_are_preserved_before_identity(self):
        self.preparation()
        self.config["semantic"]["preparation"]["command"]["argv"][2] = PREPARER.replace(
            "# FAILURE_INJECTION", "raise SystemExit(7)")
        result = self.semantic()
        self.assertEqual(result["status"], "failed", result)
        self.assertIsNone(result["identity"])
        self.assertEqual(result["preparation"]["status"], "failed")
        command = json.loads((self.evidence / "semantic/preparation/generation/command.json").read_text())
        self.assertEqual(command["returncode"], 7)
        self.assertTrue((self.evidence / "semantic/preparation/result.json").is_file())

    def test_zero_exit_without_complete_receipts_never_prepares_inputs(self):
        self.preparation()
        self.config["semantic"]["preparation"]["command"]["argv"][2] = "pass"
        result = self.semantic()
        self.assertEqual(result["status"], "blocked", result)
        self.assertIn("no complete receipts", result["reason"])
        self.assertIsNone(result["identity"])

    def test_mutated_complete_output_and_external_input_resource_are_rejected(self):
        for artifact in ("goldens/rocket/attempt-fixture/sfc/FireSim-generated.sv", "resource.v",
                         "bootrom-source-0.img", "platform-blackbox.v"):
            with self.subTest(artifact=artifact):
                self.preparation()
                root = self.repo / "goldens"
                if not (root / "rocket/complete.json").exists():
                    self.assertTrue(self.semantic()["ok"])
                path = self.repo / artifact
                original = path.read_bytes()
                path.write_bytes(b"changed")
                result = nodes.run_jasper(self.config, self.repo / "proof-evidence", self.repo,
                                          {"node": "semantic", "ok": True, "identity": "fixture"})
                self.assertEqual(result["status"], "blocked", result)
                self.assertIn("mismatch", result["reason"])
                path.write_bytes(original)
        self.assertEqual(self.generation_history(), [list(nodes.TARGETS)])

    def test_malformed_marker_is_blocked_without_overwriting_completed_archive(self):
        self.preparation()
        self.assertTrue(self.semantic()["ok"])
        marker = self.repo / "goldens/rocket/complete.json"
        marker.write_text(json.dumps({"status": "complete", "target": "rocket", "manifest": []}))
        self.evidence = self.repo / "malformed-evidence"
        result = self.semantic()
        self.assertEqual(result["status"], "blocked", result)
        self.assertIn("Malformed", result["reason"])
        self.assertEqual(self.generation_history(), [list(nodes.TARGETS)])

    def test_missing_resource_mapping_and_other_empty_sidecars_are_blocked(self):
        self.preparation()
        self.assertTrue(self.semantic()["ok"])
        manifest_path = self.repo / "goldens/rocket/attempt-fixture/manifest.json"
        original = json.loads(manifest_path.read_text())
        marker_path = manifest_path.parent.parent / "complete.json"
        def publish(value):
            manifest_path.write_text(json.dumps(value))
            marker = json.loads(marker_path.read_text())
            marker["manifest"].update(sha256=nodes._digest(manifest_path), bytes=manifest_path.stat().st_size)
            marker_path.write_text(json.dumps(marker))
        malformed = copy.deepcopy(original)
        malformed["preMIDAS"]["bootrom_resources"][0]["archived"] = {"path": str(self.repo / "unarchived.img")}
        publish(malformed)
        result = nodes.run_jasper(self.config, self.repo / "missing-mapping-evidence", self.repo,
                                  {"node": "semantic", "ok": True, "identity": "fixture"})
        self.assertEqual(result["status"], "blocked", result)
        self.assertIn("absent from its archived inventory", result["reason"])
        raw = manifest_path.parent / "sfc/FireSim-generated.raw.sv"
        raw.write_text("")
        original["sfc_artifacts"][raw.name] = {"sha256": nodes._digest(raw), "bytes": 0}
        publish(original)
        result = nodes.run_jasper(self.config, self.repo / "empty-sidecar-evidence", self.repo,
                                  {"node": "semantic", "ok": True, "identity": "fixture"})
        self.assertEqual(result["status"], "blocked", result)
        self.assertIn("empty", result["reason"])

    def test_real_preparation_shell_preserves_target_arguments_and_clears_source_args(self):
        actual = yaml.safe_load(Path(__file__).with_name("verification.yaml").read_text())
        shell = actual["semantic"]["preparation"]["command"]["argv"][2]
        fakebin = self.repo / "bin"
        fakebin.mkdir()
        python = fakebin / "python3"
        captured = self.repo / "forwarded-args.json"
        python.write_text("#!" + sys.executable + "\nimport json, sys\nfrom pathlib import Path\nPath(" + repr(str(captured)) + ").write_text(json.dumps(sys.argv[1:]))\n")
        python.chmod(0o755)
        conda = self.repo / "conda.sh"
        conda.write_text("export PATH=" + shlex.quote(str(fakebin)) + ":$PATH\n")
        manager = self.repo / "sims/firesim"
        manager.mkdir(parents=True)
        (manager / "sourceme-manager.sh").write_text('[ "$#" -eq 0 ] || return 31\nexport FIRESIM_SOURCED=1\n')
        shell = shell.replace("/home/ubuntu/miniforge3/etc/profile.d/conda.sh", str(conda))
        subprocess.run(["bash", "-lc", shell, "fixture", str(self.repo), str(self.repo / "goldens"), "1",
                        "--target", "rocket", "--target", "shuttle"], check=True)
        self.assertEqual(json.loads(captured.read_text()), [
            "sim/goldengate-circt/test/Support/GenerateF2SFCGoldens.py", "--output-root", str(self.repo / "goldens"),
            "--threads", "1", "--target", "rocket", "--target", "shuttle"])
        self.assertNotIn("--skip-ssh-setup", shell)

    def test_four_goldens_and_both_proofs_each(self):
        semantic = self.semantic()
        self.assertTrue(semantic["ok"], semantic)
        self.assertEqual([d["target"] for d in semantic["details"]], list(nodes.TARGETS))
        formal = nodes.run_jasper(self.config, self.evidence, self.repo, semantic)
        self.assertTrue(formal["ok"], formal)
        self.assertEqual(len(formal["details"]), 8)
        self.assertEqual({d["mode"] for d in formal["details"]}, {"lec", "sec"})
        self.assertEqual(json.loads(Path(formal["evidence"]).read_text())["identity"], semantic["identity"])

    def test_failed_semantic_blocks_all_expensive_nodes(self):
        self.config["semantic"]["command"] = {"argv": [sys.executable, "-c", "import sys; sys.stderr.write('missing fixture parser\\n'); raise SystemExit(3)"], "timeout_seconds": 10}
        semantic = self.semantic()
        self.assertEqual(semantic["status"], "failed")
        self.assertEqual(len(semantic["details"]), 4)
        self.assertTrue(all("missing fixture parser" in detail["reason"] for detail in semantic["details"]))
        for function in (nodes.run_jasper, nodes.run_firesim, nodes.run_speed):
            result = function(self.config, self.evidence, self.repo, semantic)
            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["details"], [])

    def test_single_failed_target_still_records_all_four_comparisons(self):
        command = self.config["semantic"]["command"]
        command["argv"][-1] = ADAPTER + '\nif r["target"] == "rocket":\n    r.update(passed=False, semantic=False, reason="driver ABI contract mismatch", unresolved_obligations=["driver_abi mismatch"])\n    Path(os.environ["CHIA_REPORT_PATH"]).write_text(json.dumps(r))\n    raise SystemExit(4)\n'
        result = self.semantic()
        self.assertFalse(result["ok"])
        self.assertEqual([detail["target"] for detail in result["details"]], list(nodes.TARGETS))
        self.assertEqual([detail["ok"] for detail in result["details"]], [False, True, True, True])
        self.assertIn("driver ABI contract mismatch", result["details"][0]["reason"])
        self.assertIn("driver_abi mismatch", result["reason"])
        for detail in result["details"]:
            self.assertTrue(Path(detail["evidence"]).is_file())
        for function in (nodes.run_jasper, nodes.run_firesim):
            blocked = function(self.config, self.evidence, self.repo, result)
            self.assertEqual(blocked["status"], "blocked")
            self.assertEqual(blocked["details"], [])

    def test_all_four_prebuild_passes_release_both_mandatory_nodes(self):
        semantic = self.semantic()
        self.assertTrue(semantic["ok"], semantic)
        self.assertTrue(all(not detail["result"]["full_equivalence"] for detail in semantic["details"]))
        formal = nodes.run_jasper(self.config, self.evidence, self.repo, semantic)
        hardware = nodes.run_firesim(self.config, self.evidence, self.repo, semantic)
        self.assertTrue(formal["ok"], formal)
        self.assertTrue(hardware["ok"], hardware)
        self.assertEqual(len(formal["details"]), 8)
        self.assertEqual(len(hardware["details"]), 4)

    def test_partial_or_misbound_prebuild_evidence_cannot_authorize_downstream(self):
        semantic = self.semantic()
        self.assertTrue(semantic["ok"], semantic)
        for label, mutate in (
                ("partial", lambda value: value["details"].pop()),
                ("identity", lambda value: value["details"][0]["result"].update(input_identity="other")),
                ("stage", lambda value: value["details"][0]["result"].update(verification_stage="postbuild")),
                ("unresolved", lambda value: value["details"][0]["result"].update(unresolved_obligations=["missing static contract"])),
                ("syntax", lambda value: value["details"][0]["result"]["details"]["paired_syntax"]["pairs"]["circt"]["driver"].update(passed=False))):
            with self.subTest(label=label):
                dependency = copy.deepcopy(semantic)
                mutate(dependency)
                result = nodes.run_jasper(self.config, self.repo / label, self.repo, dependency)
                self.assertEqual(result["status"], "blocked", result)
                self.assertEqual(result["details"], [])

    def test_missing_adapters_and_artifacts_never_pass(self):
        self.config["semantic"]["command"] = None
        self.assertEqual(self.semantic()["status"], "blocked")
        (self.repo / "circt.v").unlink()
        result = self.semantic()
        self.assertEqual(result["status"], "blocked")
        self.assertIn("missing", result["reason"])

    def test_rejects_all_config_enumeration(self):
        self.config["targets"]["extra"] = self.config["targets"]["rocket"]
        result = self.semantic()
        self.assertEqual(result["status"], "blocked")
        self.assertIn("Exactly four", result["reason"])

    def test_artifact_or_source_changes_invalidate_semantic(self):
        semantic = self.semantic()
        (self.repo / "circt.v").write_text("changed")
        self.assertEqual(nodes.run_jasper(self.config, self.evidence, self.repo, semantic)["status"], "blocked")
        (self.repo / "circt.v").write_text("module Test; endmodule")
        (self.repo / "source.scala").write_text("changed")
        self.assertEqual(nodes.run_firesim(self.config, self.evidence, self.repo, semantic)["status"], "blocked")

    def test_midrun_input_mutation_fails_gate(self):
        self.config["semantic"]["command"]["argv"][-1] += '\nPath("circt.v").write_text("changed during verify")\n'
        result = self.semantic()
        self.assertEqual(result["status"], "failed")
        self.assertIn("changed during", result["reason"])

    def test_empty_artifact_blocks_verification(self):
        (self.repo / "circt.v").write_text("")
        result = self.semantic()
        self.assertEqual(result["status"], "blocked")
        self.assertIn("empty", result["reason"])

    def test_nested_firesim_dirty_content_change_invalidates_semantic(self):
        nested = self.repo / "sims/firesim"
        nested.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(nested)], check=True)
        source = nested / "Transform.scala"
        source.write_text("baseline")
        subprocess.run(["git", "add", "."], cwd=nested, check=True)
        subprocess.run(["git", "-c", "user.email=fixture@example.invalid", "-c", "user.name=Fixture",
                        "commit", "-qm", "native fixture"], cwd=nested, check=True)
        subprocess.run(["git", "add", "sims/firesim"], cwd=self.repo, check=True, capture_output=True)
        source.write_text("first dirty version")
        semantic = self.semantic()
        self.assertTrue(semantic["ok"], semantic)
        source.write_text("second dirty version")
        result = nodes.run_jasper(self.config, self.evidence, self.repo, semantic)
        self.assertEqual(result["status"], "blocked")

    def test_missing_hardware_receipt_blocks_gate(self):
        self.config["firesim"]["command"]["argv"][-1] += '\nPath(r["build_log"]).unlink()\n'
        semantic = self.semantic()
        result = nodes.run_firesim(self.config, self.evidence, self.repo, semantic)
        self.assertFalse(result["ok"])
        self.assertIn("missing", result["reason"])

    def test_f2_smoke_cannot_satisfy_linux_boot_and_poweroff_gate(self):
        original = self.config["firesim"]["command"]["argv"][-1]
        for field in ("linux_booted", "poweroff_observed"):
            self.config["firesim"]["command"]["argv"][-1] = original.replace(
                f'"{field}": True', f'"{field}": False')
            # Adapter configuration changes invalidate earlier semantic receipts.
            self.evidence = self.repo / ("verification-linux-" + field)
            current = self.semantic()
            self.assertTrue(current["ok"], current)
            result = nodes.run_firesim(self.config, self.evidence, self.repo, current)
            self.assertFalse(result["ok"])
            self.assertIn("Linux boot and poweroff", result["reason"])
        self.config["firesim"]["command"]["argv"][-1] = original.replace(
            '"workload_kind": "linux_boot_poweroff"', '"workload_kind": "baremetal_smoke"')
        self.evidence = self.repo / "verification-linux-kind"
        current = self.semantic()
        result = nodes.run_firesim(self.config, self.evidence, self.repo, current)
        self.assertFalse(result["ok"])
        self.assertIn("br-base Linux boot/poweroff", result["reason"])

    def test_exit_zero_without_structured_evidence_fails(self):
        self.config["semantic"]["command"]["argv"][-1] = "pass"
        result = self.semantic()
        self.assertEqual(result["status"], "failed")
        self.assertIn("fresh result", result["reason"])

    def test_wrong_identity_or_only_syntax_cannot_pass(self):
        self.config["semantic"]["command"]["argv"][-1] = ADAPTER.replace('"semantic": True', '"semantic": False')
        result = self.semantic()
        self.assertEqual(result["status"], "failed")
        self.assertIn("syntax and semantic", result["reason"])

    def test_prebuild_stage_and_both_syntax_sides_are_required(self):
        self.config["semantic"]["command"]["argv"][-1] = ADAPTER.replace('"verification_stage": "prebuild"', '"verification_stage": "postbuild"')
        result = self.semantic()
        self.assertFalse(result["ok"])
        self.assertEqual(len(result["details"]), 4)
        self.assertTrue(all("verification_stage prebuild" in detail["reason"] for detail in result["details"]))

    def test_timeout_is_durable_failure(self):
        self.config["semantic"]["command"] = {"argv": [sys.executable, "-c", "import time; time.sleep(30)"], "timeout_seconds": 0.1}
        result = self.semantic()
        self.assertEqual(result["status"], "failed")
        command = json.loads((self.evidence / "semantic/rocket/semantic/command.json").read_text())
        self.assertTrue(command["timed_out"])

    def test_jasper_unlimited_runs_both_modes_for_all_targets(self):
        for mode in ("lec", "sec"):
            self.config["jasper"][mode]["timeout_seconds"] = 0
        semantic = self.semantic()
        result = nodes.run_jasper(self.config, self.evidence, self.repo, semantic)
        self.assertTrue(result["ok"], result)
        self.assertEqual(len(result["details"]), 8)
        for detail in result["details"]:
            command = detail["command"]
            self.assertEqual(command["timeout_seconds"], 0)
            self.assertIsNone(command["wall_time_limit_seconds"])
            self.assertFalse(command["timed_out"])
            self.assertEqual(command["returncode"], 0)

    def test_non_jasper_unlimited_deadline_remains_blocked(self):
        for mode in ("semantic", "firesim", "speed"):
            with self.subTest(mode=mode):
                context = {"target": "rocket", "mode": mode, "identity": "fixture"}
                adapter = {**self.adapter, "timeout_seconds": 0}
                with self.assertRaises(nodes.VerificationBlocked):
                    nodes._command(adapter, context, self.config, self.repo,
                                   self.evidence / mode / "zero")

    def test_jasper_rejects_invalid_deadlines(self):
        for value in (None, True, False, -1, float("inf"), float("nan"), "0"):
            with self.subTest(value=value):
                adapter = {**self.adapter, "timeout_seconds": value}
                with self.assertRaises(nodes.VerificationBlocked):
                    nodes._command(adapter, {"target": "rocket", "mode": "lec", "identity": "fixture"},
                                   self.config, self.repo, self.evidence / "bad", allow_unlimited=True)

    def test_jasper_finite_deadline_is_still_enforced_and_recorded(self):
        adapter = {"argv": [sys.executable, "-c", "import time; time.sleep(30)"],
                   "timeout_seconds": 0.1}
        directory = self.evidence / "finite-lec"
        with self.assertRaisesRegex(RuntimeError, "Adapter timed out"):
            nodes._command(adapter, {"target": "rocket", "mode": "lec", "identity": "fixture"},
                           self.config, self.repo, directory, allow_unlimited=True)
        record = json.loads((directory / "command.json").read_text())
        self.assertTrue(record["timed_out"])
        self.assertEqual(record["timeout_seconds"], 0.1)
        self.assertEqual(record["wall_time_limit_seconds"], 0.1)
        self.assertIsNone(record["returncode"])

    def test_speed_requires_each_target_strictly_faster(self):
        self.config["speed"]["command"]["argv"][-1] = ADAPTER.replace('"circt_seconds": 1.0', '"circt_seconds": (2.0 if os.environ["CHIA_TARGET"] == "saturn_shuttle" else 1.0)')
        semantic = self.semantic()
        result = nodes.run_speed(self.config, self.evidence, self.repo, semantic)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(result["details"]), 3)
        self.assertIn("saturn_shuttle", result["reason"])

    def test_positive_speed_and_hardware_evidence_contract(self):
        semantic = self.semantic()
        speed = nodes.run_speed(self.config, self.evidence, self.repo, semantic)
        hardware = nodes.run_firesim(self.config, self.evidence, self.repo, semantic)
        self.assertTrue(speed["ok"], speed)
        self.assertTrue(hardware["ok"], hardware)
        self.assertTrue(all(d["circt_over_sfc"] == 0.5 for d in speed["details"]))
        self.assertTrue(all(d["command"]["cores"] == 1 for d in speed["details"]))

    def test_runtime_timings_do_not_affect_compiler_speed_gate(self):
        self.config["speed"]["command"]["argv"][-1] = ADAPTER.replace(
            '"equivalent_work": True',
            '"equivalent_work": True, "sfc_runtime_seconds": 1.0, "circt_runtime_seconds": 100.0')
        semantic = self.semantic()
        result = nodes.run_speed(self.config, self.evidence, self.repo, semantic)
        self.assertTrue(result["ok"], result)
        self.assertTrue(all(d["circt_over_sfc"] == 0.5 for d in result["details"]))

    def test_runtime_scope_cannot_substitute_for_compiler_timings(self):
        self.config["speed"]["command"]["argv"][-1] = ADAPTER.replace(
            '"timing_scope": "firesim_compiler"', '"timing_scope": "simulation_runtime"')
        semantic = self.semantic()
        result = nodes.run_speed(self.config, self.evidence, self.repo, semantic)
        self.assertEqual(result["status"], "failed")
        self.assertIn("FireSim compiler", result["reason"])

    def test_untracked_implementation_change_invalidates_semantic(self):
        (self.repo / "new_transform.scala").write_text("original")
        semantic = self.semantic()
        (self.repo / "new_transform.scala").write_text("modified")
        result = nodes.run_jasper(self.config, self.evidence, self.repo, semantic)
        self.assertEqual(result["status"], "blocked")

    def test_inconclusive_sec_is_failure(self):
        self.config["jasper"]["sec"]["argv"][-1] = ADAPTER.replace('"proof_status": "proven"', '"proof_status": "inconclusive"')
        semantic = self.semantic()
        result = nodes.run_jasper(self.config, self.evidence, self.repo, semantic)
        self.assertEqual(result["status"], "failed")
        self.assertIn("proof must be proven", result["reason"])

    def test_lec_failure_retains_receipts_and_runs_separate_sec(self):
        self.config["jasper"]["lec"]["argv"][-1] = ADAPTER + '''
if r["target"] == "rocket":
    r.update(passed=False, proof_status="counterexample", reason="unreset scalar correspondence unresolved")
    Path(os.environ["CHIA_REPORT_PATH"]).write_text(json.dumps(r))
    raise SystemExit(1)
'''
        semantic = self.semantic()
        result = nodes.run_jasper(self.config, self.evidence, self.repo, semantic)
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "failed")
        self.assertEqual([(d["target"], d["mode"]) for d in result["details"]],
                         [(target, mode) for target in nodes.TARGETS for mode in ("lec", "sec")])
        failed = result["details"][0]
        self.assertEqual(failed["command"]["returncode"], 1)
        self.assertEqual(failed["result"]["proof_status"], "counterexample")
        self.assertIn("unreset scalar correspondence", result["reason"])
        self.assertTrue(all(d["ok"] for d in result["details"][1:]))
        for detail in result["details"]:
            self.assertEqual(json.loads(Path(detail["evidence"]).read_text()), detail)

    def test_unconfirmed_lec_timeout_stops_dispatch_without_claiming_a_proof(self):
        self.config["jasper"]["lec"] = {
            "argv": [sys.executable, "-c", "import time; time.sleep(30)"], "timeout_seconds": 0.05}
        semantic = self.semantic()
        result = nodes.run_jasper(self.config, self.evidence, self.repo, semantic)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(result["details"]), 1)
        detail = result["details"][0]
        self.assertFalse(detail["ok"])
        self.assertTrue(detail["command"]["timed_out"])
        self.assertNotIn("result", detail)
        self.assertFalse(detail["dispatch_safe"])
        self.assertIn("timeout cleanup is unconfirmed", result["reason"])

    def test_cleaned_remote_lec_timeout_retains_failure_and_runs_sec(self):
        self.config["jasper"]["lec"]["argv"][-1] = ADAPTER + '''
r.update(passed=False, proof_status="incomplete", launcher_returncode=124,
         timeout_cleanup={"result": {"success": True}}, error="24h proof limit; signoff incomplete")
Path(os.environ["CHIA_REPORT_PATH"]).write_text(json.dumps(r))
raise SystemExit(1)
'''
        semantic = self.semantic()
        result = nodes.run_jasper(self.config, self.evidence, self.repo, semantic)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(result["details"]), 8)
        self.assertTrue(all(d["dispatch_safe"] for d in result["details"]))
        self.assertEqual([d["ok"] for d in result["details"]], [False, True] * 4)
        self.assertIn("24h proof limit", result["reason"])

    def test_missing_lec_adapter_does_not_skip_sec_and_remains_blocked(self):
        self.config["jasper"]["lec"] = None
        semantic = self.semantic()
        result = nodes.run_jasper(self.config, self.evidence, self.repo, semantic)
        self.assertEqual(result["status"], "blocked")
        self.assertFalse(result["ok"])
        self.assertEqual([d["status"] for d in result["details"]], ["blocked", "passed"] * 4)


if __name__ == "__main__":
    unittest.main()
