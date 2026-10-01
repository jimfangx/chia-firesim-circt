#!/usr/bin/env python3
"""Create FireSim's transient runtime configuration for the metasim gate."""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    runtime = yaml.safe_load(args.source.read_text())
    if not isinstance(runtime, dict) or "metasimulation" not in runtime:
        raise SystemExit(f"Not a FireSim runtime config: {args.source}")
    runtime["metasimulation"]["metasimulation_enabled"] = True
    args.output.write_text(yaml.safe_dump(runtime, sort_keys=False))
    print(f"Wrote FireSim metasim runtime configuration: {args.output}")


if __name__ == "__main__":
    main()
