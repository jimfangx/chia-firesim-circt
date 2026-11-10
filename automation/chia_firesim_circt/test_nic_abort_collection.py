"""Aborted real-manager lifecycle controls; no FPGA or AWS commands run."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import feature_f2
import feature_nic_manager


class AbortCollectionTest(unittest.TestCase):
    def test_kill_collection_precedes_termination_even_when_collection_fails(self):
        for failed in (False,True):
            with self.subTest(failed=failed),tempfile.TemporaryDirectory() as temporary:
                work=Path(temporary); actions=[]; result={'passed':False}
                original=TimeoutError('original runtime timeout')
                def manager(repo,directory,action,recipes,extra,timeout):
                    actions.append(action)
                    if action=='kill':
                        self.assertEqual(timeout,120)
                        if failed: raise RuntimeError('abort collection failed')
                        (work/'kill-command.json').write_text('{}')
                with patch.object(feature_f2,'run_manager',side_effect=manager):
                    feature_f2.cleanup_runfarm(work,work,work/'recipes',[],result,
                                              runtime_error=original,collect_failure=True)
                self.assertEqual(actions,['kill','terminaterunfarm'])
                self.assertTrue(result['runfarm_cleanup'])
                self.assertFalse(result['passed'])
                self.assertEqual('abort_collection_error' in result,failed)

    def test_normal_completion_does_not_abort(self):
        actions=[]; result={}
        with patch.object(feature_f2,'run_manager',side_effect=lambda r,w,a,*args,**kwargs: actions.append(a)):
            feature_f2.cleanup_runfarm(None,None,None,[],result,runtime_error=None,collect_failure=False)
        self.assertEqual(actions,['terminaterunfarm'])

    def test_raw_and_peer_files_survive_kill_and_peer_join_failures(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory=Path(temporary)
            peer=directory/'peer'; peer.write_bytes(b'frozen-peer')
            config=dict(peer_binary=str(peer),peer_sha256=hashlib.sha256(peer.read_bytes()).hexdigest(),
                        packets=8,link_latency=14,timeout_seconds=30,
                        bindings=[dict(widget=0,mode='normal',port='owned_test')])
            gets=[]
            class API:
                env=SimpleNamespace(host_string='owned-host')
                @contextmanager
                def settings(self,**kwargs): yield
                def run(self,command,**kwargs):
                    if command.startswith('test -f '): return SimpleNamespace(return_code=0)
                    raise RuntimeError('controlled peer stop/join failure')
                def get(self,source,destination):
                    gets.append(source)
                    if source.endswith('/peer.exit'): raise FileNotFoundError('peer not joined')
                    if source.endswith('/simoutput'): raise OSError('controlled transfer failure')
                    Path(destination).write_text('CIRCT_NIC_TOKEN_SEQUENCE_FAILURE retained\n' if source.endswith('/peer.stderr')
                                                 else 'actual raw bytes '+source)
            class Runtime:
                def get_boot_simulation_command(self): pass
            class Server:
                def get_local_job_results_dir_path(self): return str(directory/'results')
                def copy_back_job_results_from_run(self,slot):
                    raise AssertionError('abort must not mark job complete or mount guest rootfs')
            server=Server()
            class Manager:
                parent_node=SimpleNamespace(sim_slots=[server])
                def get_remote_sim_dir_for_slot(self,slot): return '/owned/sim_slot_0/'
                def instance_assigned_simulations(self): return True
                def start_sim_slot(self,slot): pass
                def kill_sim_slot(self,slot): raise RuntimeError('controlled driver kill failure')
            feature_nic_manager.install(dict(feature_nic=config),api=API(),runtime_class=Runtime,
                                        server_class=Server,manager_class=Manager)
            with patch.dict(os.environ,{'CHIA_FEATURE_ABORT_DIAGNOSTICS':'1'}):
                with self.assertRaisesRegex(RuntimeError,'controlled driver kill failure'):
                    Manager().kill_sim_slot(0)
            receipt,=(directory/'results').glob('abort-diagnostics/*/collection.json')
            data=json.loads(receipt.read_text())
            self.assertFalse(data['passed'])
            self.assertIn('kill failure',data['kill_error'])
            self.assertIn('cleanup/collection failed',data['peer_error'])
            self.assertTrue(any(row['path'].endswith('/uartlog') for row in data['files']))
            self.assertTrue(any(row['path'].endswith('/simoutput') for row in data['collection_failures']))
            self.assertTrue(any(source.endswith('/peer.stderr') for source in gets))
            peer_receipt,=(directory/'results').glob('circt-nic-peer-*/peer-lifecycle.json')
            peer_data=json.loads(peer_receipt.read_text())
            self.assertFalse(peer_data['passed'])
            self.assertFalse(peer_data['joined'])
            self.assertIsNone(peer_data['returncode'])
            self.assertIn('CIRCT_NIC_TOKEN_SEQUENCE_FAILURE',(peer_receipt.parent/'peer.stderr').read_text())


if __name__=='__main__': unittest.main()
