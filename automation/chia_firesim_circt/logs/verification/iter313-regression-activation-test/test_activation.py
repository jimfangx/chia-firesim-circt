import ast
import hashlib
import tempfile
import unittest
from pathlib import Path

HERE = Path('/scratch/jfx/fsim-circt/automation/chia_firesim_circt')
tree = ast.parse((HERE / 'loop.py').read_text())
nodes=[]
for node in tree.body:
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        if any(a.name.split('.')[0] in {'ray', 'chia'} for a in node.names) or (isinstance(node, ast.ImportFrom) and node.module.startswith('chia')):
            continue
        nodes.append(node)
    elif isinstance(node, ast.Assign): nodes.append(node)
    elif isinstance(node, ast.ClassDef) and node.name == 'CommandResult': nodes.append(node)
    elif isinstance(node, ast.FunctionDef) and node.name == 'verify_with_firesim':
        node.decorator_list=[]
        nodes.append(node)
ns={}
exec(compile(ast.Module(body=nodes, type_ignores=[]), str(HERE/'loop.py'), 'exec'), ns)

class ActivationTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=HERE/'logs/verification/iter313-regression-activation-test')
        self.root=Path(self.tmp.name)
        ns['AUTOMATION']=self.root
        self.source=self.root/'loop.py'
    def tearDown(self): self.tmp.cleanup()
    def write_source(self, version):
        self.source.write_text(f"def _verify_with_firesim(mode, run_id, run_hardware_gate):\n    return [{{'name': 'version{version}', 'returncode': 0, 'mode': mode, 'run_id': run_id, 'hardware': run_hardware_gate}}]\n")
    def test_same_loaded_wrapper_observes_source_edit(self):
        wrapper=ns['verify_with_firesim']
        self.write_source(1)
        first=wrapper('full','iter313',False)
        self.assertEqual(first[-1]['name'],'version1')
        self.write_source(2)
        second=wrapper('full','iter314',True)
        self.assertEqual(second[-1]['name'],'version2')
        self.assertEqual(second[-1]['run_id'],'iter314')
        self.assertTrue(second[-1]['hardware'])
        self.assertEqual(second[0]['name'],'verification_source')
        self.assertIn(hashlib.sha256(self.source.read_bytes()).hexdigest(),second[0]['stdout'])
        self.assertNotEqual(first[0]['stdout'],second[0]['stdout'])
    def test_none_keeps_empty_feedback(self):
        self.source.write_text("def _verify_with_firesim(*args): return []\n")
        self.assertEqual(ns['verify_with_firesim']('none','iter313'),[])
    def test_import_does_not_start_main(self):
        self.write_source(1)
        with self.source.open('a') as f:
            f.write("if __name__ == '__main__': raise AssertionError('started driver')\n")
        self.assertEqual(ns['verify_with_firesim']('compile','iter313')[-1]['mode'],'compile')
    def test_dispatch_failure_propagates(self):
        self.source.write_text("def _verify_with_firesim(*args): raise RuntimeError('fail closed')\n")
        with self.assertRaisesRegex(RuntimeError,'fail closed'):
            ns['verify_with_firesim']('full','iter313',True)

if __name__ == '__main__': unittest.main(verbosity=2)
