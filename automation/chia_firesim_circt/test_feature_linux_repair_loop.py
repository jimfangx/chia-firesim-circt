"""Check all-seven dispatch, current-evidence gates and automatic repair turns."""
import argparse
import ast
import copy
from datetime import datetime, timezone
import fcntl
import json
from pathlib import Path
import re
from types import SimpleNamespace
import tempfile
import time
import unittest
from unittest.mock import patch

import yaml

SOURCE = Path(__file__).with_name('loop.py')
TREE = ast.parse(SOURCE.read_text())


def definitions(names):
    nodes = [node for node in TREE.body if
             (isinstance(node, ast.FunctionDef) and node.name in names) or
             (isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in names for t in node.targets))]
    return compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec')


class FeatureLinuxRepairTest(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.ns = {}
        exec(definitions({'NIC_REPAIR_CASES', 'NIC_BASELINE_TARGETS', 'FEATURE_LINUX_REPAIR_CASES',
                          '_feature_linux_repair_graph', '_feature_linux_repair_complete'}), self.ns)
        self.cases = sorted(self.ns['FEATURE_LINUX_REPAIR_CASES'])
        self.config = {'feature_linux': {'cases': self.cases}}
        self.semantic = dict(node='semantic', identity='current', status='passed', ok=True,
            details=[dict(target=t, ok=True, status='passed') for t in self.ns['NIC_BASELINE_TARGETS']])
        self.linux = dict(node='feature_linux', identity='current', status='passed', ok=True, passed=True,
            workers_joined=True, full_acceptance=False, formal_claim=False, selected_cases=self.cases,
            required_cases=list(self.cases), remaining_cases=[], full_matrix=True,
            details=[dict(case_id=c, ok=True, status='passed', result={'path': '/retained/result.json'},
                          feature_checks=[{'path': '/retained/check.json'}]) for c in self.cases])
        self.reports = [self.semantic, self.linux]
        self.worker_failure = False
        owner = self

        class Node:
            def __init__(self, name): self.name = name
            def chia_remote(self, *args, **kwargs):
                owner.events.append(('dispatch', self.name))
                return self.name

        def get(task):
            self.events.append(('join', task))
            if task == 'linux' and self.worker_failure:
                raise RuntimeError('lost worker')
            return copy.deepcopy(self.semantic if task == 'semantic' else self.linux)

        self.ns.update(get=get, verify_semantic=Node('semantic'), verify_feature_linux=Node('linux'))

    def graph(self): return self.ns['_feature_linux_repair_graph'](self.config, 'current')
    def complete(self, reports): return self.ns['_feature_linux_repair_complete'](reports)

    def test_set_is_exactly_the_seven_declared_representatives(self):
        registry = yaml.safe_load(SOURCE.with_name('feature_cases.yaml').read_text())['cases']
        self.assertEqual(set(self.cases), set(registry))
        self.assertEqual({registry[c]['feature'] for c in self.cases},
                         {'fame5', 'autocounter_print', 'multiclock_models', 'nic'})

    def test_only_four_baseline_then_all_feature_linux_nodes_dispatch_and_join(self):
        self.assertTrue(self.complete(self.graph()))
        self.assertEqual(self.events,
                         [('dispatch','semantic'),('join','semantic'),('dispatch','linux'),('join','linux')])

    def test_semantic_failure_does_not_dispatch_fpga(self):
        self.semantic.update(ok=False, status='failed')
        self.assertFalse(self.complete(self.graph()))
        self.assertEqual(self.events, [('dispatch','semantic'),('join','semantic')])

    def test_partial_nic_only_duplicate_unknown_cases_reject_before_dispatch(self):
        for cases in [sorted(self.ns['NIC_REPAIR_CASES']), self.cases[:-1],
                      self.cases+[self.cases[0]], self.cases[:-1]+[self.cases[0]], ['unknown']*7, None]:
            self.config['feature_linux']['cases'] = cases
            self.assertFalse(self.complete(self.graph()))
        self.assertEqual(self.events, [])

    def test_partial_or_stale_runtime_cannot_pass_full_matrix_gate(self):
        changes = [lambda d:d.update(full_matrix=False), lambda d:d.update(remaining_cases=['missing']),
                   lambda d:d['required_cases'].pop(), lambda d:d['selected_cases'].pop(),
                   lambda d:d['details'].pop(), lambda d:d['details'].__setitem__(0,d['details'][1]),
                   lambda d:d.update(identity='old'), lambda d:d.update(workers_joined=False),
                   lambda d:d.update(passed=False), lambda d:d['details'][0].update(ok=False),
                   lambda d:d['details'][0].pop('feature_checks'), lambda d:d['details'][0].pop('result'),
                   lambda d:d.update(formal_claim=True), lambda d:d.update(full_acceptance=True)]
        for change in changes:
            reports=copy.deepcopy(self.reports)
            change(reports[1])
            self.assertFalse(self.complete(reports))
        reports=copy.deepcopy(self.reports)
        reports[0]['details'][0]['target']='largeboom'
        self.assertFalse(self.complete(reports))

    def test_malformed_reports_cannot_complete(self):
        for reports in (None, {}, [None, self.linux], [self.semantic, {}]):
            self.assertFalse(self.complete(reports))
        for field in ('details', 'selected_cases', 'required_cases'):
            reports=copy.deepcopy(self.reports)
            reports[1][field]=None
            self.assertFalse(self.complete(reports))

    def test_worker_loss_or_unknown_join_blocks_mutation(self):
        self.worker_failure=True
        reports=self.graph()
        self.assertFalse(self.complete(reports))
        self.assertIs(reports[-1]['workers_joined'],False)
        self.worker_failure=False
        del self.linux['workers_joined']
        self.assertIs(self.graph()[-1]['workers_joined'],False)

    def run_main(self, graphs, iterations=2, verification_first=True):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            config=root/'verification.yaml'
            config.write_text(yaml.safe_dump(self.config))
            events=self.events
            remaining=iter(graphs)
            owner=self

            class Agent:
                def __init__(self,**kwargs): self.model=kwargs['model']; self.prompt=self
                def options(self,**kwargs): return self
                def chia_remote(self,*args):
                    events.append(('implementation','codex'))
                    return SimpleNamespace(result='FEATURE_IMPLEMENTATION_COMPLETE: yes\nLOOP_COMPLETE: yes',stderr='')

            def graph(*args):
                events.append(('verification','all-seven'))
                return copy.deepcopy(next(remaining))

            ns=dict(self.ns,get=lambda task:task,argparse=argparse,Path=Path,
                os=SimpleNamespace(environ={}, getpid=lambda:12345),fcntl=fcntl,
                ray=SimpleNamespace(init=lambda **kw:None,shutdown=lambda:events.append(('shutdown','ray'))),
                start_collector=lambda **kw:None,StreamingCodexLLM=Agent,AUTOMATION=root,REPO=root,
                json=json,yaml=yaml,datetime=datetime,timezone=timezone,time=time,
                CodexQueryResult=lambda result,*a,**kw:SimpleNamespace(result=result,stderr=''),
                _live_model=lambda model:model,_agent_prompt=lambda *a:'Repair integrated features',
                _require_codex_success=lambda r:None,_feature_linux_repair_graph=graph,
                _nic_repair_graph=lambda *a:owner.fail('NIC-only dispatch forbidden'),
                _verification_graph=lambda *a:owner.fail('formal/full graph dispatch forbidden'),
                _verification_feedback=lambda reports,path:'bounded diagnostics',
                GOAL_COMPLETE_RE=re.compile('LOOP_COMPLETE: yes'))
            exec(definitions({'main', '_feature_linux_repair_progress'}),ns)
            argv=['loop.py','--iterations',str(iterations),'--verify','feature-linux-repair',
                  '--verification-config',str(config)]
            if verification_first:
                argv.append('--verification-first')
            with patch('sys.argv',argv):
                try: ns['main']()
                finally:
                    pointer=root/'logs/feature-linux-repair-active.json'
                    if pointer.exists(): self.progress=json.loads(pointer.read_text())

    def test_failed_verification_repairs_then_all_seven_success_ends_scoped_phase(self):
        failed=copy.deepcopy(self.reports)
        failed[1].update(ok=False, passed=False,status='failed')
        self.run_main([failed,self.reports])
        self.assertEqual(self.events,[('verification','all-seven'),('implementation','codex'),
                                      ('verification','all-seven'),('shutdown','ray')])
        self.assertEqual(self.progress['phase'],'passed')
        self.assertEqual(self.progress['iteration'],2)
        self.assertIs(self.progress['formal_enabled'],False)
        self.assertIs(self.progress['full_acceptance'],False)

    def test_readiness_and_completion_text_cannot_replace_runtime(self):
        failed=copy.deepcopy(self.reports)
        failed[1].update(ok=False,passed=False,status='failed')
        with self.assertRaisesRegex(RuntimeError,'All-seven feature Linux repair iteration limit'):
            self.run_main([failed],iterations=1,verification_first=False)
        self.assertIn(('implementation','codex'),self.events)

    def test_unjoined_result_stops_before_another_implementation(self):
        failed=copy.deepcopy(self.reports)
        failed[1].update(ok=False,status='failed',workers_joined=False)
        with self.assertRaisesRegex(RuntimeError,'not joined'):
            self.run_main([failed])
        self.assertNotIn(('implementation','codex'),self.events)
        self.assertEqual(self.progress['phase'],'cleanup-unconfirmed')

    def test_prompt_keeps_verification_scope_and_strict_policy(self):
        ns=dict(REPO=Path('/tmp/integrated'),FEEDBACK_MAX_CHARS=96000,
                _bounded_text=lambda v,limit:str(v)[:limit],_workspace_snapshot=lambda:'source')
        exec(definitions({'_feature_linux_repair_prompt'}),ns)
        prompt=ns['_feature_linux_repair_prompt']('kernel timeout',2,99,None)
        for required in ('exactly seven', 'FAME5', 'AutoCounter/Print', 'multiclock',
                         'Do not launch Jasper', 'join every implementation subagent',
                         'zero_out_dram false', 'atomically update', 'kernel timeout',
                         'full-port completion'):
            self.assertIn(required,prompt)


if __name__=='__main__': unittest.main()
