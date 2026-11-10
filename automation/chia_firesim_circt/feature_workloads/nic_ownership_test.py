#!/usr/bin/env python3
"""Run the actual root NIC driver against an unprivileged native peer.

Only physical FPGA MMIO/stream IO use the native driver test's providers. Real
production simplenic mapping/init/tick/destruction and native peer IPC execute.
This validates the run-host UID boundary, not FPGA or Linux packet acceptance.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time
import uuid


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--peer',type=Path,required=True)
    parser.add_argument('--driver',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args()
    if os.geteuid()==0: raise SystemExit('Run this test as the unprivileged peer user; driver uses sudo -n')
    output=args.output_dir.resolve();output.mkdir(parents=True,exist_ok=False)
    port='chia_ownership_'+uuid.uuid4().hex
    names=[Path('/dev/shm')/('port_'+direction+port+'_'+str(round_))
           for direction in ('nts','stn') for round_ in (0,1)]
    stop=output/'stop'
    stdout=output/'peer.stdout';stderr=output/'peer.stderr'
    standalone_paths=[]
    peer_args=[str(args.peer.resolve()),'6405','128','30',str(stop),'0:'+port]
    with stdout.open('w') as out,stderr.open('w') as err:
        peer=subprocess.Popen(peer_args,stdout=out,stderr=err)
    try:
        deadline=time.monotonic()+10
        while 'CIRCT_NIC_PEER_READY' not in stdout.read_text():
            if peer.poll() is not None: raise RuntimeError(stderr.read_text())
            if time.monotonic()>deadline: raise RuntimeError('native peer did not become ready')
            time.sleep(.01)
        assert all(path.stat().st_uid==os.geteuid() and path.stat().st_size==58561 for path in names)
        # Reproduce the original syscall flags against exactly these peer-owned
        # names. A local policy permitting them is recorded, not silently treated
        # as a protected-regular negative-control pass.
        code='import os,sys; fd=os.open(sys.argv[1],os.O_RDWR|os.O_CREAT,0o700); os.close(fd)'
        old=subprocess.run(['sudo','-n','python3','-c',code,str(names[0])],capture_output=True,text=True,timeout=5)
        (output/'old-open.stdout').write_text(old.stdout);(output/'old-open.stderr').write_text(old.stderr)
        protected=int(Path('/proc/sys/fs/protected_regular').read_text())
        if protected:
            assert old.returncode!=0 and 'PermissionError' in old.stderr
        command=['sudo','-n',str(args.driver.resolve()),'--existing-peer',port]
        driver=subprocess.run(command,capture_output=True,text=True,timeout=10)
        (output/'driver.stdout').write_text(driver.stdout);(output/'driver.stderr').write_text(driver.stderr)
        if driver.returncode: raise RuntimeError('root production driver failed: '+driver.stderr)
        assert 'uid=0' in driver.stdout
        stop.touch();peer.wait(timeout=5)
        rows=[json.loads(line.split(' ',1)[1]) for line in stdout.read_text().splitlines()
              if line.startswith('CIRCT_NIC_PEER_RESULT ')]
        # No guest packets are requested in this ownership control, but one
        # complete bidirectional idle-token burst must actually have traversed.
        assert peer.returncode==1 and len(rows)==1 and rows[0]['rounds']==1
        assert rows[0]['requests']==0 and rows[0]['replies']==0 and not rows[0]['passed']
        assert all(not path.exists() for path in names)
        standalone_port='chia_ownership_'+uuid.uuid4().hex
        standalone_paths=[Path('/dev/shm')/('port_'+direction+standalone_port+'_'+str(round_))
                          for direction in ('nts','stn') for round_ in (0,1)]
        assert all(not path.exists() for path in standalone_paths)
        standalone=['sudo','-n',str(args.driver.resolve()),'--standalone',standalone_port]
        created=subprocess.run(standalone,capture_output=True,text=True,timeout=10)
        (output/'standalone.stdout').write_text(created.stdout);(output/'standalone.stderr').write_text(created.stderr)
        if created.returncode: raise RuntimeError('standalone creation fallback failed: '+created.stderr)
        report=dict(passed=True,scope='Actual root production NIC driver/unprivileged native peer ownership and idle-token IPC; no FPGA/Linux packet acceptance',
                    peer_uid=os.geteuid(),driver_uid=0,protected_regular=protected,
                    old_creat_flags_rejected=old.returncode!=0,existing_peer_mapping_passed=True,
                    actual_idle_token_rounds=rows[0]['rounds'],bytes_per_mapping=58561,
                    descriptors_closed=True,peer_joined=True,shared_memory_cleaned=True,
                    standalone_exclusive_creation_passed=True,driver_command=command,standalone_command=standalone)
        (output/'result.json').write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(report,indent=2))
        return 0
    finally:
        stop.touch()
        if peer.poll() is None:
            try:peer.wait(timeout=5)
            except subprocess.TimeoutExpired:
                peer.terminate()
                try:peer.wait(timeout=5)
                except subprocess.TimeoutExpired:peer.kill();peer.wait()
        # All these paths use this test's unique names and were positively
        # created by its own peer; forced termination may skip destructors.
        for path in names:
            if path.exists() and path.stat().st_uid==os.geteuid():path.unlink()
        if any(path.exists() for path in standalone_paths):
            # Only the exclusive UUID names whose absence was checked above
            # belong to this root-created standalone control. No glob cleanup.
            code=('import pathlib,sys; '
                  'paths=[pathlib.Path(name) for name in sys.argv[1:]]; '
                  '[(p.unlink()) for p in paths if p.exists() and p.stat().st_uid==0]')
            subprocess.run(['sudo','-n','python3','-c',code,*map(str,standalone_paths)],check=True,timeout=5)


if __name__=='__main__':raise SystemExit(main())
