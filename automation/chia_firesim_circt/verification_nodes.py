#!/usr/bin/env python3
"""Fail-closed verification adapters, deliberately independent of Ray/Chia.

An adapter receives CHIA_REPORT_PATH and CHIA_INPUT_IDENTITY (also available as
argv placeholders). A zero exit is necessary but insufficient: adapters must
write a fresh JSON result identifying the exact target and input identity.
See verification.yaml for the contract. No proof or semantic check is inferred
from exit status, text in a log, or an implementation agent's completion claim.
"""
from __future__ import annotations

import hashlib
import importlib.util
import copy
import json
import math
import os
from pathlib import Path
import signal
import statistics
import subprocess
import time
from typing import Any

import yaml

TARGETS = ("rocket", "megaboomv3", "shuttle", "saturn_shuttle")
FIXED_TARGETS = {
    "rocket": "firesim_rocket_singlecore_no_nic_l2_lbp",
    "megaboomv3": "firesim_megaboom_singlecore_no_nic_l2_llc4mb_ddr3",
    "shuttle": "firesim_shuttle_singlecore_no_nic_l2_llc4mb_ddr3",
    "saturn_shuttle": "firesim_saturn_GENV256D128_shuttle_singlecore_no_nic_l2_llc4mb_ddr3",
}


class VerificationBlocked(ValueError):
    """Required input or adapter is not configured."""


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def _path(repo: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else repo / path


def _digest(path: Path, nonempty: bool = False) -> str:
    if not path.is_file():
        raise VerificationBlocked(f"Required artifact is missing or not a file: {path}")
    if nonempty and path.stat().st_size == 0:
        raise VerificationBlocked(f"Required artifact/evidence is empty: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sfc_preparation(config: dict) -> dict | None:
    preparation = config.get("semantic", {}).get("preparation")
    if preparation is not None and (not isinstance(preparation, dict) or not isinstance(preparation.get("golden_root"), str)
                                    or not preparation["golden_root"].strip()):
        raise VerificationBlocked("semantic.preparation requires golden_root and a real generation command")
    return preparation


def _sfc_receipt(config: dict, repo: Path, target: str) -> tuple[list[str], set[str]] | None:
    """Verify a complete generator receipt; an unmarked attempt is never input."""
    preparation = _sfc_preparation(config)
    root = _path(repo, preparation["golden_root"]).resolve()
    marker = root / target / "complete.json"
    if not marker.exists():
        return None
    def read(path: Path) -> dict:
        try:
            value = json.loads(path.read_text())
        except (OSError, ValueError) as error:
            raise VerificationBlocked(f"Invalid immutable SFC receipt: {path}: {error}") from error
        if not isinstance(value, dict):
            raise VerificationBlocked(f"Invalid immutable SFC receipt object: {path}")
        return value
    def verify(path: Path, record: dict, nonempty: bool = False) -> str:
        if (not isinstance(record, dict) or not isinstance(record.get("sha256"), str)
                or isinstance(record.get("bytes"), bool) or not isinstance(record.get("bytes"), int)):
            raise VerificationBlocked(f"Malformed immutable SFC file metadata: {path}")
        if record["sha256"] != _digest(path, nonempty=nonempty) or record["bytes"] != path.stat().st_size:
            raise VerificationBlocked(f"Immutable SFC artifact hash/size mismatch: {path}")
        return str(path)
    def record_path(record: dict) -> Path:
        if not isinstance(record, dict) or not isinstance(record.get("path"), str) or not record["path"].strip():
            raise VerificationBlocked(f"Malformed immutable SFC path metadata: {target}")
        return _path(repo, record["path"]).resolve()
    completion = read(marker)
    if completion.get("status") != "complete" or completion.get("target") != target:
        raise VerificationBlocked(f"Incomplete/wrong-target SFC marker: {marker}")
    binding = completion.get("manifest", {})
    manifest_path = record_path(binding)
    if not manifest_path.is_relative_to(root / target) or manifest_path.name != "manifest.json":
        raise VerificationBlocked(f"SFC manifest is outside the selected target archive: {manifest_path}")
    verify(manifest_path, binding, nonempty=True)
    manifest = read(manifest_path)
    recipes = yaml.safe_load(_path(repo, config["recipes_file"]).read_text())
    if (manifest.get("status") != "complete" or manifest.get("target") != target
            or manifest.get("recipe_name") != FIXED_TARGETS[target]
            or manifest.get("recipe") != recipes[FIXED_TARGETS[target]]):
        raise VerificationBlocked(f"SFC receipt does not match the exact selected recipe: {target}")
    attempt = manifest_path.parent
    paths, allowed_empty = [str(marker), str(manifest_path)], set()
    required = {
        "artifacts": {"firechip.chip.FireSim." + manifest["recipe"]["TARGET_CONFIG"] + suffix
                      for suffix in (".sfc.fir", ".anno.json")},
        "sfc_artifacts": {"FireSim-generated" + suffix for suffix in
                          (".sv", ".const.h", ".const.vh", ".defines.vh", ".synthesis.xdc", ".implementation.xdc",
                           ".partition.const.h", ".peer2peer.const.yaml")}
                         | {"post-bridge-extraction.json", "post-fame5-transform.json", "post-sim-mapping.fir"},
        "resource_artifacts": {f"bootrom-{index}.img" for index in range(3)},
    }
    for kind, directory in [("artifacts", "inputs"), ("sfc_artifacts", "sfc"), ("resource_artifacts", "preMIDAS-resources")]:
        inventory = manifest.get(kind)
        if (not isinstance(inventory, dict) or not required[kind] <= inventory.keys()
                or not all(isinstance(name, str) and name for name in inventory)):
            raise VerificationBlocked(f"Incomplete SFC {kind} inventory for {target}")
        for name, record in inventory.items():
            path = (attempt / directory / name).resolve()
            if not path.is_relative_to(attempt / directory):
                raise VerificationBlocked(f"SFC inventory escapes archive: {name}")
            # Scala permits this exact empty sidecar when no peer routes exist.
            empty_ok = kind == "sfc_artifacts" and name == "FireSim-generated.peer2peer.const.yaml"
            verify(path, record, nonempty=not empty_ok)
            paths.append(str(path))
            if path.stat().st_size == 0:
                allowed_empty.add(str(path))
    pre = manifest.get("preMIDAS", {})
    if not isinstance(pre, dict):
        raise VerificationBlocked(f"Malformed SFC pre-MIDAS metadata: {target}")
    for kind in ("firrtl", "annotations"):
        record = pre.get(kind, {})
        path = record_path(record)
        if str(path) not in paths:
            raise VerificationBlocked(f"SFC {kind} is not in the archived input inventory: {target}")
        verify(path, record, nonempty=True)
    provenance = manifest.get("provenance", {})
    jars = provenance.get("execution_jars", {}) if isinstance(provenance, dict) else {}
    if not isinstance(jars, dict) or set(jars) != {"firechip.jar", "firesim-main.jar"}:
        raise VerificationBlocked(f"SFC receipt lacks its archived execution jars: {target}")
    for record in jars.values():
        path = record_path(record)
        verify(path, record, nonempty=True)
        paths.append(str(path))
    # A filesystem annotation still names its original path. Bind the bytes a
    # candidate would consume as well as the archived copy. Inline/classpath
    # resources are already in the annotation and pinned jar inventories.
    def resource_mappings(entries, label: str, require_source: bool):
        if not isinstance(entries, list):
            raise VerificationBlocked(f"Malformed SFC {label} mappings: {target}")
        for resource in entries:
            if not isinstance(resource, dict):
                raise VerificationBlocked(f"Malformed SFC {label} mapping: {target}")
            archived = resource.get("archived", {})
            archived_path = record_path(archived)
            if str(archived_path) not in paths:
                raise VerificationBlocked(f"SFC {label} mapping is absent from its archived inventory: {archived_path}")
            verify(archived_path, archived, nonempty=True)
            if require_source or "source" in resource:
                source = resource.get("source", {})
                source_path = record_path(source)
                verify(source_path, source, nonempty=True)
                if source["sha256"] != archived["sha256"]:
                    raise VerificationBlocked(f"SFC {label} archived/source resource mismatch: {source_path}")
                paths.append(str(source_path))
    resource_mappings(pre.get("annotation_resources"), "annotation resources", False)
    bootroms = pre.get("bootrom_resources")
    if not isinstance(bootroms, list) or len(bootroms) != 3:
        raise VerificationBlocked(f"SFC receipt must retain the three boot ROM mappings: {target}")
    resource_mappings(bootroms, "boot ROM resources", True)
    resource_mappings(manifest.get("sfc_resources"), "SFC resources", True)
    return sorted(set(paths)), allowed_empty


def _prepare_sfc(config: dict, repo: Path, directory: Path, record: dict) -> None:
    preparation = _sfc_preparation(config)
    if preparation is None:
        return
    if set(config.get("targets", {})) != set(TARGETS) or any(
            config["targets"][target].get("recipe") != FIXED_TARGETS[target] for target in TARGETS):
        raise VerificationBlocked("SFC preparation requires exactly the four fixed target recipes")
    record.update(status="checking", evidence=str(directory))
    missing = [target for target in TARGETS if _sfc_receipt(config, repo, target) is None]
    record.update(existing_complete=[target for target in TARGETS if target not in missing], requested_targets=missing)
    _write(directory / "result.json", record)
    if missing:
        command = copy.deepcopy(preparation.get("command"))
        if not isinstance(command, dict) or not isinstance(command.get("argv"), list):
            raise VerificationBlocked("Missing real SFC preparation command")
        # Append fixed target arguments as argv elements, never shell text.
        command["argv"] += [arg for target in missing for arg in ("--target", target)]
        context = {"target": "fixed_four_f2", "mode": "prepare_sfc", "identity": "not-yet-computed",
                   "golden_root": str(_path(repo, preparation["golden_root"]).resolve())}
        try:
            record.update(_command(command, context, config, repo, directory / "generation", require_result=False))
        except Exception as error:
            record.update(status="failed", reason=str(error), command_evidence=str(directory / "generation/command.json"))
            _write(directory / "result.json", record)
            raise
    absent = [target for target in TARGETS if _sfc_receipt(config, repo, target) is None]
    if absent:
        raise VerificationBlocked("SFC preparation produced no complete receipts for: " + ", ".join(absent))
    record.update(status="complete", complete_targets=list(TARGETS), verification_claim="none: SFC artifact preparation only")
    _write(directory / "result.json", record)


def _candidate_preparation(config: dict) -> dict | None:
    preparation = config.get("semantic", {}).get("candidate_preparation")
    if preparation is not None and (not isinstance(preparation, dict) or any(
            not isinstance(preparation.get(key), str) or not preparation[key].strip()
            for key in ("candidate_root", "compiler"))):
        raise VerificationBlocked("semantic.candidate_preparation requires candidate_root and compiler")
    if preparation is not None and _sfc_preparation(config) is None:
        raise VerificationBlocked("CIRCT candidate preparation requires validated immutable SFC preparation")
    return preparation


def _circt_receipt(config: dict, repo: Path, target: str) -> tuple[list[str], set[str]] | None:
    """Load only current, complete, hash-verified native compiler output."""
    preparation = _candidate_preparation(config)
    helper = repo / "sims/firesim/sim/goldengate-circt/test/Support/GenerateF2CIRCTCandidates.py"
    if not helper.is_file():
        raise VerificationBlocked(f"Missing native candidate generator/validator: {helper}")
    spec = importlib.util.spec_from_file_location("chia_circt_candidates", helper)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        receipt = module.validate_receipt(
            repo, _path(repo, _sfc_preparation(config)["golden_root"]),
            _path(repo, preparation["candidate_root"]), _path(repo, preparation["compiler"]),
            target, _cores(config))
    except (OSError, ValueError) as error:
        raise VerificationBlocked(f"Invalid CIRCT candidate receipt for {target}: {error}") from error
    if receipt is None:
        return None
    return receipt["circt_artifacts"], set(receipt["allowed_empty"])


def _prepare_circt(config: dict, repo: Path, directory: Path, record: dict) -> None:
    preparation = _candidate_preparation(config)
    if preparation is None:
        return
    # SFC preparation already enforces the exact four recipe identities. Build
    # first so stale binaries cannot authorize outputs for newer source content.
    record.update(status="checking", evidence=str(directory),
                  verification_claim="none: candidate generation/inventory only")
    context = {"target": "fixed_four_f2", "mode": "prepare_circt", "identity": "not-yet-computed",
               "golden_root": str(_path(repo, _sfc_preparation(config)["golden_root"]).resolve()),
               "candidate_root": str(_path(repo, preparation["candidate_root"]).resolve()),
               "compiler": str(_path(repo, preparation["compiler"]).resolve())}
    _write(directory / "result.json", record)
    phase = "build" if preparation.get("build_command") is not None else "generation"
    try:
        if preparation.get("build_command") is not None:
            record["build"] = _command(preparation["build_command"],
                {**context, "mode": "build_circt"}, config, repo, directory / "build", require_result=False)
        missing = [target for target in TARGETS if _circt_receipt(config, repo, target) is None]
        record.update(existing_complete=[target for target in TARGETS if target not in missing],
                      requested_targets=missing)
        _write(directory / "result.json", record)
        if missing:
            phase = "generation"
            command = copy.deepcopy(preparation.get("command"))
            if not isinstance(command, dict) or not isinstance(command.get("argv"), list):
                raise VerificationBlocked("Missing real CIRCT candidate generation command")
            command["argv"] += [arg for target in missing for arg in ("--target", target)]
            record.update(_command(command, context, config, repo, directory / "generation", require_result=False))
        absent = [target for target in TARGETS if _circt_receipt(config, repo, target) is None]
        if absent:
            raise VerificationBlocked("CIRCT preparation produced no current complete receipts for: " + ", ".join(absent))
    except Exception as error:
        record.update(status="failed", reason=str(error),
                      command_evidence=str(directory / phase / "command.json"))
        _write(directory / "result.json", record)
        raise
    record.update(status="complete", complete_targets=list(TARGETS))
    _write(directory / "result.json", record)


def _snapshot(config: dict, repo: Path) -> tuple[str, dict]:
    targets = config.get("targets", {})
    if set(targets) != set(TARGETS):
        raise VerificationBlocked("Exactly four targets are required: " + ", ".join(TARGETS))
    artifacts = {}
    preparation = _sfc_preparation(config)
    for target in TARGETS:
        spec = targets[target]
        if spec.get("recipe") != FIXED_TARGETS[target]:
            raise VerificationBlocked(f"Unexpected recipe for {target}; do not enumerate configurations")
        for kind in ("sfc_artifacts", "circt_artifacts"):
            paths = spec.get(kind)
            allowed_empty = set()
            if preparation is not None and kind == "sfc_artifacts":
                receipt = _sfc_receipt(config, repo, target)
                if receipt is None:
                    raise VerificationBlocked(f"No complete immutable SFC receipt for {target}; run semantic preparation")
                paths, allowed_empty = receipt
            if _candidate_preparation(config) is not None and kind == "circt_artifacts":
                receipt = _circt_receipt(config, repo, target)
                if receipt is None:
                    raise VerificationBlocked(f"No current complete CIRCT receipt for {target}; run semantic candidate preparation")
                paths, allowed_empty = receipt
            if not isinstance(paths, list) or not paths or not all(isinstance(p, str) for p in paths):
                raise VerificationBlocked(f"{target}.{kind} must list the complete generated design and sidecars")
            artifacts[f"{target}.{kind}"] = {str(_path(repo, p)): _digest(_path(repo, p), nonempty=str(_path(repo, p)) not in allowed_empty) for p in paths}
    recipes = config.get("recipes_file")
    if not recipes:
        raise VerificationBlocked("recipes_file is required")
    # Include explicit manifests for imported tool binaries/configuration inputs.
    inputs = [recipes, *config.get("identity_files", [])]
    input_hashes = {str(_path(repo, p)): _digest(_path(repo, p)) for p in inputs}
    def git_identity(checkout: Path) -> dict:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=checkout, capture_output=True, check=True).stdout.decode().strip()
        diff = subprocess.run(["git", "diff", "--binary", "HEAD", "--", ".", ":(exclude)automation/chia_firesim_circt/logs"],
                              cwd=checkout, capture_output=True, check=True).stdout
        untracked = subprocess.run(["git", "ls-files", "--others", "--exclude-standard", "-z"],
                                   cwd=checkout, capture_output=True, check=True).stdout.decode().split("\0")
        source_suffixes = {".scala", ".py", ".cpp", ".cc", ".c", ".h", ".td", ".tcl", ".sh"}
        excluded = {"generated-src", "build", "target", "output", "results-build", "logs", "__pycache__"}
        untracked_sources = {name: _digest(checkout / name) for name in untracked if name
                             and Path(name).suffix in source_suffixes
                             and not excluded.intersection(Path(name).parts)}
        return {"head": head, "diff_sha256": hashlib.sha256(diff).hexdigest(), "untracked_sources": untracked_sources}
    snapshot = {"git": git_identity(repo), "artifacts": artifacts, "inputs": input_hashes, "configuration": config}
    # A superproject records only the word 'dirty' for submodule source edits.
    # Include the native compiler checkout's actual content delta as well.
    firesim = repo / "sims/firesim"
    if (firesim / ".git").exists():
        snapshot["firesim_git"] = git_identity(firesim)
    identity = hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()
    return identity, snapshot


def _cores(config: dict) -> int:
    available = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else (os.cpu_count() or 1)
    requested = config.get("cores", available)
    if isinstance(requested, bool) or not isinstance(requested, int) or requested < 1:
        raise VerificationBlocked("cores must be a positive integer")
    return min(available, requested)


def _command(adapter: Any, context: dict, config: dict, repo: Path, directory: Path, require_result: bool = True,
             *, allow_unlimited: bool = False) -> dict:
    if not isinstance(adapter, dict) or not isinstance(adapter.get("argv"), list) or not adapter["argv"]:
        raise VerificationBlocked(f"Unconfigured adapter for {context['target']} {context['mode']}")
    if not all(isinstance(arg, str) for arg in adapter["argv"]):
        raise VerificationBlocked("Adapter argv must be an array of strings")
    configured_timeout = adapter.get("timeout_seconds")
    if (isinstance(configured_timeout, bool) or not isinstance(configured_timeout, (int, float))
            or not math.isfinite(configured_timeout) or configured_timeout < 0
            or (configured_timeout == 0 and not allow_unlimited)):
        raise VerificationBlocked("Adapters require finite positive timeout_seconds; only Jasper permits explicit zero for no deadline")
    # Only the Jasper dispatch opts in. All other commands retain a finite
    # deadline; None is passed to Popen.wait only for explicit Jasper zero.
    timeout = None if configured_timeout == 0 else configured_timeout
    directory.mkdir(parents=True, exist_ok=True)
    report_path = directory / "adapter-result.json"
    if report_path.exists() or (not require_result and (directory / "command.json").exists()):
        # An evidence directory is one execution, never a cache.
        raise VerificationBlocked(f"Refusing to reuse adapter evidence directory: {directory}")
    context = {**context, "repo": str(repo), "evidence_dir": str(directory),
               "report": str(report_path), "cores": _cores(config)}
    def expand(arg: str) -> str:
        for key, value in context.items():
            arg = arg.replace("{" + key + "}", str(value))
        return arg
    argv = [expand(arg) for arg in adapter["argv"]]
    env = os.environ.copy()
    env.update({"CHIA_REPORT_PATH": str(report_path), "CHIA_INPUT_IDENTITY": context["identity"],
                "CHIA_TARGET": context["target"], "CHIA_MODE": context["mode"],
                "CHIA_CORES": str(context["cores"]), "OMP_NUM_THREADS": str(context["cores"]),
                "CMAKE_BUILD_PARALLEL_LEVEL": str(context["cores"]), "MAKEFLAGS": f"-j{context['cores']}"})
    start = time.monotonic()
    record = {"argv": argv, "cores": context["cores"], "stdout": str(directory / "stdout.log"),
              "stderr": str(directory / "stderr.log"), "report": str(report_path),
              "timeout_seconds": configured_timeout, "wall_time_limit_seconds": timeout,
              "timed_out": False}
    _write(directory / "command.json", record)
    with (directory / "stdout.log").open("wb") as stdout, (directory / "stderr.log").open("wb") as stderr:
        process = subprocess.Popen(argv, cwd=repo, env=env, stdout=stdout, stderr=stderr, start_new_session=True)
        try:
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            record.update(returncode=None, timed_out=True, seconds=time.monotonic() - start)
            _write(directory / "command.json", record)
            raise RuntimeError(f"Adapter timed out: {context['target']} {context['mode']}")
    record.update(returncode=returncode, seconds=time.monotonic() - start)
    _write(directory / "command.json", record)
    if returncode:
        raise RuntimeError(f"Adapter exited {returncode}: {context['target']} {context['mode']}; {directory}")
    if not require_result:
        return {"command": record}
    if not report_path.is_file():
        raise RuntimeError(f"Adapter did not produce fresh result JSON: {report_path}")
    result = json.loads(report_path.read_text())
    if result.get("passed") is not True or result.get("target") != context["target"] or result.get("input_identity") != context["identity"]:
        raise RuntimeError(f"Adapter result failed or does not identify these inputs: {report_path}")
    if not isinstance(result.get("scope"), str) or not result["scope"].strip():
        raise RuntimeError(f"Adapter result must describe verification scope: {report_path}")
    return {"command": record, "result": result}


def _validate_prebuild_result(result: dict, target: str, identity: str) -> None:
    if (not isinstance(result, dict) or result.get("target") != target
            or result.get("input_identity") != identity or result.get("passed") is not True):
        raise RuntimeError(f"{target}: pre-build evidence failed or identifies different inputs")
    if result.get("verification_stage") != "prebuild":
        raise RuntimeError(f"{target}: comparison must identify verification_stage prebuild")
    if result.get("syntax") is not True or result.get("semantic") is not True or result.get("static_semantics") is not True:
        raise RuntimeError(f"{target}: syntax and semantic static checks must both pass")
    if result.get("unresolved_obligations"):
        raise RuntimeError(f"{target}: unresolved pre-build checks remain")
    syntax = result.get("details", {}).get("paired_syntax", {})
    if not isinstance(syntax, dict) or syntax.get("passed") is not True:
        raise RuntimeError(f"{target}: missing passing paired syntax evidence")
    pairs = syntax.get("pairs", {})
    if not isinstance(pairs, dict) or set(pairs) != {"sfc", "circt"}:
        raise RuntimeError(f"{target}: syntax evidence must include both SFC and CIRCT")
    for side in ("sfc", "circt"):
        pair = pairs[side]
        if not isinstance(pair, dict) or any(not isinstance(pair.get(kind), dict)
                or pair[kind].get("passed") is not True for kind in ("rtl", "driver")):
            raise RuntimeError(f"{target}: {side} complete RTL and driver syntax must both pass")
    # Full behavioral/formal and FPGA obligations belong to their own nodes.
    # deferred_obligations is informational and never a pre-build prerequisite.


def _semantic_target(adapter: Any, context: dict, config: dict, repo: Path, directory: Path) -> dict:
    target = context["target"]
    detail = {"target": target, "mode": "semantic", "status": "blocked", "ok": False,
              "evidence": str(directory / "target-result.json")}
    try:
        detail.update(_command(adapter, context, config, repo, directory))
        _validate_prebuild_result(detail["result"], target, context["identity"])
        detail.update(status="passed", ok=True)
    except Exception as error:
        detail.update(status="blocked" if isinstance(error, VerificationBlocked) else "failed",
                      reason=f"{type(error).__name__}: {error}")
        # An adapter may fail after writing useful mismatch/syntax diagnostics.
        # Retain those and the command receipt even on nonzero exit or invalid
        # result, instead of replacing the target's error with a generic exit.
        for name, filename in (("command", "command.json"), ("result", "adapter-result.json")):
            path = directory / filename
            if path.is_file():
                try:
                    value = json.loads(path.read_text())
                    if isinstance(value, dict):
                        detail[name] = value
                except (OSError, ValueError):
                    pass
        result = detail.get("result", {})
        diagnostics = [result.get(key) for key in ("error", "reason") if isinstance(result.get(key), str) and result[key]]
        unresolved = result.get("unresolved_obligations")
        if isinstance(unresolved, list):
            diagnostics.extend(value for value in unresolved if isinstance(value, str) and value)
        if diagnostics:
            detail["reason"] += "; " + "; ".join(diagnostics)
        elif isinstance(detail.get("command"), dict):
            # A crashed adapter may not reach its JSON writer. Keep a bounded
            # diagnostic tail so that all four failures remain actionable.
            stderr = Path(detail["command"].get("stderr", directory / "stderr.log"))
            if stderr.is_file() and stderr.stat().st_size:
                with stderr.open("rb") as stream:
                    stream.seek(max(0, stderr.stat().st_size - 4096))
                    detail["stderr_tail"] = stream.read(4096).decode(errors="replace").strip()
                if detail["stderr_tail"]:
                    detail["reason"] += "; " + detail["stderr_tail"]
    _write(directory / "target-result.json", detail)
    return detail


def _validate_semantic_dependency(dependency: dict, identity: str) -> None:
    details = dependency.get("details")
    if (not isinstance(details, list) or len(details) != len(TARGETS)
            or any(not isinstance(detail, dict) for detail in details)
            or {detail.get("target") for detail in details} != set(TARGETS)):
        raise VerificationBlocked("Semantic dependency must contain exactly four pre-build target comparisons")
    for detail in details:
        if detail.get("ok") is not True or detail.get("status") != "passed":
            raise VerificationBlocked(f"Semantic dependency did not pass for {detail['target']}")
        try:
            _validate_prebuild_result(detail.get("result"), detail["target"], identity)
        except RuntimeError as error:
            raise VerificationBlocked(f"Invalid semantic dependency: {error}") from error


def _jasper_target(adapter: Any, context: dict, config: dict, repo: Path, directory: Path) -> dict:
    """Retain a failed proof and let the independent proof modes still execute.

    Dispatch remains serial to respect the single Jasper license/RSS budget.
    Neither proof's status can substitute for the other one's signoff.
    """
    target, mode = context["target"], context["mode"]
    detail = {"target": target, "mode": mode, "status": "blocked", "ok": False,
              "evidence": str(directory / "target-result.json")}
    try:
        detail.update(_command(adapter, context, config, repo, directory, allow_unlimited=True))
        result = detail["result"]
        if result.get("mode") != mode or result.get("proof_status") != "proven":
            raise RuntimeError(f"{target}: {mode} proof must be proven; bounded/inconclusive/blackboxed is not accepted")
        if result.get("unproven_obligations") != 0:
            raise RuntimeError(f"{target}: {mode} has unproven obligations")
        detail.update(status="passed", ok=True)
    except Exception as error:
        detail.update(status="blocked" if isinstance(error, VerificationBlocked) else "failed",
                      reason=f"{type(error).__name__}: {error}")
        for name, filename in (("command", "command.json"), ("result", "adapter-result.json")):
            path = directory / filename
            if path.is_file():
                try:
                    value = json.loads(path.read_text())
                    if isinstance(value, dict):
                        detail[name] = value
                except (OSError, ValueError):
                    pass
        result = detail.get("result", {})
        for key in ("error", "reason"):
            if isinstance(result.get(key), str) and result[key]:
                detail["reason"] += "; " + result[key]
    command, result = detail.get("command", {}), detail.get("result", {})
    timed_out = command.get("timed_out") is True or result.get("launcher_returncode") in (124, 137)
    # A local deadline can kill the SSH adapter before it cleans remote engines.
    # Continue after a cleaned timeout, but never overlap the next proof with
    # an unconfirmed remote process tree.
    detail["dispatch_safe"] = not timed_out or result.get("timeout_cleanup", {}).get("result", {}).get("success") is True
    _write(directory / "target-result.json", detail)
    return detail


def _run(node: str, config: dict, evidence_dir: Path, repo: Path, dependency: dict | None = None) -> dict:
    repo, evidence_dir = Path(repo).resolve(), Path(evidence_dir).resolve()
    directory = evidence_dir / node
    directory.mkdir(parents=True, exist_ok=True)
    report = {"node": node, "status": "blocked", "ok": False, "evidence": str(directory / "result.json"),
              "identity": None, "details": []}
    try:
        if node != "semantic" and (not dependency or dependency.get("node") != "semantic" or dependency.get("ok") is not True):
            raise VerificationBlocked("A passing semantic node for these exact inputs is required")
        if node == "semantic" and _sfc_preparation(config) is not None:
            report["preparation"] = {}
            _prepare_sfc(config, repo, directory / "preparation", report["preparation"])
        if node == "semantic" and _candidate_preparation(config) is not None:
            report["candidate_preparation"] = {}
            _prepare_circt(config, repo, directory / "candidate-preparation", report["candidate_preparation"])
        identity, snapshot = _snapshot(config, repo)
        report["identity"] = identity
        _write(directory / "inputs.json", snapshot)
        if node != "semantic" and dependency.get("identity") != identity:
            raise VerificationBlocked("Semantic evidence is stale: source, configuration, or artifacts changed")
        if node != "semantic":
            _validate_semantic_dependency(dependency, identity)
        section = config.get(node, {})
        for target in TARGETS:
            recipe = FIXED_TARGETS[target]
            modes = ("lec", "sec") if node == "jasper" else (node,)
            for mode in modes:
                adapter = section.get(mode) if node == "jasper" else section.get("command")
                context = {"target": target, "recipe": recipe, "mode": mode, "identity": identity,
                           "recipes_file": str(_path(repo, config["recipes_file"]))}
                if node == "semantic":
                    report["details"].append(_semantic_target(adapter, context, config, repo, directory / target / mode))
                    _write(directory / "result.json", report)
                    continue
                if node == "jasper":
                    proof = _jasper_target(adapter, context, config, repo, directory / target / mode)
                    report["details"].append(proof)
                    _write(directory / "result.json", report)
                    if not proof["dispatch_safe"]:
                        raise RuntimeError(f"{target} {mode}: timeout cleanup is unconfirmed; further Jasper dispatch would risk overlapping remote proof jobs")
                    continue
                detail = _command(adapter, context, config, repo, directory / target / mode,
                                  allow_unlimited=node == "jasper")
                result = detail["result"]
                if node == "firesim":
                    required = ("local_bitstream_build", "firesim_manager", "f2_runfarm", "workload_passed",
                                "linux_booted", "poweroff_observed")
                    if not all(result.get(key) is True for key in required):
                        raise RuntimeError(f"{target}: F2 evidence requires local build, manager, F2 runfarm, Linux boot and poweroff")
                    if result.get("workload_kind") != "linux_boot_poweroff":
                        raise RuntimeError(f"{target}: F2 verification requires the br-base Linux boot/poweroff workload")
                    for key in ("build_log", "manager_log", "agfi", "workload_evidence", "workload_inputs"):
                        if not isinstance(result.get(key), str) or not result[key].strip():
                            raise RuntimeError(f"{target}: F2 result lacks {key}")
                    detail["receipt_sha256"] = {key: _digest(_path(repo, result[key]), nonempty=True)
                                                 for key in ("build_log", "manager_log", "workload_evidence", "workload_inputs")}
                if node == "speed":
                    if result.get("timing_scope") != "firesim_compiler":
                        raise RuntimeError(f"{target}: speed must measure FireSim compiler transformation/compile time")
                    baseline, candidate = result.get("sfc_seconds"), result.get("circt_seconds")
                    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0
                           for v in (baseline, candidate)):
                        raise RuntimeError(f"{target}: speed requires positive finite SFC and CIRCT timings")
                    if result.get("equivalent_work") is not True or candidate >= baseline:
                        raise RuntimeError(f"{target}: CIRCT must beat SFC for equivalent transform work")
                    samples = result.get("samples")
                    if not isinstance(samples, list) or len(samples) < 3:
                        raise RuntimeError(f"{target}: speed requires at least three matched timing samples")
                    timing_keys = ("sfc_seconds", "circt_seconds")
                    for sample in samples:
                        if not isinstance(sample, dict) or any(
                            isinstance(sample.get(key), bool) or not isinstance(sample.get(key), (int, float))
                            or not math.isfinite(sample[key]) or sample[key] <= 0 for key in timing_keys):
                            raise RuntimeError(f"{target}: every sample needs positive FireSim compiler timings")
                    if not math.isclose(baseline, statistics.median(s["sfc_seconds"] for s in samples)) or not math.isclose(
                            candidate, statistics.median(s["circt_seconds"] for s in samples)):
                        raise RuntimeError(f"{target}: reported compiler timings must be sample medians")
                    for key in ("sfc_command", "circt_command", "cache_policy"):
                        if not result.get(key):
                            raise RuntimeError(f"{target}: speed result lacks {key}")
                    if result.get("cores") != _cores(config):
                        raise RuntimeError(f"{target}: speed CPU budget differs from the assigned budget")
                    detail["circt_over_sfc"] = candidate / baseline
                report["details"].append({"target": target, "mode": mode, **detail})
        end_identity, _ = _snapshot(config, repo)
        if end_identity != identity:
            raise RuntimeError("Verification inputs changed during execution; results cannot be reused")
        failures = [detail for detail in report["details"] if detail.get("ok") is not True] if node in ("semantic", "jasper") else []
        if failures:
            report.update(status="failed" if any(detail["status"] == "failed" for detail in failures) else "blocked",
                          reason="; ".join(f"{detail['target']} {detail['mode']}: {detail['reason']}" for detail in failures))
        else:
            report.update(status="passed", ok=True)
    except VerificationBlocked as exc:
        report["reason"] = str(exc)
    except Exception as exc:
        report.update(status="failed", reason=f"{type(exc).__name__}: {exc}")
    if report.get("preparation") is not None and report["preparation"].get("status") != "complete":
        report["preparation"].update(status=report["status"], reason=report.get("reason", "Preparation incomplete"))
        _write(directory / "preparation/result.json", report["preparation"])
    if report.get("candidate_preparation") is not None and report["candidate_preparation"].get("status") != "complete":
        report["candidate_preparation"].update(status=report["status"], reason=report.get("reason", "Candidate preparation incomplete"))
        _write(directory / "candidate-preparation/result.json", report["candidate_preparation"])
    _write(directory / "result.json", report)
    return report


def run_semantic(config: dict, evidence_dir: Path, repo: Path) -> dict:
    return _run("semantic", config, evidence_dir, repo)


def run_jasper(config: dict, evidence_dir: Path, repo: Path, semantic: dict) -> dict:
    return _run("jasper", config, evidence_dir, repo, semantic)


def run_firesim(config: dict, evidence_dir: Path, repo: Path, semantic: dict) -> dict:
    return _run("firesim", config, evidence_dir, repo, semantic)


def run_speed(config: dict, evidence_dir: Path, repo: Path, semantic: dict | None = None) -> dict:
    return _run("speed", config, evidence_dir, repo, semantic)
