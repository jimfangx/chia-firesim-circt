#!/usr/bin/env python3
"""Real localhost SSH/Fabric detachment regression with a bounded control peer.

This checks launch/readiness/original manager start/stop/join over the production
transport. The small peer is a lifecycle control, not packet or FPGA acceptance;
nic_manager_test.py separately exercises the real native packet transport.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import sys
import time
import uuid

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import feature_nic_manager
import nic_manager_test as focused


PEER = '''#!/usr/bin/env python3
import json,pathlib,sys,time
stop=pathlib.Path(sys.argv[4]); deadline=time.monotonic()+30
print('CIRCT_NIC_PEER_READY lifecycle-control',flush=True)
while not stop.exists():
    if time.monotonic()>deadline: raise SystemExit('bounded control peer timeout')
    time.sleep(.02)
for binding in sys.argv[5:]:
    print('CIRCT_NIC_PEER_RESULT '+json.dumps(dict(widget=int(binding.split(':')[0]),
        passed=True,requests=int(sys.argv[2]),replies=int(sys.argv[2]),rounds=1)),flush=True)
'''


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--key',type=Path,default=Path.home()/'firesim.pem')
    args=parser.parse_args()
    output=args.output_dir.resolve();output.mkdir(parents=True,exist_ok=False)
    binary=output/'bounded-control-peer';binary.write_text(PEER);binary.chmod(0o755)
    import fabric.api as api
    from fabric.exceptions import CommandTimeout
    before=output/'before';before.mkdir()
    launcher=before/'launch.sh'
    launcher.write_text('#!/bin/bash\nset +e\n'+shlex.join([
        str(binary),'14','8','30',str(before/'stop'),'0:bounded-control'])+
        ' >peer.stdout 2>peer.stderr &\nchild=$!\nprintf "%s\\n" "$child" >peer.pid\n'
        'wait "$child"\nresult=$?\nprintf "%s\\n" "$result" >peer.exit\nexit "$result"\n')
    timings={}
    with api.settings(host_string='ubuntu@localhost',key_filename=str(args.key.resolve()),
                      disable_known_hosts=True,abort_on_prompts=True,connection_attempts=1,timeout=5):
        # This is the exact pre-fix shape, under Fabric's real cd wrapper.
        started=time.monotonic()
        try:
            with api.cd(str(before)):
                api.run('nohup bash ./launch.sh >launcher.log 2>&1 </dev/null &',pty=False,timeout=2)
        except CommandTimeout:
            timings['old_launch_timed_out_seconds']=time.monotonic()-started
        else:
            raise AssertionError('Local SSH transport did not reproduce the old retained-channel failure')
        finally:
            api.run('python3 -c '+shlex.quote(feature_nic_manager.STOP)+' '+shlex.quote(str(before)),timeout=25)
        assert (before/'peer.exit').read_text().strip()=='0'
        assert 'CIRCT_NIC_PEER_READY' in (before/'peer.stdout').read_text()

        class Manager(focused.Manager):
            def start_sim_slot(self,slot):
                directories=list((self.directory/'sim_slot_0').glob('circt-nic-peer-*'))
                assert len(directories)==1
                self.peer_directory=directories[0]
                assert 'CIRCT_NIC_PEER_READY' in (self.peer_directory/'peer.stdout').read_text()
                assert not (self.peer_directory/'peer.exit').exists(), 'original_start waited for peer termination'
                super().start_sim_slot(slot)
                (self.directory/'original-start-called').touch()

        token=uuid.uuid4().hex
        config=dict(peer_binary=str(binary),peer_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
                    bindings=[dict(widget=i,port='chia_ssh_control_'+token+'_'+str(i),mode='normal') for i in range(2)],
                    packets=8,link_latency=14,timeout_seconds=30)
        assert feature_nic_manager.install(dict(feature_nic=config),api=api,runtime_class=focused.Runtime,
                                           manager_class=Manager,server_class=focused.Server)
        manager=Manager(output/'after')
        started=time.monotonic()
        try:
            manager.start_sim_slot(0)
            timings['fixed_manager_start_seconds']=time.monotonic()-started
            assert timings['fixed_manager_start_seconds']<10
            assert (manager.directory/'original-start-called').exists()
            assert not (manager.peer_directory/'peer.exit').exists()
            assert manager.boot['all_shmemportnames']==[row['port'] for row in config['bindings']]
            manager.server.copy_back_job_results_from_run(0)
            lifecycle,=manager.server.results.glob('*/peer-lifecycle.json')
            result=json.loads(lifecycle.read_text())
            assert result['passed'] and result['joined'] and result['returncode']==0
            assert (manager.server.results/'real-manager-copy-called').exists()
        finally:
            manager.kill_sim_slot(0)
    report=dict(passed=True,scope='Actual localhost SSH/Fabric launch lifecycle with bounded control peer; no packet or FPGA acceptance',
                old_cd_background_command_reproduced=True,detached_ssh_returned=True,
                ready_before_original_start=True,original_start_before_peer_exit=True,
                unique_widget_ports=True,peer_delivery_hash_checked=True,peer_joined=True,**timings)
    (output/'result.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))
    return 0


if __name__=='__main__':raise SystemExit(main())
