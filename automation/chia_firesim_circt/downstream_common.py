"""Shared input validation for the real Jasper and FireSim adapters."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml
import verification_nodes as gate


def record(path):
    path = Path(path).resolve()
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "bytes": path.stat().st_size}


def inputs(repo, target):
    repo = Path(repo).resolve()
    config = yaml.safe_load((repo / "automation/chia_firesim_circt/verification.yaml").read_text())
    golden = gate._sfc_receipt(config, repo, target)
    candidate = gate._circt_receipt(config, repo, target)
    if golden is None or candidate is None:
        raise ValueError(f"{target}: current complete SFC and CIRCT receipts required")
    def files(receipt):
        return [Path(path) if Path(path).is_absolute() else repo / path for path in receipt[0]]
    def rtl(paths, root, kind):
        # Candidate provenance includes the archived SFC RTL as well as its own
        # output. Select only the validated output subtree, never the first
        # inventory member that happens to share the generated basename.
        root = gate._path(repo, root).resolve() / target
        matches = [path.resolve() for path in paths if path.name == "FireSim-generated.sv"
                   and path.parent.name == kind and path.resolve().is_relative_to(root)]
        if len(matches) != 1:
            raise ValueError("expected exactly one complete generated simulator RTL")
        return matches[0].resolve()
    sfc, circt = files(golden), files(candidate)
    recipes = yaml.safe_load((repo / config["recipes_file"]).read_text())
    recipe_name = gate.FIXED_TARGETS[target]
    return {"config": config, "recipe_name": recipe_name, "recipe": recipes[recipe_name],
            "sfc": sfc, "circt": circt,
            "sfc_rtl": rtl(sfc, config["semantic"]["preparation"]["golden_root"], "sfc"),
            "circt_rtl": rtl(circt, config["semantic"]["candidate_preparation"]["candidate_root"], "circt")}


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
