"""Explicit, immutable feature inputs for full-design Jasper proofs.

This is an alternative to the baseline receipt lookup, never a fallback. It
binds both compiler outputs to one elaboration inventory and feature profile;
it establishes neither equivalence nor an initial-memory relation.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re


def input_identity(records):
    """Stable identity of the shared elaboration input inventory."""
    canonical = sorted(records, key=lambda item: item["path"])
    return hashlib.sha256(json.dumps(canonical, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


def _object(value, fields, context):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ValueError(f"{context}: expected fields {sorted(fields)}")
    return value


def _name(value, context):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context}: nonempty string required")
    return value


def _artifact(root, value):
    _object(value, {"path", "sha256", "bytes"}, "artifact")
    name = value["path"]
    if not isinstance(name, str) or not name or "\x00" in name:
        raise ValueError("artifact path must be a canonical root-relative path")
    relative = Path(name)
    if relative.is_absolute() or ".." in relative.parts or relative.as_posix() != name:
        raise ValueError("artifact path must be a canonical root-relative path")
    path = root
    for component in relative.parts:
        path = path / component
        if path.is_symlink():
            raise ValueError("artifact symlink is not permitted: " + name)
    if not path.is_file() or not path.resolve().is_relative_to(root):
        raise ValueError("artifact missing or outside manifest root: " + name)
    if (not isinstance(value["sha256"], str)
            or not re.fullmatch("[0-9a-f]{64}", value["sha256"])
            or type(value["bytes"]) is not int or value["bytes"] < 0):
        raise ValueError("invalid artifact digest or byte count: " + name)
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    if path.stat().st_size != value["bytes"] or digest.hexdigest() != value["sha256"]:
        raise ValueError("artifact hash/bytes mismatch: " + name)
    return path


def _inventory(root, values, context):
    if not isinstance(values, list) or not values:
        raise ValueError(context + ": nonempty artifact inventory required")
    paths = [_artifact(root, value) for value in values]
    if len(set(paths)) != len(paths):
        raise ValueError(context + ": duplicate artifact path")
    return paths


def load_feature_inputs(manifest_path, target, profile, expected_identity=None):
    """Validate a snapshotted feature case; never consult baseline receipts."""
    manifest_path = Path(manifest_path)
    if manifest_path.is_symlink():
        raise ValueError("input manifest must not be a symlink")
    manifest_path = manifest_path.resolve(strict=True)
    root = manifest_path.parent
    manifest = json.loads(manifest_path.read_text())
    _object(manifest, {"schema_version", "target", "profile", "recipe_name", "recipe",
                       "input_design_identity", "input_artifacts", "sfc", "circt"}, "manifest")
    if type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1:
        raise ValueError("unsupported feature-input manifest schema")
    if manifest["target"] != _name(target, "target"):
        raise ValueError("feature input target mismatch")
    if manifest["profile"] != _name(profile, "profile"):
        raise ValueError("feature input profile mismatch")
    _name(manifest["recipe_name"], "recipe_name")
    recipe = manifest["recipe"]
    if (not isinstance(recipe, dict) or recipe.get("PLATFORM_CONFIG") != profile
            or not isinstance(recipe.get("TARGET_CONFIG"), str) or not recipe["TARGET_CONFIG"].strip()):
        raise ValueError("feature recipe/profile mismatch or missing TARGET_CONFIG")
    shared = _inventory(root, manifest["input_artifacts"], "input_artifacts")
    identity = input_identity(manifest["input_artifacts"])
    if manifest["input_design_identity"] != identity:
        raise ValueError("shared input design identity mismatch")
    if expected_identity is not None and expected_identity != identity:
        raise ValueError("requested input design identity mismatch")
    result = {"config": {}, "recipe_name": manifest["recipe_name"], "recipe": recipe,
              "profile": profile, "input_design_identity": identity,
              "input_artifacts": shared, "input_manifest": manifest_path}
    for side in ("sfc", "circt"):
        entry = manifest[side]
        fields = {"profile", "input_design_identity", "rtl", "artifacts"}
        if side == "circt":
            fields.add("memory_origins")
        _object(entry, fields, side)
        if entry["profile"] != profile:
            raise ValueError(side + ": feature profile mismatch")
        if entry["input_design_identity"] != identity:
            raise ValueError(side + ": input design identity mismatch")
        artifacts = _inventory(root, entry["artifacts"], side + " artifacts")
        rtl = _artifact(root, entry["rtl"])
        if rtl not in artifacts or entry["rtl"] not in entry["artifacts"]:
            raise ValueError(side + ": RTL missing from exact artifact inventory")
        result[side], result[side + "_rtl"] = artifacts, rtl
        if side == "circt":
            origins = _artifact(root, entry["memory_origins"])
            if (origins != rtl.with_suffix(".memory-origins.json") or origins not in artifacts
                    or entry["memory_origins"] not in entry["artifacts"]):
                raise ValueError("circt: native memory origins must accompany the exact RTL")
    if result["sfc_rtl"] == result["circt_rtl"]:
        raise ValueError("SFC and CIRCT RTL must be distinct artifacts")
    return result
