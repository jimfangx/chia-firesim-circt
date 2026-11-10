#!/usr/bin/env python3
"""Copy migration inputs from the old host without overwriting destination files.

Run in a host terminal with SSH access. No repository or credential state is
copied, and this does not install packages, start jobs, or modify destination Git.
"""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="ubuntu@44.255.111.245")
    parser.add_argument("--destination", default="/scratch/jfx/fsim-circt")
    parser.add_argument("--inventory-only", action="store_true")
    args = parser.parse_args()
    # These are supplied to rsync's remote-shell protocol; keep them simple.
    if not all(c.isalnum() or c in "@._-" for c in args.host) or not args.host:
        parser.error("Unsupported SSH host spelling")
    if not args.destination.startswith("/") or any(c not in "/._-" and not c.isalnum()
                                                    for c in args.destination):
        parser.error("Destination must be an absolute path without shell metacharacters")
    source = Path(__file__).resolve().parents[2]
    paths = set()
    report = Path(tempfile.mkdtemp(prefix="fsim-migration-"))

    def include(path):
        path = Path(path)
        if path.is_symlink():
            paths.add(str(path.relative_to(source)))
        elif path.is_dir():
            for parent, dirs, files in os.walk(path, followlinks=False):
                for name in list(dirs):
                    q = Path(parent) / name
                    if q.is_symlink():
                        paths.add(str(q.relative_to(source)))
                        dirs.remove(name)
                for name in files:
                    paths.add(str((Path(parent) / name).relative_to(source)))
        elif path.is_file():
            paths.add(str(path.relative_to(source)))

    # Git stores submodule contents separately; enumerate the three relevant
    # repositories explicitly, skipping gitlinks, bytecode and credentials.
    for repo in [source, source / "sims/firesim", source / "software/firemarshal"]:
        entries = subprocess.check_output(["git", "ls-files", "--stage", "-z"], cwd=repo)
        for entry in entries.split(b"\0"):
            if not entry:
                continue
            header, name = entry.split(b"\t", 1)
            if header.startswith(b"160000"):
                continue
            p = repo / os.fsdecode(name)
            if any(x in {"__pycache__", ".codex", ".aws", ".ssh", ".git"} for x in p.parts):
                continue
            if p.suffix not in {".pyc", ".pyo"}:
                include(p)

    automation = source / "automation/chia_firesim_circt"
    for p in automation.iterdir():
        if p.is_file() and p.suffix in {".py", ".yaml", ".md", ".sh", ".c"}:
            include(p)
    include(automation / "patches")
    include(automation / "golden/.gitignore")
    for p in (automation / "workloads").iterdir():
        if p.suffix == ".json":
            include(p)
    for p in (automation / "workloads/circt-metasim-bare").iterdir():
        if p.is_file() and (p.suffix in {".c", ".h", ".ld", ".py", ".sh", ".md"}
                            or p.name in {"Makefile", ".gitignore"}):
            include(p)
    for name in ["list-firesim-annotations.py", "report-fame-handoff.py"]:
        include(source / "tools" / name)
    for name in ["config_build.yaml", "config_build_recipes.yaml", "config_hwdb.yaml",
                 "config_runtime.yaml", "config_runtime_metasim.yaml"]:
        include(source / "sims/firesim/deploy" / name)
    for version in ["2023.2", "2023.2.2"]:
        include(source / "sims/firesim/platforms/xilinx_alveo_u250/cl_firesim/scripts/bd_lib" / version)

    # Preserve exact immutable references and the last completed/interrupted
    # comparison evidence, rather than moving terabytes of obsolete replays.
    include(automation / "golden/u250-sfc-20261013")
    include(source / "sims/firesim-staging/generated-src/firechip.chip.FireSim.FireSimRocketConfig.sfc-golden-2026-10-01")
    original = source / "sims/firesim/deploy/results-build/2026-10-01--04-55-23-circt_u250_firesim_rocket_singlecore"
    for p in original.glob("*.sfc-golden-2026-10-01"):
        include(p)
    generated = source / "sims/firesim/sim/generated-src/xilinx_alveo_u250/xilinx_alveo_u250-firesim-FireSim-FireSimRocketConfig-BaseXilinxAlveoU250Config"
    for n in [262, 263]:
        include(generated / f"iteration{n}-u250-target-matrix")
    for n in range(259, 263):
        include(automation / f"logs/verification/20261006T131125Z-pid1620159-iter{n}-full")
    include(automation / "logs/codex_20261006_131125.log")

    # Include the actual local bitstream referenced by the current HWDB.
    import yaml
    hwdb = source / "sims/firesim/deploy/config_hwdb.yaml"
    for config in yaml.safe_load(hwdb.read_text()).values():
        uri = config.get("bitstream_tar", "")
        if uri.startswith("file://"):
            p = Path(uri[7:])
            if p.is_relative_to(source):
                include(p)

    manifest = report / "files.nul"
    manifest.write_bytes(b"".join(os.fsencode(p) + b"\0" for p in sorted(paths)))
    allocated = sum((source / p).lstat().st_blocks * 512 for p in paths)
    state = {"source": str(source), "destination": args.host + ":" + args.destination,
             "files": len(paths), "allocated_GiB": round(allocated / 2**30, 2),
             "python": platform.python_version(), "packages": {}, "conda_packages": [],
             "handoff": "Iteration 262 complete; iteration 263 interrupted by disk exhaustion. Preserve WIP; do not reset it."}
    for name in ["ray", "chia", "chia-agent", "PyYAML"]:
        try:
            state["packages"][name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            pass
    for env in [source / ".conda-env", source / ".conda-env/riscv-tools"]:
        for p in (env / "conda-meta").glob("*.json"):
            package = json.loads(p.read_text())
            state["conda_packages"].append({"environment": str(env), **{
                k: package.get(k) for k in ["name", "version", "build"]}})
    (report / "handoff.json").write_text(json.dumps(state, indent=2) + "\n")
    print(json.dumps({k: state[k] for k in ["source", "destination", "files", "allocated_GiB"]}, indent=2), flush=True)
    print("Inventory and reports:", report, flush=True)
    if args.inventory_only:
        return
    ssh = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", args.host]
    subprocess.run(ssh + [f"mkdir -p {args.destination}"], check=True)
    remote = args.host + ":" + args.destination.rstrip("/") + "/"
    common = ["rsync", "-rlpt", "--from0", f"--files-from={manifest}",
              "-e", "ssh -o BatchMode=yes -o ConnectTimeout=15"]
    subprocess.run(common + ["--ignore-existing", "--info=progress2", "--stats",
                              f"--log-file={report / 'copy.log'}", str(source) + "/", remote], check=True)
    # Existing differences are reported only: destination work is never replaced.
    with (report / "remaining-differences.txt").open("w") as output:
        subprocess.run(common + ["--dry-run", "--checksum", "--itemize-changes",
                                  str(source) + "/", remote], stdout=output, check=True)
    remote_report = args.host + ":" + args.destination.rstrip("/") + "/automation/chia_firesim_circt/migration-handoff/"
    subprocess.run(["rsync", "-rlt", "--ignore-existing", "-e", "ssh -o BatchMode=yes",
                    str(report / "handoff.json"), str(report / "remaining-differences.txt"),
                    remote_report], check=True)
    print("Missing files copied. Review existing differences:", report / "remaining-differences.txt")
    print("No jobs restarted, credentials copied, or existing destination files overwritten.")


if __name__ == "__main__":
    main()
