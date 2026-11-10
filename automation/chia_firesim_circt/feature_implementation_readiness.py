"""Check the implementation milestone only; never award formal/F2 acceptance.

Readiness binds all feature_scope representatives to production artifacts and
successful compile, driver and checker command receipts for the current sources.
It deliberately does not claim those workloads ran on an FPGA or passed Jasper.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import yaml

ROLES = {'config', 'rtl', 'driver', 'workload', 'checker', 'execution_adapter'}
PURPOSES = {'production_compile', 'driver_build', 'workload_checker'}


def source_identity(repo):
    """Hash compiler/automation source content, excluding mutable evidence."""
    repo = Path(repo)
    roots = [repo / name for name in (
        'sims/firesim/sim/goldengate-circt', 'automation/chia_firesim_circt',
        'generators/firechip', 'generators/chipyard/src/main/scala/config',
        'generators/chipyard/src/main/scala/iobinders', 'generators/icenet/src',
        'sims/firesim/sim/midas/src', 'sims/firesim/sim/firesim-lib',
        'sims/firesim/sim/make', 'sims/firesim/sim/src',
        'sims/firesim/deploy/buildtools', 'sims/firesim/deploy/runtools',
        'sims/firesim/sim/custom-runtime-configs')]
    suffixes = {'.cpp', '.cc', '.c', '.h', '.td', '.py', '.scala', '.fir',
                '.json', '.yaml', '.yml', '.sh', '.cmake', '.mk', '.sbt',
                '.sv', '.v', '.vh', '.tcl', '.dts'}
    excluded = {'logs', 'profiles', '__pycache__', 'build', 'generated-src', 'target',
                'output', 'migration-handoff', '.git'}
    files = {}
    for root in roots:
        for directory, directories, names in os.walk(root):
            directories[:] = [name for name in directories if name not in excluded]
            for name in names:
                path = Path(directory) / name
                if path.is_file() and (path.suffix in suffixes or name in {'CMakeLists.txt', 'Makefile'}):
                    files[str(path.relative_to(repo))] = hashlib.sha256(path.read_bytes()).hexdigest()
    for name in ['sims/firesim/sim/Makefile', 'sims/firesim/sim/build.sbt']:
        path = repo / name
        if path.exists():
            files[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()


def validate_readiness(report, repo, started_ns):
    """Reject text-only, partial, stale or drifting implementation declarations."""
    report, repo = Path(report), Path(repo)
    if not report.is_file() or report.stat().st_mtime_ns < started_ns:
        raise ValueError('A fresh implementation readiness report is required for this turn')
    original = report.read_bytes()
    data = json.loads(original)
    identity = source_identity(repo)
    if data.get('schema_version') != 1 or data.get('source_identity') != identity:
        raise ValueError('Readiness report does not bind the current compiler/automation sources')
    scope = yaml.safe_load((repo / 'automation/chia_firesim_circt/feature_scope.yaml').read_text())['features']
    expected = {case: name for name, feature in scope.items()
                if feature.get('required') for case in feature['representative_cases']}
    features = {name for name, feature in scope.items() if feature.get('required')}
    if (set(data.get('features', {})) != features or data.get('remaining') != []
            or any(value.get('implemented') is not True or value.get('production_integrated') is not True
                   or value.get('remaining') != [] for value in data['features'].values())):
        raise ValueError('All required feature implementations and production integration must be complete')
    bindings = {}

    def bound(record, nonempty=True):
        if not isinstance(record, dict) or not isinstance(record.get('path'), str):
            raise ValueError('Artifact/receipt requires path and SHA256')
        path = Path(record['path'])
        path = path if path.is_absolute() else repo / path
        payload = path.read_bytes()
        if (nonempty and not payload) or hashlib.sha256(payload).hexdigest() != record.get('sha256'):
            raise ValueError(f'Stale, empty or unbound implementation artifact: {path}')
        bindings[path] = record['sha256']
        return payload

    bound(data.get('compiler'))
    cases = data.get('cases', [])
    if len(cases) != len(expected) or {case.get('case_id') for case in cases} != set(expected):
        raise ValueError('Every bounded representative case is required exactly once')
    for case in cases:
        if case.get('feature') != expected[case['case_id']] or set(case.get('artifacts', {})) != ROLES:
            raise ValueError('Case must bind its feature and production config/RTL/driver/workload/checker/adapter')
        for record in case['artifacts'].values():
            bound(record)
        purposes = set()
        for record in case.get('commands', []):
            command = json.loads(bound(record))
            if (command.get('case_id') != case['case_id'] or command.get('source_identity') != identity
                    or type(command.get('returncode')) is not int
                    or command['returncode'] != 0 or command.get('timed_out') is not False
                    or not isinstance(command.get('argv'), list) or not command['argv']
                    or not all(isinstance(arg, str) for arg in command['argv'])):
                raise ValueError('Case command must have succeeded for the current sources')
            bound(command.get('stdout'), nonempty=False)
            bound(command.get('stderr'), nonempty=False)
            purposes.add(command.get('purpose'))
        if not PURPOSES <= purposes:
            raise ValueError('Case needs production compile, driver build and workload/checker validation receipts')
    if (source_identity(repo) != identity or report.read_bytes() != original
            or any(hashlib.sha256(path.read_bytes()).hexdigest() != digest for path, digest in bindings.items())):
        raise ValueError('Implementation inputs changed during readiness validation')
    return {'implementation_ready': True, 'verified_complete': False,
            'source_identity': identity, 'representative_cases': len(expected),
            'bound_files': len(bindings), 'report': str(report)}
