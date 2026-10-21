#!/usr/bin/env python3
"""Generate FireSim workload descriptors here without changing the live Chia config."""
import hashlib
import json
from pathlib import Path
from check_uart import GROUPS


def main():
    root = Path(__file__).resolve().parent
    output = root / "firesim-workloads"
    output.mkdir(exist_ok=True)
    dummy = root.parents[3] / "software/firemarshal/boards/default/installers/firesim/dummy.rootfs"
    if not dummy.is_file():
        raise SystemExit(f"Missing FireMarshal dummy rootfs: {dummy}")
    binaries = ["circt-fame-suite", "circt-fame-rocket-suite"] + ["circt-fame-" + name for name in GROUPS]
    inventory = {}
    for name in binaries:
        binary = root / name
        data = binary.read_bytes()
        if data[:6] != b"\x7fELF\x02\x01":
            raise SystemExit(f"Not a little-endian ELF64 binary: {binary}")
        manifest = {"benchmark_name": name, "common_simulation_outputs": ["uartlog"],
                    "common_bootbinary": str(binary), "common_rootfs": str(dummy)}
        (output / (name + ".json")).write_text(json.dumps(manifest, indent=2) + "\n")
        inventory[name] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
    (root / "binary-inventory.json").write_text(json.dumps(inventory, indent=2) + "\n")
    print(f"Prepared {len(binaries)} FireSim descriptors and binary-inventory.json")


if __name__ == "__main__":
    main()
