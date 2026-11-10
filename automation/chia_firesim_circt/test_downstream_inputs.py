"""Select actual compiler outputs from provenance-inclusive inventories."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml
import downstream_common as common


class DownstreamInputsTest(unittest.TestCase):
    def test_candidate_provenance_does_not_select_or_conflict_with_sfc_rtl(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            config = {"recipes_file": "recipes.yaml", "semantic": {
                "preparation": {"golden_root": str(repo / "goldens")},
                "candidate_preparation": {"candidate_root": str(repo / "candidates")}}}
            path = repo / "automation/chia_firesim_circt/verification.yaml"
            path.parent.mkdir(parents=True)
            path.write_text(yaml.safe_dump(config))
            recipe = common.gate.FIXED_TARGETS["rocket"]
            (repo / "recipes.yaml").write_text(yaml.safe_dump({recipe: {"TARGET_CONFIG": "RocketConfig"}}))
            golden = repo / "goldens/rocket/attempt-a/sfc/FireSim-generated.sv"
            candidate = repo / "candidates/rocket/attempt-b/circt/FireSim-generated.sv"
            # Ordering is deliberate: never silently choose the golden input.
            with patch.object(common.gate, "_sfc_receipt", return_value=([str(golden)], set())), \
                 patch.object(common.gate, "_circt_receipt", return_value=([str(golden), str(candidate)], set())) as native:
                result = common.inputs(repo, "rocket")
                self.assertEqual(result["sfc_rtl"], golden)
                self.assertEqual(result["circt_rtl"], candidate)
                for bad in ([str(golden)], [str(candidate), str(repo / "candidates/rocket/attempt-c/circt/FireSim-generated.sv")]):
                    native.return_value = (bad, set())
                    with self.assertRaisesRegex(ValueError, "exactly one"):
                        common.inputs(repo, "rocket")


if __name__ == "__main__":
    unittest.main()
