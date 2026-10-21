#!/usr/bin/env python3
"""Require the expected PASS marker from one tagged FireSim bare-metal run."""

from __future__ import annotations

import argparse
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-root", type=Path, required=True)
    parser.add_argument("--workload-name", required=True)
    parser.add_argument("--suffix-tag", required=True)
    parser.add_argument("--pass-marker", required=True)
    args = parser.parse_args()

    directory_marker = f"-{args.workload_name}-{args.suffix_tag}"
    candidates = sorted(
        path for path in args.results_root.rglob("uartlog")
        if directory_marker in str(path.parent.parent)
    )
    if len(candidates) != 1:
        raise SystemExit(
            f"Expected exactly one UART log for {directory_marker!r}, found: {candidates}"
        )
    uartlog = candidates[0]
    output = uartlog.read_text(errors="replace").replace("\r", "")
    print(f"Checked UART log: {uartlog}")
    print(output[-4000:])
    if args.pass_marker not in output:
        raise SystemExit(f"Missing bare-metal PASS marker {args.pass_marker!r} in {uartlog}")


if __name__ == "__main__":
    main()
