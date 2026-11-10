#!/usr/bin/env python3
"""Build the declared feature matrix and publish validated implementation evidence.

This command performs production compilation, actual driver linking and native
workload/checker validation. It never submits an FPGA job or launches formal.
An incomplete, stale or source-drifting matrix cannot publish readiness.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sys
import time

import feature_execution as execution
import feature_implementation_readiness as readiness
from downstream_common import record, write


CHECKERS = dict(threads='thread_check.py', instrumentation='instrumentation_check.py', nic='nic_check.py')
DEFAULT_COMPILER = Path('/scratch/jfx/goldengate-circt-f2-release-build/goldengate-circt')


def bound(binding):
    """Validate an existing producer binding without rewriting its receipt."""
    if not isinstance(binding, dict) or not isinstance(binding.get('path'), str):
        raise ValueError('Expected an artifact/receipt path and SHA256')
    actual = record(binding['path'])
    if actual['sha256'] != binding.get('sha256') or ('bytes' in binding and actual['bytes'] != binding['bytes']):
        raise ValueError('Implementation artifact changed: ' + binding['path'])
    return Path(binding['path'])


def successful_command(path, case_id, purpose, identity):
    path = Path(path)
    data = json.loads(path.read_text())
    if (data.get('case_id') != case_id or data.get('purpose') != purpose or
            data.get('source_identity') != identity or type(data.get('returncode')) is not int or
            data['returncode'] != 0 or data.get('timed_out') is not False or
            not isinstance(data.get('argv'), list) or not data['argv'] or
            not all(isinstance(arg, str) for arg in data['argv'])):
        raise ValueError('A successful current-source ' + purpose + ' receipt is required: ' + str(path))
    bound(data.get('stdout')); bound(data.get('stderr'))
    return record(path)


@contextmanager
def cached_command_identity(repo, identity):
    """Cache only this process's nested command stamps, never the fresh audits.

    Child workload-validator processes independently compute their identities.
    The final uncached source hash rejects any edit during the whole batch.
    """
    original = execution.source_identity
    def cached(path):
        if Path(path).resolve() != repo:
            return original(path)
        return identity
    execution.source_identity = cached
    try:
        yield
    finally:
        execution.source_identity = original


def compile_evidence(directory, case_id, config, compiler, generator, identity):
    manifest = json.loads((directory/'compile.json').read_text())
    if manifest.get('case_id') != case_id or manifest.get('config') != config:
        raise ValueError('Production compile configuration differs: ' + case_id)
    if bound(manifest['compiler']) != compiler or manifest['compiler']['sha256'] != record(compiler)['sha256']:
        raise ValueError('Production compiler differs: ' + case_id)
    bound(manifest['generator'])
    if generator is not None and manifest['generator']['sha256'] != record(generator)['sha256']:
        raise ValueError('Production generator jar differs: ' + case_id)
    for item in manifest['inputs'] + manifest['products'] + manifest.get('resources', []):
        bound(item)
    expected = directory/'production_compile/command.json'
    if bound(manifest['command']) != expected:
        raise ValueError('Compile manifest does not bind its production command')
    successful_command(expected, case_id, 'production_compile', identity)
    execution.enabled_contract(directory/'generated', config)
    return manifest


def case_evidence(repo, directory, case_id, config, compiler, generator, identity):
    compile_manifest = compile_evidence(directory, case_id, config, compiler, generator, identity)
    driver = json.loads((directory/'driver.json').read_text())
    driver_path = bound(driver['driver'])
    if not os.access(driver_path, os.X_OK):
        raise ValueError('Production driver is not executable: ' + str(driver_path))
    if bound(driver['command']) != directory/'driver_build/command.json':
        raise ValueError('Driver manifest does not bind its production build command')
    commands = [successful_command(directory/(purpose+'/command.json'), case_id, purpose, identity)
                for purpose in sorted(readiness.PURPOSES)]
    validation = json.loads((directory/'workload-validation/result.json').read_text())
    if validation.get('passed') is not True or validation.get('case_id') != case_id:
        raise ValueError('Actual workload/tool checker validation did not pass: ' + case_id)
    guests = validation.get('guest_manifests', [])
    if len(guests) != 1:
        raise ValueError('Expected the bounded representative private Linux preparation manifest')
    workload = bound(guests[0])
    # The private preparation manifest carries the real kernel/rootfs/overlay
    # hashes, while the checker receipt binds actual tool execution and output.
    prepared = json.loads(workload.read_text())
    if prepared.get('schema_version') != 1 or not prepared.get('private_rootfs'):
        raise ValueError('Private Linux preparation manifest is incomplete')
    for item in validation.get('commands', []):
        command_path = bound(item)
        details = json.loads(command_path.read_text())
        successful_command(command_path, case_id, details['purpose'], identity)
    descriptor = dict(schema_version=1, case_id=case_id, source_identity=identity,
        config=config, recipe=execution.recipe(config, repo), compiler=compile_manifest['compiler'],
        generator=compile_manifest['generator'])
    descriptor_path = directory/'implementation-config.json'
    if descriptor_path.exists() and json.loads(descriptor_path.read_text()) != descriptor:
        raise ValueError('Existing case descriptor differs; preserve it and use a fresh output directory')
    if not descriptor_path.exists():
        write(descriptor_path, descriptor)
    artifacts = dict(config=record(descriptor_path), rtl=record(directory/'generated'/('FireSim-generated.sv')),
        driver=record(driver_path), workload=record(workload),
        checker=record(repo/'automation/chia_firesim_circt/feature_workloads'/CHECKERS[config['workload']]),
        execution_adapter=record(repo/'automation/chia_firesim_circt/feature_f2.py'))
    return dict(case_id=case_id, feature=config['feature'], artifacts=artifacts, commands=commands)


def ensure_case(repo, directory, case_id, config, compiler, generator, identity):
    directory.mkdir(parents=True, exist_ok=True)
    if (directory/'compile.json').exists():
        compile_evidence(directory, case_id, config, compiler, generator, identity)
    else:
        if any((directory/name).exists() for name in ('inputs', 'generated', 'production_compile')):
            raise ValueError('Partial compile evidence is retained; use a fresh case/output directory: ' + str(directory))
        if generator is None:
            raise ValueError('--generator-jar is required for a case that has not been compiled: ' + case_id)
        execution.compile_case(repo, directory, case_id, config, compiler, generator)
        compile_evidence(directory, case_id, config, compiler, generator, identity)
    if not (directory/'driver.json').exists():
        if (directory/'driver_build').exists() or (directory/'driver').exists():
            raise ValueError('Partial driver evidence is retained; use a fresh case/output directory: ' + str(directory))
        execution.build_driver(repo, directory, case_id, config)
    if not (directory/'workload-validation/result.json').exists():
        if (directory/'workload-validation').exists() or (directory/'workload_checker').exists():
            raise ValueError('Partial workload evidence is retained; use a fresh case/output directory: ' + str(directory))
        execution.command(repo, directory/'workload_checker', case_id, 'workload_checker', [
            repo/'.conda-env/bin/python3', '-B', repo/'automation/chia_firesim_circt/feature_workload_validate.py',
            '--repo', repo, '--case', case_id, '--directory', directory/'workload-validation',
            '--generated', directory/'generated'])
    return case_evidence(repo, directory, case_id, config, compiler, generator, identity)


def run_matrix(repo, output, compiler=DEFAULT_COMPILER, generator=None, selected=None, report=None,
               result_name='matrix-result.json', publish=True):
    repo, output, compiler = (Path(path).resolve() for path in (repo, output, compiler))
    generator = Path(generator).resolve() if generator is not None else None
    report = Path(report).resolve() if report is not None else output/'readiness.json'
    if not result_name or Path(result_name).name != result_name:
        raise ValueError('--result-name must be a plain file name')
    result_path = output/result_name
    started_ns = time.time_ns()
    identity = readiness.source_identity(repo)  # Always the real module function.
    compiler_binding = record(compiler)
    if generator is not None:
        generator_binding = record(generator)
    registry = execution.cases(repo)
    selected = list(registry) if selected is None else list(selected)
    if len(set(selected)) != len(selected) or not set(selected) <= set(registry):
        raise ValueError('--case must name distinct declared representatives')
    output.mkdir(parents=True, exist_ok=True)
    result = dict(schema_version=1, source_identity=identity, compiler=compiler_binding,
        selected_cases=selected, passed=False, readiness_published=False,
        verification_claim='Implementation evidence only; no formal or FPGA acceptance', cases=[], remaining=[])
    try:
        with cached_command_identity(repo, identity):
            for case_id in selected:
                case = ensure_case(repo, output/case_id, case_id, registry[case_id], compiler, generator, identity)
                result['cases'].append(case)
                write(result_path, result)
            # Aggregation also permits separate selected-case invocations. An
            # unselected case must already have complete, current receipts.
            by_id = {case['case_id']:case for case in result['cases']}
            for case_id, config in registry.items():
                if case_id in by_id:
                    continue
                if not publish:
                    result['remaining'].append(dict(case_id=case_id, reason='Not selected by this worker'))
                    continue
                try:
                    by_id[case_id] = case_evidence(repo, output/case_id, case_id, config, compiler, generator, identity)
                except (OSError, ValueError, KeyError) as error:
                    result['remaining'].append(dict(case_id=case_id, reason=str(error)))
        if readiness.source_identity(repo) != identity:
            raise ValueError('Sources changed during the matrix batch; no readiness can be published')
        if record(compiler) != compiler_binding or (generator is not None and record(generator) != generator_binding):
            raise ValueError('Compiler/generator changed during the matrix batch')
        result['cases'] = [by_id[key] for key in registry if key in by_id]
        result['passed'] = True  # Selected work succeeded; completeness remains explicit.
        if publish and not result['remaining']:
            required = {config['feature'] for config in registry.values()}
            data = dict(schema_version=1, source_identity=identity, compiler=compiler_binding, remaining=[],
                features={feature:dict(implemented=True, production_integrated=True, remaining=[]) for feature in sorted(required)},
                cases=result['cases'])
            candidate = output/'.readiness-candidate.json'
            write(candidate, data)
            readiness.validate_readiness(candidate, repo, started_ns)
            report.parent.mkdir(parents=True, exist_ok=True)
            # The requested report is written only after the complete candidate
            # passed the unmodified readiness validator.
            write(report, data)
            result['readiness_validation'] = readiness.validate_readiness(report, repo, started_ns)
            result['readiness_published'] = True
            result['readiness'] = record(report)
        write(result_path, result)
        return result
    except BaseException as error:
        result.update(passed=False, error=type(error).__name__ + ': ' + str(error))
        write(result_path, result)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--compiler', type=Path, default=DEFAULT_COMPILER)
    parser.add_argument('--generator-jar', type=Path, help='Required only for newly compiled cases')
    parser.add_argument('--case', action='append', help='Build selected cases; publish only if the whole matrix is current')
    parser.add_argument('--readiness', type=Path, help='Requested readiness.json path; defaults to OUTPUT_DIR/readiness.json')
    parser.add_argument('--result-name', default='matrix-result.json', help='Separate receipt name for a selected-case worker')
    parser.add_argument('--no-publish', action='store_true', help='Build selected cases without readiness publication; exit 0 on selected success')
    args = parser.parse_args()
    try:
        result = run_matrix(args.repo, args.output_dir, args.compiler, args.generator_jar, args.case, args.readiness,
                            args.result_name, not args.no_publish)
    except (ValueError, OSError, KeyError) as error:
        print(type(error).__name__ + ': ' + str(error), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0 if result['readiness_published'] or args.no_publish else 2


if __name__ == '__main__':
    raise SystemExit(main())
