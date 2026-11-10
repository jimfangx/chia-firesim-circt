#!/usr/bin/env python3
"""Freeze enabled feature artifacts and submit independent real-manager F2 work.

Submission returns immediately after immutable capture/Ray dispatch. A worker
serializes with baseline FPGA jobs, builds or reuses identical hardware, prepares
private Linux images, runs actual feature checkers and cleans its run farm.
This adapter never launches Jasper and never awards full port acceptance.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import sys
import time
import uuid
import yaml
import background_f2 as bg
import feature_execution as features
import run_f2
from downstream_common import record, write


def validate_case(config):
    if config.get('supernode'):
        raise ValueError('Feature representatives require one Linux instance; supernode is unsupported')
    if config.get('workload') not in ('threads', 'instrumentation', 'nic'):
        raise ValueError('Unsupported feature workload')


def run_manager(repo,directory,action,recipes,extra=(),timeout=21600):
    """Use the real manager with the required environment and joined children."""
    # Git snapshots omit ignored empty directories. FireSim opens its log
    # before dispatching an action and expects this directory to exist.
    for name in ('logs','results-build','results-workload'):
        (Path(repo)/'sims/firesim/deploy'/name).mkdir(parents=True,exist_ok=True)
    argv=run_f2.manager_command(repo,directory,action,recipes,extra)
    shell=('set -e\nfeature_repo=$1\nshift\nfeature_argv=("$@")\nset --\n'
           'source "${CONDA_EXE%/bin/conda}/etc/profile.d/conda.sh"\n'
           'cd "$feature_repo/sims/firesim"\nsource ./sourceme-manager.sh\n'
           'cd deploy\nexec "${feature_argv[@]}"')
    log=directory/(action+'.log')
    command=['bash','-lc',shell,'chia-feature-manager',str(repo),*argv]
    receipt=dict(argv=command,action=action,returncode=None,timed_out=False)
    started=time.monotonic()
    try:
        with log.open('w') as stream:
            environment=os.environ.copy()
            if action=='kill': environment['CHIA_FEATURE_ABORT_DIAGNOSTICS']='1'
            process=subprocess.Popen(command,env=environment,
                                     stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
            try:
                status=process.wait(timeout=timeout)
            except BaseException as error:
                receipt['timed_out']=isinstance(error,subprocess.TimeoutExpired)
                # Manager/Fabric/SSH children share this owned session. Remote
                # localhost build commands additionally have their own timeout.
                try: os.killpg(process.pid,signal.SIGTERM)
                except ProcessLookupError: pass
                try: process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    try: os.killpg(process.pid,signal.SIGKILL)
                    except ProcessLookupError: pass
                    process.wait()
                # The manager can exit on TERM before a tool that ignores TERM.
                # Its owned process group remains addressable until the last child
                # exits, so reclaim stragglers even after wait() returned.
                try: os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError: pass
                raise
            finally:
                receipt['returncode']=process.returncode
    finally:
        # Combined stdout/stderr is intentional; preserve the exact bytes and
        # command status even on timeout/cancellation before propagating it.
        if not log.exists(): log.touch()
        receipt.update(stdout=record(log),stderr=record(log),wall_seconds=time.monotonic()-started)
        write(directory/(action+'-command.json'),receipt)
    if status: raise ValueError(f'FireSim {action} exited {status}; {log}')
    return log


def cleanup_runfarm(repo, work, recipes, extra, result, *, runtime_error, collect_failure):
    """Retain aborted driver/peer outputs before destroying the owned farm."""
    if collect_failure:
        try:
            run_manager(repo,work,'kill',recipes,extra,timeout=120)
        except BaseException as error:
            result['abort_collection_error']=type(error).__name__+': '+str(error)
        finally:
            if (work/'kill-command.json').is_file():
                result['abort_collection_command']=record(work/'kill-command.json')
    try:
        run_manager(repo,work,'terminaterunfarm',recipes,[*extra,'-q'],timeout=600)
        result['runfarm_cleanup']=True
    except BaseException as error:
        result.update(runfarm_cleanup=False,cleanup_error=type(error).__name__+': '+str(error))
        if runtime_error is None: raise


def validate_context(context, config, case_id):
    """A baseline identity is provenance, never enabled-case semantic signoff."""
    if context is None: return None
    if (not isinstance(context,dict) or not isinstance(context.get('input_identity'),str)
            or not context['input_identity'] or context.get('enabled_features')!=[config['feature']]
            or context.get('case_id',case_id)!=case_id):
        raise ValueError('Feature context requires matching identity, case and enabled feature')
    artifacts=context.get('artifacts',[])
    if not isinstance(artifacts,list): raise ValueError('Feature artifacts must be bindings')
    for item in artifacts:
        if not isinstance(item,dict) or not isinstance(item.get('role'),str):
            raise ValueError('Feature artifact role/binding missing')
        if record(item['path'])['sha256']!=item.get('sha256'):
            raise ValueError('Feature context artifact changed')
    return context


def guest_tools(config, repo):
    if config['workload'] not in ('threads', 'nic'):
        return []
    from guest_toolchain import bindings
    return bindings(repo)


def freeze(repo, case_id, implementation, directory, *, context=None):
    repo, implementation, directory = map(lambda p: Path(p).resolve(), (repo, implementation, directory))
    config = features.cases(repo)[case_id]
    validate_case(config)
    tools = guest_tools(config, repo)
    validate_context(context,config,case_id)
    generated = implementation/'generated'
    features.enabled_contract(generated, config)
    compile_manifest = json.loads((implementation/'compile.json').read_text())
    driver_manifest = json.loads((implementation/'driver.json').read_text())
    for item in (context or {}).get('artifacts',[]):
        expected={'rtl':generated/'FireSim-generated.sv','driver':Path(driver_manifest['driver']['path'])}.get(item['role'])
        if expected is not None and record(expected)['sha256']!=item['sha256']:
            raise ValueError('Feature context RTL/driver does not match the compiled candidate')
    for item in compile_manifest['products'] + compile_manifest['inputs'] + [driver_manifest['driver']]:
        if record(item['path']) != item: raise ValueError('Production inputs changed before freezing')
    roots, sources = bg.source_files(repo)
    # The manager parses its configured hardware database even for a new
    # build. This site-local generated file is ignored by Git but is a real
    # manager input and must belong to the immutable source closure.
    hwdb=repo/'sims/firesim/deploy/config_hwdb.yaml'
    if not hwdb.is_file(): raise ValueError('Feature snapshot requires the manager hardware database')
    sources=sorted(set(sources)|{hwdb})
    if config['workload']=='nic':
        module=repo/'software/firemarshal/boards/firechip/drivers/icenet-driver/icenet.ko'
        if not module.is_file(): raise ValueError('NIC snapshot requires the matching built IceNIC kernel module')
        sources=sorted(set(sources)|{module})
    before = {str(path):bg.digest(path) for path in sources}
    frozen_repo, bindings, images = bg.copy_checkout(repo,directory,roots,sources)
    target = directory/'accepted'
    shutil.copytree(generated,target,symlinks=False)
    shutil.copy2(driver_manifest['driver']['path'],directory/'accepted-driver')
    image_id=hashlib.sha256(json.dumps({field:config.get(field,[]) for field in
        ('target_config','platform_config','compiler_flags')},sort_keys=True).encode()).hexdigest()
    key = bg.hardware_key({image_id:dict(circt_rtl=target/'FireSim-generated.sv',recipe=features.recipe(config,repo))},sources,repo)
    manifest = dict(schema_version=1, case_id=case_id, config=config, repo=str(frozen_repo), source_repo=str(repo),
        accepted=str(target), source_files=bindings, shared_immutable_images=images,
        accepted_files=[record(path) for path in sorted(target.rglob('*')) if path.is_file()],
        driver=record(directory/'accepted-driver'), hardware_key=key,
        source_identity=features.source_identity(repo), compile_receipt=compile_manifest,
        guest_toolchain=tools)
    if context is not None: manifest['context']=context
    # Compiler and driver products are immutable copies; runtime policy, checker,
    # guest programs and peers are captured in the copied source closure.
    # Storage paths change for each capture; deduplicate by actual relative
    # source identities rather than the fresh snapshot directory name.
    # env.sh is intentionally rewritten to each snapshot's absolute root, so
    # use its validated original bytes for equivalence across captures too.
    closure=[dict(path=str(path.relative_to(repo)),sha256=before[str(path)]) for path in sources]
    manifest['runtime_key']=hashlib.sha256(json.dumps(dict(hardware=key,config=config,
        sources=closure, guest_toolchain=tools),sort_keys=True).encode()).hexdigest()
    if {str(path):bg.digest(path) for path in sources} != before:
        raise ValueError('Sources changed while freezing the feature candidate')
    bg.validate(bindings+images+manifest['accepted_files']+[manifest['driver']]+tools)
    write(directory/'snapshot.json',manifest)
    return manifest


def submit(repo, case_id, implementation, directory, *, context=None):
    from ray.job_submission import JobSubmissionClient
    directory.mkdir(parents=True)
    manifest=freeze(repo,case_id,implementation,directory,context=context)
    root=Path(repo)/'automation/chia_firesim_circt/logs/background-features'
    root.mkdir(parents=True,exist_ok=True)
    client=JobSubmissionClient(os.environ.get('RAY_API_SERVER_ADDRESS','http://127.0.0.1:8265'))
    with bg.locked(root/'.queue.lock'):
        pending=0
        for path in root.glob('*/job.json'):
            data=json.loads(path.read_text())
            if data.get('phase') in ('queued','running'):
                status=client.get_job_status(data['job_id'])
                status=getattr(status,'value',str(status))
                if status in ('FAILED','STOPPED','SUCCEEDED'):
                    data['phase']='interrupted' if status!='SUCCEEDED' else 'finished'
                    write(path,data)
                elif (data.get('runtime_key')==manifest['runtime_key']
                      and data.get('input_identity')==(context or {}).get('input_identity')):
                    write(directory/'reuse.json',data)
                    return data
                else: pending+=1
        if pending>=2: raise ValueError('Feature queue bounded at two pending/running jobs; consume retained outcomes first')
        job_id='firesim-feature-'+directory.name
        data=dict(job_id=job_id,phase='queued',case_id=case_id,hardware_key=manifest['hardware_key'],
                  runtime_key=manifest['runtime_key'],snapshot=str(directory/'snapshot.json'),formal_dispatched=False,
                  input_identity=(context or {}).get('input_identity'))
        write(directory/'job.json',data)
        argv=[str(Path(repo)/'.conda-env/bin/python3'),'-B',str(Path(manifest['repo'])/'automation/chia_firesim_circt/feature_f2.py'),
              '--snapshot',str(directory/'snapshot.json')]
        try:
            lock_environment={name:os.environ[name] for name in
                ('CHIA_F2_BUILD_LOCK','CHIA_FEATURE_WORKER_LOCK','CHIA_GUEST_TOOLCHAIN_PREFIX',
                 'PYTHONDONTWRITEBYTECODE') if name in os.environ}
            # Ray's three-second default can kill bounded manager/farm cleanup.
            lock_environment['RAY_JOB_STOP_WAIT_TIME_S']='720'
            client.submit_job(entrypoint=shlex.join(argv),submission_id=job_id,entrypoint_num_cpus=4,
                              entrypoint_memory=20*1024**3,runtime_env={'env_vars':lock_environment})
        except BaseException as error:
            data.update(phase='failed',error='Ray submission: '+type(error).__name__+': '+str(error))
            write(directory/'job.json',data)
            raise
        return data


def runtime(repo,directory,manifest,design):
    config=manifest['config']; case_id=manifest['case_id']
    validate_case(config)
    roots=features.guest_assets(repo,directory/'workload',case_id,config)
    deploy=repo/'sims/firesim/deploy'; name='chia-feature-'+uuid.uuid4().hex[:12]
    assets=deploy/'workloads'/name; assets.mkdir()
    workload=json.loads(roots[0]['template'].read_text())
    workload.update(benchmark_name=name,common_bootbinary=os.path.relpath(roots[0]['bootbinary'],assets),
                    common_rootfs=os.path.relpath(roots[0]['rootfs'],assets))
    workload['common_simulation_outputs']=['uartlog*','AUTOCOUNTER*.csv','PRINT*','memory_stats*.csv']
    if len(roots)!=1: raise ValueError('Expected one private Linux rootfs')
    workload_path=deploy/'workloads'/(name+'.json'); workload_path.write_text(json.dumps(workload,indent=2)+'\n')
    runconfig=yaml.safe_load((deploy/'config_runtime.yaml').read_text())
    runconfig['run_farm']=dict(base_recipe=str(deploy/'run-farm-recipes/aws_ec2.yaml'),recipe_arg_overrides=dict(
        run_farm_tag=name,always_expand_run_farm=True,run_farm_hosts_to_use=[{'f2.6xlarge':1}],default_simulation_dir='/home/ubuntu'))
    runconfig['metasimulation']['metasimulation_enabled']=False
    runconfig['host_debug']['zero_out_dram']=False
    plusargs='+max-cycles=100000000000'
    if config['workload'] in ('threads','instrumentation'):
        monitor=monitor_manifest(Path(manifest['accepted']))
        write(directory/'monitor-manifest.json',monitor)
        plusargs+=' +autocounter-readrate='+str(monitor['sample_period_base'])+' +autocounter-filename-base=AUTOCOUNTER +print-file=PRINT'
    runconfig['target_config'].update(topology='no_net_config',no_net_num_nodes=1,default_hw_config=design['recipe_name'],plusarg_passthrough=plusargs)
    runconfig['workload'].update(workload_name=workload_path.name,suffix_tag=name,terminate_on_completion=True)
    path=directory/'runtime.yaml'; path.write_text(yaml.safe_dump(runconfig,sort_keys=False))
    write(directory/'runtime-inputs.json',dict(workload=record(workload_path),runtime=record(path),
          workload_kind='linux_boot_poweroff',workload_json=record(workload_path),
          bootbinary=record(roots[0]['bootbinary']),rootfs=record(roots[0]['rootfs']),
          linux_preparation=record(roots[0]['manifest']),template=record(roots[0]['template']),
          roots=[record(root['manifest']) for root in roots],zero_out_dram=False))
    return name,path


def check_outputs(repo,directory,manifest,name):
    config=manifest['config']; source=repo/'sims/firesim/deploy/results-workload'
    validate_case(config)
    runs=[path for path in source.iterdir() if name in path.name]
    if len(runs)!=1: raise ValueError('Expected one uniquely tagged feature result directory')
    retained=directory/'runtime-output'; shutil.copytree(runs[0],retained)
    observations=[path for path in sorted(retained.rglob('*')) if path.is_file()]
    before=[record(path) for path in observations]
    uarts=sorted(path for path in retained.rglob('uartlog*') if path.is_file())
    expected=1
    if len(uarts)!=expected: raise ValueError('Feature UART count differs from Linux instances')
    # The real driver exit is recorded in the root UART script transcript.
    for index,uart in enumerate(uarts):
        text=uart.read_text(errors='replace').replace('\r','')
        if index==0: run_f2.validate_uart(text)
        elif 'reboot: Power down' not in text or 'CIRCT_LINUX_POWEROFF: REQUESTED' not in text:
            raise ValueError('Supernode Linux did not power off')
    if config['workload']=='threads':
        from feature_workloads.thread_check import check
        verdict=check(uarts[0].read_text(errors='replace'),config['threads'])
    elif config['workload']=='instrumentation':
        from feature_workloads.instrumentation_check import check
        monitor=monitor_manifest(Path(manifest['accepted']))
        verdict=check(monitor,[p.read_text() for p in sorted(retained.rglob('AUTOCOUNTER*.csv'))],
                      [p.read_text() for p in sorted(retained.rglob('PRINT*')) if p.is_file()],
                      uarts[0].read_text(errors='replace'),require_shutdown=True)
    else:
        from feature_workloads.nic_check import check
        lifecycles=[json.loads(path.read_text()) for path in retained.rglob('peer-lifecycle.json')]
        if any(mode=='normal' for mode in config['nic_modes']):
            if len(lifecycles)!=1 or lifecycles[0].get('passed') is not True or lifecycles[0].get('joined') is not True or lifecycles[0].get('returncode')!=0:
                raise ValueError('Normal NIC requires one successful joined real peer lifecycle')
        peers=sorted(retained.rglob('peer.stdout'))
        verdict=check([p.read_text(errors='replace') for p in uarts],list(range(len(config['nic_modes']))),
            {i:'normal' if mode=='normal' else 'loopback' for i,mode in enumerate(config['nic_modes'])},128,
            '\n'.join(p.read_text() for p in peers) if peers else None,require_worker_lifecycle=True)
    write(directory/'feature-check.json',verdict)
    if verdict.get('passed') is not True: raise ValueError('Feature checker failed')
    if before!=[record(path) for path in observations]:
        raise ValueError('Feature observations changed during checking')
    return verdict


def observed_capabilities(config):
    """Only runtime observations; no inferred reset/stall or compiler ABI proof."""
    if config['workload']=='threads':
        return ['independent_register_state','independent_threaded_memories']
    if config['workload']=='instrumentation':
        if config['feature']=='multiclock_models':
            return ['checked_multidomain_multimodel_runtime_activity']
        return ['checked_counter_and_print_runtime_output','counter_sampling_rollover_reset','print_payload_format_order']
    capabilities=['checked_packet_runtime_traffic']
    if len(config['nic_modes'])>1: capabilities.append('multiple_independent_nic_widgets')
    if 'compile_time_loopback' in config['nic_modes']: capabilities.append('compile_time_loopback_nic')
    if 'runtime_loopback' in config['nic_modes']: capabilities.append('runtime_per_widget_nic_loopback')
    return capabilities


def run_checker(repo,directory,manifest,name):
    """Run the real output checker as a retained command, then bind its inputs."""
    snapshot=directory/'checker-input.json'; write(snapshot,manifest)
    argv=[repo/'.conda-env/bin/python3','-B',repo/'automation/chia_firesim_circt/feature_f2.py',
          '--check-outputs',snapshot,'--output-dir',directory,'--result-tag',name]
    command=features.command(repo,directory/'checker-command',manifest['case_id'],'fpga_feature_checker',argv)
    path=directory/'feature-check.json'; verdict=json.loads(path.read_text())
    if verdict.get('passed') is not True: raise ValueError('Actual feature checker did not pass')
    observations=[record(p) for p in sorted((directory/'runtime-output').rglob('*')) if p.is_file()]
    if not observations: raise ValueError('Actual feature observations missing')
    context=manifest.get('context') or {}
    capabilities=observed_capabilities(manifest['config'])
    requested=context.get('capabilities',capabilities)
    if (not isinstance(requested,list) or not requested or len(set(requested))!=len(requested)
            or set(requested)-set(capabilities)):
        raise ValueError('Requested runtime coverage exceeds observed workload capabilities')
    verdict.update(case_id=manifest['case_id'],input_identity=context.get('input_identity',manifest['source_identity']),
                   command=record(command),observations=observations,capabilities=requested,
                   verification_scope='Linux runtime observations only; no enabled-case semantic/formal acceptance')
    write(path,verdict)
    return verdict


def linux_receipts(directory,manifest,result):
    """Bind actual build/run/checker evidence without granting other stages."""
    work=directory/'execution'
    command=work/'runworkload-command.json'
    check=work/'feature-check.json'
    verdict=json.loads(check.read_text())
    if verdict.get('passed') is not True: raise ValueError('Missing passing feature observations')
    context=manifest.get('context') or {}
    identity=context.get('input_identity',manifest['source_identity'])
    if (verdict.get('case_id')!=manifest['case_id'] or verdict.get('input_identity')!=identity
            or result.get('runfarm_cleanup') is not True):
        raise ValueError('Feature observations must bind this case/identity and successful farm cleanup')
    uarts=sorted((work/'runtime-output').rglob('uartlog*'))
    if len(uarts)!=1: raise ValueError('One actual Linux UART required')
    run_f2.validate_uart(uarts[0].read_text(errors='replace'))
    for path in [work/(action+'-command.json') for action in
                 ('buildbitstream','launchrunfarm','infrasetup','runworkload','terminaterunfarm')]:
        receipt=json.loads(path.read_text())
        if receipt.get('returncode')!=0 or type(receipt.get('returncode')) is not int or receipt.get('timed_out') is not False:
            raise ValueError('Actual successful build and run commands required')
        bg.validate([receipt['stdout'],receipt['stderr']])
    bg.validate([verdict['command'],*verdict['observations']])
    checker_command=json.loads(Path(verdict['command']['path']).read_text())
    if (type(checker_command.get('returncode')) is not int or checker_command['returncode']!=0
            or checker_command.get('timed_out') is not False):
        raise ValueError('Actual successful feature checker command required')
    bg.validate([checker_command['stdout'],checker_command['stderr']])
    stage=dict(result,target=manifest['case_id'],input_identity=identity,
               enabled_features=[manifest['config']['feature']],artifacts=context.get('artifacts',[]),
               scope='Actual local FireSim F2 build and manager-launched Linux boot, enabled feature exercise, clean poweroff and driver exit; baseline identity is provenance only, not enabled-case semantic/formal signoff.',
               workload_kind='linux_boot_poweroff',command=record(command),feature_checks=[record(check)],
               local_bitstream_build=True,firesim_manager=True,f2_runfarm=True,linux_booted=True,
               poweroff_observed=True,workload_passed=True,full_acceptance=False,
               receipts={key:record(path) for key,path in dict(
                   build_log=Path(result['build_log']),manager_log=work/'runworkload.log',
                   workload_evidence=uarts[0],workload_inputs=work/'runtime-inputs.json',
                   frozen_inputs=work/'frozen-inputs.json',build_config=work/'build.yaml',
                   hwdb=work/'candidate-hwdb.yaml').items()})
    write(work/'linux-stage.json',stage)
    return stage


def monitor_manifest(generated):
    import re
    from feature_workloads.instrumentation_check import reset_acquisition
    rows=json.loads((generated/'post-wrap-top-all.json').read_text())
    resets=[row for row in rows if row.get('widgetClass')=='midas.widgets.ResetPulseBridgeModule']
    clocks=[row for row in rows if row.get('widgetClass')=='midas.widgets.ClockBridgeModule']
    if len(resets)!=1 or len(clocks)!=1:
        raise ValueError('Monitor requires one generated base ResetPulse and ClockBridge')
    reset_key=resets[0]['widgetConstructorKey']; reset_clock=resets[0]['clockInfo']
    base_reset=reset_key.get('defaultPulseLength'); maximum=reset_key.get('maxPulseLength')
    if (reset_key.get('activeHigh') is not True or type(base_reset) is not int or
            type(maximum) is not int or not 0<base_reset<=maximum or
            type(reset_clock.get('multiplier')) is not int or type(reset_clock.get('divisor')) is not int or
            reset_clock.get('multiplier')!=1 or reset_clock.get('divisor')!=1):
        raise ValueError('Monitor requires a positive generated active-high base-clock reset pulse')
    clock_key=clocks[0]['widgetConstructorKey']
    if set(clock_key)!={'class','clocks'} or clock_key['class']!='firesim.lib.bridges.ClockParameters':
        raise ValueError('Monitor requires the phase-zero ClockBridge constructor contract')
    declared_clocks=clock_key['clocks']
    if not isinstance(declared_clocks,list) or not declared_clocks:
        raise ValueError('Monitor requires declared ClockBridge domains')
    domains=[]
    for row in rows:
        if row.get('widgetClass')!='midas.widgets.AutoCounterBridgeModule': continue
        key=row['widgetConstructorKey']; clock=row['clockInfo']
        labels=[event['label'] for event in key['eventMetadata']]
        ids={int(m[1]) for label in labels if (m:=re.search(r'feature_domain(\d+)_active$',label))}
        if not ids: continue  # Other selected library counters remain legal.
        if len(ids)!=1: raise ValueError('Expected one declared monitor per CSV domain')
        for suffix in ('active','cycles','wrap'):
            if sum(bool(re.search(r'feature_domain%d_%s$' % (next(iter(ids)),suffix),label)) for label in labels)!=1:
                raise ValueError('Monitor CSV metadata is incomplete or ambiguous')
        if any(type(clock.get(field)) is not int or clock[field]<=0 for field in ('multiplier','divisor')):
            raise ValueError('Monitor clock ratio must be positive integers')
        if sum(item==clock for item in declared_clocks)!=1:
            raise ValueError('Monitor domain differs from generated ClockBridge declaration')
        acquisition=reset_acquisition(base_reset,clock['multiplier'],clock['divisor'])
        domains.append(dict(id=ids.pop(),name=clock['name'],multiplier=clock['multiplier'],divisor=clock['divisor'],
                            **acquisition))
    if not domains: raise ValueError('No actual enabled domain monitors in emitted metadata')
    if len({d['id'] for d in domains})!=len(domains) or len({d['name'] for d in domains})!=len(domains):
        raise ValueError('Monitor domain ownership is duplicated')
    quantum=math.lcm(*(d['divisor']//math.gcd(d['multiplier'],d['divisor']) for d in domains))
    period=((2**20+7+quantum-1)//quantum)*quantum
    return dict(schema_version=2,reset_base_cycles=base_reset,clock_phase='all-countdowns-zero',
                domains=domains,sample_period_base=period,min_samples=3,min_prints=2)


def run_snapshot(path):
    manifest=json.loads(path.read_text()); directory=path.parent; repo=Path(manifest['repo'])
    shared=Path(manifest['source_repo'])/'automation/chia_firesim_circt/logs'
    data=json.loads((directory/'job.json').read_text())
    result=dict(passed=False,case_id=manifest['case_id'],hardware_key=manifest['hardware_key'],formal_dispatched=False,
                full_acceptance=False,local_bitstream_build=False,firesim_manager=False,f2_runfarm=False,
                linux_booted=False,poweroff_observed=False,workload_passed=False)
    launched=False
    def interrupted(signum,frame):
        # Let the first interrupt enter finally; do not interrupt cleanup.
        signal.signal(signal.SIGTERM,signal.SIG_IGN); signal.signal(signal.SIGINT,signal.SIG_IGN)
        raise RuntimeError('Feature FPGA worker interrupted '+str(signum))
    signal.signal(signal.SIGTERM,interrupted); signal.signal(signal.SIGINT,interrupted)
    worker_lock=Path(os.environ.get('CHIA_FEATURE_WORKER_LOCK',str(shared/'background-f2/.worker.lock')))
    with bg.locked(worker_lock):
        data['phase']='running'; write(directory/'job.json',data)
        try:
            bg.validate(manifest['source_files']+manifest['shared_immutable_images']+manifest['accepted_files']+[manifest['driver']]+manifest.get('guest_toolchain', []))
            case_id=manifest['case_id']; config=manifest['config']; name='circt_'+case_id
            validate_case(config)
            if manifest.get('guest_toolchain', []) != guest_tools(config, repo):
                raise ValueError('Guest toolchain differs from the accepted runtime inputs')
            validate_context(manifest.get('context'),config,case_id)
            design=dict(circt_rtl=Path(manifest['accepted'])/'FireSim-generated.sv',recipe=features.recipe(config,repo),recipe_name=name)
            work=directory/'execution'; work.mkdir()
            frozen=run_f2.prepare(repo,case_id,work,design)
            frozen['feature_resources']=dict(cpu_ids=sorted(os.sched_getaffinity(0))[:4],memory_bytes=20*1024**3,
                                             timeout_seconds=21600)
            if config['workload']=='nic':
                peer=work/'nic-peer'
                features.command(repo,work/'peer-build',case_id,'peer_build',[
                    repo/'.conda-env/bin/c++','-O2','-std=c++17','-static',
                    repo/'automation/chia_firesim_circt/feature_workloads/nic_peer.cc','-o',peer])
                frozen['feature_nic']=dict(peer_binary=str(peer),peer_sha256=bg.digest(peer),packets=128,link_latency=6405,timeout_seconds=7200,
                    bindings=[dict(widget=i,port='circt_'+directory.name[-24:]+'_'+str(i),mode=mode) for i,mode in enumerate(config['nic_modes'])])
            write(work/'frozen-inputs.json',frozen)
            recipes=work/'recipes.yaml'; recipes.write_text(yaml.safe_dump({name:design['recipe']},sort_keys=False))
            hwdb_path=work/'candidate-hwdb.yaml'
            build_lock=Path(os.environ.get('CHIA_F2_BUILD_LOCK',str(shared/'.f2-build.lock')))
            with bg.locked(build_lock):
                # Reuse only actual hardware identities, independently of runtime mode.
                old=None
                for receipt in (shared/'background-features').glob('*/execution/build-receipt.json'):
                    previous=json.loads(receipt.read_text())
                    if previous.get('hardware_key')==manifest['hardware_key'] and previous.get('agfi'):
                        try:
                            bg.validate([previous['build_log'],previous['build_command']])
                            old_command=json.loads(Path(previous['build_command']['path']).read_text())
                            if (type(old_command.get('returncode')) is not int or old_command['returncode']!=0
                                    or old_command.get('timed_out') is not False): continue
                            bg.validate([old_command['stdout'],old_command['stderr']])
                        except (OSError,KeyError,ValueError): continue
                        old=previous; break
                if old:
                    hwdb={name:dict(old['hwdb_entry'])}; result['hardware_reuse']=str(receipt)
                    result['build_log']=old['build_log']['path']
                    shutil.copy2(old['build_command']['path'],work/'buildbitstream-command.json')
                else:
                    entry=repo/'sims/firesim/deploy/built-hwdb-entries'/name
                    # Remove the possibility of accepting a stale delivery;
                    # wall-clock vs filesystem timestamp granularity is not a
                    # reliable freshness test for a quickly returning build.
                    if entry.exists(): shutil.move(str(entry),str(work/'previous-hwdb-entry.yaml'))
                    log=run_manager(repo,work,'buildbitstream',recipes,['-q'])
                    if not entry.is_file(): raise ValueError('No fresh feature HWDB entry')
                    hwdb=yaml.safe_load(entry.read_text()); result['build_log']=str(log)
                entry=hwdb[name]
                if not isinstance(entry.get('agfi'),str) or not entry['agfi'].startswith('agfi-'): raise ValueError('Feature build has no AGFI')
                entry.update(deploy_quintuplet_override=frozen['quintuplet'],deploy_makefrag_override=str(repo/'generators/firechip/chip/src/main/makefrag/firesim'))
                hwdb_path.write_text(yaml.safe_dump(hwdb,sort_keys=False))
                write(work/'build-receipt.json',dict(hardware_key=manifest['hardware_key'],agfi=entry['agfi'],hwdb_entry=entry,
                      build_log=record(result['build_log']),build_command=record(work/'buildbitstream-command.json'),
                      stage='pending_runtime',full_acceptance=False))
            tag,runtime_path=runtime(repo,work,manifest,design)
            extra=['-c',str(runtime_path),'-a',str(hwdb_path)]
            runtime_error=None
            try:
                for action in ('launchrunfarm','infrasetup','runworkload'):
                    launched=launched or action=='launchrunfarm'
                    run_manager(repo,work,action,recipes,extra,timeout=7200)
                result['checker']=run_checker(repo,work,manifest,tag)
                result.update(passed=True,agfi=entry['agfi'],linux_exercised=True)
            except BaseException as error:
                runtime_error=error
                raise
            finally:
                if launched:
                    cleanup_runfarm(repo,work,recipes,extra,result,runtime_error=runtime_error,
                                    collect_failure=runtime_error is not None and action=='runworkload')
            bg.validate(manifest['source_files']+manifest['shared_immutable_images']+manifest['accepted_files']+[manifest['driver']]+manifest.get('guest_toolchain', []))
            validate_context(manifest.get('context'),config,case_id)
            result=linux_receipts(directory,manifest,result)
        except BaseException as error:
            result.update(passed=False,error=type(error).__name__+': '+str(error))
        write(directory/'result.json',result)
        data.update(phase='passed' if result['passed'] else 'failed',result=str(directory/'result.json'))
        write(directory/'job.json',data)
    return 0 if result['passed'] else 1


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',type=Path); parser.add_argument('--case'); parser.add_argument('--implementation',type=Path)
    parser.add_argument('--output-dir',type=Path); parser.add_argument('--snapshot',type=Path)
    parser.add_argument('--check-outputs',type=Path); parser.add_argument('--result-tag')
    parser.add_argument('--context',type=Path,help='Optional bound diagnostic identity/artifact context; no semantic/formal signoff')
    args=parser.parse_args()
    if args.check_outputs:
        if not args.output_dir or not args.result_tag: parser.error('checker requires output-dir/result-tag')
        manifest=json.loads(args.check_outputs.read_text())
        check_outputs(Path(manifest['repo']),args.output_dir.resolve(),manifest,args.result_tag)
        return 0
    if args.snapshot: return run_snapshot(args.snapshot.resolve())
    if not all((args.repo,args.case,args.implementation,args.output_dir)): parser.error('submission requires repo/case/implementation/output-dir')
    context=json.loads(args.context.read_text()) if args.context else None
    print(json.dumps(submit(args.repo.resolve(),args.case,args.implementation.resolve(),args.output_dir.resolve(),context=context),indent=2))
    return 0

if __name__=='__main__': raise SystemExit(main())
