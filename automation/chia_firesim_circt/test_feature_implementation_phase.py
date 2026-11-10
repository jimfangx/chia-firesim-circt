"""Feature-phase scheduling/readiness checks without Ray, models or FPGA jobs."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import yaml

import feature_implementation_readiness as readiness
import loop


class FeatureReadinessTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repo = Path(self.temporary.name)
        automation = self.repo / 'automation/chia_firesim_circt'
        automation.mkdir(parents=True)
        scope = Path(__file__).with_name('feature_scope.yaml')
        (automation / scope.name).write_bytes(scope.read_bytes())
        self.features = yaml.safe_load(scope.read_text())['features']
        self.output = self.repo / 'artifacts'
        self.output.mkdir()
        self.report = self.output / 'readiness.json'
        self.identity = readiness.source_identity(self.repo)
        compiler = self.artifact('compiler', b'compiler fixture')
        data = {'schema_version': 1, 'source_identity': self.identity, 'remaining': [],
                'compiler': compiler, 'features': {}, 'cases': []}
        for feature, value in self.features.items():
            data['features'][feature] = {'implemented': True, 'production_integrated': True, 'remaining': []}
            for case_id in value['representative_cases']:
                case = {'case_id': case_id, 'feature': feature,
                        'artifacts': {role: self.artifact(case_id + role, role.encode())
                                      for role in readiness.ROLES}, 'commands': []}
                for purpose in readiness.PURPOSES:
                    command = {'argv': ['fixture', purpose], 'source_identity': self.identity, 'case_id': case_id,
                               'returncode': 0, 'timed_out': False, 'purpose': purpose,
                               'stdout': self.artifact(case_id + purpose + 'stdout', b'checked'),
                               'stderr': self.artifact(case_id + purpose + 'stderr', b'')}
                    case['commands'].append(self.artifact(case_id + purpose, json.dumps(command).encode()))
                data['cases'].append(case)
        self.data = data
        self.save()

    def artifact(self, name, payload):
        path = self.output / name
        path.write_bytes(payload)
        return {'path': str(path), 'sha256': hashlib.sha256(payload).hexdigest()}

    def save(self):
        self.report.write_text(json.dumps(self.data))

    def validate(self):
        return readiness.validate_readiness(self.report, self.repo, 0)

    def test_all_seven_ready_cases_never_imply_verification(self):
        result = self.validate()
        self.assertTrue(result['implementation_ready'])
        self.assertFalse(result['verified_complete'])
        self.assertEqual(result['representative_cases'], 7)

    def test_missing_case_or_executable_role_rejects(self):
        complete = json.dumps(self.data)
        self.data['cases'].pop()
        self.save()
        with self.assertRaisesRegex(ValueError, 'Every bounded'):
            self.validate()
        self.data = json.loads(complete)
        self.data['cases'][0]['artifacts'].pop('execution_adapter')
        self.save()
        with self.assertRaisesRegex(ValueError, 'Case must bind'):
            self.validate()

    def test_failed_or_missing_driver_command_rejects(self):
        case = self.data['cases'][0]
        case['commands'] = [record for record in case['commands']
                            if json.loads(Path(record['path']).read_text())['purpose'] != 'driver_build']
        self.save()
        with self.assertRaisesRegex(ValueError, 'driver build'):
            self.validate()
        failed = {'argv': ['build'], 'source_identity': self.identity,
                  'returncode': 1, 'timed_out': False}
        case['commands'].append(self.artifact('failed-command', json.dumps(failed).encode()))
        self.save()
        with self.assertRaisesRegex(ValueError, 'must have succeeded'):
            self.validate()

    def test_stale_artifact_source_and_previous_turn_report_reject(self):
        record = self.data['cases'][0]['artifacts']['rtl']
        Path(record['path']).write_text('changed')
        with self.assertRaisesRegex(ValueError, 'Stale'):
            self.validate()
        with self.assertRaisesRegex(ValueError, 'fresh'):
            readiness.validate_readiness(self.report, self.repo, time.time_ns() + 1)
        source = self.repo / 'sims/firesim/sim/goldengate-circt/new.cpp'
        source.parent.mkdir(parents=True)
        source.write_text('changed compiler')
        with self.assertRaisesRegex(ValueError, 'current compiler'):
            self.validate()

    def test_remaining_integration_gaps_reject(self):
        self.data['features']['fame5']['remaining'] = ['production grouping']
        self.save()
        with self.assertRaisesRegex(ValueError, 'production integration'):
            self.validate()

    def test_successful_command_for_another_case_cannot_supply_readiness(self):
        self.data['cases'][0]['commands'] = self.data['cases'][1]['commands']
        self.save()
        with self.assertRaisesRegex(ValueError, 'must have succeeded'):
            self.validate()


class FeaturePhaseSchedulingTest(unittest.TestCase):
    def test_phase_prompt_keeps_formal_off_and_background_snapshot_boundary(self):
        with patch.object(loop, '_workspace_snapshot', return_value='fixture'):
            text = loop._agent_prompt('diagnostics', 1, 3, 'model', 'feature-implementation', Path('/tmp/readiness.json'))
        self.assertIn('Do not launch Jasper', text)
        self.assertIn('independent background job', text)
        self.assertIn('all seven bounded cases', text)
        self.assertIn('/tmp/readiness.json', text)
        self.assertIn('LOOP_COMPLETE: no', text)
        self.assertLess(len(text), loop.PROMPT_MAX_CHARS)

    def test_semantic_failure_never_dispatches_fpga_or_readiness(self):
        with patch.object(loop, 'verify_semantic') as semantic, \
             patch.object(loop, 'get', return_value={'ok': False, 'status': 'failed'}), \
             patch.object(loop.runpy, 'run_path') as loader:
            result, reports = loop._feature_implementation_iteration({}, 'run', '', Path('/tmp/report'), 0)
        self.assertFalse(result['implementation_ready'])
        self.assertFalse(result['formal_dispatched'])
        self.assertEqual(len(reports), 1)
        loader.assert_not_called()

    def test_semantic_success_launches_frozen_background_without_waiting(self):
        passed = {'ok': True, 'status': 'passed', 'identity': 'candidate'}
        launch = Mock(return_value={'job_id': 'background-f2', 'snapshot_dir': '/frozen'})
        with patch.object(loop, 'verify_semantic'), patch.object(loop, 'get', return_value=passed) as get, \
             patch.object(loop.runpy, 'run_path', return_value={'launch': launch}):
            result, _ = loop._feature_implementation_iteration({}, 'run', 'LOOP_COMPLETE: yes', Path('/tmp/report'), 0)
        self.assertEqual(get.call_count, 1)  # Only the semantic task is joined.
        launch.assert_called_once_with({}, 'run', passed, loop.REPO)
        self.assertFalse(result['implementation_ready'])
        self.assertFalse(result['verified_complete'])
        self.assertEqual(result['background_f2']['job_id'], 'background-f2')

    def test_bare_feature_declaration_cannot_finish_phase(self):
        with patch.object(loop.runpy, 'run_path', return_value={
                'validate_readiness': Mock(side_effect=ValueError('missing artifacts'))}):
            result = loop._feature_implementation_result(
                'FEATURE_IMPLEMENTATION_COMPLETE: yes', Path('/tmp/report'), 0)
        self.assertFalse(result['implementation_ready'])
        self.assertIn('missing artifacts', result['reason'])

    def test_main_continues_feature_turns_and_never_dispatches_full_or_legacy_graph(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'verification.yaml').write_text('{}')
            prompt = Mock()
            prompt.options.return_value = prompt
            prompt.chia_remote.return_value = SimpleNamespace(
                returncode=0, success=True, result='LOOP_COMPLETE: yes', stderr='')
            agent = SimpleNamespace(prompt=prompt, model=None)
            results = [({'implementation_ready': False, 'verified_complete': False}, []),
                       ({'implementation_ready': True, 'verified_complete': False}, [])]
            with patch.object(loop, 'AUTOMATION', root), \
                 patch.object(loop.sys, 'argv', ['loop.py', '--verify', 'feature-implementation',
                                                '--iterations', '2', '--verification-config', str(root / 'verification.yaml')]), \
                 patch.object(loop, 'StreamingCodexLLM', return_value=agent), \
                 patch.object(loop, '_live_model', return_value='fixture-model'), \
                 patch.object(loop, '_workspace_snapshot', return_value='fixture'), \
                 patch.object(loop, 'get', side_effect=lambda value: value), \
                 patch.object(loop, '_feature_implementation_iteration', side_effect=results) as phase, \
                 patch.object(loop, '_verification_graph', side_effect=AssertionError('full graph forbidden')), \
                 patch.object(loop, 'verify_with_firesim') as legacy, \
                 patch.object(loop.ray, 'init'), patch.object(loop.ray, 'shutdown'), \
                 patch.object(loop, 'start_collector'), contextlib.redirect_stdout(io.StringIO()) as output:
                loop.main()
            self.assertEqual(phase.call_count, 2)
            self.assertEqual(prompt.chia_remote.call_count, 2)
            legacy.chia_remote.assert_not_called()
            self.assertIn('Full semantic/formal/F2/speed acceptance remains incomplete', output.getvalue())
            receipts = list((root / 'logs/feature-implementation').glob('*/phase-result.json'))
            self.assertEqual(len(receipts), 2)
            self.assertTrue(all(json.loads(p.read_text())['verified_complete'] is False for p in receipts))


if __name__ == '__main__':
    unittest.main()
