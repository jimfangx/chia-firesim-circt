#!/usr/bin/env python3
"""Local owned-farm recovery controls; no FireSim, SSH or AWS commands."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml

import feature_farm_cleanup as cleanup
from downstream_common import record, write


class FarmCleanupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        self.work = self.root / 'execution'
        self.work.mkdir()
        self.snapshot = self.root / 'snapshot.json'
        self.case_id = 'enabled_case'
        self.name = 'circt_' + self.case_id
        self.tag = 'chia-feature-012345abcdef'
        write(self.snapshot, dict(repo=str(self.repo), case_id=self.case_id))
        self.runtime = dict(run_farm=dict(
            base_recipe=str(self.repo / 'sims/firesim/deploy/run-farm-recipes/aws_ec2.yaml'),
            recipe_arg_overrides=dict(run_farm_tag=self.tag, run_farm_hosts_to_use=[{'f2.6xlarge': 1}])),
            target_config=dict(topology='no_net_config', no_net_num_nodes=1, default_hw_config=self.name),
            metasimulation=dict(metasimulation_enabled=False),
            workload=dict(suffix_tag=self.tag, workload_name=self.tag + '.json'))
        self.write_runtime()
        (self.work / 'build.yaml').write_text(yaml.safe_dump(dict(builds_to_run=[self.name], build_farm=dict(
            recipe_arg_overrides=dict(build_farm_hosts=['localhost'])))))
        (self.work / 'recipes.yaml').write_text(yaml.safe_dump({self.name: dict(PLATFORM='f2')}))
        (self.work / 'candidate-hwdb.yaml').write_text(yaml.safe_dump({self.name: dict(agfi='agfi-0123')}))
        write(self.work / 'frozen-inputs.json', dict(repo=str(self.repo), target=self.case_id))
        self.calls = []

    def write_runtime(self):
        path = self.work / 'runtime.yaml'
        path.write_text(yaml.safe_dump(self.runtime))
        write(self.work / 'runtime-inputs.json', dict(runtime=record(path)))

    def manager(self, repo, work, action, recipes, extra=(), timeout=0, *, failed=False):
        self.calls.append((repo, work, action, recipes, extra, timeout))
        self.assertEqual(action, 'terminaterunfarm')
        self.assertEqual(timeout, 600)
        self.assertEqual(list(extra), ['-c', str(self.work / 'runtime.yaml'),
                                      '-a', str(self.work / 'candidate-hwdb.yaml'), '-q'])
        log = work / 'terminaterunfarm.log'
        log.write_text('actual manager fixture: ' + ('failed' if failed else 'terminated owned farm'))
        tail = cleanup.run_f2.manager_command(repo, work, action, recipes, extra)
        write(work / 'terminaterunfarm-command.json', dict(argv=['fixture-shell', *tail],
            action=action, returncode=1 if failed else 0, timed_out=False,
            stdout=record(log), stderr=record(log)))
        if failed:
            raise ValueError('fixture termination failed')
        return log

    def test_missing_runtime_is_not_launched_without_requiring_repo(self):
        (self.work / 'runtime.yaml').unlink()
        write(self.snapshot, {})
        with patch.object(cleanup.feature_f2, 'run_manager') as manager:
            result = cleanup.recover(self.snapshot)
        self.assertTrue(result['success'])
        self.assertEqual(result['status'], 'not_launched')
        manager.assert_not_called()
        self.assertTrue(Path(result['evidence']).is_file())

    def test_recovery_uses_only_owned_tag_and_retains_actual_command(self):
        with patch.object(cleanup.feature_f2, 'run_manager', side_effect=self.manager):
            result = cleanup.recover(self.snapshot)
        self.assertTrue(result['success'], result)
        self.assertEqual(result['tag'], self.tag)
        self.assertEqual(result['status'], 'cleaned')
        cleanup._bound(result['command'])
        cleanup._bound(result['log'])
        self.assertEqual(len(self.calls), 1)
        self.assertNotIn('passed', result)
        self.assertNotIn('linux_booted', result)

    def test_successful_external_cleanup_is_hash_verified_and_reused(self):
        with patch.object(cleanup.feature_f2, 'run_manager', side_effect=self.manager):
            first = cleanup.recover(self.snapshot)
        with patch.object(cleanup.feature_f2, 'run_manager') as manager:
            reused = cleanup.recover(self.snapshot)
        manager.assert_not_called()
        self.assertTrue(reused['success'])
        self.assertTrue(reused['reused'])
        self.assertEqual(first['command'], reused['command'])

    def test_existing_worker_cleanup_is_verified_without_duplicate_termination(self):
        self.manager(self.repo, self.work, 'terminaterunfarm', self.work / 'recipes.yaml',
                     ['-c', str(self.work / 'runtime.yaml'), '-a', str(self.work / 'candidate-hwdb.yaml'), '-q'], 600)
        with patch.object(cleanup.feature_f2, 'run_manager') as manager:
            result = cleanup.recover(self.snapshot)
        self.assertTrue(result['success'], result)
        self.assertEqual(result['status'], 'already_cleaned')
        manager.assert_not_called()

    def test_corrupt_previous_log_is_preserved_and_requires_new_termination(self):
        with patch.object(cleanup.feature_f2, 'run_manager', side_effect=self.manager):
            cleanup.recover(self.snapshot)
        (self.work / 'terminaterunfarm.log').write_text('retained former log, different from its stale hash')
        with patch.object(cleanup.feature_f2, 'run_manager', side_effect=self.manager):
            result = cleanup.recover(self.snapshot)
        self.assertTrue(result['success'], result)
        self.assertEqual(len(self.calls), 2)
        inventory = json.loads(cleanup._bound(result['previous_evidence']).read_text())
        old_log = next(item['retained'] for item in inventory['files'] if item['original_path'].endswith('.log'))
        self.assertIn('retained former log', cleanup._bound(old_log).read_text())

    def test_failed_termination_stays_failed_with_bound_attempt_receipt(self):
        def failed(*args, **kwargs):
            return self.manager(*args, **kwargs, failed=True)
        with patch.object(cleanup.feature_f2, 'run_manager', side_effect=failed):
            result = cleanup.recover(self.snapshot)
        self.assertFalse(result['success'])
        self.assertIn('fixture termination failed', result['error'])
        receipt = json.loads(cleanup._bound(result['attempt_command']).read_text())
        self.assertEqual(receipt['returncode'], 1)
        self.assertFalse(json.loads(Path(result['evidence']).read_text())['success'])

    def test_misbound_tag_or_non_f2_runtime_cannot_dispatch_cleanup(self):
        mutations = [lambda: self.runtime['run_farm']['recipe_arg_overrides'].update(run_farm_tag='unrelated'),
                     lambda: self.runtime['workload'].update(suffix_tag='chia-feature-deadbeef1234'),
                     lambda: self.runtime['run_farm']['recipe_arg_overrides'].update(run_farm_hosts_to_use=[{'f1.2xlarge': 1}]),
                     lambda: self.runtime['target_config'].update(no_net_num_nodes=2)]
        original = json.loads(json.dumps(self.runtime))
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                self.runtime = json.loads(json.dumps(original))
                mutate()
                self.write_runtime()
                with patch.object(cleanup.feature_f2, 'run_manager') as manager:
                    result = cleanup.recover(self.snapshot)
                self.assertFalse(result['success'], result)
                manager.assert_not_called()

    def test_runtime_change_after_launch_preparation_blocks_cleanup_dispatch(self):
        self.runtime['run_farm']['recipe_arg_overrides']['run_farm_tag'] = 'chia-feature-deadbeef1234'
        self.runtime['workload'].update(suffix_tag='chia-feature-deadbeef1234', workload_name='chia-feature-deadbeef1234.json')
        (self.work / 'runtime.yaml').write_text(yaml.safe_dump(self.runtime))
        with patch.object(cleanup.feature_f2, 'run_manager') as manager:
            result = cleanup.recover(self.snapshot)
        self.assertFalse(result['success'])
        self.assertIn('changed', result['error'])
        manager.assert_not_called()

    def test_misbound_old_command_is_not_reused_for_this_runtime(self):
        self.manager(self.repo, self.work, 'terminaterunfarm', self.work / 'recipes.yaml',
                     ['-c', str(self.work / 'runtime.yaml'), '-a', str(self.work / 'candidate-hwdb.yaml'), '-q'], 600)
        path = self.work / 'terminaterunfarm-command.json'
        old = json.loads(path.read_text())
        old['argv'][old['argv'].index('-c') + 1] = str(self.root / 'another-runtime.yaml')
        write(path, old)
        with patch.object(cleanup.feature_f2, 'run_manager', side_effect=self.manager):
            result = cleanup.recover(self.snapshot)
        self.assertTrue(result['success'], result)
        self.assertEqual(result['status'], 'cleaned')
        self.assertEqual(len(self.calls), 2)
        self.assertIn('previous_evidence', result)

    def test_symlinked_runtime_cannot_redirect_cleanup_to_another_farm(self):
        original = self.work / 'runtime.yaml'
        other = self.root / 'other-runtime.yaml'
        original.rename(other)
        original.symlink_to(other)
        self.write_runtime()
        with patch.object(cleanup.feature_f2, 'run_manager') as manager:
            result = cleanup.recover(self.snapshot)
        self.assertFalse(result['success'])
        self.assertIn('belong to this snapshot', result['error'])
        manager.assert_not_called()


if __name__ == '__main__':
    unittest.main()
