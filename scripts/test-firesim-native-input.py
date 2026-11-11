#!/usr/bin/env python3
"""Exercise FireSim's Chipyard make handoff with an isolated fake generator."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

RULES = Path(__file__).resolve().parents[1] / 'generators/firechip/chip/src/main/makefrag/firesim/build.mk'


class NativeInputTest(unittest.TestCase):
    def test_native_emission_replaces_legacy_symlink(self):
        with tempfile.TemporaryDirectory(prefix='firesim-native-input-') as temp:
            root = Path(temp)
            staging = root / 'sims/firesim-staging'
            output = root / 'output'
            generated = staging / 'generated-src/Test'
            generated.mkdir(parents=True)
            output.mkdir()
            (root / 'env.sh').write_text('true\n')
            (staging / 'Makefile').write_text(
                'ifneq ($(EXTRA_CHISEL_OPTIONS),)\n$(error Unexpected legacy emission options)\nendif\n'
                'all:\n\tprintf native > generated-src/Test/Test.fir\n'
                '\tprintf "[]" > generated-src/Test/Test.anno.json\n'
                '\ttouch generated-src/Test/Test.d\n')
            (output / 'input.fir').symlink_to(generated / 'Test.sfc.fir')
            (root / 'Makefile').write_text(
                f'chipyard_dir := {root}\nGENERATED_DIR := {output}\n'
                f'FIRRTL_FILE := {output}/input.fir\nANNO_FILE := {output}/input.json\n'
                'long_name := Test\n.PHONY: run firesim_target_symlink_hook\n'
                'run: $(FIRRTL_FILE) $(ANNO_FILE)\nfiresim_target_symlink_hook:\n'
                f'include {RULES}\n')
            env = dict(os.environ, EXTRA_CHISEL_OPTIONS='--emit-legacy-sfc')
            env.pop('MAKEFLAGS', None)
            env.pop('MFLAGS', None)
            result = subprocess.run(['make', '--no-print-directory', '-f', str(root / 'Makefile'), 'run'],
                                    cwd=root, env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual((output / 'input.fir').resolve(), generated / 'Test.fir')
            self.assertEqual((output / 'input.fir').read_text(), 'native')
            self.assertEqual((output / 'input.json').read_text(), '[]')
            self.assertFalse((generated / 'Test.sfc.fir').exists())


if __name__ == '__main__':
    unittest.main()
