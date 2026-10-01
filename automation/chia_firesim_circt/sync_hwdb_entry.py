#!/usr/bin/env python3
"""Install FireSim's generated U250 HWDB stanza after a successful build."""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deploy-dir", type=Path, required=True)
    parser.add_argument("--name", required=True)
    args = parser.parse_args()

    generated = args.deploy_dir / "built-hwdb-entries" / args.name
    hwdb_path = args.deploy_dir / "config_hwdb.yaml"
    if not generated.is_file():
        raise SystemExit(f"FireSim did not create the expected HWDB entry: {generated}")

    entry = yaml.safe_load(generated.read_text())
    if not isinstance(entry, dict) or set(entry) != {args.name}:
        raise SystemExit(f"Unexpected generated HWDB entry in {generated}")
    if not entry[args.name].get("bitstream_tar", "").startswith("file://"):
        raise SystemExit(f"Generated entry does not name a local bitstream: {generated}")

    hwdb = yaml.safe_load(hwdb_path.read_text()) or {}
    hwdb[args.name] = entry[args.name]
    hwdb_path.write_text(yaml.safe_dump(hwdb, sort_keys=False))
    print(f"Installed {args.name} from {generated} into {hwdb_path}")


if __name__ == "__main__":
    main()
