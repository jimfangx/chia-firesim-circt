"""Adapter contract tests: exercise preparation/parsing without proofs or AWS."""
import json
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest import mock

import yaml
import f2_manager
import run_f2
import run_jasper


class JasperSignoffTest(unittest.TestCase):
    def test_raw_witnesses_are_retained_without_signoff_claims(self):
        with tempfile.TemporaryDirectory() as directory:
            proof = Path(directory)
            self.assertFalse(run_jasper.collect_raw_witnesses(proof)["available"])
            (proof / "jasper.log").write_text(
                'INFO (IPF055): A counterexample (cex) with 1 cycles was found for the property "AutoProveCutPointsAssertions:state_CP_assert" in 4.5 s.\n'
                'INFO (IPF055): A counterexample (cex) with 3 cycles was found for the property "AutoProveCutPointsAssertions:state_CP_assert" in 6.5 s.\n'
                'INFO (IPF055): A counterexample (cex) with 2 cycles was found for the property "helper_pair" in 7.0 s.\n'
                'unrelated text and incomplete counterexample (cex)\n')
            result = run_jasper.collect_raw_witnesses(proof)
            self.assertEqual(result["unique_witnesses"], 2)
            self.assertEqual(len(result["actual_cutpoints"]), 1)
            self.assertEqual(result["actual_cutpoints"][0]["cycles"], 1)
            self.assertTrue(result["acceptance"].startswith("none:"))
            inventory = json.loads(Path(result["inventory"]["path"]).read_text())
            self.assertEqual(len(inventory["witnesses"]), 2)
            self.assertNotIn("passed", inventory)
            self.assertFalse((proof / "reports/signoff_summary.txt").exists())

    def test_native_origins_must_belong_to_current_candidate_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rtl = root / "circt/FireSim-generated.sv"
            rtl.parent.mkdir()
            rtl.write_text("native simulator")
            origins = rtl.with_suffix(".memory-origins.json")
            origins.write_text("{}")
            historical = root / "sfc/FireSim-generated.memory-origins.json"
            historical.parent.mkdir()
            historical.write_text("{}")
            design = {"circt_rtl": rtl, "circt": [rtl, historical]}
            with self.assertRaisesRegex(ValueError, "candidate inventory lacks"):
                run_jasper.stage_native_memory_correspondence(design, root / "flow")
            design["circt"].append(origins)
            with mock.patch.object(run_jasper, "recover_capture_layouts", return_value=({}, ["checked"])), \
                 mock.patch.object(run_jasper, "validate_origins", return_value={"joined_records": 1}) as validate, \
                 mock.patch.object(run_jasper, "stage_memory_layout_adapter") as layout, \
                 mock.patch.object(run_jasper, "stage_helper_cex_reporter") as traces:
                result = run_jasper.stage_native_memory_correspondence(design, root / "flow")
                validate.assert_called_once_with([rtl], {}, ["checked"])
                layout.assert_called_once_with(root / "flow", origins)
                traces.assert_called_once_with(root / "flow")
                self.assertEqual(result["collateral"]["path"], str(origins))
                self.assertEqual(result["structural_join"]["joined_records"], 1)

    def fixture(self, root, mode="sec"):
        (root / "reports").mkdir()
        (root / "config.json").write_text(json.dumps({
            "strategy": "state_matching" if mode == "lec" else "proof",
            "spec_top": "F1Shim", "imp_top": "F1Shim", "spec_bbox": [], "imp_bbox": [],
            "allow_unmatched_ports": False}))
        (root / "reports/signoff_summary.txt").write_text(
            "".join(f"category\t{name}\tComplete\n" for name in sorted(run_jasper.SIGNOFF_CATEGORIES)) +
            "".join(f"targets\t{name}\t{2 if name == 'proven' else 0}\n" for name in sorted(run_jasper.TARGET_STATUSES)))
        (root / "reports/targets.txt").write_text("output_a\noutput_b\n")
        (root / "reports/status.txt").write_text("proven\t0\toutput_a\nproven\t0\toutput_b\n")
        if mode == "lec":
            with (root / "reports/signoff_summary.txt").open("a") as stream:
                stream.write("category\tAuto cutpoints proof\tComplete\n")
            (root / "reports/lec_auto_cutpoints.json").write_text(json.dumps({
                "Status": "Complete", "Properties": [
                    {"property_name": "actual_auto_cutpoint", "property_status": "proven"}]}))

    def test_authoritative_signoff_required(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            self.assertEqual(run_jasper.signoff(root, "sec")["target_count"], 2)
            path = root / "reports/signoff_summary.txt"
            good = path.read_text()
            for bad in (good.replace("Complete", "Incomplete", 1),
                        good.replace("proven\t2", "proven\t0"),
                        good.replace("undetermined\t0", "undetermined\t1"),
                        good + "targets\tunreachable-precondition\t1\n",
                        "category\tOverall SEC status\tComplete\ntargets\tproven\t2\n",
                        good.replace("targets\tproven\t2", "targets\tproven\t-1")):
                path.write_text(bad)
                with self.assertRaises(ValueError):
                    run_jasper.signoff(root, "sec")
            path.unlink()
            with self.assertRaises(FileNotFoundError):
                run_jasper.signoff(root, "sec")

    def test_lec_requires_cutpoints_and_real_strategy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root, "lec")
            run_jasper.signoff(root, "lec")
            summary = root / "reports/signoff_summary.txt"
            complete = summary.read_text()
            summary.write_text(complete.replace("category\tAuto cutpoints proof\tComplete\n", ""))
            with self.assertRaisesRegex(ValueError, "missing authoritative"):
                run_jasper.signoff(root, "lec")
            summary.write_text(complete)
            with self.assertRaises(ValueError):
                run_jasper.signoff(root, "sec")
            with (root / "reports/status.txt").open("a") as stream:
                stream.write("undetermined\t0\tinternal_cutpoint\n")
            with self.assertRaises(ValueError):
                run_jasper.signoff(root, "lec")

    def test_blackbox_and_empty_targets_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            config_path = root / "config.json"
            config = json.loads(config_path.read_text())
            config["spec_bbox"] = ["UnmodeledLogic"]
            config_path.write_text(json.dumps(config))
            with self.assertRaises(ValueError):
                run_jasper.signoff(root, "sec")
            config["spec_bbox"] = []
            config_path.write_text(json.dumps(config))
            (root / "reports/targets.txt").write_text("")
            with self.assertRaises(ValueError):
                run_jasper.signoff(root, "sec")


class FireSimPreparationTest(unittest.TestCase):
    def test_linux_runtime_preserves_uniform_template_and_private_rootfs(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            deploy = repo / "sims/firesim/deploy"
            (deploy / "workloads").mkdir(parents=True)
            (deploy / "config_runtime.yaml").write_text(yaml.safe_dump({
                "metasimulation": {"metasimulation_enabled": True},
                "target_config": {}, "workload": {}}))
            template = deploy / "workloads/br-base-uniform.json"
            template.write_text(json.dumps({"benchmark_name": "br-base-uniform",
                "common_bootbinary": "base-bin", "common_rootfs": "base.img",
                "common_outputs": ["/etc/os-release"],
                "common_simulation_outputs": ["uartlog", "memory_stats*.csv"]}))
            image = repo / "private.img"
            kernel = repo / "base-bin"
            manifest = repo / "linux-inputs.json"
            for path in (image, kernel, manifest):
                path.write_text("fixture input")
            prepared = {"template": template, "rootfs": image, "bootbinary": kernel, "manifest": manifest}
            evidence = repo / "evidence"
            evidence.mkdir()
            with mock.patch("prepare_f2_linux.prepare_linux_workload", return_value=prepared) as prepare:
                name, runtime = run_f2.runtime(repo, "rocket", evidence,
                    {"recipe_name": "four_selected_rocket", "config": {}}, {})
            prepare.assert_called_once_with(repo, evidence / "linux-workload", template)
            workload = json.loads((deploy / "workloads" / (name + ".json")).read_text())
            assets = deploy / "workloads" / name
            self.assertEqual((assets / workload["common_bootbinary"]).resolve(), kernel)
            self.assertEqual((assets / workload["common_rootfs"]).resolve(), image)
            self.assertEqual(workload["common_outputs"], ["/etc/os-release"])
            self.assertEqual(json.loads(template.read_text())["benchmark_name"], "br-base-uniform")
            config = yaml.safe_load(runtime.read_text())
            self.assertFalse(config["metasimulation"]["metasimulation_enabled"])
            self.assertEqual(config["target_config"]["plusarg_passthrough"], "+max-cycles=100000000000")
            self.assertTrue(config["workload"]["terminate_on_completion"])
            receipt = json.loads((evidence / "runtime-inputs.json").read_text())
            self.assertEqual(receipt["workload_kind"], "linux_boot_poweroff")
            self.assertEqual(receipt["rootfs"]["path"], str(image))

    def test_driver_packaging_uses_output_directory_and_checks_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            generated = root / "generated"
            generated.mkdir()
            output = root / "sim/output/f2/f2-selected"
            output.mkdir(parents=True)
            driver = output / "FireSim-f2"
            driver.write_bytes(b"built driver")

            class RuntimeConfig:
                quintuplet = "f2-selected"

                def get_deployquintuplet_for_config(self):
                    return self.quintuplet

                def get_local_driver_dir(self):
                    return str(output) + "/"

                def get_local_driver_path(self):
                    return self.get_local_driver_dir() + "FireSim-f2"

            f2_manager.bind_runtime_driver_directory(RuntimeConfig, {
                "quintuplet": "f2-selected", "generated_dir": str(generated)})
            config = RuntimeConfig()
            self.assertEqual(Path(config.get_local_driver_path()), driver)
            self.assertTrue(Path(config.get_local_driver_path()).is_file())
            self.assertFalse((generated / "FireSim-f2").exists())
            config.quintuplet = "f2-different"
            with self.assertRaisesRegex(ValueError, "different hardware configuration"):
                config.get_local_driver_path()

    def test_nested_manager_source_keeps_full_ssh_setup(self):
        calls = []
        factory = lambda command: calls.append(command) or command
        prefix = f2_manager.full_ssh_prefix(factory)
        self.assertEqual(prefix("cd sim/"), "cd sim/")
        self.assertEqual(prefix("source sourceme-manager.sh --skip-ssh-setup"),
                         "set -- && source sourceme-manager.sh")
        self.assertNotIn("--skip-ssh-setup", calls[-1])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sourceme-manager.sh").write_text('test "$#" = 0 || exit 19\nexport TEST_FULL_SSH=enabled\n')
            completed = subprocess.run(["bash", "-c", 'set -- unexpected inherited arguments; ' + calls[-1] +
                                        '; test "$TEST_FULL_SSH" = enabled'], cwd=root)
            self.assertEqual(completed.returncode, 0)

    def test_outer_manager_source_does_not_receive_manager_argv(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            directory = repo / "evidence"
            directory.mkdir()
            manager = repo / "sims/firesim"
            (manager / "deploy").mkdir(parents=True)
            conda = repo / "conda.sh"
            conda.write_text('test "$#" = 0 || exit 20\n')
            (manager / "sourceme-manager.sh").write_text('test "$#" = 0 || exit 21\n')
            original_run = subprocess.run
            def execute_setup(argv, **kwargs):
                self.assertEqual(argv[:2], ["bash", "-lc"])
                argv = list(argv)
                argv[2] = argv[2].replace('/home/ubuntu/miniforge3/etc/profile.d/conda.sh', str(conda))
                return original_run(argv, **kwargs)
            # A harmless stand-in checks that arguments survive both sourced
            # scripts verbatim; no FireSim manager or cloud action is executed.
            command = ["bash", "-c", 'test "$1" = buildbitstream && test "$2" = "two words"',
                       "test-manager", "buildbitstream", "two words"]
            with mock.patch.object(run_f2, "manager_command", return_value=command), \
                    mock.patch.object(run_f2.subprocess, "run", side_effect=execute_setup):
                run_f2.run_manager(repo, directory, "buildbitstream", repo / "selected.yaml", timeout=10)

    def test_previous_delivery_is_preserved_to_force_fresh_make_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            generated = repo / "evidence/generated"
            generated.mkdir(parents=True)
            manifest = {"generated_dir": str(generated), "quintuplet": "f2-selected"}
            delivery = f2_manager.delivery_directory(repo, manifest)
            delivery.mkdir(parents=True)
            files = [delivery / "FireSim-generated.sv", delivery / "FireSim-generated.ila.ipgen.tcl"]
            for path in files:
                path.write_text("old candidate")
            (delivery / "board-template.sv").write_text("retain template")
            f2_manager.archive_previous_delivery(repo, manifest)
            for path in files:
                self.assertFalse(path.exists())
                self.assertEqual((generated.parent / "previous-fpga-delivery" / path.name).read_text(), "old candidate")
            self.assertEqual((delivery / "board-template.sv").read_text(), "retain template")

    def test_local_builder_and_frozen_make_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "candidate"
            source.mkdir()
            for suffix in (".sv", ".const.h", ".const.vh", ".partition.const.h", ".peer2peer.const.yaml",
                           ".defines.vh", ".synthesis.xdc", ".implementation.xdc"):
                (source / ("FireSim-generated" + suffix)).write_text("candidate" + suffix)
            (source / "post-bridge-extraction-all.json").write_text("[]")
            design = {"circt_rtl": source / "FireSim-generated.sv", "recipe_name": "four_selected_rocket",
                      "recipe": {"PLATFORM": "f2", "TARGET_PROJECT": "firesim", "DESIGN": "FireSim",
                                 "TARGET_CONFIG": "RocketConfig", "PLATFORM_CONFIG": "BaseF2Config"}}
            output = root / "evidence"
            frozen = run_f2.prepare(root, "rocket", output, design)
            f2_manager.validate(frozen)
            build = yaml.safe_load((output / "build.yaml").read_text())
            self.assertEqual(build["build_farm"]["recipe_arg_overrides"]["build_farm_hosts"], ["localhost"])
            self.assertIn("externally_provisioned", build["build_farm"]["base_recipe"])
            self.assertEqual(build["builds_to_run"], ["four_selected_rocket"])
            argv = run_f2.manager_command(root, output, "buildbitstream", root / "selected.yaml", ["-q"])
            self.assertIn("buildbitstream", argv)
            self.assertIn("--manifest", argv)
            supplied = f2_manager.frozen_arguments(frozen)
            self.assertIn("GOLDENGATE_COMPILER=circt", supplied)
            self.assertEqual(supplied.count("-o"), len(frozen["files"]))
            # Exercise actual GNU make's supplied-input semantics: its original
            # compile rule has both a forced rebuild and an absent FIRRTL input.
            # Delivery succeeds while neither dependency can run/regenerate RTL.
            rtl = generated_rtl = Path(frozen["generated_dir"]) / "FireSim-generated.sv"
            makefile = root / "fixture.mk"
            makefile.write_text(f".PHONY: force\n{rtl}: missing.fir force\n\t@echo UNEXPECTED_COMPILER\n"
                                f"replace-rtl: {rtl}\n\tcp {rtl} {root / 'delivered.sv'}\n")
            completed = subprocess.run(["make", "-f", str(makefile), "replace-rtl", *supplied],
                                       cwd=root, check=True, text=True, capture_output=True)
            self.assertNotIn("UNEXPECTED_COMPILER", completed.stdout)
            self.assertEqual((root / "delivered.sv").read_bytes(), generated_rtl.read_bytes())
            Path(frozen["files"][0]["path"]).write_text("changed")
            with self.assertRaises(ValueError):
                f2_manager.validate(frozen)

    def test_uart_requires_successful_driver_exit(self):
        passing = ('Linux version 6.6.0 test kernel\nCIRCT_LINUX_BOOT: PASS\n'
                   'CIRCT_LINUX_POWEROFF: REQUESTED\n[1.234] reboot: Power down\n'
                   '*** PASSED *** after 1234 cycles\nScript done on 2026-10-21 [COMMAND_EXIT_CODE="0"]\n')
        run_f2.validate_uart(passing)
        for bad in (passing.replace('COMMAND_EXIT_CODE="0"', 'COMMAND_EXIT_CODE="1"'),
                    passing.replace("*** PASSED *** after 1234 cycles", ""),
                    passing.replace("reboot: Power down", ""),
                    passing.replace("CIRCT_LINUX_BOOT: PASS", ""),
                    passing.replace("CIRCT_LINUX_POWEROFF: REQUESTED", ""),
                    passing.replace("Linux version 6.6.0 test kernel", ""),
                    passing.replace("CIRCT_LINUX_BOOT: PASS", "CIRCT_LINUX_POWEROFF: REQUESTED").replace(
                        "CIRCT_LINUX_POWEROFF: REQUESTED\n[1.234]", "CIRCT_LINUX_BOOT: PASS\n[1.234]"),
                    passing + "*** FAILED *** simulation timed out\n",
                    passing + "Kernel panic - not syncing\n",
                    passing + "CIRCT_LINUX_POWEROFF: FAIL poweroff returned\n",
                    'CIRCT_METASIM_BARE: PASS\n*** PASSED *** after 1 cycles\n'
                    'Script done [COMMAND_EXIT_CODE="0"]\n'):
            with self.assertRaises(ValueError):
                run_f2.validate_uart(bad)


if __name__ == "__main__":
    unittest.main()
