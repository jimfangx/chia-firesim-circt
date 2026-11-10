#!/usr/bin/env python3
"""Build the archived CIRCT design locally, then verify it on an F2 run farm.

Called only by the semantic-gated CHIA FireSim node. Preparation is available
without synthesis/cloud launches via --prepare-only. A successful build receipt
is retained even if workload preparation or runtime deployment is still pending.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import subprocess
import time
import uuid

import yaml
from downstream_common import inputs, record, write


def prepare(repo, target, directory, design):
    repo, directory = Path(repo).resolve(), Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    generated = directory / "generated"
    generated.mkdir()
    source = design["circt_rtl"].parent
    # The compiler inventory already validates all files and external resources.
    shutil.copytree(source, generated, dirs_exist_ok=True)
    required = ["FireSim-generated" + suffix for suffix in (
        ".sv", ".const.h", ".const.vh", ".partition.const.h", ".peer2peer.const.yaml",
        ".defines.vh", ".synthesis.xdc", ".implementation.xdc")]
    shutil.copy2(generated / "post-bridge-extraction-all.json", generated / "post-bridge-extraction.json")
    required.append("post-bridge-extraction.json")
    required.extend(path.name for path in sorted(generated.glob("*.ipgen.tcl")))
    recipe = design["recipe"]
    quintuplet = "-".join(recipe[key] for key in ("PLATFORM", "TARGET_PROJECT", "DESIGN", "TARGET_CONFIG", "PLATFORM_CONFIG"))
    frozen = {"repo": str(repo), "target": target, "quintuplet": quintuplet,
              "generated_dir": str(generated), "candidate_rtl": record(design["circt_rtl"]),
              "files": [record(generated / name) for name in required]}
    write(directory / "frozen-inputs.json", frozen)
    deploy = repo / "sims/firesim/deploy"
    build = {"build_farm": {"base_recipe": str(deploy / "build-farm-recipes/externally_provisioned.yaml"),
             "recipe_arg_overrides": {"default_build_dir": str(directory / "local-vivado"),
                                      "build_farm_hosts": ["localhost"]}},
             "builds_to_run": [design["recipe_name"]], "agfis_to_share": [], "share_with_accounts": {}}
    (directory / "build.yaml").write_text(yaml.safe_dump(build, sort_keys=False))
    return frozen


def manager_command(repo, directory, action, recipes, extra=()):
    return [str(repo / ".conda-env/bin/python3"), str(repo / "automation/chia_firesim_circt/f2_manager.py"),
            "--manifest", str(directory / "frozen-inputs.json"), action,
            "-b", str(directory / "build.yaml"), "-r", str(recipes), *extra]


def run_manager(repo, directory, action, recipes, extra=(), timeout=21600):
    command = manager_command(repo, directory, action, recipes, extra)
    # Positional parameters keep shell expansion out of paths and command text.
    shell = ('set -e\nmanager_repo=$1\nshift\nmanager_argv=("$@")\nset --\n'
             'source "${CONDA_EXE%/bin/conda}/etc/profile.d/conda.sh"\n'
             'cd "$manager_repo/sims/firesim"\nsource ./sourceme-manager.sh\n'
             'cd deploy\nexec "${manager_argv[@]}"')
    log = directory / (action + ".log")
    with log.open("w") as stream:
        completed = subprocess.run(["bash", "-lc", shell, "chia-f2-manager", str(repo), *command],
                                   stdout=stream, stderr=subprocess.STDOUT, timeout=timeout)
    if completed.returncode:
        raise ValueError(f"FireSim {action} exited {completed.returncode}; {log}")
    return log


def runtime(repo, target, directory, design, frozen):
    from prepare_f2_linux import prepare_linux_workload

    deploy = repo / "sims/firesim/deploy"
    name = "chia-f2-" + target + "-" + uuid.uuid4().hex[:12]
    section = design["config"].get("firesim", {})
    template = repo / section.get("workload_template", "sims/firesim/deploy/workloads/br-base-uniform.json")
    max_cycles = section.get("linux_max_cycles", 100_000_000_000)
    if isinstance(max_cycles, bool) or not isinstance(max_cycles, int) or max_cycles <= 0:
        raise ValueError("linux_max_cycles must be a positive integer")
    assets = deploy / "workloads" / name
    assets.mkdir()
    # Keep generated scripts in the verification evidence tree. A new .sh
    # under deploy/workloads is otherwise inventoried as untracked source and
    # invalidates the accepted semantic identity before the speed node runs.
    # FireSim's relative workload paths also resolve this archived rootfs.
    linux = prepare_linux_workload(repo, directory / "linux-workload", template)
    workload_json = json.loads(linux["template"].read_text())
    # FireSim resolves workload paths relative to workloads/<benchmark_name>.
    # Retain the br-base-uniform template, use its built kernel and a private
    # rootfs with the supported late-init FireMarshal command/poweroff hook.
    workload_json.update(benchmark_name=name,
                         common_bootbinary=os.path.relpath(linux["bootbinary"], assets),
                         common_rootfs=os.path.relpath(linux["rootfs"], assets))
    workload_path = deploy / "workloads" / (name + ".json")
    workload_path.write_text(json.dumps(workload_json, indent=2) + "\n")
    config = yaml.safe_load((deploy / "config_runtime.yaml").read_text())
    config["run_farm"] = {"base_recipe": str(deploy / "run-farm-recipes/aws_ec2.yaml"),
                          "recipe_arg_overrides": {"run_farm_tag": name, "always_expand_run_farm": True,
                                                   "run_farm_hosts_to_use": [{"f2.6xlarge": 1}],
                                                   "default_simulation_dir": "/home/ubuntu"}}
    config["metasimulation"]["metasimulation_enabled"] = False
    config["target_config"].update(topology="no_net_config", no_net_num_nodes=1,
                                   default_hw_config=design["recipe_name"], plusarg_passthrough=f"+max-cycles={max_cycles}")
    config["workload"].update(workload_name=workload_path.name, suffix_tag=name, terminate_on_completion=True)
    path = directory / "runtime.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    write(directory / "runtime-inputs.json", {"workload_kind": "linux_boot_poweroff",
                                            "template": record(linux["template"]),
                                            "bootbinary": record(linux["bootbinary"]),
                                            "rootfs": record(linux["rootfs"]),
                                            "linux_preparation": record(linux["manifest"]),
                                            "workload_json": record(workload_path), "runtime": record(path)})
    return name, path


def validate_uart(uart):
    uart = uart.replace("\r", "")
    if any(failure in uart for failure in ("Kernel panic", "Oops:", "CIRCT_LINUX_BOOT: FAIL",
                                          "CIRCT_LINUX_POWEROFF: FAIL", "*** FAILED ***")):
        raise ValueError("Linux FPGA workload reported a boot, shutdown or driver failure")
    checks = (r"Linux version [^\n]+", r"(?m)^CIRCT_LINUX_BOOT: PASS\s*$",
              r"(?m)^CIRCT_LINUX_POWEROFF: REQUESTED\s*$", r"reboot: Power down",
              r"\*\*\* PASSED \*\*\* after [0-9]+ cycles", r'Script done[^\n]*COMMAND_EXIT_CODE="0"')
    position = 0
    for pattern in checks:
        match = re.search(pattern, uart[position:])
        if match is None:
            raise ValueError("FPGA Linux workload requires ordered kernel boot, userspace PASS, "
                             "poweroff request, kernel power-down, driver PASS and script exit code 0")
        position += match.end()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--prepare-only", action="store_true", help="write real configs/inputs; no manager/tool/cloud action")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    def interrupted(signum, frame):
        # Let the run-farm finally block execute when CHIA cancels a command.
        # The first signal initiates cleanup; subsequent signals do not interrupt it.
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        raise RuntimeError(f"F2 verification interrupted by signal {signum}")
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    repo = args.repo.resolve()
    report_path = Path(os.environ.get("CHIA_REPORT_PATH", str((args.output_dir or Path.cwd()) / "adapter-result.json"))).resolve()
    directory = args.output_dir.resolve() if args.output_dir else report_path.parent
    result = {"passed": False, "target": args.target, "input_identity": os.environ.get("CHIA_INPUT_IDENTITY", "preparation-only"),
              "scope": "Local Vivado via FireSim manager consuming hash-verified frozen CIRCT simulator RTL/collateral; "
                       "one AWS f2.6xlarge through runtime YAML; br-base-uniform Linux boot, "
                       "verified userspace/rootfs and explicit poweroff on a private rootfs copy.",
              "stage": "preparing", "local_bitstream_build": False, "firesim_manager": False,
              "f2_runfarm": False, "workload_passed": False, "workload_kind": "linux_boot_poweroff",
              "linux_booted": False, "poweroff_observed": False}
    directory.mkdir(parents=True, exist_ok=True)
    write(report_path, result)
    try:
        design = inputs(repo, args.target)
        frozen = prepare(repo, args.target, directory, design)
        result["frozen_inputs"] = record(directory / "frozen-inputs.json")
        recipes = (repo / design["config"]["recipes_file"]).resolve()
        result["build_argv"] = manager_command(repo, directory, "buildbitstream", recipes, ["-q"])
        if args.prepare_only:
            result.update(stage="prepared", verification_claim="none: no bitstream or runtime action")
            write(report_path, result)
            return 0
        # Serialize only local synthesis/delivery, independently from remote Jasper.
        lock_path = Path(os.environ.get("CHIA_F2_BUILD_LOCK", str(
            repo / "automation/chia_firesim_circt/logs/.f2-build.lock")))
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            result.update(stage="building")
            write(report_path, result)
            start = time.time()
            build_log = run_manager(repo, directory, "buildbitstream", recipes, ["-q"])
            entry = repo / "sims/firesim/deploy/built-hwdb-entries" / design["recipe_name"]
            if not entry.is_file() or entry.stat().st_mtime < start:
                raise ValueError("manager did not publish a fresh candidate HWDB entry")
            hwdb = yaml.safe_load(entry.read_text())
            agfi = hwdb[design["recipe_name"]].get("agfi", "")
            if not re.fullmatch(r"agfi-[0-9a-f]+", agfi):
                raise ValueError("build did not produce an AGFI")
            # Force runtime metadata to the same recipe and frozen local driver.
            hwdb[design["recipe_name"]]["deploy_quintuplet_override"] = frozen["quintuplet"]
            hwdb[design["recipe_name"]]["deploy_makefrag_override"] = str(
                repo / "generators/firechip/chip/src/main/makefrag/firesim")
            hwdb_path = directory / "candidate-hwdb.yaml"
            hwdb_path.write_text(yaml.safe_dump(hwdb, sort_keys=False))
            result.update(stage="pending_runtime", local_bitstream_build=True, firesim_manager=True,
                          build_log=str(build_log), agfi=agfi, hwdb=record(hwdb_path))
            write(directory / "build-receipt.json", result)
            write(report_path, result)
        name, runtime_path = runtime(repo, args.target, directory, design, frozen)
        result["workload_inputs"] = str(directory / "runtime-inputs.json")
        extra = ["-c", str(runtime_path), "-a", str(hwdb_path)]
        launched = False
        try:
            result.update(stage="running")
            write(report_path, result)
            for action in ("launchrunfarm", "infrasetup", "runworkload"):
                launched = launched or action == "launchrunfarm"
                manager_log = run_manager(repo, directory, action, recipes, extra, timeout=7200)
            result["f2_runfarm"] = True
            logs = [path for path in (repo / "sims/firesim/deploy/results-workload").rglob("uartlog")
                    if name in str(path)]
            if len(logs) != 1:
                raise ValueError(f"expected exactly one fresh tagged FPGA UART log, found {len(logs)}")
            uart = logs[0].read_text(errors="replace").replace("\r", "")
            validate_uart(uart)
            evidence = directory / "fpga-uartlog"
            shutil.copy2(logs[0], evidence)
            result.update(stage="complete", workload_passed=True, linux_booted=True, poweroff_observed=True,
                          workload_evidence=str(evidence), manager_log=str(manager_log))
        finally:
            if launched:
                run_manager(repo, directory, "terminaterunfarm", recipes, [*extra, "-q"], timeout=600)
        result["passed"] = True
    except Exception as error:
        result["error"] = f"{type(error).__name__}: {error}"
        if result["local_bitstream_build"]:
            result["stage"] = "pending_runtime"
    write(report_path, result)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
