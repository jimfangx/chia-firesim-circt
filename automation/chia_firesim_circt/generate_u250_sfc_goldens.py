#!/usr/bin/env python3
"""Archive MIDAS-transformed SFC RTL for every U250 sample recipe.

Run after sourcing the FireSim manager environment. Dedicated input/output
directories and snapshotted assembly jars keep the live CIRCT build untouched.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from zipfile import ZipFile

import yaml

REPO = Path(__file__).resolve().parents[2]
RECIPES = REPO / "sims/firesim-staging/sample_config_build_recipes.yaml"
ALIASES = {
    "QuadTileRingNoCBase": "QuadTileRingNoCU250Base",
    "QuadTileRingNoC0": "QuadTileRingNoCU250Partition0",
    "QuadTileRingNoC1": "QuadTileRingNoCU250Partition1",
}


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write_manifest(root, manifest):
    temp = root / "manifest.json.tmp"
    temp.write_text(json.dumps(manifest, indent=2) + "\n")
    temp.replace(root / "manifest.json")


def run(command, cwd, log, env):
    print("RUN", log, flush=True)
    with log.open("w") as output:
        output.write("COMMAND " + json.dumps(command) + "\n")
        output.flush()
        result = subprocess.run(command, cwd=cwd, env=env, stdout=output,
                                stderr=subprocess.STDOUT)
    if result.returncode:
        raise RuntimeError(f"exit {result.returncode}: {log}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    root = args.output_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    selected = {k: v for k, v in yaml.safe_load(RECIPES.read_text()).items()
                if v.get("PLATFORM") == "xilinx_alveo_u250"}
    toolchain = root / "toolchain"
    toolchain.mkdir(exist_ok=True)
    for name, source in {
        "firechip.jar": REPO / ".classpath_cache/firechip.jar",
        "firesim-main.jar": REPO / "sims/firesim/sim/generated-src/firesim-main.jar",
    }.items():
        destination = toolchain / name
        if not destination.exists():
            shutil.copy2(source, destination)
    manifest_path = root / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        if manifest["recipes_sha256"] != digest(RECIPES):
            raise RuntimeError("Recipes changed; use a fresh output root")
        if manifest["toolchain"] != {p.name: digest(p) for p in toolchain.glob("*.jar")}:
            raise RuntimeError("Archived assembly jars changed; use a fresh output root")
    else:
        manifest = {"recipes": str(RECIPES), "recipes_sha256": digest(RECIPES),
                    "toolchain": {p.name: digest(p) for p in toolchain.glob("*.jar")},
                    "targets": {}, "config_aliases": ALIASES}
        for cwd in [REPO, REPO / "sims/firesim"]:
            manifest.setdefault("commits", {})[str(cwd)] = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=cwd, text=True).strip()
            manifest.setdefault("worktree_status", {})[str(cwd)] = subprocess.check_output(
                ["git", "status", "--short", "--untracked-files=no"], cwd=cwd, text=True)
    write_manifest(root, manifest)
    java = shutil.which("java")
    if not java:
        raise RuntimeError("Source sourceme-manager.sh --skip-ssh-setup first")
    with ZipFile(toolchain / "firesim-main.jar") as jar:
        classes = set(jar.namelist())
    inputs = root / "inputs"
    inputs.mkdir(exist_ok=True)
    env = os.environ.copy()
    java_tmp = root / "java-tmp"
    java_tmp.mkdir(exist_ok=True)
    env["JAVA_TOOL_OPTIONS"] = f"-Xmx24G -Xss8M -Djava.io.tmpdir={java_tmp}"
    failures = []
    for name, recipe in selected.items():
        previous = manifest["targets"].get(name, {})
        if previous.get("status") == "passed":
            if all((root / p).is_file() and digest(root / p) == h
                   for p, h in previous["sha256"].items()):
                print("PRESERVED", name, flush=True)
                continue
            raise RuntimeError(f"Golden fixture was modified: {name}")
        start = time.monotonic()
        requested = recipe["PLATFORM_CONFIG"]
        effective = ALIASES.get(requested, requested)
        long_name = f"firechip.chip.{recipe['DESIGN']}.{recipe['TARGET_CONFIG']}"
        input_dir = inputs / long_name
        target = root / name
        target.mkdir(exist_ok=True)
        record = {"recipe": recipe, "requested_platform_config": requested,
                  "effective_platform_config": effective, "input_dir": str(input_dir),
                  "golden_dir": str(target / "design"), "status": "building"}
        manifest["targets"][name] = record
        write_manifest(root, manifest)
        try:
            for fragment in effective.split("_"):
                if not any(f"{p}/{fragment}.class" in classes for p in
                           ["firesim/firesim", "firesim/configs", "firesim/util", "midas"]):
                    raise RuntimeError(f"Missing platform class: {fragment}")
            firrtl = input_dir / (long_name + ".sfc.fir")
            annos = input_dir / (long_name + ".anno.json")
            if not firrtl.exists() or not annos.exists():
                input_dir.mkdir(exist_ok=True)
                command = [java, "-cp", str(toolchain / "firechip.jar"), "chipyard.Generator",
                           "--target-dir", str(input_dir), "--name", long_name,
                           "--top-module", "firechip.chip." + recipe["DESIGN"],
                           "--legacy-configs", "firechip.chip:" + recipe["TARGET_CONFIG"],
                           "--emit-legacy-sfc"]
                run(command, REPO / "sims/firesim-staging", target / "elaboration.log", env)
            if not firrtl.is_file() or not annos.is_file():
                raise RuntimeError("Elaboration did not emit pre-MIDAS FIRRTL and annotations")
            design = target / ("attempt-" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()))
            design.mkdir()
            command = [java, "-cp", str(toolchain / "firesim-main.jar"),
                       "midas.stage.GoldenGateMain", "-i", str(firrtl), "-td", str(design),
                       "-faf", str(annos), "-ggcp", "firesim.firesim", "-ggcs", effective,
                       "--output-filename-base", "FireSim-generated",
                       "--allow-unrecognized-annotations", "--no-dedup"]
            record["sfc_command"] = command
            run(command, REPO / "sims/firesim/sim", target / "sfc.log", env)
            rtl = design / "FireSim-generated.sv"
            if not rtl.is_file() or rtl.stat().st_size == 0:
                raise RuntimeError("SFC emitted no transformed RTL")
            blackboxes = design / "firrtl_black_box_resource_files.f"
            if blackboxes.exists():
                with rtl.open("ab") as output:
                    for line in blackboxes.read_text().splitlines():
                        if not line.strip():
                            continue
                        resource = Path(line.strip())
                        if not resource.is_absolute():
                            resource = REPO / "sims/firesim/sim" / resource
                        with resource.open("rb") as source:
                            shutil.copyfileobj(source, output)
                        output.write(b"\n")
            required = [rtl, design / "FireSim-generated.const.h",
                        design / "FireSim-generated.synthesis.xdc",
                        design / "FireSim-generated.implementation.xdc"]
            if not all(p.is_file() for p in required):
                raise RuntimeError("SFC RTL/collateral incomplete")
            archived_attempt = design
            design.rename(target / "design")
            # SFC collateral contains absolute resource paths into its original
            # target directory. Preserve that path after giving the archive a
            # stable public name; do not rewrite the compiler's golden metadata.
            archived_attempt.symlink_to("design", target_is_directory=True)
            files = [p for p in (target / "design").rglob("*") if p.is_file()]
            files.extend([firrtl, annos])
            record["sha256"] = {str(p.relative_to(root)): digest(p) for p in files}
            record["rtl_bytes"] = (target / "design/FireSim-generated.sv").stat().st_size
            record["status"] = "passed"
            print("PASS", name, record["rtl_bytes"], "bytes", flush=True)
        except Exception as error:
            record.update(status="failed", error=str(error))
            failures.append(name)
            print("FAIL", name, error, flush=True)
        record["seconds"] = round(time.monotonic() - start, 2)
        write_manifest(root, manifest)
    print("SUMMARY", len(selected) - len(failures), "passed;", failures, flush=True)
    return bool(failures)


if __name__ == "__main__":
    raise SystemExit(main())
