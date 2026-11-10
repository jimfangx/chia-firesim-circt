#!/usr/bin/env python3
"""Recover one owned F2 run farm after its Ray worker has stopped.

A terminal Ray job only establishes process termination. This helper retains
and verifies a separate real manager cleanup receipt; it never awards Linux,
semantic or formal acceptance. Call only after the owning worker is terminal.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
import time
import uuid

import yaml

import feature_f2
import run_f2
from downstream_common import record, write


def _read(path):
    value = json.loads(Path(path).read_text())
    if not isinstance(value, dict):
        raise ValueError('Cleanup evidence must be a JSON object: ' + str(path))
    return value


def _bound(binding):
    if not isinstance(binding, dict) or not isinstance(binding.get('path'), str):
        raise ValueError('Cleanup evidence requires a bound file')
    path = Path(binding['path'])
    actual = record(path)
    if (actual['sha256'] != binding.get('sha256')
            or ('bytes' in binding and actual['bytes'] != binding['bytes'])):
        raise ValueError('Cleanup evidence changed: ' + str(path))
    return path


def _configuration(snapshot_path, snapshot):
    directory = snapshot_path.parent
    work = directory / 'execution'
    repo = Path(snapshot['repo']).resolve()
    if repo != directory / 'repo' or not repo.is_dir() or work.resolve() != work:
        raise ValueError('Cleanup must use this snapshot\'s isolated repository')
    case_id = snapshot.get('case_id')
    if not isinstance(case_id, str) or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', case_id) is None:
        raise ValueError('Cleanup requires a stable owned case identifier')
    paths = dict(runtime=work / 'runtime.yaml', build_config=work / 'build.yaml',
                 recipes=work / 'recipes.yaml', hwdb=work / 'candidate-hwdb.yaml',
                 frozen_inputs=work / 'frozen-inputs.json', runtime_inputs=work / 'runtime-inputs.json')
    if any(path.resolve() != path for path in paths.values()):
        raise ValueError('Cleanup configuration must belong to this snapshot execution directory')
    bindings = {name: record(path) for name, path in paths.items()}
    runtime = yaml.safe_load(paths['runtime'].read_text())
    if not isinstance(runtime, dict):
        raise ValueError('Owned farm runtime is missing')
    farm = runtime.get('run_farm', {})
    args = farm.get('recipe_arg_overrides', {})
    tag = args.get('run_farm_tag')
    if not isinstance(tag, str) or re.fullmatch(r'chia-feature-[0-9a-f]{12}', tag) is None:
        raise ValueError('Refusing cleanup without a unique owned feature-farm tag')
    hosts = args.get('run_farm_hosts_to_use')
    target = runtime.get('target_config', {})
    workload = runtime.get('workload', {})
    name = 'circt_' + case_id
    if (not isinstance(hosts, list) or len(hosts) != 1 or not isinstance(hosts[0], dict)
            or len(hosts[0]) != 1 or any(not isinstance(host, str) or not host.startswith('f2.')
                or type(count) is not int or count != 1 for host, count in hosts[0].items())
            or target.get('topology') != 'no_net_config' or type(target.get('no_net_num_nodes')) is not int
            or target['no_net_num_nodes'] != 1 or target.get('default_hw_config') != name
            or runtime.get('metasimulation', {}).get('metasimulation_enabled') is not False
            or workload.get('suffix_tag') != tag or workload.get('workload_name') != tag + '.json'):
        raise ValueError('Cleanup runtime must bind this single-node real F2 feature case')
    expected_recipe = repo / 'sims/firesim/deploy/run-farm-recipes/aws_ec2.yaml'
    if Path(farm.get('base_recipe', '')).resolve() != expected_recipe:
        raise ValueError('Cleanup runtime must use the snapshot\'s AWS run-farm recipe')
    build = yaml.safe_load(paths['build_config'].read_text())
    recipes = yaml.safe_load(paths['recipes'].read_text())
    hwdb = yaml.safe_load(paths['hwdb'].read_text())
    if (build.get('builds_to_run') != [name]
            or build.get('build_farm', {}).get('recipe_arg_overrides', {}).get('build_farm_hosts') != ['localhost']
            or not isinstance(recipes, dict) or set(recipes) != {name}
            or recipes[name].get('PLATFORM') != 'f2' or name not in hwdb):
        raise ValueError('Cleanup build/recipe/hardware database is not bound to this feature case')
    frozen = _read(paths['frozen_inputs'])
    if Path(frozen.get('repo', '')).resolve() != repo or frozen.get('target') != case_id:
        raise ValueError('Cleanup frozen inputs refer to another snapshot/case')
    runtime_inputs = _read(paths['runtime_inputs'])
    if _bound(runtime_inputs.get('runtime')).resolve() != paths['runtime'].resolve():
        raise ValueError('Cleanup runtime is not the worker\'s prepared runtime')
    extra = ['-c', str(paths['runtime']), '-a', str(paths['hwdb']), '-q']
    tail = run_f2.manager_command(repo, work, 'terminaterunfarm', paths['recipes'], extra)
    return repo, work, tag, paths, bindings, tail, extra


def _successful_command(path, tail, work):
    command = _read(path)
    argv = command.get('argv')
    if (command.get('action') != 'terminaterunfarm' or type(command.get('returncode')) is not int
            or command['returncode'] != 0 or command.get('timed_out') is not False
            or not isinstance(argv, list) or argv[-len(tail):] != tail):
        raise ValueError('Cleanup requires an actual successful manager command for this farm')
    for stream in ('stdout', 'stderr'):
        if _bound(command.get(stream)).resolve() != (work / 'terminaterunfarm.log').resolve():
            raise ValueError('Cleanup command log refers to another farm execution')
    return record(path)


def _preserve(directory, report_path):
    work = directory / 'execution'
    sources = [p for p in (work / 'terminaterunfarm.log', work / 'terminaterunfarm-command.json', report_path)
               if p.exists()]
    if not sources:
        return None
    archive = directory / 'cleanup-history' / (str(time.time_ns()) + '-' + uuid.uuid4().hex[:8])
    archive.mkdir(parents=True)
    entries = []
    for source in sources:
        destination = archive / source.name
        shutil.copy2(source, destination)
        entries.append(dict(original_path=str(source), retained=record(destination)))
    # Keep raw command bytes unchanged. Their original stdout path can later
    # be overwritten, so this explicit mapping binds the retained former logs.
    write(archive / 'inventory.json', dict(files=entries))
    return record(archive / 'inventory.json')


def recover(snapshot_path):
    """Return {success,evidence,...}; errors retain failed cleanup evidence."""
    snapshot_path = Path(snapshot_path).resolve()
    directory = snapshot_path.parent
    evidence = directory / 'external-cleanup.json'
    report = dict(schema_version=1, success=False, status='failed', evidence=str(evidence),
                  requested_ns=time.time_ns(), scope='Owned F2 run-farm cleanup only')
    try:
        snapshot = _read(snapshot_path)
        report.update(case_id=snapshot.get('case_id'), snapshot=record(snapshot_path))
        if not (directory / 'execution/runtime.yaml').exists():
            report.update(success=True, status='not_launched', reason='No runtime was prepared; worker could not launch its farm')
            if evidence.exists(): report['previous_evidence'] = _preserve(directory, evidence)
            write(evidence, report)
            return report
        repo, work, tag, paths, bindings, tail, extra = _configuration(snapshot_path, snapshot)
        report.update(tag=tag, inputs=bindings)
        command_path = work / 'terminaterunfarm-command.json'
        if evidence.exists():
            try:
                old = _read(evidence)
                if (old.get('success') is True and old.get('snapshot') == report['snapshot']
                        and old.get('inputs') == bindings and old.get('tag') == tag
                        and _bound(old.get('command')) == command_path
                        and _successful_command(command_path, tail, work) == old['command']):
                    return dict(old, reused=True)
            except (OSError, ValueError, KeyError, TypeError):
                pass
        if command_path.exists():
            try:
                command = _successful_command(command_path, tail, work)
                report.update(success=True, status='already_cleaned', command=command,
                              log=record(work / 'terminaterunfarm.log'))
                if evidence.exists(): report['previous_evidence'] = _preserve(directory, evidence)
                write(evidence, report)
                return report
            except (OSError, ValueError, KeyError, TypeError):
                pass
        previous = _preserve(directory, evidence)
        if previous: report['previous_evidence'] = previous
        feature_f2.run_manager(repo, work, 'terminaterunfarm', paths['recipes'], extra, timeout=600)
        command = _successful_command(command_path, tail, work)
        # Runtime/build ownership may not change while the external manager
        # runs, including a quick zero-status command.
        if bindings != {name: record(path) for name, path in paths.items()} or record(snapshot_path) != report['snapshot']:
            raise ValueError('Owned cleanup inputs changed during termination')
        report.update(success=True, status='cleaned', command=command,
                      log=record(work / 'terminaterunfarm.log'))
    except (Exception, KeyboardInterrupt) as error:
        report.update(success=False, status='failed', error=type(error).__name__ + ': ' + str(error))
        command_path = directory / 'execution/terminaterunfarm-command.json'
        if command_path.is_file(): report['attempt_command'] = record(command_path)
    write(evidence, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshot', type=Path)
    args = parser.parse_args()
    result = recover(args.snapshot)
    print(json.dumps(result, indent=2))
    return 0 if result['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
