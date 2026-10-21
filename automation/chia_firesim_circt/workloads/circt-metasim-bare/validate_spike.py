#!/usr/bin/env python3
"""Execute the portable Rocket tests on Spike and retain logs and reference results."""
import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path
from check_uart import GROUPS, validate


def main():
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spike", default=str(root.parents[3] / ".conda-env/riscv-tools/bin/spike"))
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--binary", action="append", help="restrict validation to named binaries")
    args = parser.parse_args()
    names = args.binary or ["circt-fame-" + group for group in GROUPS if group != "uart"] + ["circt-fame-suite"]
    output = root / "spike-results"
    output.mkdir(exist_ok=True)
    manifest = {"isa": "rv64gc_zicntr_zihpm", "binaries": {}, "sha256": {},
                "excluded": ["circt-fame-uart", "circt-fame-rocket-suite"]}
    failed = False
    for name in names:
        if name not in ["circt-fame-" + group for group in GROUPS] + ["circt-fame-suite", "circt-fame-rocket-suite"]:
            parser.error(f"unknown binary: {name}")
        binary = root / name
        command = [args.spike, "--isa=rv64gc_zicntr_zihpm", "--pmpregions=8", "-m64", str(binary)]
        try:
            env = dict(os.environ)
            env["PATH"] = os.pathsep.join([str(Path(args.spike).resolve().parent),
                str(root.parents[3] / ".conda-env/bin"), env.get("PATH", "")])
            run = subprocess.run(command, capture_output=True, timeout=args.timeout, cwd=root, env=env)
            transcript = (run.stdout + run.stderr).decode(errors="replace")
            (output / (name + ".log")).write_text(transcript)
            if run.returncode:
                raise ValueError(f"Spike exit {run.returncode}")
            result = validate(transcript)
            manifest["binaries"][name] = result
            manifest["sha256"][name] = hashlib.sha256(binary.read_bytes()).hexdigest()
            print(f"PASS {name}: {result['checks']} checks {result['signature']}", flush=True)
        except (ValueError, subprocess.TimeoutExpired) as error:
            print(f"FAIL {name}: {error}", flush=True)
            failed = True
    (output / "expected-results.json").write_text(json.dumps(manifest, indent=2) + "\n")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
