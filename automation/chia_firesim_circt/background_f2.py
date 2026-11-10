"""Queue isolated, hash-bound FireSim F2 candidates without blocking coding.

Each candidate gets copied source/driver/platform files and compiler outputs.
Only the preinstalled Conda environment is shared; Linux images are copied.
At most one worker builds/runs and one newer candidate waits; newer pending
candidates supersede older pending candidates. No Jasper work is dispatched.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import time

import yaml

TARGETS = ("rocket", "megaboomv3", "shuttle", "saturn_shuttle")
EXCLUDED = {".git", "logs", "__pycache__", "generated-src", "output", "target",
            "results-build", "results-workload", "local-vivado", "checkpoints"}
SOURCE_SUFFIXES = {".py", ".sh", ".cc", ".cpp", ".c", ".h", ".hpp", ".mk", ".scala",
                   ".sbt", ".json", ".yaml", ".yml", ".v", ".sv", ".vh", ".tcl", ".xdc"}


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def record(path):
    path = Path(path).resolve()
    return {"path": str(path), "sha256": digest(path), "bytes": path.stat().st_size}


def validate(records):
    for item in records:
        path = Path(item["path"])
        if not path.is_file() or digest(path) != item["sha256"] or path.stat().st_size != item["bytes"]:
            raise ValueError("Frozen background F2 input changed: " + str(path))


@contextlib.contextmanager
def locked(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def source_repositories(repo):
    """Only build/driver closure; exclude unrelated Linux/toolchain source trees."""
    repo = Path(repo).resolve()
    wanted = [repo, repo / "sims/firesim", repo / "generators/testchipip"]
    platform = repo / "sims/firesim/platforms/f2/aws-fpga-firesim-f2"
    wanted.append(platform)
    result = []
    for root in wanted:
        if not (root / ".git").exists():
            continue
        result.append(root)
        if root == platform:
            command = subprocess.run(["git", "submodule", "foreach", "--quiet", "--recursive", "pwd"],
                                     cwd=root, capture_output=True, text=True, check=True)
            result.extend(Path(line) for line in command.stdout.splitlines() if line.startswith("/"))
    return sorted(set(result), key=lambda p: (len(p.parts), str(p)))


def source_files(repo):
    repo = Path(repo).resolve()
    roots = source_repositories(repo)
    paths = set()
    for root in roots:
        tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=root).decode().split("\0")
        untracked = subprocess.check_output(["git", "ls-files", "--others", "--exclude-standard", "-z"],
                                            cwd=root).decode().split("\0")
        for name in tracked + [name for name in untracked if Path(name).suffix in SOURCE_SUFFIXES]:
            relative = Path(name)
            if not name or EXCLUDED.intersection(relative.parts):
                continue
            if ("built-hwdb-entries" in relative.parts or
                    any(part.startswith("chia-f2-") for part in relative.parts)):
                continue
            # Never copy prior mutable FPGA deliveries/build trees.
            if "developer_designs" in relative.parts and any(part.startswith("cl_") and part != "cl_firesim"
                                                             for part in relative.parts[:-1]):
                continue
            path = root / relative
            if path.is_file():
                paths.add(path)
    # Setup/runtime configs and SDK shared libraries are generated/ignored.
    for relative in ["env.sh", "sims/firesim/env.sh", "sims/firesim/deploy/config_runtime.yaml"]:
        path = repo / relative
        if path.is_file():
            paths.add(path)
    sdk = repo / "sims/firesim/platforms/f2/aws-fpga-firesim-f2/sdk/userspace/lib"
    if sdk.exists():
        paths.update(path for path in sdk.iterdir() if path.is_file())
    return roots, sorted(paths)


def hardware_key(designs, files, repo):
    """Key actual hardware/driver/build/runtime inputs, excluding compiler source."""
    repo = Path(repo)
    records = {}
    for path in files:
        rel = path.relative_to(repo)
        # Scala/native transformation and documentation changes do not alter an
        # already-frozen RTL design. Driver runtime and build rules still bind.
        if ("goldengate-circt" in rel.parts and "runtime" not in rel.parts
                or path.suffix in {".scala", ".md", ".rst"}):
            continue
        if rel.parts[:2] == ("automation", "chia_firesim_circt") and path.name not in {
                "background_f2.py", "run_f2.py", "f2_manager.py", "prepare_f2_linux.py",
                "downstream_common.py", "verification_nodes.py", "verification.yaml",
                "selected_config_build_recipes.yaml"}:
            continue
        records[str(rel)] = digest(path)
    for target, design in designs.items():
        for path in sorted(Path(design["circt_rtl"]).parent.rglob("*")):
            if path.is_file() and (path.suffix in {".sv", ".v", ".h", ".vh", ".xdc", ".tcl"}
                                   or path.name == "FireSim-generated.peer2peer.const.yaml"):
                records[target + "/" + str(path.relative_to(Path(design["circt_rtl"]).parent))] = digest(path)
        records[target + "/recipe"] = design["recipe"]
    for rel in ["software/firemarshal/images/firechip/br-base/br-base-bin",
                "software/firemarshal/images/firechip/br-base/br-base.img"]:
        path = repo / rel
        if path.is_file():
            records[rel] = digest(path)
    return hashlib.sha256(json.dumps(records, sort_keys=True).encode()).hexdigest()


def copy_checkout(repo, directory, roots, files):
    """Copy real build inputs without hardlinks back to changing source/images."""
    repo, directory = Path(repo).resolve(), Path(directory).resolve()
    frozen_repo = directory / "repo"
    frozen_repo.mkdir(parents=True)
    # Own Git metadata/index, read-only shared object cache. git describe remains
    # available for AWS version selection without a live checkout/index link.
    for root in roots:
        destination = frozen_repo / root.relative_to(repo)
        subprocess.run(["git", "clone", "--shared", "--no-checkout", "-q", str(root), str(destination)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        # --no-checkout deliberately leaves the clone's index empty. Populate
        # only its private index: a later snapshot must still discover tracked
        # extensionless inputs (deploy/firesim, Makefile, etc.) via ls-files.
        # Do not use checkout/reset --hard or read-tree -u: copied dirty bytes
        # and dereferenced source links, not HEAD's worktree, are authoritative.
        subprocess.run(["git", "read-tree", "HEAD"], cwd=destination,
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    def copy(path):
        destination = frozen_repo / path.relative_to(repo)
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Dereference source links: copied mutable source must not point back at
        # the implementation checkout. No source hardlinks are permitted.
        shutil.copy2(path, destination, follow_symlinks=True)
        return destination
    with ThreadPoolExecutor(max_workers=8) as pool:
        copied = list(pool.map(copy, files))
    # Source-generated env files contain manager checkout absolute paths.
    for relative in ["env.sh", "sims/firesim/env.sh"]:
        path = frozen_repo / relative
        path.write_text(path.read_text().replace(str(repo), str(frozen_repo)))
    (frozen_repo / ".conda-env").symlink_to(repo / ".conda-env", target_is_directory=True)
    shared = []
    for relative in ["software/firemarshal/images/firechip/br-base/br-base-bin",
                     "software/firemarshal/images/firechip/br-base/br-base.img"]:
        path = repo / relative
        if not path.is_file():
            raise ValueError("Missing immutable Linux image: " + str(path))
        destination = frozen_repo / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["cp", "--reflink=auto", "--", str(path), str(destination)], check=True)
        shared.append(record(destination))
    return frozen_repo, [record(path) for path in copied], shared


def freeze(repo, directory, config, semantic):
    """Capture before returning to the implementation agent; no live markers used later."""
    import verification_nodes as gates
    from downstream_common import inputs

    repo, directory = Path(repo).resolve(), Path(directory).resolve()
    identity, snapshot = gates._snapshot(config, repo)
    gates._validate_semantic_dependency(semantic, identity)
    designs = {target: inputs(repo, target) for target in TARGETS}
    roots, files = source_files(repo)
    key = hardware_key(designs, files, repo)
    frozen_repo, frozen_sources, shared = copy_checkout(repo, directory, roots, files)
    targets = {}
    for target, design in designs.items():
        original = Path(design["circt_rtl"]).parent
        destination = directory / "inputs" / target / "circt"
        shutil.copytree(original, destination, symlinks=False)
        rtl = destination / "FireSim-generated.sv"
        targets[target] = {"recipe": design["recipe"], "recipe_name": design["recipe_name"],
                           "circt_rtl": str(rtl), "config": config,
                           "files": [record(p) for p in sorted(destination.rglob("*")) if p.is_file()]}
    # Validate semantic/source snapshot again while coding is still gated.
    after, _ = gates._snapshot(config, repo)
    if after != identity:
        raise ValueError("Candidate changed while preparing background snapshot")
    manifest = {"schema_version": 1, "identity": identity, "hardware_key": key,
                "source_repo": str(repo), "repo": str(frozen_repo), "targets": targets,
                "source_files": frozen_sources, "shared_immutable_images": shared,
                "shared_tools": {"conda_env": str((repo / '.conda-env').resolve()),
                                 "scope": "preinstalled environment, not compiler sources/driver/build outputs"}}
    write(directory / "snapshot.json", manifest)
    validate(frozen_sources + shared)
    return manifest


def frozen_design(manifest, target):
    data = manifest["targets"][target]
    validate(data["files"] + manifest["source_files"] + manifest["shared_immutable_images"])
    return {"config": data["config"], "recipe_name": data["recipe_name"], "recipe": data["recipe"],
            "circt_rtl": Path(data["circt_rtl"]), "circt": [Path(v["path"]) for v in data["files"]],
            "sfc": [], "sfc_rtl": None}


def root_directory(repo):
    return Path(repo).resolve() / "automation/chia_firesim_circt/logs/background-f2"


def refresh_job(client, path, data):
    """A crashed/stopped Ray driver must not leave a reusable running receipt."""
    if data.get("phase") in {"queued", "running"}:
        try:
            state = str(client.get_job_info(data["job_id"]).status)
        except Exception:
            return data  # Unreachable dashboard is not proof that work stopped.
        if state in {"FAILED", "STOPPED", "SUCCEEDED"}:
            data.update(phase="failed", error="Background job ended without a final F2 receipt: " + state)
            write(path, data)
    return data


def status(repo):
    root = root_directory(repo)
    result = []
    try:
        from ray.job_submission import JobSubmissionClient
        client = JobSubmissionClient(os.environ.get("RAY_API_SERVER_ADDRESS", "http://127.0.0.1:8265"))
    except Exception:
        client = None
    for path in sorted(root.glob("*/job.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:8]:
        try:
            d = json.loads(path.read_text())
            if client is not None:
                d = refresh_job(client, path, d)
            result.append({k: d.get(k) for k in ["job_id", "phase", "identity", "hardware_key", "error", "result"]})
            result[-1]["evidence"] = str(path)
        except (OSError, ValueError):
            continue
    return result


def launch(config, run_id, semantic, repo):
    """Freeze and submit; return once the independent job is queued, never await F2."""
    from ray.job_submission import JobSubmissionClient
    repo = Path(repo).resolve()
    root = root_directory(repo)
    root.mkdir(parents=True, exist_ok=True)
    directory = root / re.sub(r"[^A-Za-z0-9_.-]", "_", run_id)
    if directory.exists():
        raise ValueError("Background snapshot directory already exists")
    directory.mkdir()
    try:
        manifest = freeze(repo, directory, config, semantic)
        client = JobSubmissionClient(os.environ.get("RAY_API_SERVER_ADDRESS", "http://127.0.0.1:8265"))
        with locked(root / ".queue.lock"):
            for path in root.glob("*/job.json"):
                old = refresh_job(client, path, json.loads(path.read_text()))
                if old.get("phase") in {"queued", "running", "passed"} and old.get("hardware_key") == manifest["hardware_key"]:
                    old.update(deduplicated=True, snapshot_dir=str(directory),
                               reused_hardware_identity=old.get("identity"), requested_identity=manifest["identity"])
                    # Do not claim the old semantic identity is the new one.
                    write(directory / "reuse.json", old)
                    return old
            # Keep only the newest pending candidate. Active builds/runs finish.
            for path in root.glob("*/job.json"):
                old = json.loads(path.read_text())
                if old.get("phase") == "queued":
                    old.update(phase="superseded", superseded_by=str(directory))
                    write(path, old)
                    client.stop_job(old["job_id"])
            job_id = "firesim-bg-" + directory.name
            receipt = {"job_id": job_id, "phase": "queued", "identity": manifest["identity"],
                       "hardware_key": manifest["hardware_key"], "snapshot_dir": str(directory),
                       "evidence": str(directory / "job.json"), "formal_dispatched": False}
            write(directory / "job.json", receipt)
            script = Path(manifest["repo"]) / "automation/chia_firesim_circt/background_f2.py"
            command = shlex.join([str(repo / ".conda-env/bin/python"), "-B", str(script),
                                  "--snapshot", str(directory / "snapshot.json")])
            client.submit_job(entrypoint=command, submission_id=job_id)
            return receipt
    except Exception as error:
        receipt = {"phase": "blocked", "error": f"{type(error).__name__}: {error}",
                   "snapshot_dir": str(directory), "evidence": str(directory / "job.json")}
        write(directory / "job.json", receipt)
        return receipt


def run_snapshot(path):
    manifest = json.loads(Path(path).read_text())
    directory = Path(path).parent
    repo = Path(manifest["repo"])
    root = root_directory(manifest["source_repo"])
    # Global lock is outside every frozen checkout. It serializes both Vivado
    # and cloud work, so background candidates cannot overrun manager capacity.
    with locked(root / ".worker.lock"):
        with locked(root / ".queue.lock"):
            receipt = json.loads((directory / "job.json").read_text())
            if receipt["phase"] != "queued":
                return 0
            receipt["phase"] = "running"
            write(directory / "job.json", receipt)
        result = {"identity": manifest["identity"], "hardware_key": manifest["hardware_key"],
                  "scope": "Frozen baseline candidate; does not accept later live source changes", "targets": []}
        try:
            validate(manifest["source_files"] + manifest["shared_immutable_images"])
            # Import from this immutable checkout, never the changing live one.
            sys.path.insert(0, str(repo / "automation/chia_firesim_circt"))
            import run_f2
            for target in TARGETS:
                design = frozen_design(manifest, target)
                run_f2.inputs = lambda requested_repo, requested_target, expected=target, value=design: (
                    value if requested_target == expected and Path(requested_repo).resolve() == repo
                    else (_ for _ in ()).throw(ValueError("Frozen target/repo mismatch")))
                output = directory / "firesim" / target
                output.mkdir(parents=True)
                os.environ.update(CHIA_REPORT_PATH=str(output / "adapter-result.json"),
                                  CHIA_INPUT_IDENTITY=manifest["identity"],
                                  CHIA_F2_BUILD_LOCK=str(Path(manifest["source_repo"]) /
                                      "automation/chia_firesim_circt/logs/.f2-build.lock"))
                sys.argv = [str(repo / "automation/chia_firesim_circt/run_f2.py"), "--repo", str(repo),
                            "--target", target, "--output-dir", str(output)]
                code = run_f2.main()
                report = json.loads((output / "adapter-result.json").read_text())
                result["targets"].append({"target": target, "passed": code == 0 and report.get("passed") is True,
                                          "evidence": str(output / "adapter-result.json"), "error": report.get("error")})
                write(directory / "result.json", result)
            result["passed"] = all(t["passed"] for t in result["targets"])
            receipt["phase"] = "passed" if result["passed"] else "failed"
        except Exception as error:
            result.update(passed=False, error=f"{type(error).__name__}: {error}")
            receipt.update(phase="failed", error=result["error"])
        write(directory / "result.json", result)
        receipt["result"] = str(directory / "result.json")
        write(directory / "job.json", receipt)
        return 0 if result.get("passed") else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    raise SystemExit(run_snapshot(parser.parse_args().snapshot))
