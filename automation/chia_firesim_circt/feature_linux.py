#!/usr/bin/env python3
"""Execute every bounded enabled-feature Linux case through the real F2 adapter.

This is a Linux-only verification node. It never awards matched feature SFC
semantics, Jasper signoff or final port acceptance. Four passing baseline semantic
comparisons release the node; optionally require separate feature prebuild reports
as well. All submitted workers are joined before returning to source mutation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import threading
import time

import yaml

import feature_execution as execution
import feature_f2
import feature_implementation_matrix as matrix
import feature_implementation_readiness as readiness
import verification_nodes as nodes
from downstream_common import record, write


DEFAULT_MATRIX = Path('/scratch/jfx/goldengate-circt-f2-build/iteration42-feature-implementation/production-matrix-ready/matrix-result.json')
DEFAULT_COMPILER = Path('/scratch/jfx/goldengate-circt-f2-release-build/goldengate-circt')
FLAGS = ('local_bitstream_build', 'firesim_manager', 'f2_runfarm', 'linux_booted',
         'poweroff_observed', 'workload_passed', 'runfarm_cleanup')
RECEIPTS = {'build_log', 'manager_log', 'workload_evidence', 'workload_inputs',
            'frozen_inputs', 'build_config', 'hwdb'}
TERMINAL = {'FAILED', 'STOPPED', 'SUCCEEDED'}


def _read(path):
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict):
        raise ValueError('Expected JSON object: ' + str(path))
    return data


def _bound(binding):
    return matrix.bound(binding)


def _command(binding):
    data = _read(_bound(binding))
    if (type(data.get('returncode')) is not int or data['returncode'] != 0
            or data.get('timed_out') is not False or not isinstance(data.get('argv'), list)
            or not data['argv'] or not all(isinstance(arg, str) and arg for arg in data['argv'])):
        raise ValueError('Successful execution command receipt required')
    _bound(data.get('stdout')); _bound(data.get('stderr'))
    return data


def _cache_directory(index, case_id):
    matches = [item for item in index.get('cases', []) if item.get('case_id') == case_id]
    if len(matches) != 1:
        raise ValueError('Cached matrix must bind exactly one case: ' + case_id)
    config = _bound(matches[0]['artifacts']['config'])
    return config.parent


def _cached_elaboration(directory, case_id, config, compiler, generator):
    """Bind the exact target inputs; a stale global hash cannot bless old drivers."""
    manifest = _read(directory/'compile.json')
    if manifest.get('case_id') != case_id or manifest.get('config') != config:
        raise ValueError('Cached target/platform/compiler flags differ')
    # Target elaboration precedes the native compiler. Retain/bind the old
    # compiler record, but a new compiler can safely consume the exact same
    # generator/config-bound pre-MIDAS input. compile_evidence below still
    # forbids reusing old transformed RTL or its driver with a new compiler.
    _bound(manifest['compiler'])
    cached_generator = _bound(manifest['generator'])
    if generator is not None and record(generator)['sha256'] != manifest['generator']['sha256']:
        raise ValueError('Cached target generator differs')
    for item in manifest.get('inputs', []):
        _bound(item)
    inputs = manifest.get('inputs', [])
    if len(inputs) != 2 or {Path(item['path']).suffix for item in inputs} != {'.fir', '.json'}:
        raise ValueError('Cached exact pre-MIDAS FIRRTL and annotations are required')
    if any(Path(item['path']).parent != directory/'inputs' for item in inputs):
        raise ValueError('Cached pre-MIDAS inputs escape their case')
    _command(manifest['command'])
    return manifest, cached_generator


def prepare_case(repo, directory, case_id, config, options, source_identity, index):
    """Reuse current hash-bound products, otherwise replay real compile and link.

    Old target elaboration may be reused only with matching configuration and
    generator bindings. Changed compiler or global source identity forces a
    fresh production compilation/driver build in a new directory, preserving
    old evidence and avoiding seven unnecessary Scala target elaborations.
    """
    compiler = Path(options.get('compiler', DEFAULT_COMPILER)).resolve()
    generator = options.get('generator_jar')
    generator = Path(generator).resolve() if generator else None
    cached = None
    rejection = None
    if index is not None:
        try:
            cached = _cache_directory(index, case_id)
            _, cached_generator = _cached_elaboration(cached, case_id, config, compiler, generator)
            generator = generator or cached_generator
            matrix.compile_evidence(cached, case_id, config, compiler, generator, source_identity)
            driver = _read(cached/'driver.json')
            binary = _bound(driver['driver'])
            if not os.access(binary, os.X_OK):
                raise ValueError('Cached production driver is not executable')
            matrix.successful_command(_bound(driver['command']), case_id, 'driver_build', source_identity)
            return cached
        except (ValueError, OSError, KeyError) as error:
            rejection = type(error).__name__ + ': ' + str(error)
    implementation = directory/'implementation'
    implementation.mkdir(parents=True, exist_ok=False)
    if rejection:
        write(directory/'cache-rejection.json', dict(reason=rejection))
    source_inputs = None
    if cached is not None:
        try:
            manifest, cached_generator = _cached_elaboration(cached, case_id, config, compiler, generator)
            generator = generator or cached_generator
            source_inputs = cached/'inputs'
            write(directory/'elaboration-provenance.json', dict(inputs=manifest['inputs'],
                generator=manifest['generator'], cached_compile=record(cached/'compile.json')))
        except (ValueError, OSError, KeyError):
            # A changed generator/config/compiler must use actual fresh target
            # elaboration, never relabel mismatched archived inputs.
            source_inputs = None
    if generator is None:
        raise ValueError('generator_jar is required to prepare uncached enabled cases')
    execution.compile_case(repo, implementation, case_id, config, compiler, generator, source_inputs)
    execution.build_driver(repo, implementation, case_id, config)
    matrix.compile_evidence(implementation, case_id, config, compiler, generator, source_identity)
    driver = _read(implementation/'driver.json')
    _bound(driver['driver'])
    matrix.successful_command(_bound(driver['command']), case_id, 'driver_build', source_identity)
    return implementation


def _prebuild(options, repo, case_id, identity):
    path = options.get('semantic_reports', {}).get(case_id)
    if path is None:
        if options.get('require_feature_semantic', False):
            raise nodes.VerificationBlocked('No matched enabled SFC/CIRCT prebuild report for ' + case_id)
        return {'status': 'not_claimed', 'reason': 'Linux-only execution; enabled SFC semantic verification remains separate'}
    binding = record(nodes._path(repo, path))
    report = _read(binding['path'])
    nodes._validate_prebuild_result(report, case_id, identity)
    return {'status': 'passed', 'report': binding}


def _bind_feature_prebuild(prebuild, case_id, config, implementation):
    if prebuild['status'] != 'passed':
        return
    report = _read(_bound(prebuild['report']))
    if report.get('case_id') != case_id or report.get('enabled_features') != [config['feature']]:
        raise ValueError('Enabled SFC semantic report identifies a different feature case')
    artifacts = report.get('artifacts', [])
    if not isinstance(artifacts, list) or {item.get('role') for item in artifacts} != {'config', 'rtl', 'golden_rtl'}:
        raise ValueError('Enabled semantic report must bind config, CIRCT and golden SFC RTL')
    rtl = record(implementation/'generated/FireSim-generated.sv')
    paired = False
    for artifact in artifacts:
        path = _bound(artifact)
        if artifact['role'] == 'rtl' and record(path)['sha256'] == rtl['sha256']:
            paired = True
        if artifact['role'] == 'config':
            descriptor = _read(path)
            if descriptor.get('case_id') != case_id or descriptor.get('config') != config:
                raise ValueError('Enabled semantic configuration differs from the Linux case')
    if not paired:
        raise ValueError('Enabled semantic CIRCT RTL differs from the actual Linux case')
    _command(report.get('command'))
    for side in ('sfc', 'circt'):
        for kind in ('rtl', 'driver'):
            _command(report['details']['paired_syntax']['pairs'][side][kind].get('command'))


def _client():
    from ray.job_submission import JobSubmissionClient
    return JobSubmissionClient(os.environ.get('RAY_API_SERVER_ADDRESS', 'http://127.0.0.1:8265'))


def select_cases(options, registry):
    """Select bounded retries without changing the required full matrix."""
    if 'cases' not in options:
        return list(registry)
    selected = options['cases']
    if (not isinstance(selected, list) or not selected
            or any(not isinstance(case_id, str) or not case_id for case_id in selected)
            or len(set(selected)) != len(selected) or not set(selected) <= set(registry)):
        raise nodes.VerificationBlocked('feature_linux.cases must be a nonempty distinct list from the seven declared representatives')
    return list(selected)


def _status(client, job_id):
    status = client.get_job_status(job_id)
    return getattr(status, 'value', str(status))


def _worker_failure(client, pending, state):
    """Keep the child's root cause instead of reporting only Ray's FAILED."""
    detail = pending['detail']
    reasons = ['Enabled FPGA worker finished with ' + state]
    result_path = pending['snapshot'].parent/'result.json'
    if result_path.is_file():
        detail['worker_result'] = record(result_path)
        try:
            result = _read(result_path)
            diagnostic = {key: str(result[key])[:8000] for key in ('error', 'reason', 'stage') if result.get(key)}
            if diagnostic:
                detail['worker_diagnostics'] = diagnostic
                reasons.extend(diagnostic[key] for key in ('error', 'reason') if key in diagnostic)
        except (OSError, ValueError) as error:
            detail['worker_result_error'] = str(error)
    try:
        info = client.get_job_info(pending['job']['job_id'])
        message = info.get('message') if isinstance(info, dict) else info.message
        if message:
            detail['ray_status_message'] = str(message)[-8000:]
            reasons.append(detail['ray_status_message'])
    except Exception as error:
        # A missing/unavailable status-message API must not erase the actual
        # terminal status or a locally retained worker diagnostic.
        detail['ray_status_message_error'] = type(error).__name__ + ': ' + str(error)
    return '; '.join(reasons)


def validate_linux_result(path, snapshot, case_id, identity, config):
    """Require actual Linux/checker outputs, not a successful build checkbox."""
    result = _read(path)
    if (result.get('passed') is not True or any(result.get(key) is not True for key in FLAGS)
            or result.get('case_id') != case_id or result.get('target') != case_id
            or result.get('input_identity') != identity or result.get('full_acceptance') is not False
            or result.get('workload_kind') != 'linux_boot_poweroff'
            or not isinstance(result.get('agfi'), str) or not result['agfi'].startswith('agfi-')):
        raise ValueError('Enabled case lacks completed real F2 Linux/checker/cleanup evidence')
    _command(result.get('command'))
    receipts = result.get('receipts', {})
    if set(receipts) != RECEIPTS:
        raise ValueError('Enabled Linux build/run/input receipts are incomplete')
    files = {name: _bound(binding) for name, binding in receipts.items()}
    feature_f2.run_f2.validate_uart(files['workload_evidence'].read_text(errors='replace'))
    frozen = _read(files['frozen_inputs'])
    rtl = _bound(frozen.get('candidate_rtl'))
    if record(rtl)['sha256'] != record(Path(snapshot['accepted'])/'FireSim-generated.sv')['sha256']:
        raise ValueError('Linux FPGA RTL differs from the submitted enabled case')
    runtime = yaml.safe_load(files['workload_inputs'].read_text())
    runtime_binding = runtime.get('runtime')
    runtime_config = yaml.safe_load(_bound(runtime_binding).read_text())
    if runtime_config.get('host_debug', {}).get('zero_out_dram') is not False:
        raise ValueError('Feature Linux must leave DRAM zero-out disabled')
    if runtime_config.get('metasimulation', {}).get('metasimulation_enabled') is not False:
        raise ValueError('Feature Linux must execute on real F2')
    run_hosts = runtime_config.get('run_farm', {}).get('recipe_arg_overrides', {}).get('run_farm_hosts_to_use')
    if (not isinstance(run_hosts, list) or not run_hosts
            or any(not isinstance(item, dict) or len(item) != 1
                   or any(not key.startswith('f2.') or type(count) is not int or count < 1
                          for key, count in item.items()) for item in run_hosts)):
        raise ValueError('Feature Linux runtime lacks actual F2 hosts')
    checks = result.get('feature_checks')
    if not isinstance(checks, list) or not checks:
        raise ValueError('Enabled Linux requires actual bound feature observations')
    for binding in checks:
        check = _read(_bound(binding))
        if (check.get('passed') is not True or check.get('case_id') != case_id
                or check.get('input_identity') != identity or not check.get('capabilities')):
            raise ValueError('Feature runtime checker is failed, unbound or belongs to another candidate')
        _command(check.get('command'))
        observations = check.get('observations')
        if not isinstance(observations, list) or not observations:
            raise ValueError('Feature runtime checker has no actual observations')
        for observation in observations:
            _bound(observation)
    feature_f2.bg.validate(snapshot['source_files'] + snapshot['shared_immutable_images']
        + snapshot['accepted_files'] + [snapshot['driver']] + snapshot.get('guest_toolchain', []))
    if snapshot.get('case_id') != case_id or snapshot.get('config') != config:
        raise ValueError('Frozen enabled case/configuration differs')
    return result


def run_feature_linux(config, evidence_dir, repo, semantic):
    repo, evidence_dir = Path(repo).resolve(), Path(evidence_dir).resolve()
    directory = evidence_dir/'feature-linux'
    directory.mkdir(parents=True, exist_ok=False)
    options = config.get('feature_linux', {})
    report = dict(node='feature_linux', status='blocked', ok=False, passed=False,
        identity=semantic.get('identity'), verification_scope='enabled_feature_linux_only',
        full_acceptance=False, formal_claim=False, feature_semantic_claim=False, workers_joined=True,
        evidence=str(directory/'result.json'), details=[])
    active = []
    client = None
    interrupted = None
    timeout = float(options.get('job_timeout_seconds', 86400))
    interval = float(options.get('poll_seconds', 15))
    grace = float(options.get('cleanup_timeout_seconds', 900))
    max_pending = options.get('max_pending', 1)
    if type(max_pending) is not int or max_pending not in (1, 2):
        raise ValueError('feature_linux.max_pending must be an integer 1 or 2')
    report['max_pending'] = max_pending
    stop_on_failure = options.get('stop_on_case_failure', False)
    if type(stop_on_failure) is not bool:
        raise ValueError('feature_linux.stop_on_case_failure must be a boolean')
    report['stop_on_case_failure'] = stop_on_failure
    if (not math.isfinite(timeout) or not math.isfinite(interval) or not math.isfinite(grace)
            or not 0 < timeout or not 0 < interval <= 60 or not 0 < grace <= 1800):
        raise ValueError('Feature Linux needs a positive job deadline and poll interval at most60s')
    def persist():
        write(directory/'result.json', report)
    def recover_farm(pending):
        # A terminal Ray process can leave its remote farm behind, particularly
        # after SIGKILL. Reconcile only this snapshot's uniquely tagged farm.
        from feature_farm_cleanup import recover
        cleanup = recover(pending['snapshot'])
        detail = pending['detail']
        detail['external_cleanup'] = record(cleanup['evidence'])
        write(Path(detail['evidence']), detail)
        if cleanup.get('success') is not True:
            report.update(workers_joined=False, ok=False, passed=False)
            report.setdefault('unjoined_resources', []).append(pending['job']['job_id'])
            raise RuntimeError('Owned feature run farm cleanup is unconfirmed: '+pending['job']['job_id'])
    previous_signals = {}
    if threading.current_thread() is threading.main_thread():
        def interrupted_signal(signum, frame):
            for number in previous_signals:
                signal.signal(number, signal.SIG_IGN)
            raise KeyboardInterrupt('Enabled-feature suite interrupted by signal '+str(signum))
        for number in (signal.SIGTERM, signal.SIGINT):
            previous_signals[number] = signal.getsignal(number)
            signal.signal(number, interrupted_signal)
    try:
        identity, _ = nodes._snapshot(config, repo)
        if semantic.get('ok') is not True or semantic.get('status') != 'passed' or semantic.get('identity') != identity:
            raise nodes.VerificationBlocked('A current passing four-target baseline semantic result is required')
        nodes._validate_semantic_dependency(semantic, identity)
        source_identity = readiness.source_identity(repo)
        report['runtime_source_identity'] = source_identity
        cases = execution.cases(repo)
        if len(cases) != 7:
            raise nodes.VerificationBlocked('Feature Linux requires exactly the seven bounded representatives')
        report['required_cases'] = list(cases)
        selected = select_cases(options, cases)
        remaining = [case_id for case_id in cases if case_id not in selected]
        report.update(selected_cases=selected, remaining_cases=remaining, full_matrix=not remaining)
        matrix_path = options.get('implementation_matrix', DEFAULT_MATRIX)
        index = _read(nodes._path(repo, str(matrix_path))) if matrix_path else None
        prepared = []
        # Prepare serially before dispatch. This limits target/JVM memory use;
        # workers themselves serialize synthesis using the shared build lock.
        for case_id in selected:
            case_config = cases[case_id]
            case_dir = directory/case_id
            case_dir.mkdir()
            detail = dict(case_id=case_id, target=case_id, feature=case_config['feature'],
                          ok=False, status='blocked', evidence=str(case_dir/'result.json'))
            report['details'].append(detail)
            try:
                detail['feature_semantic'] = _prebuild(options, repo, case_id, identity)
                implementation = prepare_case(repo, case_dir, case_id, case_config, options, source_identity, index)
                execution.enabled_contract(implementation/'generated', case_config)
                _bind_feature_prebuild(detail['feature_semantic'], case_id, case_config, implementation)
                compile_manifest = _read(implementation/'compile.json')
                driver = _read(implementation/'driver.json')
                descriptor = case_dir/'configuration.json'
                write(descriptor, dict(case_id=case_id, config=case_config, source_identity=source_identity,
                    compiler=compile_manifest['compiler'], generator=compile_manifest['generator']))
                artifacts = [dict(role='config', **record(descriptor)),
                    dict(role='rtl', **record(implementation/'generated/FireSim-generated.sv')),
                    dict(role='driver', **driver['driver'])]
                detail.update(status='prepared', implementation=str(implementation), artifacts=artifacts)
                prepared.append((case_id, case_config, implementation, detail))
            except (OSError, ValueError, KeyError) as error:
                detail.update(reason=type(error).__name__ + ': ' + str(error))
            write(case_dir/'result.json', detail)
            persist()
        if readiness.source_identity(repo) != source_identity or nodes._snapshot(config, repo)[0] != identity:
            raise ValueError('Verification sources changed while preparing enabled cases')
        client = _client()
        queue = list(prepared)
        while queue or active:
            # During a shared transport repair, do not launch more hours of
            # synthesis after the first failure. Already active workers still
            # finish and join; failed farms are reconciled before this point.
            if stop_on_failure and any(d.get('reason') and d['status'] in ('failed', 'blocked')
                                       for d in report['details']):
                report['reason'] = 'Stopped further dispatch after an enabled case failure; remaining cases require verification'
                for _, _, _, detail in queue:
                    detail.update(status='not_run', ok=False, reason=report['reason'])
                    write(Path(detail['evidence']), detail)
                queue.clear()
                persist()
            # Serialize by default: every child reserves20GiB for its lifetime,
            # including waiting for the shared build lock. Two such reservations
            # can strand workers on a manager already hosting the suite driver.
            # Sites with sufficient Ray memory may explicitly permit two.
            while queue and len(active) < max_pending:
                case_id, case_config, implementation, detail = queue.pop(0)
                suffix = hashlib.sha256((str(directory)+case_id).encode()).hexdigest()[:16]
                job_dir = repo/'automation/chia_firesim_circt/logs/background-features'/('linux-'+suffix)
                try:
                    job = feature_f2.submit(repo, case_id, implementation, job_dir, context=dict(
                        input_identity=identity, enabled_features=[case_config['feature']], artifacts=detail['artifacts']))
                    snapshot = Path(job['snapshot'])
                    detail.update(status='running', job_id=job['job_id'], snapshot=record(snapshot))
                    active.append(dict(job=job, snapshot=snapshot, detail=detail, config=case_config,
                                       deadline=time.monotonic()+timeout))
                    report.update(status='running', workers_joined=False)
                except Exception as error:
                    detail.update(status='failed', reason=type(error).__name__ + ': ' + str(error))
                write(Path(detail['evidence']), detail)
                persist()
            for pending in list(active):
                job, detail = pending['job'], pending['detail']
                try:
                    state = _status(client, job['job_id'])
                except Exception as error:
                    # API failure must not release source mutation while a
                    # worker may still be active. Keep joining up to deadline.
                    detail['poll_error'] = type(error).__name__ + ': ' + str(error)
                    state = 'UNKNOWN'
                if state not in TERMINAL and time.monotonic() >= pending['deadline']:
                    client.stop_job(job['job_id'])
                    detail.update(status='failed', reason='Enabled FPGA job exceeded its wall deadline')
                    pending['deadline'] = float('inf')
                    pending['timed_out'] = True
                    pending['join_deadline'] = time.monotonic()+grace
                if state not in TERMINAL and time.monotonic() >= pending.get('join_deadline', float('inf')):
                    raise RuntimeError('Owned FPGA worker termination is unconfirmed; stop before speed or source mutation')
                if state not in TERMINAL:
                    continue
                active.remove(pending)
                try:
                    if state != 'SUCCEEDED' or pending.get('timed_out'):
                        raise ValueError(_worker_failure(client, pending, state))
                    snapshot = _read(pending['snapshot'])
                    result_path = pending['snapshot'].parent/'result.json'
                    result = validate_linux_result(result_path, snapshot, detail['case_id'], identity, pending['config'])
                    detail.update(status='passed', ok=True, result=record(result_path), agfi=result['agfi'],
                                  feature_checks=result['feature_checks'])
                except (OSError, ValueError, KeyError) as error:
                    detail.update(status='failed', reason=type(error).__name__ + ': ' + str(error))
                if detail.get('ok') is not True:
                    recover_farm(pending)
                write(Path(detail['evidence']), detail)
                persist()
            if active:
                time.sleep(interval)
        if readiness.source_identity(repo) != source_identity or nodes._snapshot(config, repo)[0] != identity:
            raise ValueError('Verification inputs changed during enabled FPGA execution')
        report.update(ok=len(report['details']) == len(selected) and all(d['ok'] for d in report['details']))
        report.update(status='passed' if report['ok'] else 'failed', passed=report['ok'])
    except BaseException as error:
        report.update(status='blocked' if isinstance(error, nodes.VerificationBlocked) else 'failed',
                      reason=type(error).__name__ + ': ' + str(error))
        interrupted = error if not isinstance(error, Exception) else None
    finally:
        # Cancellation must stop AND join every owned job. Preserve diagnostics;
        # never let a subsequent implementation edit a running worker's inputs.
        if active and client is not None:
            for pending in active:
                pending.setdefault('join_deadline', time.monotonic()+grace)
                try: client.stop_job(pending['job']['job_id'])
                except Exception as error: report.setdefault('cleanup_errors', []).append(str(error))
            while active and time.monotonic() < max(p['join_deadline'] for p in active):
                for pending in list(active):
                    try:
                        if _status(client, pending['job']['job_id']) in TERMINAL:
                            pending['detail'].update(status='failed', ok=False, reason='Suite interrupted; owned worker joined')
                            write(Path(pending['detail']['evidence']), pending['detail'])
                            active.remove(pending)
                            recover_farm(pending)
                    except Exception as error:
                        report.setdefault('cleanup_errors', []).append(str(error))
                if active: time.sleep(interval)
            if active:
                report.update(workers_joined=False, ok=False, passed=False, status='failed',
                              unjoined_jobs=[pending['job']['job_id'] for pending in active])
                report['reason'] = 'Owned FPGA workers are not confirmed terminal; do not benchmark or resume implementation'
        if not active and not report.get('unjoined_resources'):
            report['workers_joined'] = True
        persist()
        for number, handler in previous_signals.items():
            signal.signal(number, handler)
    if interrupted is not None:
        raise interrupted
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--semantic', type=Path, required=True)
    parser.add_argument('--evidence-dir', type=Path, required=True)
    args = parser.parse_args()
    result = run_feature_linux(yaml.safe_load(args.config.read_text()), args.evidence_dir,
                               args.repo, _read(args.semantic))
    print(json.dumps(result, indent=2))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
