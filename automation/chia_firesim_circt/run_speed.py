#!/usr/bin/env python3
"""Replay both frozen FireSim compilers; CHIA owns the actual long benchmark.

Each compiler starts in a fresh process and emits into an empty directory. The
timed interval includes startup and the required resource append, but excludes
input/output validation and syntax checks. Repeated outputs must reproduce the
accepted output of that same compiler before their times can establish speed.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import shutil
import signal
import statistics
import subprocess
import time

from downstream_common import inputs, record, write


BASE = "FireSim-generated"
JAVA_OVERRIDES = {"JAVA_TOOL_OPTIONS", "JDK_JAVA_OPTIONS", "_JAVA_OPTIONS"}


def load_support(repo):
    import importlib.util
    path = repo / "sims/firesim/sim/goldengate-circt/test/Support/GenerateF2CIRCTCandidates.py"
    spec = importlib.util.spec_from_file_location("speed_candidate_support", path)
    native = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(native)
    semantic = native.load_module("speed_semantic_support", path.with_name("CompareF2Semantics.py"))
    return native, semantic


def replace_value(argv, option, value):
    if argv.count(option) != 1 or argv.index(option) + 1 == len(argv):
        raise ValueError("expected exactly one compiler option: " + option)
    argv[argv.index(option) + 1] = str(value)


def commands(repo, design, gm, cm, output_dirs, cores, native):
    """Retain the accepted compiler stages/flags; vary destinations and budget."""
    accepted = [value for value in gm["commands"] if value["phase"] == "firesim_compiler"]
    if len(accepted) != 1:
        raise ValueError("golden must retain exactly one complete compiler launch")
    provenance = gm["provenance"]
    jars = {name: Path(item["path"]) for name, item in provenance["execution_jars"].items()}
    expected = native.SFC.commands(repo, Path(provenance["java"]["path"]), jars, design["recipe"],
                                   design["sfc_rtl"].parent.parent, gm["heap_gb"], gm["threads"])[1]
    if any(accepted[0][key] != expected[key] for key in ("argv", "cwd", "phase", "timing_scope")):
        raise ValueError("golden compiler launch differs from the pinned complete SFC profile")
    for key, option in (("firrtl", "-i"), ("annotations", "-faf")):
        if accepted[0]["argv"][accepted[0]["argv"].index(option) + 1] != gm["preMIDAS"][key]["path"]:
            raise ValueError("SFC command does not use the frozen pre-MIDAS input")
    sfc = copy.deepcopy(expected)
    replace_value(sfc["argv"], "-td", output_dirs["sfc"])
    sfc["argv"] = [f"-XX:ActiveProcessorCount={cores}" if arg.startswith("-XX:ActiveProcessorCount=")
                   else "-Djava.io.tmpdir=" + str(output_dirs["sfc"].parent / "java-tmp")
                   if arg.startswith("-Djava.io.tmpdir=") else arg for arg in sfc["argv"]]
    profile = cm["provenance"]
    if cm["command"] != native.command(profile, design["circt_rtl"].parent):
        raise ValueError("candidate command differs from the complete native compiler profile")
    circt = native.command(profile, output_dirs["circt"])
    affinity = sorted(os.sched_getaffinity(0))
    if isinstance(cores, bool) or not isinstance(cores, int) or not 1 <= cores <= len(affinity):
        raise ValueError("compiler budget must fit current CPU affinity")
    for value in (sfc, circt):
        value["cpu_affinity"] = affinity[:cores]
        value["threads_requested"] = cores
    return {"sfc": sfc, "circt": circt}


def append_resources(output, cwd):
    """Only the production resource append belongs in the measured interval."""
    rtl = output / (BASE + ".sv")
    raw_size = rtl.stat().st_size
    listing = output / "firrtl_black_box_resource_files.f"
    resources = []
    if listing.exists():
        for line in listing.read_text().splitlines():
            if line.strip():
                path = Path(line.strip())
                path = (path if path.is_absolute() else cwd / path).resolve()
                if path == rtl.resolve():
                    raise ValueError("resource append references its own destination")
                resources.append(path)
    with rtl.open("ab") as outgoing:
        for path in resources:
            with path.open("rb") as incoming:
                shutil.copyfileobj(incoming, outgoing)
            outgoing.write(b"\n")
    return raw_size, resources


def run_compiler(command, output, log, timeout):
    """wait4 accounts for this launch only, unlike RUSAGE_CHILDREN maxima."""
    env = {key: value for key, value in os.environ.items() if key not in JAVA_OVERRIDES}
    diagnostic = {"status": "launching", "command": command, "log": str(log),
                  "exit_observation_granularity_seconds": 0.02,
                  "exit_observation": "wait4 WNOHANG polls at a requested 20ms cadence; observed completion includes polling and scheduler delay, applied equally to both compilers"}
    diagnostic_path = log.with_suffix(".json")
    write(diagnostic_path, diagnostic)
    with log.open("x") as stream:
        start = time.monotonic()
        process = subprocess.Popen(command["argv"], cwd=command["cwd"], env=env, stdout=stream,
                                   stderr=subprocess.STDOUT, start_new_session=True,
                                   preexec_fn=lambda: os.sched_setaffinity(0, command["cpu_affinity"]))
        timed_out = False
        max_threads = 0
        try:
            while True:
                try:
                    status_text = Path(f"/proc/{process.pid}/status").read_text()
                    observed = next(int(line.split()[1]) for line in status_text.splitlines()
                                    if line.startswith("Threads:"))
                    max_threads = max(max_threads, observed)
                except (OSError, StopIteration):
                    pass
                child, status, usage = os.wait4(process.pid, os.WNOHANG)
                if child:
                    break
                if time.monotonic() - start >= timeout:
                    timed_out = True
                    os.killpg(process.pid, signal.SIGKILL)
                    _, status, usage = os.wait4(process.pid, 0)
                    break
                time.sleep(0.02)
        except BaseException as error:
            os.killpg(process.pid, signal.SIGKILL)
            _, status, _ = os.wait4(process.pid, 0)
            process.returncode = os.waitstatus_to_exitcode(status)
            write(diagnostic_path, dict(diagnostic, status="interrupted", exit_code=process.returncode,
                                        observed_process_seconds=time.monotonic() - start,
                                        error=f"{type(error).__name__}: {error}"))
            raise
        process.returncode = os.waitstatus_to_exitcode(status)
        process_seconds = time.monotonic() - start
        if timed_out or process.returncode:
            write(diagnostic_path, dict(diagnostic, status="failed", exit_code=process.returncode,
                                        timed_out=timed_out, observed_process_seconds=process_seconds,
                                        peak_rss_kib=usage.ru_maxrss))
            raise ValueError(f"compiler failed: exit={process.returncode}, timeout={timed_out}; {log}")
        try:
            raw_size, resources = append_resources(output, Path(command["cwd"]))
        except Exception as error:
            write(diagnostic_path, dict(diagnostic, status="append_failed", exit_code=process.returncode,
                                        observed_process_seconds=process_seconds, peak_rss_kib=usage.ru_maxrss,
                                        incomplete_compiler_seconds=time.monotonic() - start,
                                        error=f"{type(error).__name__}: {error}"))
            raise
        seconds = time.monotonic() - start
    result = {**diagnostic, "status": "emitted_pending_validation", "seconds": seconds,
            "compiler_process_seconds": process_seconds,
            "resource_append_seconds": seconds - process_seconds,
            "peak_rss_kib": usage.ru_maxrss, "peak_rss_source": "per-launch wait4 ru_maxrss",
            "max_observed_process_threads": max_threads, "thread_sampling_interval_seconds": 0.02,
            "exit_code": process.returncode, "command": command, "log": str(log),
            "raw_rtl_bytes": raw_size, "resource_paths": [str(path) for path in resources],
            "startup": "fresh JVM, including startup/JIT" if command["argv"][0].endswith("java")
                       else "fresh native process; compiler does not invoke a JVM"}
    write(diagnostic_path, result)
    return result


def archive_resources(output, run):
    """Audit copies and hashing occur after the measured outputs are complete."""
    raw = output / (BASE + ".raw.sv")
    remaining = run["raw_rtl_bytes"]
    with (output / (BASE + ".sv")).open("rb") as source, raw.open("xb") as outgoing:
        while remaining:
            block = source.read(min(1024 * 1024, remaining))
            if not block:
                raise ValueError("truncated compiler RTL")
            outgoing.write(block)
            remaining -= len(block)
    external = output / "external-resources"
    external.mkdir(exist_ok=True)
    mappings = []
    for index, value in enumerate(run["resource_paths"]):
        source = Path(value)
        archived = source
        if not source.is_relative_to(output.resolve()):
            archived = external / f"{index}-{source.name}"
            shutil.copy2(source, archived)
        mappings.append({"source": record(source), "archived": record(archived)})
    return mappings


def normalize_metadata(value, output):
    """Only absolute references to this output directory may vary on replay."""
    if isinstance(value, str):
        root = str(output.resolve())
        return "@OUTPUT@" + value[len(root):] if value == root or value.startswith(root + "/") else value
    if isinstance(value, list):
        return [normalize_metadata(child, output) for child in value]
    if isinstance(value, dict):
        return {key: normalize_metadata(child, output) for key, child in value.items()}
    return value


def repeated_output(accepted, measured, inventory, native):
    """Same-compiler reproducibility transfers accepted contracts to each sample.

    No cross-compiler byte equality is used. All RTL, headers, XDC, resources and
    FIRRTL remain exact. JSON preserves array order/duplicates and normalizes
    only its own output-directory strings; file lists normalize the same prefix.
    Unknown/nondeterministic differences fail closed rather than shrinking scope.
    """
    actual = native.inventory(measured)
    if set(actual) != set(inventory):
        raise ValueError("replayed compiler changed output inventory membership")
    checked, normalized = [], []
    for name in sorted(inventory):
        old, new = accepted / name, measured / name
        if native.digest(old) != inventory[name]["sha256"] or old.stat().st_size != inventory[name]["bytes"]:
            raise ValueError("accepted output changed: " + name)
        if actual[name] == inventory[name]:
            checked.append(name)
            continue
        match = False
        if old.suffix == ".json":
            match = normalize_metadata(json.loads(old.read_text()), accepted) == normalize_metadata(
                json.loads(new.read_text()), measured)
        elif old.suffix == ".f":
            match = [normalize_metadata(line, accepted) for line in old.read_text().splitlines()] == [
                normalize_metadata(line, measured) for line in new.read_text().splitlines()]
        if not match:
            raise ValueError("replayed compiler differs from accepted output: " + name)
        normalized.append(name)
    return {"passed": True, "exact_files": checked, "normalized_metadata": normalized,
            "inventory": actual, "scope": "same-compiler complete output reproduction; accepted cross-compiler semantic contracts remain mandatory"}


def sample_order(index):
    return ("sfc", "circt") if index % 2 == 0 else ("circt", "sfc")


def snapshot_changes(before, after, path=()):
    """Report identity drift without dropping or normalizing any identity field."""
    if isinstance(before, dict) and isinstance(after, dict):
        changes = []
        for key in sorted(before.keys() | after.keys()):
            if key not in before or key not in after:
                changes.append({"path": [*path, key], "before_present": key in before,
                                "after_present": key in after,
                                "before": before.get(key), "after": after.get(key)})
            else:
                changes.extend(snapshot_changes(before[key], after[key], (*path, key)))
        return changes
    return [] if before == after else [{"path": list(path), "before": before, "after": after}]


def accepted_contracts(semantic, repo, target, roots, identity):
    details = semantic.compare(repo, target, roots["sfc"], roots["circt"])
    verdict = semantic.report(target, identity, details)
    if verdict["static_semantics"] is not True:
        raise ValueError("accepted outputs no longer match the cross-compiler static contract")
    return details


def main():
    def interrupted(signum, frame):
        raise RuntimeError(f"speed benchmark interrupted by signal {signum}")
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--compiler-timeout", type=int, default=7200)
    args = parser.parse_args()
    report_path = Path(os.environ["CHIA_REPORT_PATH"]).resolve()
    directory, repo = report_path.parent, args.repo.resolve()
    result = {"passed": False, "target": args.target, "input_identity": os.environ["CHIA_INPUT_IDENTITY"],
              "timing_scope": "firesim_compiler", "equivalent_work": False,
              "scope": "Both complete frozen-input FireSim compiler launches through emitted simulator RTL/collateral and production blackbox append, with repeated accepted-output reproduction and paired RTL/driver syntax outside timing. Excludes target elaboration, validation, proofs, builds and runtime.",
              "samples": [], "cache_policy": "No output reuse or compiler cache hits. Prerequisite validation reads accepted inputs/outputs before the initial fresh-process pair, recorded separately; repeated pairs use naturally warmed filesystem/page caches. OS cache eviction and persistent warmed JVM are not claimed. Every SFC sample starts a fresh JVM, including startup/JIT.",
              "bridge_elaboration": {"sfc": "Compiler-invoked PlatformShim/bridge Chisel elaboration occurs inside GoldenGateMain and is fully included in launch timing; its separate duration is not isolated.",
                                     "circt": "Native bridge materialization occurs inside compiler timing; no JVM or bridge elaboration subprocess is invoked."}}
    write(report_path, result)
    try:
        if args.repetitions < 3 or args.compiler_timeout <= 0:
            raise ValueError("at least three repeated pairs and a positive timeout are required")
        cores = int(os.environ["CHIA_CORES"])
        native, semantic = load_support(repo)
        design = inputs(repo, args.target)
        config = design["config"]
        roots = {"sfc": Path(config["semantic"]["preparation"]["golden_root"]),
                 "circt": Path(config["semantic"]["candidate_preparation"]["candidate_root"])}
        accepted, manifests, bindings = {}, {}, {}
        for kind in ("sfc", "circt"):
            accepted[kind], manifests[kind], bindings[kind] = semantic.receipt(roots[kind], args.target, kind)
        gm, cm = manifests["sfc"], manifests["circt"]
        semantic.verify_pair(gm, cm, bindings["sfc"], design["recipe"], design["recipe_name"])
        identity, initial_snapshot = native.load_module("speed_gate", repo / "automation/chia_firesim_circt/verification_nodes.py")._snapshot(config, repo)
        write(directory / "identity-start.json", initial_snapshot)
        result["identity_check"] = {"expected": result["input_identity"], "start": identity,
                                    "start_snapshot": str(directory / "identity-start.json")}
        write(report_path, result)
        if identity != result["input_identity"]:
            raise ValueError("speed inputs differ from CHIA's accepted semantic candidate")
        result["accepted_static_contracts"] = accepted_contracts(semantic, repo, args.target, roots, identity)
        result.update(cores=cores, cpu_affinity=sorted(os.sched_getaffinity(0))[:cores],
                      accepted_manifests=bindings, inputs=gm["preMIDAS"],
                      compiler_files={"sfc_java": gm["provenance"]["java"],
                                      "sfc_jar": gm["provenance"]["execution_jars"]["firesim-main.jar"],
                                      "circt": cm["provenance"]["compiler"]},
                      memory_conditions="same inherited manager/cgroup RAM limits; SFC retains accepted -Xmx heap; sequential compilers, identical CPU affinity")
        for index in range(args.repetitions + 1):
            sample_dir = directory / ("initial" if index == 0 else f"repeat-{index}")
            sample_dir.mkdir(exist_ok=False)
            outputs = {kind: sample_dir / kind for kind in ("sfc", "circt")}
            for output in outputs.values():
                output.mkdir()
            (sample_dir / "java-tmp").mkdir()
            planned = commands(repo, design, gm, cm, outputs, cores, native)
            sample = {"index": index, "order": list(sample_order(index)), "runs": {},
                      "status": "measuring", "planned_commands": planned,
                      "cache_state": "initial fresh processes; OS page cache uncontrolled" if index == 0
                                     else "filesystem naturally warmed by prior complete pair; fresh JVM/native process"}
            sample_path = sample_dir / "sample.json"
            write(sample_path, sample)
            for kind in sample_order(index):
                run = run_compiler(planned[kind], outputs[kind], sample_dir / (kind + ".log"), args.compiler_timeout)
                sample["runs"][kind] = run
                sample[kind + "_seconds"] = run["seconds"]
                write(sample_path, sample)
            # All output hashing, resource archives and lint run after both timers.
            fresh = {}
            sample["reproduction"] = {}
            sample["status"] = "validating_output_reproduction"
            write(sample_path, sample)
            for kind in ("sfc", "circt"):
                mappings = archive_resources(outputs[kind], sample["runs"][kind])
                fresh[kind] = {"sfc_resources" if kind == "sfc" else "resources": mappings}
                inventory = manifests[kind]["sfc_artifacts" if kind == "sfc" else "circt_inventory"]
                sample["reproduction"][kind] = repeated_output(accepted[kind], outputs[kind], inventory, native)
                write(sample_path, sample)
            sample["status"] = "checking_syntax"
            write(sample_path, sample)
            sample["syntax"] = semantic.paired_syntax(repo, outputs["sfc"], outputs["circt"],
                                                     fresh["sfc"], fresh["circt"], sample_dir / "syntax")
            write(sample_path, sample)
            if not sample["syntax"]["passed"]:
                raise ValueError("replayed compiler pair failed complete RTL/driver syntax")
            sample["status"] = "validated"
            write(sample_path, sample)
            if index == 0:
                result["initial_sample"] = sample
            else:
                result["samples"].append(sample)
            write(report_path, result)
        # The same current accepted identity must still hold after all replay.
        end_design = inputs(repo, args.target)
        end_identity, end_snapshot = native.load_module("speed_end_gate", repo / "automation/chia_firesim_circt/verification_nodes.py")._snapshot(end_design["config"], repo)
        write(directory / "identity-end.json", end_snapshot)
        result["identity_check"].update(end=end_identity,
                                        end_snapshot=str(directory / "identity-end.json"),
                                        changes=snapshot_changes(initial_snapshot, end_snapshot))
        write(report_path, result)
        if identity != end_identity:
            raise ValueError("accepted compiler/input/output/config identity changed during benchmark")
        result.update(sfc_seconds=statistics.median(sample["sfc_seconds"] for sample in result["samples"]),
                      circt_seconds=statistics.median(sample["circt_seconds"] for sample in result["samples"]),
                      sfc_command=result["samples"][0]["runs"]["sfc"]["command"],
                      circt_command=result["samples"][0]["runs"]["circt"]["command"], equivalent_work=True)
        result["circt_over_sfc"] = result["circt_seconds"] / result["sfc_seconds"]
        result["passed"] = result["circt_over_sfc"] < 1
        if not result["passed"]:
            result["error"] = "CIRCT median compiler time did not beat SFC"
    except Exception as error:
        result["error"] = f"{type(error).__name__}: {error}"
    write(report_path, result)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
