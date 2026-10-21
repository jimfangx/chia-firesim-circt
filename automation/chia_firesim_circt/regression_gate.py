#!/usr/bin/env python3
"""Version Rocket regression inputs and validate one exact tagged metasim run."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

GROUPS = ('integer', 'memory', 'control', 'atomics', 'csr', 'traps', 'vm',
          'interrupts', 'fp', 'icache', 'workloads', 'uart')
BINARIES = ('circt-fame-suite', 'circt-fame-rocket-suite') + tuple('circt-fame-' + g for g in GROUPS)


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def install_workload(descriptor, deploy):
    # FireSim concatenates its workload directory even for absolute inputs.
    # Keep the generator's source paths, but install directory-local names.
    manifest = json.loads(descriptor.read_text())
    inputs = deploy / 'workloads' / manifest['benchmark_name']
    inputs.mkdir(parents=True, exist_ok=True)
    for key in ('common_bootbinary', 'common_rootfs'):
        source = Path(manifest[key])
        if not source.is_absolute() or not source.is_file():
            raise ValueError(f'Missing absolute workload input: {source}')
        destination = inputs / source.name
        shutil.copy2(source, destination)
        if sha256(destination) != sha256(source):
            raise ValueError(f'Installed workload input differs: {destination}')
        manifest[key] = source.name
    (deploy / 'workloads' / descriptor.name).write_text(json.dumps(manifest, indent=2) + '\n')


def prepare(root, deploy, log_dir, versions):
    repo = root.parents[3]
    compiler = Path(shutil.which('riscv64-unknown-elf-gcc') or
                    repo / '.conda-env/riscv-tools/bin/riscv64-unknown-elf-gcc').resolve()
    inputs = sorted(set([*root.glob('*.c'), *root.glob('*.h'), root / 'Makefile',
                         root / 'fame_link.ld', root / 'prepare_workloads.py',
                         root / 'validate_spike.py', root / 'check_uart.py',
                         root / 'test_validator.py', deploy / 'config_runtime.yaml',
                         Path(__file__).resolve(), compiler,
                         * (repo / 'software/firemarshal/test/bare').glob('*')]))
    identity = {str(path): sha256(path) for path in inputs if path.is_file()}
    version = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    archive = versions / version
    inventory_path = archive / 'binary-inventory.json'
    rebuild = not inventory_path.is_file()
    if not rebuild:
        inventory = json.loads(inventory_path.read_text())
        rebuild = any(not (root / name).is_file() or sha256(root / name) != inventory[name]['sha256']
                      for name in BINARIES)
    if rebuild:
        # A compiler/runtime change must rebuild even when make timestamps agree.
        subprocess.run(['make', '-B', '-C', str(root), 'suite', 'manifests'], check=True)
        subprocess.run(['make', '-C', str(root), 'validate-spike'], check=True)
        subprocess.run([sys.executable, '-m', 'unittest', '-v', 'test_validator'], cwd=root, check=True)
        archive.mkdir(parents=True, exist_ok=True)
        for name in BINARIES:
            shutil.copy2(root / name, archive / name)
        shutil.copytree(root / 'spike-results', archive / 'spike-results', dirs_exist_ok=True)
        shutil.copy2(root / 'binary-inventory.json', inventory_path)
        (archive / 'input-identity.json').write_text(json.dumps(identity, indent=2) + '\n')
        # Invalidate previous metasim evidence if its installed ELF was changed.
        (archive / 'standalones-passed.json').unlink(missing_ok=True)
    inventory = json.loads(inventory_path.read_text())
    expected = json.loads((archive / 'spike-results/expected-results.json').read_text())
    for name in BINARIES:
        if sha256(root / name) != inventory[name]['sha256']:
            raise ValueError(f'ELF inventory mismatch: {name}')
        if name in expected['binaries'] and expected['sha256'][name] != inventory[name]['sha256']:
            raise ValueError(f'Spike reference uses another ELF: {name}')
        install_workload(root / 'firesim-workloads' / (name + '.json'), deploy)
    metadata = {'version': version, 'archive': str(archive), 'binaries': inventory,
                'portable_reference': str(archive / 'spike-results/expected-results.json'),
                'initial_standalones_required': not (archive / 'standalones-passed.json').is_file(),
                'rocket_differential': 'pending SFC UART-bearing baseline'}
    (log_dir / 'regression-inputs.json').write_text(json.dumps(metadata, indent=2) + '\n')
    print(json.dumps(metadata, indent=2))


def validate(root, results_root, binary, suffix, log_dir, reference):
    checker_spec = importlib.util.spec_from_file_location('fame_checker', root / 'check_uart.py')
    checker = importlib.util.module_from_spec(checker_spec)
    checker_spec.loader.exec_module(checker)
    marker = f'-{binary}-{suffix}'
    logs = [p for p in results_root.rglob('uartlog') if p.parent.parent.name.endswith(marker)]
    report = {'binary': binary, 'suffix_tag': suffix, 'reference': str(reference) if reference else None,
              'differential': 'portable Spike' if reference else 'pending SFC UART-bearing baseline',
              'failed_groups': []}
    try:
        if len(logs) != 1:
            raise ValueError(f'Expected one exact UART log for {marker}, found {logs}')
        uartlog = logs[0]
        output = uartlog.read_text(errors='replace').replace('\r', '')
        report.update(uartlog=str(uartlog), result_directory=str(uartlog.parent.parent),
                      elf_sha256=sha256(root / binary))
        if reference:
            data = json.loads(reference.read_text())
            if data['sha256'][binary] != report['elf_sha256']:
                raise ValueError('reference ELF identity differs from candidate workload')
            expected = data['binaries'][binary]
            report['reference_sha256'] = sha256(reference)
        else:
            expected = None
        wanted_mask = 2047 if binary == 'circt-fame-suite' else (4095 if binary == 'circt-fame-rocket-suite'
                      else 1 << GROUPS.index(binary.removeprefix('circt-fame-')))
        selected = [g for i, g in enumerate(GROUPS) if wanted_mask & (1 << i)]
        passed = {m[1] for m in checker.TEST.finditer(output)}
        report['failed_groups'] = [g for g in selected if g not in passed]
        result = checker.validate(output, expected)
        if result['mask'] != wanted_mask:
            raise ValueError(f'wrong selected workload mask: {result["mask"]} != {wanted_mask}')
        report.update(result=result, failed_groups=[], returncode=0)
        print(f'PASS {binary}: {result["checks"]} checks {result["signature"]}; {report["differential"]}')
    except (ValueError, KeyError, OSError) as error:
        report.update(error=str(error), returncode=1)
        print(f'FAIL {binary}: {error}; failed groups: {report["failed_groups"]}', file=sys.stderr)
    (log_dir / 'validation-result.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    return report['returncode']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'validate'))
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--log-dir', type=Path, required=True)
    parser.add_argument('--deploy', type=Path)
    parser.add_argument('--versions', type=Path)
    parser.add_argument('--results-root', type=Path)
    parser.add_argument('--binary', choices=BINARIES)
    parser.add_argument('--suffix-tag')
    parser.add_argument('--reference', type=Path)
    args = parser.parse_args()
    args.log_dir.mkdir(parents=True, exist_ok=True)
    if args.action == 'prepare':
        prepare(args.root, args.deploy, args.log_dir, args.versions)
    else:
        raise SystemExit(validate(args.root, args.results_root, args.binary, args.suffix_tag,
                                 args.log_dir, args.reference))


if __name__ == '__main__':
    main()
