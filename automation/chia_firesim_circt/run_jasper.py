#!/usr/bin/env python3
"""Run separate combinational state-matching and sequential proofs on the top.

Uses the existing circt_compare flow with strict settings. Its convenient exit
code alone is insufficient: this adapter also requires complete Jasper signoff,
nonempty proof obligations, no blackboxes, and no omitted boundary ports.
Jasper's documented state_matching strategy proves combinational cones with
double-sided cuts on state. LEC acceptance requires its actual cutpoint proof
inventory and signoff; helper equality reports alone are insufficient.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path
import posixpath
import re
import shlex
import shutil
import subprocess
import tarfile
import uuid

from downstream_common import inputs, record, write
from jasper_feature_inputs import load_feature_inputs
from jasper_contract import stage_boundary_contract, validate_configuration, audit_initial_relation
from jasper_memory_layout import (recover_capture_layouts, validate_origins,
                                  stage_memory_layout_adapter, stage_helper_cex_reporter)

SIGNOFF_CATEGORIES = {"Boundary outputs mapping", "Boundary outputs proof", "User cutpoints proof",
                      "Imported assertions proof", "Imported covers proof", "Reset value abstraction",
                      "X signals and undrivens", "Mapped stopats", "Assumption management", "Overall SEC status"}
TARGET_STATUSES = {"proven", "unprocessed", "undetermined", "cex", "unreachable-precondition",
                   "undetermined-precondition", "unprocessed-precondition"}
DEFAULT_MAX_MEM_GIB = 128
DEFAULT_PROVE_TIME = "24h"
DEFAULT_REMOTE_WALL_TIMEOUT_SECONDS = 87000  # 24h proof + 10m report grace

# Installed Jasper 2026.06 SEC User Guide, pp.49,54-55; Command Reference,
# check_sec, pp.562,609,615. This adds reports only to the immutable run copy.
LEC_REPORT_HOOK = r'''
rename sec_flow_signoff_summary sec_flow_original_signoff_summary
proc sec_flow_signoff_summary {dir} {
  sec_flow_original_signoff_summary $dir
  check_sec -signoff_info -get_category_status auto_cutpoints -detailed -json \
    -file $dir/lec_auto_cutpoints.json -force -silent
}
'''


def stage_lec_reporter(flow):
    """Instrument only the staged flow copy, without altering proof settings."""
    library = Path(flow) / "tcl/sec_lib.tcl"
    with library.open("a") as stream:
        stream.write("\n# CHIA: retain actual combinational cutpoint proof evidence.\n" + LEC_REPORT_HOOK)


def stage_timeout_cleanup(flow):
    """Instrument only this run's copy; retain the detached launcher identity."""
    flow = Path(flow)
    shutil.copy2(Path(__file__).with_name("jasper_timeout_cleanup.py"),
                 flow / "jasper_timeout_cleanup.py")
    script = flow / "run_sec.py"
    text = script.read_text()
    anchor = '        if max_mem:\n            threading.Thread(target=memory_guard'
    if text.count(anchor) != 1:
        raise ValueError("Jasper launcher identity hook no longer matches the copied flow")
    hook = ('        from jasper_timeout_cleanup import record_launcher\n'
            '        record_launcher(proj, proc.pid)\n')
    script.write_text(text.replace(anchor, hook + anchor, 1))


def stage_native_memory_correspondence(design, flow):
    """Use only the validated candidate's own compiler-origin collateral.

    Check the emitted structure locally before staging; the remote scanner
    repeats the join against the exact staged RTL. These are helper proposals,
    with no initialization mapping or assumption of state equality.
    """
    rtl = Path(design["circt_rtl"]).resolve()
    origins = rtl.with_suffix(".memory-origins.json")
    if origins not in {Path(path).resolve() for path in design["circt"]}:
        raise ValueError("current candidate inventory lacks native memory origins")
    _, records = recover_capture_layouts([rtl])
    joined = validate_origins([rtl], json.loads(origins.read_text()), records)
    stage_memory_layout_adapter(flow, origins)
    stage_helper_cex_reporter(flow)
    return {"collateral": record(origins), "structural_join": joined,
            "scope": "Compiler-origin field packing checked against emitted RTL; helper assertions only. "
                     "No initial equality, initialization map or proof waiver."}


def lec_cutpoint_signoff(report):
    report = Path(report)
    detail = json.loads((report / "lec_auto_cutpoints.json").read_text())
    category = detail.get("Auto cutpoints proof", detail)
    if not isinstance(category, dict) or category.get("Status") != "Complete":
        raise ValueError("actual LEC auto-cutpoint signoff is missing or incomplete")
    status_keys = {"proven", "bounded_proven", "cex", "undetermined", "unprocessed", "waived"}
    if status_keys & category.keys():
        # Actual Jasper 2026.06p002 detailed category schema, observed in the
        # retained iteration-4 report. Every non-proven list remains blocking.
        if category.keys() - {"Status", "Description"} != status_keys:
            raise ValueError("actual LEC cutpoint inventory has missing or unknown status categories")
        for status in status_keys:
            names = category[status]
            if not isinstance(names, list) or any(not isinstance(name, str) or not name.strip() for name in names):
                raise ValueError("actual LEC cutpoint status inventory is malformed")
            if status != "proven" and names:
                raise ValueError("actual LEC cutpoints are bounded, unproven or waived")
        properties = category["proven"]
    else:
        properties = lec_named_property_inventory(category)
    if not properties or len(set(properties)) != len(properties):
        raise ValueError("actual LEC cutpoint inventory is empty, duplicated or has an unsupported schema")
    return {"cutpoint_property_count": len(properties), "properties": properties,
            "auto_cutpoint_signoff": detail, "inventory": record(report / "lec_auto_cutpoints.json")}


def lec_named_property_inventory(category):
    """Retain the earlier explicit property-name/status representation."""
    if category.keys() - {"Status", "Description", "Properties"}:
        raise ValueError("actual LEC cutpoint inventory has an unsupported schema")
    entries = category.get("Properties")
    if not isinstance(entries, list):
        raise ValueError("actual LEC cutpoint inventory is missing detailed properties")
    properties = []
    for entry in entries:
        if not isinstance(entry, dict) or entry.keys() != {"property_name", "property_status"}:
            raise ValueError("actual LEC cutpoint property is malformed")
        name = entry["property_name"]
        if not isinstance(name, str) or not name.strip() or entry["property_status"] != "proven":
            raise ValueError("actual LEC cutpoint property is malformed or unproven")
        properties.append(name)
    return properties


def memory_evidence(log_text):
    """Retain the existing flow's sampled, rounded aggregate RSS observations."""
    peaks = re.findall(r"^SEC_FLOW: peak memory of this run's Jasper processes: ([0-9.]+) GB$",
                       log_text, re.MULTILINE)
    aborts = re.findall(r"^SEC_FLOW: ABORTED by memory guard: ([0-9.]+) GB RSS > --max-mem-gb ([0-9.]+)$",
                        log_text, re.MULTILINE)
    observed = [float(value) for value in peaks] + [float(value) for value, _ in aborts]
    return {"sampled_peak_rss_gib": max(observed) if observed else None,
            "memory_guard_aborted": bool(aborts),
            "measurement": "Existing flow sums project-process RSS every five seconds in GiB; "
                           "its GB-labelled log rounds to whole GiB. Missing output does not imply zero RSS."}


def proof_archive_members(stream):
    """Validate the whole archive before writing, without following any links.

    Jasper creates relative log/cache aliases inside its project. Their real
    files are already archived separately. Keep those aliases in the inventory
    but never create or dereference them locally, including dangling aliases.
    """
    members, paths, skipped = stream.getmembers(), {}, []
    for member in members:
        name = member.name.rstrip("/")
        path = Path(name)
        if (not name or path.is_absolute() or ".." in path.parts or path.parts[0] != "proof"
                or path.as_posix() != name or "\x00" in name or name in paths
                or member.name.endswith("/") and not member.isdir()):
            raise ValueError("unsafe remote proof archive path: " + repr(member.name))
        paths[name] = member
        if member.issym() or member.islnk():
            link = member.linkname
            if not link or posixpath.isabs(link) or "\x00" in link:
                raise ValueError("unsafe remote proof archive link: " + repr(member.name))
            target = posixpath.normpath(posixpath.join(posixpath.dirname(name), link)
                                       if member.issym() else link)
            if target != "proof" and not target.startswith("proof/"):
                raise ValueError("escaping remote proof archive link: " + repr(member.name))
            skipped.append({"path": name, "type": "symlink" if member.issym() else "hardlink",
                            "linkname": link, "resolved_archive_target": target})
        elif not member.isdir() and not member.isfile():
            raise ValueError("unsafe remote proof archive member type: " + repr(member.name))
        if name == "proof" and not member.isdir():
            raise ValueError("proof archive root must be a directory")
    for name in paths:
        for parent in Path(name).parents:
            if parent.as_posix() in paths and not paths[parent.as_posix()].isdir():
                raise ValueError("non-directory remote proof archive ancestor: " + repr(name))
    return members, skipped


def collect_proof_archive(archive, directory):
    """Collect regular evidence into a fresh proof subtree, without tar modes."""
    directory = Path(directory)
    proof = directory / "proof"
    if proof.exists() or proof.is_symlink():
        raise ValueError("refusing to replace an existing proof evidence subtree")
    with tarfile.open(archive) as stream:
        members, skipped = proof_archive_members(stream)
        proof.mkdir()
        files, directories = 0, 0
        for member in members:
            destination = directory / member.name
            if member.isdir():
                destination.mkdir(parents=True, exist_ok=True)
                directories += 1
            elif member.isfile():
                destination.parent.mkdir(parents=True, exist_ok=True)
                with stream.extractfile(member) as incoming, destination.open("xb") as outgoing:
                    shutil.copyfileobj(incoming, outgoing)
                files += 1
    return {"regular_files": files, "directories": directories, "skipped_links": skipped,
            "policy": "Only canonical proof/ regular files/directories are collected. Internal aliases are inventoried but never created or dereferenced; escaping links, traversal, duplicate/conflicting paths and special files reject before extraction."}


def collect_raw_witnesses(proof):
    """Save timeout diagnostics without confusing engine witnesses with signoff."""
    proof = Path(proof)
    log = proof / "jasper.log"
    if not log.is_file():
        return {"available": False, "acceptance": "none: no raw proof log"}
    pattern = re.compile(r'counterexample \(cex\) with (\d+) cycles was found for the property "([^"]+)" in ([0-9.]+) s')
    witnesses = {}
    with log.open(errors="replace") as stream:
        for line in stream:
            match = pattern.search(line)
            if match:
                cycles, name, seconds = match.groups()
                row = {"property": name, "cycles": int(cycles), "seconds": float(seconds),
                       "kind": "automatic_cutpoint" if name.startswith("AutoProveCutPointsAssertions:") else "engine_witness"}
                if name not in witnesses or row["cycles"] < witnesses[name]["cycles"]:
                    witnesses[name] = row
    inventory = proof / "raw-witness-inventory.json"
    write(inventory, {"scope": "Raw engine diagnostics only; not authoritative signoff or independently classified compiler defects.",
                      "log": record(log), "witnesses": list(witnesses.values())})
    return {"available": True, "inventory": record(inventory), "unique_witnesses": len(witnesses),
            "actual_cutpoints": [row for row in witnesses.values() if row["kind"] == "automatic_cutpoint"],
            "acceptance": "none: complete separate LEC/SEC signoff remains required"}


def signoff(directory, mode, initial_relation=None):
    directory = Path(directory)
    config = json.loads((directory / "config.json").read_text())
    if config.get("strategy") not in (("state_matching",) if mode == "lec" else ("proof", "basic")):
        raise ValueError("wrong Jasper proof strategy")
    if config.get("spec_top") != "F1Shim" or config.get("imp_top") != "F1Shim":
        raise ValueError("proof must cover the full F2 shim (named F1Shim)")
    if config.get("spec_bbox") or config.get("imp_bbox") or config.get("allow_unmatched_ports"):
        raise ValueError("blackboxed or omitted boundary scope cannot establish full proof")
    report = directory / "reports"
    categories, targets = {}, {}
    for line in (report / "signoff_summary.txt").read_text().splitlines():
        values = line.split("\t")
        if len(values) == 3 and values[0] == "category":
            if values[1] in categories:
                raise ValueError("duplicate signoff category")
            categories[values[1]] = values[2]
        elif len(values) == 3 and values[0] == "targets":
            if values[1] in targets or not values[2].isdigit():
                raise ValueError("duplicate or malformed proof target counts")
            targets[values[1]] = int(values[2])
    required_categories = SIGNOFF_CATEGORIES | ({"Auto cutpoints proof"} if mode == "lec" else set())
    if not required_categories <= categories.keys() or not TARGET_STATUSES <= targets.keys():
        raise ValueError("missing authoritative Jasper signoff categories")
    incomplete = {key: value for key, value in categories.items() if value != "Complete"}
    relation_signoff = None
    if incomplete and initial_relation is not None:
        if incomplete != {"X signals and undrivens": "Incomplete", "Overall SEC status": "Incomplete"}:
            raise ValueError(f"non-memory Jasper signoff remains incomplete: {incomplete}")
        relation_signoff = audit_initial_relation(report, initial_relation)
    elif incomplete:
        raise ValueError(f"incomplete Jasper signoff: {incomplete}")
    if targets.get("proven", 0) <= 0 or any(value for key, value in targets.items() if key != "proven"):
        raise ValueError(f"all nonempty obligations must be proven, without bounded/vacuous credit: {targets}")
    names = [line for line in (report / "targets.txt").read_text().splitlines() if line.strip()]
    if not names:
        raise ValueError("no proof targets")
    rows = [line.split("\t") for line in (report / "status.txt").read_text().splitlines()]
    if not rows or any(len(row) != 3 for row in rows):
        raise ValueError("missing or malformed proof status rows")
    if len(set(names)) != len(names):
        raise ValueError("duplicate boundary target names")
    boundary = [row for row in rows if row[2] in names]
    missing = set(names) - {row[2] for row in boundary}
    preconditions = []
    if missing:
        # A conditional output mapping also contributes a cover precondition.
        # It is absent from status.txt (assertions only), so require its actual
        # typed CSV result to be covered; do not count it as a proven assertion
        # or accept unreachable/vacuous qualification.
        with (report / "properties.csv").open(newline="") as stream:
            properties = list(csv.DictReader(stream))
        for name in sorted(missing):
            found = [p for p in properties if p.get("Task", "") + "::" + p.get("Name", "") == name]
            if (not name.endswith("_precondition") or len(found) != 1
                    or found[0].get("Type") != "cover(Sec)" or found[0].get("Result") != "covered"):
                raise ValueError("boundary precondition is missing, unclassified or not covered")
            preconditions.append(name)
    if not boundary or len({row[2] for row in boundary}) != len(boundary) or any(row[0] != "proven" for row in boundary):
        raise ValueError("intended boundary targets are missing or not proven")
    if mode == "lec" and any(len(row) == 3 and row[0] != "proven" for row in rows):
        raise ValueError("LEC internal cutpoint/helper obligations are not all proven")
    result = {"categories": categories, "targets": targets, "target_count": len(names),
              "configuration": config, "covered_preconditions": preconditions,
              "boundary_assertion_count": len(boundary)}
    if relation_signoff is not None:
        result["classified_initial_relation"] = relation_signoff
    if mode == "lec":
        result["combinational_cutpoint_proof"] = lec_cutpoint_signoff(report)
    return result


def choose_strategy(mode, jobs):
    if mode not in ("lec", "sec") or not isinstance(jobs, int) or jobs < 1:
        raise ValueError("invalid Jasper mode/job budget")
    return "state_matching" if mode == "lec" else ("proof" if jobs >= 4 else "basic")


def nonnegative_seconds(value):
    try:
        seconds = int(value)
    except (TypeError, ValueError) as error:
        raise argparse.ArgumentTypeError("wall timeout must be a nonnegative integer") from error
    if seconds < 0:
        raise argparse.ArgumentTypeError("wall timeout must be a nonnegative integer")
    return seconds


def proof_shell(command, remote, license_script, wall_timeout_seconds=DEFAULT_REMOTE_WALL_TIMEOUT_SECONDS):
    """Bound remote proof/report time; an explicit zero disables the deadline."""
    if isinstance(wall_timeout_seconds, bool) or not isinstance(wall_timeout_seconds, int) or wall_timeout_seconds < 0:
        raise ValueError("wall timeout must be a nonnegative integer")
    launch = list(command)
    if wall_timeout_seconds:
        launch = ["timeout", "--signal=TERM", "--kill-after=30s", str(wall_timeout_seconds), *launch]
    return ("source " + shlex.quote(str(license_script)) + " && cd " +
            shlex.quote(str(remote) + "/bundle") + " && " + shlex.join(launch))


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--mode", choices=("lec", "sec"), required=True)
    parser.add_argument("--input-manifest", type=Path,
                        help="explicit hash-bound feature input manifest; never falls back to baseline receipts")
    parser.add_argument("--profile", help="exact PLATFORM_CONFIG required with --input-manifest")
    parser.add_argument("--stage-only", action="store_true",
                        help="validate and stage local proof inputs only; never contacts SSH or starts Jasper")
    parser.add_argument("--flow", type=Path, default=Path("/scratch/jfx/circt_compare/sec_flow"))
    parser.add_argument("--host", default="eda")
    parser.add_argument("--jg", default="/tools/Cadence/jasper_2026.06p002/bin/jg")
    parser.add_argument("--license-script", default="/tools/flexlm/flexlm.sh")
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--max-mem-gb", type=int, default=DEFAULT_MAX_MEM_GIB,
                        help="sampled Jasper process-tree RSS watchdog in GiB (default: 128); not a RAM reservation")
    # Installed Jasper Command Reference 2026.06 pp.2017-2018:
    # set_prove_time_limit 0s explicitly removes the proof/Visualize time limit.
    parser.add_argument("--prove-time", default=DEFAULT_PROVE_TIME,
                        help="Jasper proof time budget (default: 24h); explicit 0s removes it")
    parser.add_argument("--wall-timeout-seconds", type=nonnegative_seconds,
                        default=DEFAULT_REMOTE_WALL_TIMEOUT_SECONDS,
                        help="remote proof/report wall limit (default: 87000 = 24h + 10m); explicit 0 disables it")
    parser.add_argument("--preflight", action="store_true", help="read-only prerequisites; never starts Jasper")
    return parser


def proof_inputs(args, expected_identity=None):
    """Keep default baseline lookup and explicit feature lookup disjoint."""
    if args.input_manifest is not None:
        if not args.profile:
            raise ValueError("--input-manifest requires explicit --profile")
        return load_feature_inputs(args.input_manifest, args.target, args.profile, expected_identity)
    if args.profile:
        raise ValueError("--profile requires --input-manifest")
    return inputs(args.repo, args.target)


def main():
    parser = build_parser()
    args = parser.parse_args()
    if args.input_manifest is not None and not args.profile or args.profile and args.input_manifest is None:
        parser.error("--input-manifest and --profile must be supplied together")
    if args.stage_only and args.preflight:
        parser.error("choose --stage-only or --preflight")
    ssh = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", args.host]
    for required in ("run_sec.py", "tcl/sec_lib.tcl", "scripts/match_state.py"):
        if not (args.flow / required).is_file():
            parser.error(f"missing existing formal flow: {args.flow / required}")
    if not args.stage_only:
        if args.input_manifest is not None:
            # Reject mismatched snapshots even for read-only preflight.
            proof_inputs(args, os.environ.get("CHIA_INPUT_IDENTITY"))
        subprocess.run(ssh + ["test -x " + shlex.quote(args.jg) + " && test -r " + shlex.quote(args.license_script)],
                       check=True, timeout=25)
    if args.preflight:
        print("Jasper executable, license setup and local flow available; no proof run")
        return 0
    jobs = min(args.jobs, int(os.environ.get("CHIA_CORES", "1")))
    if jobs < 1:
        parser.error("Jasper requires at least one available proof job")
    # Installed SEC User Guide pp.53-54: basic proves original sequential
    # targets without auxiliary mappings. The proof strategy requires >=4 jobs
    # (EMTR415 in a retained 2026.06p002 experiment), so never silently request
    # that strategy under the normal one-job memory/license budget.
    strategy = choose_strategy(args.mode, jobs)
    report_path = Path(os.environ["CHIA_REPORT_PATH"]).resolve()
    directory = report_path.parent
    result = {"passed": False, "target": args.target, "mode": args.mode,
              "input_identity": os.environ["CHIA_INPUT_IDENTITY"], "proof_status": "incomplete",
              "scope": "Full F2 F1Shim top, SYNTHESIS; no unmatched ports, blackboxes, matcher-added init mappings "
                       "or spec-assert assumptions. Explicit AXI payload comparison while producer valid; "
                       "valid/ready and live payload including stalled cycles remain strict. Reset and primitive models "
                       "are inventoried in the retained preflight/config reports. Automatic reset-X and undriven "
                       "mappings are disabled; complete X/reset signoff remains required. No production "
                       "initial-memory relation is inferred by name or shape.",
              "proof_contract": {
                  "kind": "combinational_state_matching" if args.mode == "lec" else "sequential_equivalence",
                  "strategy": strategy,
                  "state_assumptions": "LEC cuts corresponding state signals with assume-guarantee obligations; "
                                       "all actual cutpoint properties and full signoff must pass. SEC uses "
                                       "sequential proof with abstraction validation and refinement. "
                                       "Name/shape matcher helpers alone do not establish either proof. "
                                       "At fewer than four jobs, SEC uses documented basic sequential strategy "
                                       "on original boundary targets; proof strategy requires four jobs.",
                  "documentation": "/tools/Cadence/jasper_2026.06p002/doc/jasper_sec_userguide.pdf "
                                   "pp.49,54-55; jasper_command_reference.pdf check_sec pp.562,609,615"}}
    write(report_path, result)
    try:
        design = proof_inputs(args, result["input_identity"])
        if args.input_manifest is not None:
            result["feature_inputs"] = {
                "manifest": record(design["input_manifest"]), "profile": design["profile"],
                "recipe_name": design["recipe_name"], "recipe": design["recipe"],
                "input_design_identity": design["input_design_identity"],
                "input_artifacts": [record(path) for path in design["input_artifacts"]],
                "sfc_artifacts": [record(path) for path in design["sfc"]],
                "circt_artifacts": [record(path) for path in design["circt"]]}
        bundle = directory / "bundle"
        bundle.mkdir()
        shutil.copytree(args.flow, bundle / "sec_flow", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        # GNU timeout cannot reach JG's detached process session. Keep the
        # cleanup helper and launcher identity hook in the hash-bound inventory.
        stage_timeout_cleanup(bundle / "sec_flow")
        if args.mode == "lec":
            stage_lec_reporter(bundle / "sec_flow")
        result["native_memory_correspondence"] = stage_native_memory_correspondence(design, bundle / "sec_flow")
        shutil.copy2(design["sfc_rtl"], bundle / "spec.sv")
        shutil.copy2(design["circt_rtl"], bundle / "imp.sv")
        contract, conditions, contract_path = stage_boundary_contract(
            bundle / "spec.sv", bundle / "imp.sv", bundle / "sec_flow", bundle)
        result["protocol_memory_contract"] = contract
        result["contract_files"] = [record(contract_path), record(conditions)]
        result["input_files"] = [record(design[key]) for key in ("sfc_rtl", "circt_rtl")]
        result["input_files"].append(result["native_memory_correspondence"]["collateral"])
        result["flow_files"] = [record(path) for path in sorted((bundle / "sec_flow").rglob("*")) if path.is_file()]
        if args.stage_only:
            result["proof_status"] = "staged_not_run"
            write(report_path, result)
            print(f"Full-top proof inputs staged at {bundle}; Jasper was not started; equivalence remains unproven")
            return 0
        case_prefix = "feature-" if args.input_manifest is not None else ""
        remote = f"/scratch/jfx/chia-formal/{case_prefix}{uuid.uuid4().hex}-{args.target}-{args.mode}"
        archive = directory / "formal-inputs.tar"
        with tarfile.open(archive, "w") as stream:
            stream.add(bundle, arcname="bundle")
        subprocess.run(ssh + ["mkdir -p " + shlex.quote(remote)], check=True, timeout=25)
        with archive.open("rb") as incoming:
            subprocess.run(ssh + ["tar -xf - -C " + shlex.quote(remote)], stdin=incoming, check=True, timeout=180)
        command = ["python3", "sec_flow/run_sec.py", "--spec", "spec.sv", "--imp", "imp.sv",
                   "--top", "F1Shim", "--jg", args.jg, "--strategy",
                   strategy, "--out", "proof",
                   "--jobs", str(jobs),
                   "--max-mem-gb", str(args.max_mem_gb), "--prove-time", args.prove_time,
                   "--no-init-map", "--no-valid-qualify", "--output-conditions",
                   "protocol-output-conditions.txt", "--no-assume-spec-asserts"]
        result.update(remote_directory=remote, proof_argv=command)
        resource_probe = (
            "import json, os, time; "
            "m = {line.split(':')[0]: int(line.split()[1]) * 1024 "
            "for line in open('/proc/meminfo')}; "
            "print(json.dumps({'observed_unix_seconds': time.time(), 'physical_memory_bytes': m['MemTotal'], "
            "'available_memory_bytes': m['MemAvailable'], 'swap_total_bytes': m['SwapTotal'], "
            "'swap_free_bytes': m['SwapFree'], 'logical_cpus': os.cpu_count()}))")
        inventory = subprocess.run(ssh + [shlex.join(["python3", "-c", resource_probe])],
                                   check=True, capture_output=True, text=True, timeout=25)
        result["remote_resources_before_proof"] = json.loads(inventory.stdout)
        result["resource_policy"] = {
            "proof_time_limit": args.prove_time,
            "remote_wall_timeout_seconds": args.wall_timeout_seconds,
            "time_limit_policy": "Defaults: 24h proof and 87000s remote wall time, including 10m report grace. "
                                 "Explicit 0s/0 disables the respective limit; the RSS watchdog is unchanged.",
            "max_process_tree_rss_gib": args.max_mem_gb,
            "proofgrid_jobs": int(command[command.index("--jobs") + 1]),
            "watchdog_exceeds_physical_memory": args.max_mem_gb * 2**30 >
                                                result["remote_resources_before_proof"]["physical_memory_bytes"],
            "strategy": strategy,
            "strategy_policy": "state_matching for LEC; original-target sequential basic below four jobs, "
                               "sequential proof at four or more jobs (SEC User Guide pp.53-54; EMTR415).",
            "watchdog": "Five-second sampled process-tree RSS watchdog, not a kernel allocation limit; "
                        "raising it does not provision RAM or guarantee capacity. "
                        "ProofGrid jobs do not limit OS threads; memory aborts remain incomplete."}
        write(report_path, result)
        # The proof gets 24h by default, with 10m remote report grace before
        # its wall-clock kill timer. Explicit zero retains unlimited overrides.
        shell = proof_shell(command, remote, args.license_script, args.wall_timeout_seconds)
        with (directory / "ssh-jasper.log").open("w") as log:
            completed = subprocess.run(ssh + ["bash -lc " + shlex.quote(shell)], stdout=log, stderr=subprocess.STDOUT)
        result["memory_usage"] = memory_evidence((directory / "ssh-jasper.log").read_text(errors="replace"))
        result["launcher_returncode"] = completed.returncode
        write(report_path, result)
        if completed.returncode in (124, 137):
            cleanup = ["python3", "sec_flow/jasper_timeout_cleanup.py", "--project",
                       remote + "/bundle/proof/jgproject"]
            cleanup_shell = "cd " + shlex.quote(remote + "/bundle") + " && " + shlex.join(cleanup)
            try:
                stopped = subprocess.run(ssh + [cleanup_shell], capture_output=True, text=True, timeout=60)
                result["timeout_cleanup"] = {"returncode": stopped.returncode,
                                             "stdout": stopped.stdout, "stderr": stopped.stderr}
                if stopped.returncode == 0:
                    result["timeout_cleanup"]["result"] = json.loads(stopped.stdout)
            except Exception as error:
                # Preserve the cleanup failure and still fetch the available
                # diagnostics; a timeout can never become passed verification.
                result["timeout_cleanup"] = {"error": f"{type(error).__name__}: {error}"}
            write(report_path, result)
        # Reports are fetched even when a setup error or proof failure occurs.
        proof_archive = directory / "formal-results.tar"
        with proof_archive.open("wb") as outgoing:
            subprocess.run(ssh + ["tar -cf - -C " + shlex.quote(remote + "/bundle") + " proof"],
                           stdout=outgoing, check=True, timeout=180)
        result["proof_collection"] = collect_proof_archive(proof_archive, directory)
        result["raw_witness_diagnostics"] = collect_raw_witnesses(directory / "proof")
        launcher_result = directory / "proof/RESULT.txt"
        if launcher_result.is_file():
            result["launcher_result"] = launcher_result.read_text()[:8192]
        write(report_path, result)
        actual_config = json.loads((directory / "proof/config.json").read_text())
        result["protocol_contract_validation"] = validate_configuration(actual_config, contract)
        # A hard timeout frequently leaves no summary at all. Preserve its
        # actual cause and raw witnesses instead of hiding it behind a missing
        # signoff_summary.txt exception. Existing summaries are still retained.
        if completed.returncode in (124, 137):
            raise ValueError(f"Jasper launcher exited {completed.returncode}; proof timed out or was killed; signoff incomplete")
        result["signoff"] = signoff(directory / "proof", args.mode)
        if completed.returncode:
            raise ValueError(f"Jasper launcher exited {completed.returncode}")
        result.update(passed=True, proof_status="proven", unproven_obligations=0)
    except Exception as error:
        result["error"] = f"{type(error).__name__}: {error}"
    write(report_path, result)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
