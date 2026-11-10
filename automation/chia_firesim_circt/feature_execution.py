#!/usr/bin/env python3
"""Production compile, driver and private Linux preparation for bounded features.

The separate feature_f2 adapter dispatches immutable cloud/FPGA execution.
Compilation, driver linking and checker validation are implementation evidence;
none of these substitute for the later full CHIA acceptance graph.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import time
import yaml

from feature_implementation_readiness import source_identity
from downstream_common import record, write

BASE = 'FireSim-generated'
REQUIRED = tuple(BASE + suffix for suffix in ('.sv', '.const.h', '.const.vh', '.partition.const.h',
    '.peer2peer.const.yaml', '.defines.vh', '.synthesis.xdc', '.implementation.xdc', '.memory-origins.json',
    '.f2-cpu-stream-protocol.json'))


def cases(repo):
    registry = yaml.safe_load((repo / 'automation/chia_firesim_circt/feature_cases.yaml').read_text())
    scope = yaml.safe_load((repo / 'automation/chia_firesim_circt/feature_scope.yaml').read_text())
    expected = {case: feature for feature, data in scope['features'].items() if data.get('required')
                for case in data['representative_cases']}
    if registry['schema_version'] != 1 or set(registry['cases']) != set(expected):
        raise ValueError('Execution registry must cover exactly the declared bounded representatives')
    for case, config in registry['cases'].items():
        if config['feature'] != expected[case]:
            raise ValueError('Case feature identity differs')
    return registry['cases']


def command(repo, directory, case_id, purpose, argv, *, timeout=1800, cwd=None, manager=False):
    directory.mkdir(parents=True, exist_ok=True)
    argv = list(map(str, argv))
    if manager:
        # SSH setup is enabled. Preserve arguments through environment sourcing.
        shell = ('set -e\nfeature_repo=$1\nshift\nfeature_args=("$@")\nset --\n'
                 'source "${CONDA_EXE%/bin/conda}/etc/profile.d/conda.sh"\n'
                 'cd "$feature_repo/sims/firesim"\nsource ./sourceme-manager.sh\n'
                 'cd sim\nexec "${feature_args[@]}"')
        argv = ['bash', '-lc', shell, 'chia-feature', str(repo), *argv]
    receipt = dict(case_id=case_id, purpose=purpose, source_identity=source_identity(repo),
                   argv=argv, cwd=str(cwd or repo), timed_out=False, returncode=None)
    start = time.monotonic()
    with (directory/'stdout.log').open('wb') as out, (directory/'stderr.log').open('wb') as err:
        try:
            process = subprocess.Popen(argv, cwd=cwd or repo, stdout=out, stderr=err,
                start_new_session=True,
                preexec_fn=lambda: os.sched_setaffinity(0, sorted(os.sched_getaffinity(0))[:8]))
            try:
                receipt['returncode'] = process.wait(timeout=timeout)
            except BaseException:
                try: os.killpg(process.pid,signal.SIGTERM)
                except ProcessLookupError: pass
                try: process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    try: os.killpg(process.pid,signal.SIGKILL)
                    except ProcessLookupError: pass
                    process.wait()
                try: os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError: pass
                raise
        except subprocess.TimeoutExpired:
            receipt['timed_out'] = True
    receipt.update(stdout=record(directory/'stdout.log'), stderr=record(directory/'stderr.log'),
                   wall_seconds=time.monotonic()-start)
    write(directory/'command.json', receipt)
    if receipt['returncode'] != 0 or receipt['timed_out']:
        raise ValueError(f'{purpose} failed; {directory}/command.json')
    return directory/'command.json'


def recipe(config, repo):
    return dict(PLATFORM='f2', TARGET_PROJECT='firesim',
        TARGET_PROJECT_MAKEFRAG=str(Path(repo).resolve()/'generators/firechip/chip/src/main/makefrag/firesim'),
        DESIGN='FireSim', TARGET_CONFIG=config['target_config'], PLATFORM_CONFIG=config['platform_config'],
        deploy_quintuplet=None, platform_config_args=dict(fpga_frequency=50, build_strategy='TIMING'),
        post_build_hook=None, metasim_customruntimeconfig=None, bit_builder_recipe='bit-builder-recipes/f2.yaml')


def compile_case(repo, directory, case_id, config, compiler, jar, source_inputs=None):
    name = 'firechip.chip.FireSim.' + config['target_config']
    inputs, generated = directory/'inputs', directory/'generated'
    inputs.mkdir(); generated.mkdir()
    java = repo/'.conda-env/lib/jvm/bin/java'
    elaboration = [java, '-Xmx12G', '-Xss8M', '-XX:ActiveProcessorCount=4', '-cp', jar,
        'chipyard.Generator', '--target-dir', inputs, '--name', name,
        '--top-module', 'firechip.chip.FireSim', '--legacy-configs', 'firechip.chip:'+config['target_config'],
        '--emit-legacy-sfc']
    fir, annotations = inputs/(name+'.sfc.fir'), inputs/(name+'.anno.json')
    if source_inputs is None:
        command(repo, directory/'elaboration', case_id, 'elaboration', elaboration,
                manager=True)
    else:
        # Reuse precisely bound target elaboration for runtime-only modes or a
        # compiler replay. Compilation still executes the production pipeline.
        sources=[Path(source_inputs)/path.name for path in (fir,annotations)]
        before=[record(path) for path in sources]
        for source,destination in zip(sources,(fir,annotations)):
            shutil.copy2(source,destination)
        if before != [record(path) for path in sources]:
            raise ValueError('Elaboration inputs changed during copy')
        write(directory/'elaboration-reuse.json',dict(inputs=before,generator=record(jar)))
    argv = [compiler, fir, '--annotation-file', annotations, '--output-dir', generated,
        '--compile-baseline', '--platform-config', config['platform_config'], *config.get('compiler_flags', [])]
    receipt = command(repo, directory/'production_compile', case_id, 'production_compile', argv,
                      cwd=repo/'sims/firesim/sim', manager=True)
    for filename in REQUIRED:
        if not (generated/filename).is_file():
            raise ValueError('Incomplete production compile: '+filename)
    # Finalize the same blackbox inclusion used by production make/CHIA. Retain
    # compiler bytes and resource identities for inspection instead of editing an oracle.
    rtl = generated/(BASE+'.sv')
    shutil.copy2(rtl, generated/(BASE+'.raw.sv'))
    resources = []
    listing = generated/'firrtl_black_box_resource_files.f'
    if listing.exists():
        with rtl.open('ab') as output:
            for line in listing.read_text().splitlines():
                if not line.strip(): continue
                resource = Path(line.strip())
                resource = resource if resource.is_absolute() else repo/'sims/firesim/sim'/resource
                resources.append(record(resource))
                output.write(resource.read_bytes()+b'\n')
    enabled_contract(generated, config)
    manifest = dict(case_id=case_id, config=config, compiler=record(compiler), generator=record(jar),
                    inputs=[record(fir), record(annotations)], resources=resources,
                    products=[record(p) for p in sorted(generated.iterdir()) if p.is_file()],
                    command=record(receipt), verification_claim='none: compilation only')
    write(directory/'compile.json', manifest)
    return manifest


def enabled_contract(generated, config):
    header = (generated/(BASE+'.const.h')).read_text()
    if config['workload'] in ('threads', 'instrumentation'):
        if 'new autocounter_t(' not in header or 'make_print_bridge_' not in header:
            raise ValueError('Representative requires enabled CSV and Print driver constructors')
    if config['feature'] == 'fame5':
        graph = json.loads((generated/'fame-model-graph.json').read_text())
        if graph['threaded_groups'] < 1 or graph['shared_threads'] < config['threads']:
            raise ValueError('Representative did not share all logical threads')
    if config['feature'] == 'multiclock_models':
        graph = json.loads((generated/'fame-model-graph.json').read_text())
        if graph['clock_domains'] < config['min_domains'] or graph['definitions'] < config['min_models']:
            raise ValueError('Representative lacks actual multiple transformed models/domains')
    if config['workload'] == 'nic':
        loopback = '--nic-mode=loopback' in config.get('compiler_flags', [])
        constructor = 'new simplenic_loopback_t(' if loopback else 'new simplenic_t('
        if header.count(constructor) != len(config['nic_modes']):
            raise ValueError('Representative NIC constructor count differs')


def build_driver(repo, directory, case_id, config):
    generated = directory/'generated'
    enabled_contract(generated, config)
    output = directory/'driver'
    argv = ['make', '-j4', 'f2', 'GOLDENGATE_COMPILER=circt', 'GENERATED_DIR='+str(generated),
            'OUTPUT_DIR='+str(output), *[key+'='+str(value) for key,value in recipe(config,repo).items()
                if key in ('PLATFORM','TARGET_PROJECT','TARGET_PROJECT_MAKEFRAG','DESIGN','TARGET_CONFIG','PLATFORM_CONFIG')]]
    argv += [arg for path in sorted(generated.iterdir()) if path.is_file() for arg in ('-o', str(path))]
    receipt = command(repo,directory/'driver_build',case_id,'driver_build',argv,manager=True)
    binary = output/'FireSim-f2'
    if not binary.is_file() or not os.access(binary,os.X_OK):
        raise ValueError('Production make did not link the actual F2 simulator driver')
    write(directory/'driver.json',dict(driver=record(binary),command=record(receipt)))
    return binary


def guest_assets(repo, directory, case_id, config):
    src = repo/'automation/chia_firesim_circt/feature_workloads'
    assets=directory/'assets'; assets.mkdir(parents=True)
    overlays=[]
    if config['workload']=='threads':
        binary=assets/'circt-thread-state'
        from guest_toolchain import build
        build(repo,directory/'guest_build',case_id,src/'thread_state.c',binary,pthread=True,command=command)
        script=src/'thread_guest.sh'; overlays=[(binary,'/usr/bin/circt-thread-state',0o755)]
    elif config['workload']=='instrumentation':
        # Execute the feature hook inside the standard validated boot/poweroff
        # script. Its failure propagates before shutdown can be declared.
        from prepare_f2_linux import GUEST_SCRIPT
        script=assets/'firemarshal.sh'
        script.write_text(GUEST_SCRIPT.replace("printf 'CIRCT_LINUX_POWEROFF: REQUESTED\\n'",
            "/usr/bin/circt-instrumentation || fail 'feature workload failed'\nprintf 'CIRCT_LINUX_POWEROFF: REQUESTED\\n'"))
        overlays=[(src/'instrumentation_guest.sh','/usr/bin/circt-instrumentation',0o755)]
    else:
        binary=assets/'circt-nic-packets'
        from guest_toolchain import build
        build(repo,directory/'guest_build',case_id,src/'nic_packets.c',binary,command=command)
        script=src/'nic_guest.sh'
        overlays=[(binary,'/usr/bin/circt-nic-packets',0o755)]
    from prepare_f2_linux import prepare_linux_workload
    roots=[]
    # A supernode has one Linux rootfs/driver UART per target chip. All widgets
    # still belong to the one shared F2 image/driver and use distinct bindings.
    count=len(config.get('nic_modes',[])) if config.get('supernode') else 1
    for node in range(count):
        node_overlays=list(overlays)
        if config['workload']=='nic':
            rows=assets/f'nic-node{node}.conf'
            bindings=list(enumerate(config['nic_modes'])) if count==1 else [(node,config['nic_modes'][node])]
            rows.write_text(''.join(f'{"normal" if mode=="normal" else "loopback"} {widget} eth{widget if count==1 else 0} 128\n'
                                    for widget,mode in bindings))
            node_overlays.append((rows,'/etc/circt-nic-case',0o644))
            module=repo/'software/firemarshal/boards/firechip/drivers/icenet-driver/icenet.ko'
            node_overlays.append((module,'/usr/lib/icenet.ko',0o644))
        roots.append(prepare_linux_workload(repo,directory/f'linux-node{node}',guest_script=script,overlays=node_overlays))
    write(directory/'workload.json',dict(case_id=case_id,nodes=[{key:str(value) for key,value in root.items()} for root in roots],
        verification_claim='none: private filesystem preparation only'))
    return roots


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',type=Path,required=True)
    parser.add_argument('--case',required=True)
    parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--compiler',type=Path,default=Path('/scratch/jfx/goldengate-circt-f2-release-build/goldengate-circt'))
    parser.add_argument('--generator-jar',type=Path)
    parser.add_argument('--input-directory',type=Path,help='Previously elaborated, hash-bound matching target inputs')
    parser.add_argument('--stage',choices=['compile','driver','workload','checker'],required=True)
    args=parser.parse_args(); repo=args.repo.resolve(); directory=args.directory.resolve()
    config=cases(repo)[args.case]; directory.mkdir(parents=True,exist_ok=True)
    if args.stage=='compile':
        if not args.generator_jar: parser.error('--generator-jar is required for production elaboration')
        compile_case(repo,directory,args.case,config,args.compiler.resolve(),args.generator_jar.resolve(),args.input_directory)
    elif args.stage=='driver': build_driver(repo,directory,args.case,config)
    elif args.stage=='workload': guest_assets(repo,directory,args.case,config)
    else:
        command(repo,directory/'workload_checker',args.case,'workload_checker',[
            repo/'.conda-env/bin/python3','-B',repo/'automation/chia_firesim_circt/feature_workload_validate.py',
            '--repo',repo,'--case',args.case,'--directory',directory/'workload-validation','--generated',directory/'generated'])
    return 0

if __name__=='__main__':
    raise SystemExit(main())
