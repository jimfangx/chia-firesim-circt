#!/usr/bin/env python3
"""Validate runnable feature workloads/checkers; never claim an FPGA execution."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

import feature_execution as adapter
from downstream_common import record, write


def validate(repo, directory, case_id, generated):
    config = adapter.cases(repo)[case_id]
    adapter.enabled_contract(generated, config)
    src = repo/'automation/chia_firesim_circt/feature_workloads'
    directory.mkdir(parents=True, exist_ok=False)
    result = dict(passed=False, case_id=case_id,
        scope='Native workload/tool validation and private guest preparation; no FPGA or formal acceptance')
    receipts = []
    def run(name, argv):
        receipts.append(adapter.command(repo, directory/name, case_id, name, argv))
        return directory/name/'stdout.log'
    roots = adapter.guest_assets(repo, directory/'guest', case_id, config)
    result['guest_manifests'] = [record(root['manifest']) for root in roots]
    if config['workload']=='threads':
        binary = directory/'native-threads'
        run('native_build',[repo/'.conda-env/bin/cc','-O2','-Wall','-Wextra','-Werror','-pthread',src/'thread_state.c','-o',binary])
        # Run native threads on separate allowed host cores. Their CPU numbers
        # are host-specific; the FPGA checker additionally pins guest CPU0..3.
        cpus = sorted(os.sched_getaffinity(0))
        if len(cpus)<config['threads']: raise ValueError('Need one allowed host CPU per representative thread')
        uart = run('native_execution',[binary,str(config['threads'])])
        from feature_workloads.thread_check import check
        result['checker'] = check(uart.read_text(),config['threads'],shutdown=False)
        text=uart.read_text()
        altered=text.replace('13b3e2d85d5e36b9','13b3e2d85d5e36b8')
        try: check(altered,config['threads'],shutdown=False)
        except ValueError: pass
        else: raise ValueError('Thread checker accepted altered written state')
    elif config['workload']=='instrumentation':
        run('native_guest_hook',['sh',src/'instrumentation_guest.sh'])
        run('checker_controls',[sys.executable,'-B',src/'instrumentation_check_test.py'])
        # Bind the exact emitted domain/monitor ABI rather than a disabled
        # fixture. Full observation checks run later on actual FPGA output.
        from feature_f2 import monitor_manifest
        result['monitor_contract']=monitor_manifest(generated)
        if len(result['monitor_contract']['domains'])<config.get('min_domains',1):
            raise ValueError('Missing emitted representative domains')
    else:
        run('nic_tools',[sys.executable,'-B',src/'nic_validate.py','--output-dir',directory/'nic-tools',
            '--cc',repo/'.conda-env/bin/cc','--cxx',repo/'.conda-env/bin/c++',
            '--riscv-cc',repo/'.conda-env/riscv-tools/bin/riscv64-unknown-linux-gnu-gcc'])
        result['checker']=json.loads((directory/'nic-tools/result.json').read_text())
        if result['checker'].get('passed') is not True: raise ValueError('NIC native execution tools failed')
        result['declared_runtime_modes']=config['nic_modes']
    result.update(passed=True,commands=[record(path) for path in receipts])
    write(directory/'result.json',result)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',type=Path,required=True)
    parser.add_argument('--case',required=True)
    parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--generated',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(validate(args.repo.resolve(),args.directory.resolve(),args.case,args.generated.resolve()),indent=2))
    return 0

if __name__=='__main__': raise SystemExit(main())
