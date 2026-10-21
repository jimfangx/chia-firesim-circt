import ast
import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path('/scratch/jfx/fsim-circt/automation/chia_firesim_circt')
tree = ast.parse((HERE / 'loop.py').read_text())
nodes = []
for node in tree.body:
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        if any(a.name.split('.')[0] in {'ray', 'chia'} for a in node.names) or (isinstance(node, ast.ImportFrom) and node.module.startswith('chia')):
            continue
        nodes.append(node)
    elif isinstance(node, ast.Assign):
        nodes.append(node)
    elif isinstance(node, ast.ClassDef) and node.name == 'CommandResult':
        nodes.append(node)
    elif isinstance(node, ast.FunctionDef) and node.name != 'main':
        node.decorator_list = []
        nodes.append(node)
ns = {}
exec(compile(ast.Module(body=nodes, type_ignores=[]), str(HERE / 'loop.py'), 'exec'), ns)

class DispatchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=HERE / 'logs/verification/iter312-regression-dispatch-test')
        self.root = Path(self.tmp.name)
        self.deploy = self.root / 'sims/firesim/deploy'
        self.deploy.mkdir(parents=True)
        (self.deploy / 'config_runtime.yaml').write_text('metasimulation: {metasimulation_enabled: false, metasimulation_host_simulator: vcs, metasimulation_only_plusargs: "+max-cycles=2"}\nworkload: {}\n')
        generated = self.root / 'sims/firesim/sim/generated-src/xilinx_alveo_u250/xilinx_alveo_u250-firesim-FireSim-FireSimRocketConfig-BaseXilinxAlveoU250Config'
        generated.mkdir(parents=True)
        (generated / 'FireSim-generated.sv').write_text('module FireSim; endmodule')
        (generated / 'AbstractClockGate.v').write_text('clock')
        self.archive = self.root / 'version'
        self.archive.mkdir()
        ns.update(REPO=self.root, DEPLOY=self.deploy)
        self.calls = []
        self.fail = None
        self.initial = True
        def command(name, command, logdir):
            self.calls.append(name)
            logdir.mkdir(parents=True, exist_ok=True)
            if name == 'prepare_rocket_regressions':
                (logdir / 'regression-inputs.json').write_text(json.dumps({'version': 'v1', 'archive': str(self.archive), 'initial_standalones_required': self.initial, 'portable_reference': 'reference.json', 'rocket_differential': 'pending SFC'}))
            return ns['CommandResult'](name, 1 if name == self.fail else 0, '', '')
        def manager(action, logdir, runtime_config=None, name=None, wall_timeout=None):
            name = name or action
            self.calls.append(name)
            if name != 'replacertl':
                self.assertGreater(wall_timeout or 0, 0)
            return ns['CommandResult'](name, 124 if name == self.fail else 0, '', '', 'compile.log')
        ns.update(_run_and_log=command, _run_manager=manager)
        self.logdir = self.root / 'logs'
        self.logdir.mkdir()
    def tearDown(self):
        self.tmp.cleanup()
    def run_case(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return ns['_run_metasim_regressions'](self.logdir, 'iter312-full')
    def test_initial_order_unique_configs_and_limits(self):
        results = self.run_case()
        self.assertEqual(results[-1].name, 'required_rocket_regressions')
        launch = [n.removesuffix('_launchrunfarm') for n in self.calls if n.endswith('_launchrunfarm')]
        self.assertEqual(launch, ['circt-metasim-bare', 'circt-fame-suite', 'circt-fame-rocket-suite'] + ['circt-fame-' + g for g in ns['REGRESSION_GROUPS']])
        self.assertEqual(len([n for n in self.calls if n.endswith('_builddriver')]), 1)
        self.assertEqual(len([n for n in self.calls if n.endswith('_terminaterunfarm')]), 15)
        configs = list(self.logdir.glob('*/runtime.yaml'))
        self.assertEqual(len(configs), 15)
        for path in configs:
            data = ns['yaml'].safe_load(path.read_text())
            self.assertEqual(data['metasimulation']['metasimulation_host_simulator'], 'verilator')
            self.assertIn('+max-cycles=1000000000', data['metasimulation']['metasimulation_only_plusargs'])
            self.assertNotIn('+max-cycles=2', data['metasimulation']['metasimulation_only_plusargs'])
            self.assertEqual(data['workload']['workload_name'], path.parent.name + '.json')
        self.assertTrue((self.archive / 'standalones-passed.json').is_file())
    def test_subsequent_iteration_combined_only(self):
        self.initial = False
        self.run_case()
        self.assertEqual(len([n for n in self.calls if n.endswith('_launchrunfarm')]), 3)
    def test_setup_failure_cleanup_and_stop(self):
        self.fail = 'circt-metasim-bare_infrasetup'
        results = self.run_case()
        self.assertEqual(results[-1].name, 'circt-metasim-bare_terminaterunfarm')
        self.assertNotIn('circt-fame-suite_launchrunfarm', self.calls)
    def test_failed_launch_cleanup(self):
        self.fail = 'circt-metasim-bare_launchrunfarm'
        self.run_case()
        self.assertIn('circt-metasim-bare_terminaterunfarm', self.calls)
    def test_runtime_timeout_cleanup_and_stop(self):
        self.fail = 'circt-fame-suite_runworkload'
        results = self.run_case()
        self.assertTrue(any(r.returncode == 124 for r in results))
        self.assertEqual(results[-1].name, 'circt-fame-suite_terminaterunfarm')
        self.assertNotIn('circt-fame-rocket-suite_launchrunfarm', self.calls)
    def test_validator_failure_cleanup_and_stop(self):
        self.fail = 'circt-fame-suite_validate'
        self.run_case()
        self.assertIn('circt-fame-suite_terminaterunfarm', self.calls)
        self.assertNotIn('circt-fame-rocket-suite_launchrunfarm', self.calls)
    def test_cleanup_failure_prevents_success(self):
        self.fail = 'circt-metasim-bare_terminaterunfarm'
        results = self.run_case()
        self.assertNotIn('required_rocket_regressions', [r.name for r in results])
        self.assertNotIn('circt-fame-suite_launchrunfarm', self.calls)
    def test_exception_after_launch_cleans_and_returns_failure(self):
        manager = ns['_run_manager']
        def throwing(action, *a, **kw):
            if action == 'infrasetup':
                raise OSError('injected manager failure')
            return manager(action, *a, **kw)
        ns['_run_manager'] = throwing
        results = self.run_case()
        self.assertTrue(any(r.name.endswith('_dispatch') and not r.ok for r in results))
        self.assertEqual(results[-1].name, 'circt-metasim-bare_terminaterunfarm')
    def test_full_profile_failures_never_reach_u250(self):
        self.fail = 'circt-fame-suite_validate'
        ns['_prepare_workload'] = lambda name, source, logdir: ns['CommandResult'](name, 0, '', '')
        ns['AUTOMATION'] = self.root
        with contextlib.redirect_stdout(io.StringIO()):
            results = ns['_verify_with_firesim']('full', 'iter312-full', True)
        self.assertTrue(any(r['returncode'] != 0 for r in results))
        self.assertNotIn('buildbitstream', self.calls)
    def test_compile_profile_does_not_mark_regression_pass(self):
        ns['AUTOMATION'] = self.root
        results = ns['_verify_with_firesim']('compile', 'iter312-compile', True)
        self.assertEqual([r['name'] for r in results], ['replacertl', 'verify_circt_replacertl'])

if __name__ == '__main__':
    unittest.main(verbosity=2)
