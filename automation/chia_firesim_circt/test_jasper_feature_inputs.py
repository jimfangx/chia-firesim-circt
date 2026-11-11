"""Feature proof input provenance; no proof or remote work is launched."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import jasper_feature_inputs as feature
import run_jasper


class FeatureInputsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.manifest_path = self.root / "proof-inputs.json"
        self.profile = "WithMultiCycleRamModels_BaseF2Config"

        def artifact(path, text):
            file = self.root / path
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(text)
            return {"path": path, "sha256": hashlib.sha256(file.read_bytes()).hexdigest(),
                    "bytes": file.stat().st_size}

        shared = [artifact("inputs/design.legacy.fir", "legacy"),
                  artifact("inputs/design.native.fir", "native"),
                  artifact("inputs/design.anno.json", "[]")]
        identity = feature.input_identity(shared)
        spec = artifact("sfc/FireSim-generated.sv", "module F1Shim; endmodule\n")
        imp = artifact("circt/FireSim-generated.sv", "module F1Shim; wire impl; endmodule\n")
        origins = artifact("circt/FireSim-generated.memory-origins.json", "{}")
        self.manifest = {
            "schema_version": 1, "target": "rocket", "profile": self.profile,
            "recipe_name": "rocket_ram_models", "recipe": {"TARGET_CONFIG": "RocketConfig",
                                                              "PLATFORM_CONFIG": self.profile},
            "input_design_identity": identity, "input_artifacts": shared,
            "sfc": {"profile": self.profile, "input_design_identity": identity,
                    "rtl": spec, "artifacts": [spec]},
            "circt": {"profile": self.profile, "input_design_identity": identity,
                      "rtl": imp, "memory_origins": origins, "artifacts": [imp, origins]}}
        self.save()

    def save(self):
        self.manifest_path.write_text(json.dumps(self.manifest))

    def load(self, **kwargs):
        return feature.load_feature_inputs(self.manifest_path, kwargs.get("target", "rocket"),
                                           kwargs.get("profile", self.profile),
                                           kwargs.get("expected_identity"))

    def test_matching_snapshots_select_full_outputs_and_exact_origins(self):
        design = self.load(expected_identity=self.manifest["input_design_identity"])
        self.assertEqual(design["sfc_rtl"], self.root / "sfc/FireSim-generated.sv")
        self.assertEqual(design["circt_rtl"], self.root / "circt/FireSim-generated.sv")
        self.assertIn(design["circt_rtl"].with_suffix(".memory-origins.json"), design["circt"])
        self.assertEqual(design["recipe"]["PLATFORM_CONFIG"], self.profile)

    def test_wrong_target_and_requested_profile_fail(self):
        for kwargs in ({"target": "shuttle"}, {"profile": "BaseF2Config"}):
            with self.subTest(kwargs=kwargs), self.assertRaisesRegex(ValueError, "mismatch"):
                self.load(**kwargs)

    def test_mismatched_profile_recipe_or_side_fails(self):
        original = copy.deepcopy(self.manifest)
        for side in ("recipe", "sfc", "circt"):
            self.manifest = copy.deepcopy(original)
            self.manifest[side]["PLATFORM_CONFIG" if side == "recipe" else "profile"] = "BaseF2Config"
            self.save()
            with self.subTest(side=side), self.assertRaisesRegex(ValueError, "profile mismatch"):
                self.load()

    def test_shared_or_per_compiler_input_identity_mismatch_fails(self):
        original = copy.deepcopy(self.manifest)
        for side in (None, "sfc", "circt"):
            self.manifest = copy.deepcopy(original)
            (self.manifest if side is None else self.manifest[side])["input_design_identity"] = "0" * 64
            self.save()
            with self.subTest(side=side), self.assertRaisesRegex(ValueError, "input design identity mismatch"):
                self.load()
        self.manifest = original
        self.save()
        with self.assertRaisesRegex(ValueError, "requested input design identity mismatch"):
            feature.load_feature_inputs(self.manifest_path, "rocket", self.profile, "1" * 64)

    def test_every_input_and_output_hash_is_verified(self):
        for relative in ("inputs/design.anno.json", "sfc/FireSim-generated.sv",
                         "circt/FireSim-generated.sv", "circt/FireSim-generated.memory-origins.json"):
            file = self.root / relative
            old = file.read_text()
            file.write_text(old + "tamper")
            with self.subTest(path=relative), self.assertRaisesRegex(ValueError, "hash/bytes mismatch"):
                self.load()
            file.write_text(old)

    def test_missing_origin_and_duplicate_inventory_are_rejected(self):
        self.manifest["circt"]["artifacts"].pop()
        self.save()
        with self.assertRaisesRegex(ValueError, "native memory origins"):
            self.load()
        self.manifest["input_artifacts"].append(self.manifest["input_artifacts"][0])
        self.save()
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.load()

    def test_path_traversal_and_symlinks_are_rejected(self):
        original = copy.deepcopy(self.manifest)
        for path in ("../outside", "/tmp/outside", "inputs/../inputs/design.anno.json"):
            self.manifest = copy.deepcopy(original)
            self.manifest["input_artifacts"][0]["path"] = path
            self.save()
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "root-relative"):
                self.load()
        self.manifest = original
        self.save()
        file = self.root / "inputs/design.anno.json"
        file.unlink()
        file.symlink_to(self.root / "inputs/design.native.fir")
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.load()

    def test_default_lookup_is_preserved_and_explicit_failure_never_falls_back(self):
        parser = run_jasper.build_parser()
        base = ["--repo", str(self.root), "--target", "rocket", "--mode", "lec"]
        with patch.object(run_jasper, "inputs", return_value={"baseline": True}) as lookup:
            self.assertEqual(run_jasper.proof_inputs(parser.parse_args(base)), {"baseline": True})
            lookup.assert_called_once_with(self.root, "rocket")
        args = parser.parse_args(base + ["--input-manifest", str(self.manifest_path),
                                         "--profile", "BaseF2Config"])
        with patch.object(run_jasper, "inputs") as lookup, self.assertRaisesRegex(ValueError, "profile mismatch"):
            run_jasper.proof_inputs(args)
        lookup.assert_not_called()

    def test_invalid_explicit_preflight_never_contacts_remote(self):
        base = ["run_jasper.py", "--repo", str(self.root), "--target", "shuttle", "--mode", "lec",
                "--flow", str(self.root), "--preflight", "--input-manifest", str(self.manifest_path),
                "--profile", self.profile]
        for relative in ("run_sec.py", "tcl/sec_lib.tcl", "scripts/match_state.py"):
            file = self.root / relative
            file.parent.mkdir(exist_ok=True)
            file.touch()
        with patch("sys.argv", base), patch.object(run_jasper.subprocess, "run") as remote:
            with self.assertRaisesRegex(ValueError, "target mismatch"):
                run_jasper.main()
        remote.assert_not_called()


if __name__ == "__main__":
    unittest.main()
