#!/usr/bin/env python3
"""Read-only, fail-closed acceptance for the declared full compiler scope.

Scope YAML is immutable input: schema_version: 1; features exactly fame5,
autocounter_print, multiclock_models, nic. Each requires required: true,
nonempty capabilities and representative_cases string lists, and evidence: [].
Optional supporting_cases strings are guidance, never acceptance substitutes.

Receipts are discovered separately at
  <feature_evidence_root>/<current input identity>/<feature>/*.json
(default root automation/chia_firesim_circt/logs/feature-evidence).
Each case manifest has exactly schema_version:1, case_id, enabled_features,
capabilities, artifacts, stages. artifacts contains {role: config|rtl|golden_rtl,
path,sha256}; all three roles are required, binding enabled configuration and
both CIRCT and SFC outputs. stages maps semantic/jasper_lec/jasper_sec/
firesim to {path,sha256} stage-report bindings. Paths are repo-relative or
absolute. Every binding is checked against existing bytes and retained in the
features result; no evidence/source file is modified and no command is run.

All stage reports require passed:true, case_id and target equal to case_id,
input_identity equal to the current snapshot, enabled_features, the exact same
artifacts inventory, nonempty scope, and command:{path,sha256}. A command
receipt requires argv:[...], returncode:0, timed_out:false, stdout/stderr file
bindings (empty logs allowed). semantic additionally uses the existing
prebuild syntax/static-semantic contract; each paired RTL/driver syntax entry
requires its own command binding. Formal stages require mode:lec|sec,
proof_status:proven, unproven_obligations:0, proof_directory, and proof_files
bindings for config.json and reports/{signoff_summary.txt,targets.txt,status.txt}
(plus lec_auto_cutpoints.json for LEC). The existing authoritative Jasper
signoff parser validates the actual inventories; checkboxes alone do not pass.

firesim requires the existing local-build/manager/F2/Linux boot-poweroff result
flags, workload_kind:linux_boot_poweroff, nonempty agfi, and receipts mapping
build_log,manager_log,workload_evidence,workload_inputs,frozen_inputs,build_config,
hwdb to file bindings. UART is parsed by the existing Linux validator. Frozen
candidate_rtl must match a declared RTL artifact. workload_inputs contains
bindings bootbinary,rootfs,runtime,workload_json,linux_preparation,template;
local build/F2 runtime YAMLs are inspected. F2 feature_checks must bind nonempty
checker JSONs, each with passed:true, case_id, input_identity, capabilities,
command binding, and observations:[file bindings]. Their union covers every
claimed case capability; an idle baseline Linux boot cannot supply coverage. Evidence may not substitute a
prebuilt tutorial image, a metasimulator, or a self-reported workload pass.

Registry changes invalidate the semantic identity. Case/stage evidence is
outside the hashed registry to avoid self-referential report hashes. Missing
cases/capabilities/evidence block full-scope completion, not the prebuild gate.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import runpy
from typing import Any

import yaml

# Ray workers survive implementation turns. Read the current validation code
# by path instead of retaining imported helpers from an earlier source revision.
_HERE = Path(__file__).resolve().parent
_nodes = runpy.run_path(str(_HERE / "verification_nodes.py"))
(_digest, _path, _snapshot, _validate_prebuild_result,
 _validate_semantic_dependency, _write, VerificationBlocked) = (
    _nodes[name] for name in ("_digest", "_path", "_snapshot", "_validate_prebuild_result",
                             "_validate_semantic_dependency", "_write", "VerificationBlocked"))

FEATURES = ("fame5", "autocounter_print", "multiclock_models", "nic")
STAGES = ("semantic", "jasper_lec", "jasper_sec", "firesim")


def _object(value: Any, label: str) -> dict:
    if not isinstance(value, dict):
        raise VerificationBlocked(f"{label} must be an object")
    return value


def _strings(value: Any, label: str, nonempty: bool = True) -> list[str]:
    if (not isinstance(value, list) or (nonempty and not value)
            or any(not isinstance(v, str) or not v.strip() for v in value)
            or len(set(value)) != len(value)):
        raise VerificationBlocked(f"{label} must contain unique nonempty strings")
    return value


def _read(path: Path) -> dict:
    try:
        return _object(json.loads(path.read_text()), str(path))
    except (OSError, ValueError) as error:
        raise VerificationBlocked(f"Cannot read JSON evidence {path}: {error}") from error


class Evidence:
    def __init__(self, repo: Path):
        self.repo = repo
        self.files: dict[str, str] = {}

    def bind(self, value: Any, label: str, nonempty: bool = True) -> Path:
        value = _object(value, label)
        if (not isinstance(value.get("path"), str) or not value["path"].strip()
                or not isinstance(value.get("sha256"), str)
                or re.fullmatch(r"[0-9a-f]{64}", value["sha256"]) is None):
            raise VerificationBlocked(f"{label} requires path and SHA256 binding")
        path = _path(self.repo, value["path"]).resolve()
        actual = _digest(path, nonempty=nonempty)
        if actual != value["sha256"]:
            raise VerificationBlocked(f"{label} hash mismatch: {path}")
        if str(path) in self.files and self.files[str(path)] != actual:
            raise VerificationBlocked(f"Evidence changed during reading: {path}")
        self.files[str(path)] = actual
        return path

    def command(self, binding: dict, label: str) -> None:
        command = _read(self.bind(binding, label))
        argv = command.get("argv")
        if (not isinstance(argv, list) or not argv or any(not isinstance(v, str) or not v for v in argv)
                or type(command.get("returncode")) is not int or command["returncode"] != 0
                or command.get("timed_out") is not False):
            raise VerificationBlocked(f"{label}: missing successful command receipt")
        for stream in ("stdout", "stderr"):
            self.bind(command.get(stream), label + "." + stream, nonempty=False)

    def unchanged(self) -> None:
        for filename, expected in self.files.items():
            if _digest(Path(filename)) != expected:
                raise VerificationBlocked(f"Evidence changed during validation: {filename}")


def _artifacts(value: Any, evidence: Evidence, label: str) -> set[tuple[str, str, str]]:
    if not isinstance(value, list) or not value:
        raise VerificationBlocked(f"{label}: config, CIRCT and golden SFC RTL bindings are required")
    result = set()
    for item in value:
        item = _object(item, label)
        if set(item) != {"role", "path", "sha256"} or item["role"] not in ("config", "rtl", "golden_rtl"):
            raise VerificationBlocked(f"{label}: unsupported artifact role/schema")
        path = evidence.bind(item, label)
        entry = (item["role"], str(path), item["sha256"])
        if entry in result:
            raise VerificationBlocked(f"{label}: duplicate artifact")
        result.add(entry)
    if {entry[0] for entry in result} != {"config", "rtl", "golden_rtl"}:
        raise VerificationBlocked(f"{label}: config, CIRCT and golden SFC RTL artifacts are mandatory")
    return result


def _proof(report: dict, mode: str, evidence: Evidence) -> None:
    if report.get("mode") != mode or report.get("proof_status") != "proven" or type(report.get("unproven_obligations")) is not int or report["unproven_obligations"] != 0:
        raise VerificationBlocked(f"{mode}: actual nonempty complete proof is required")
    directory = report.get("proof_directory")
    if not isinstance(directory, str) or not directory:
        raise VerificationBlocked(f"{mode}: proof_directory is required")
    directory = _path(evidence.repo, directory).resolve()
    inventory = report.get("proof_files")
    if not isinstance(inventory, list):
        raise VerificationBlocked(f"{mode}: bound proof_files inventory required")
    bound = {evidence.bind(item, mode + " proof file") for item in inventory}
    required = {directory / "config.json", *(directory / "reports" / name for name in
                  ("signoff_summary.txt", "targets.txt", "status.txt"))}
    if mode == "lec":
        required.add(directory / "reports/lec_auto_cutpoints.json")
    if bound != required:
        raise VerificationBlocked(f"{mode}: authoritative proof inventory is missing or unsupported")
    signoff = runpy.run_path(str(_HERE / "run_jasper.py"))["signoff"]
    try:
        signoff(directory, mode)
    except (OSError, ValueError, KeyError) as error:
        raise VerificationBlocked(f"{mode}: Jasper signoff rejected: {error}") from error


def _fpga(report: dict, evidence: Evidence, artifacts: set, coverage: list[str]) -> None:
    flags = ("local_bitstream_build", "firesim_manager", "f2_runfarm", "workload_passed", "linux_booted", "poweroff_observed")
    if (any(report.get(key) is not True for key in flags)
            or report.get("workload_kind") != "linux_boot_poweroff"
            or not isinstance(report.get("agfi"), str) or not report["agfi"].startswith("agfi-")):
        raise VerificationBlocked("FireSim requires local build, manager, F2 and Linux boot/poweroff")
    receipts = _object(report.get("receipts"), "FireSim receipts")
    keys = {"build_log", "manager_log", "workload_evidence", "workload_inputs", "frozen_inputs", "build_config", "hwdb"}
    if set(receipts) != keys:
        raise VerificationBlocked("FireSim requires all build/run/input receipt bindings")
    files = {key: evidence.bind(value, "FireSim " + key) for key, value in receipts.items()}
    validate_uart = runpy.run_path(str(_HERE / "run_f2.py"))["validate_uart"]
    try:
        validate_uart(files["workload_evidence"].read_text(errors="replace"))
    except ValueError as error:
        raise VerificationBlocked(str(error)) from error
    frozen = _read(files["frozen_inputs"])
    rtl = evidence.bind(frozen.get("candidate_rtl"), "frozen candidate RTL")
    if ("rtl", str(rtl), frozen["candidate_rtl"]["sha256"]) not in artifacts:
        raise VerificationBlocked("F2 frozen candidate does not match the feature RTL")
    copied = frozen.get("files")
    if not isinstance(copied, list) or not copied:
        raise VerificationBlocked("F2 frozen input inventory is missing")
    for item in copied:
        evidence.bind(item, "frozen build input")
    workload = _read(files["workload_inputs"])
    for key in ("bootbinary", "rootfs", "runtime", "workload_json", "linux_preparation", "template"):
        evidence.bind(workload.get(key), "Linux workload " + key)
    runtime = yaml.safe_load(evidence.bind(workload["runtime"], "F2 runtime").read_text())
    build = yaml.safe_load(files["build_config"].read_text())
    hosts = build.get("build_farm", {}).get("recipe_arg_overrides", {}).get("build_farm_hosts")
    if hosts != ["localhost"]:
        raise VerificationBlocked("Bitstream build must use the local build host")
    run_hosts = runtime.get("run_farm", {}).get("recipe_arg_overrides", {}).get("run_farm_hosts_to_use")
    if not isinstance(run_hosts, list) or not run_hosts or any(not isinstance(h, dict) or len(h) != 1
            or any(not key.startswith("f2.") or type(count) is not int or count < 1 for key, count in h.items()) for h in run_hosts):
        raise VerificationBlocked("Runtime must select real AWS F2 hosts")
    if runtime.get("metasimulation", {}).get("metasimulation_enabled") is not False:
        raise VerificationBlocked("Feature FPGA evidence must disable metasimulation")
    checks = report.get("feature_checks")
    if not isinstance(checks, list) or not checks:
        raise VerificationBlocked("Enabled feature runtime requires bound observation/checker evidence")
    checked = set()
    for binding in checks:
        check = _read(evidence.bind(binding, "feature runtime checker"))
        if (check.get("passed") is not True or check.get("case_id") != report["case_id"]
                or check.get("input_identity") != report["input_identity"]):
            raise VerificationBlocked("Feature runtime checker is failed, stale or misbound")
        covered = _strings(check.get("capabilities"), "runtime checker capabilities")
        if set(covered) - set(coverage):
            raise VerificationBlocked("Feature runtime checker claims undeclared capability")
        evidence.command(check.get("command"), "feature runtime checker command")
        observations = check.get("observations")
        if not isinstance(observations, list) or not observations:
            raise VerificationBlocked("Feature runtime checker must retain actual observations")
        for observation in observations:
            evidence.bind(observation, "feature runtime observation")
        checked.update(covered)
    if checked != set(coverage):
        raise VerificationBlocked("Runtime checker evidence does not cover the case capabilities")
    hwdb = yaml.safe_load(files["hwdb"].read_text())
    if not isinstance(hwdb, dict) or not any(isinstance(value, dict) and value.get("agfi") == report["agfi"] for value in hwdb.values()):
        raise VerificationBlocked("Feature AGFI is absent from the bound hardware database")


def _case(path: Path, feature: str, identity: str, declared: dict, evidence: Evidence) -> dict:
    manifest_binding = {"path": str(path), "sha256": _digest(path, nonempty=True)}
    evidence.bind(manifest_binding, "case manifest")
    case = _read(path)
    if set(case) != {"schema_version", "case_id", "enabled_features", "capabilities", "artifacts", "stages"} or case["schema_version"] != 1:
        raise VerificationBlocked(f"Unsupported case schema: {path}")
    case_id = case["case_id"]
    if not isinstance(case_id, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", case_id) is None:
        raise VerificationBlocked("case_id must be a stable nonempty identifier")
    if case_id not in declared["representative_cases"]:
        raise VerificationBlocked(f"Undeclared representative case: {case_id}")
    enabled = _strings(case["enabled_features"], "enabled_features")
    if feature not in enabled or set(enabled) - set(FEATURES):
        raise VerificationBlocked(f"{case_id} does not enable a known {feature} feature")
    coverage = _strings(case["capabilities"], "case capabilities")
    if set(coverage) - set(declared["capabilities"]):
        raise VerificationBlocked(f"{case_id} claims unknown capabilities")
    artifacts = _artifacts(case["artifacts"], evidence, case_id)
    stages = _object(case["stages"], "stages")
    if set(stages) != set(STAGES):
        raise VerificationBlocked(f"{case_id}: semantic, LEC, SEC and F2 stages are mandatory")
    for name in STAGES:
        report = _read(evidence.bind(stages[name], name + " report"))
        if (report.get("passed") is not True or report.get("case_id") != case_id or report.get("target") != case_id
                or report.get("input_identity") != identity or report.get("enabled_features") != enabled
                or not isinstance(report.get("scope"), str) or not report["scope"].strip()):
            raise VerificationBlocked(f"{case_id}: {name} report is failed, stale or describes different enabled inputs")
        if _artifacts(report.get("artifacts"), evidence, name) != artifacts:
            raise VerificationBlocked(f"{case_id}: {name} checked different config/RTL artifacts")
        evidence.command(report.get("command"), name + " command")
        if name == "semantic":
            try:
                _validate_prebuild_result(report, case_id, identity)
            except RuntimeError as error:
                raise VerificationBlocked(str(error)) from error
            for side in ("sfc", "circt"):
                for kind in ("rtl", "driver"):
                    check = report["details"]["paired_syntax"]["pairs"][side][kind]
                    evidence.command(check.get("command"), side + " " + kind)
        elif name.startswith("jasper_"):
            _proof(report, name.removeprefix("jasper_"), evidence)
        else:
            _fpga(report, evidence, artifacts, coverage)
    return {"case_id": case_id, "capabilities": coverage, "manifest": manifest_binding, "ok": True}


def run_features(config: dict, evidence_dir: Path, repo: Path, semantic: dict) -> dict:
    repo = Path(repo).resolve()
    output = Path(evidence_dir).resolve() / "features/result.json"
    report = {"node": "features", "status": "blocked", "ok": False, "identity": None,
              "details": [], "evidence": str(output)}
    evidence = Evidence(repo)
    try:
        if not isinstance(semantic, dict) or semantic.get("node") != "semantic" or semantic.get("ok") is not True:
            raise VerificationBlocked("Passing four-target prebuild semantics is required")
        identity, snapshot = _snapshot(config, repo)
        report["identity"] = identity
        if semantic.get("identity") != identity:
            raise VerificationBlocked("Feature evidence requires the current semantic input identity")
        _validate_semantic_dependency(semantic, identity)
        filename = config.get("feature_scope_file")
        if not isinstance(filename, str) or not filename:
            raise VerificationBlocked("feature_scope_file is required")
        scope = _path(repo, filename).resolve()
        digest = _digest(scope, nonempty=True)
        if snapshot.get("inputs", {}).get(str(scope)) != digest:
            raise VerificationBlocked("feature_scope_file must be included in identity_files")
        evidence.bind({"path": str(scope), "sha256": digest}, "feature registry")
        registry = _object(yaml.safe_load(scope.read_text()), "feature registry")
        if set(registry) != {"schema_version", "features"} or registry["schema_version"] != 1:
            raise VerificationBlocked("Unsupported feature registry schema")
        features = _object(registry["features"], "features")
        if set(features) != set(FEATURES):
            raise VerificationBlocked("All four declared compiler feature families are mandatory")
        root = _path(repo, config.get("feature_evidence_root", "automation/chia_firesim_circt/logs/feature-evidence")) / identity
        for feature in FEATURES:
            detail = {"feature": feature, "status": "blocked", "ok": False, "cases": []}
            try:
                declared = _object(features[feature], feature)
                if set(declared) - {"required", "capabilities", "representative_cases", "supporting_cases", "evidence"} or declared.get("required") is not True:
                    raise VerificationBlocked(f"{feature}: unknown scope fields or required feature disabled")
                _strings(declared.get("capabilities"), feature + " capabilities")
                _strings(declared.get("representative_cases"), feature + " representative_cases")
                if "supporting_cases" in declared:
                    _strings(declared["supporting_cases"], feature + " supporting_cases", nonempty=False)
                detail["pending_capabilities"] = list(declared["capabilities"])
                detail["pending_cases"] = list(declared["representative_cases"])
                if declared.get("evidence", []) != []:
                    raise VerificationBlocked("Keep scope immutable; put case receipts under feature_evidence_root")
                coverage, cases = set(), set()
                for path in sorted((root / feature).glob("*.json")):
                    case = _case(path, feature, identity, declared, evidence)
                    if case["case_id"] in cases:
                        raise VerificationBlocked(f"Duplicate case evidence: {case['case_id']}")
                    detail["cases"].append(case)
                    cases.add(case["case_id"])
                    coverage.update(case["capabilities"])
                detail["pending_capabilities"] = sorted(set(declared["capabilities"]) - coverage)
                detail["pending_cases"] = sorted(set(declared["representative_cases"]) - cases)
                if detail["pending_capabilities"] or detail["pending_cases"]:
                    raise VerificationBlocked(f"{feature}: missing enabled-case evidence for capabilities {detail['pending_capabilities']} and cases {detail['pending_cases']}")
                detail.update(status="passed", ok=True)
            except (VerificationBlocked, OSError, ValueError, KeyError, TypeError) as error:
                detail["reason"] = str(error)
            report["details"].append(detail)
        evidence.unchanged()
        if _snapshot(config, repo)[0] != identity:
            raise VerificationBlocked("Compiler inputs changed while checking feature evidence")
        if all(detail["ok"] for detail in report["details"]):
            report.update(status="passed", ok=True)
        else:
            report["reason"] = "; ".join(detail["reason"] for detail in report["details"] if not detail["ok"])
    except (VerificationBlocked, OSError, ValueError, KeyError, TypeError) as error:
        report["reason"] = str(error)
    except Exception as error:
        report.update(status="failed", reason=f"{type(error).__name__}: {error}")
    report["file_sha256"] = evidence.files
    _write(output, report)
    return report
