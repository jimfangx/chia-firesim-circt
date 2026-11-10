#!/usr/bin/env python3
"""CHIA job: fresh four-target prebuild checks, then enabled FPGA Linux tests.

This Linux-only job can run in an isolated checkout while another candidate is
under formal verification. It never claims Jasper or full feature acceptance.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import runpy
import sys

import ray
import yaml
from chia.base.ChiaFunction import ChiaFunction, get


def execute(node, config, evidence_dir, repo, semantic=None):
    repo, evidence_dir = Path(repo), Path(evidence_dir)
    sys.path.insert(0, str(repo / 'automation/chia_firesim_circt'))
    filename = 'verification_nodes.py' if node == 'semantic' else 'feature_linux.py'
    implementation = runpy.run_path(str(repo / 'automation/chia_firesim_circt' / filename))
    function = implementation['run_' + node]
    if semantic is None:
        return function(config, evidence_dir, repo)
    return function(config, evidence_dir, repo, semantic)


@ChiaFunction(resources={'golden_compare': 0.01})
def prebuild(config, evidence_dir, repo):
    return execute('semantic', config, evidence_dir, repo)


@ChiaFunction(resources={'firesim_manager': 0.01})
def enabled_linux(config, evidence_dir, repo, semantic):
    return execute('feature_linux', config, evidence_dir, repo, semantic)


def linux_summary(result):
    selected = result.get('selected_cases', [])
    passed = sum(detail.get('ok') is True for detail in result.get('details', []))
    total = len(result.get('required_cases', []))
    scope = 'full matrix' if result.get('full_matrix') is True else 'partial retry'
    return (f'{passed}/{len(selected)} selected enabled Linux cases passed '
            f'({scope}; {len(selected)}/{total} required cases selected). '
            'Formal/full feature acceptance remains separate.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--evidence-dir', type=Path, required=True)
    args = parser.parse_args()
    config = yaml.safe_load(args.config.read_text())
    if not isinstance(config, dict):
        parser.error('configuration must be a mapping')
    sys.path.insert(0, str(args.repo / 'automation/chia_firesim_circt'))
    import feature_linux
    registry = feature_linux.execution.cases(args.repo)
    selected = feature_linux.select_cases(config.get('feature_linux', {}), registry)
    args.evidence_dir.mkdir(parents=True, exist_ok=True)
    graph = []
    ray.init(address='auto')
    try:
        print('Checking exactly four baseline semantics in the isolated checkout.', flush=True)
        semantic = get(prebuild.chia_remote(config, str(args.evidence_dir), str(args.repo),
                                           _chia_tag='enabled-linux-prebuild'))
        graph.append(semantic)
        print(json.dumps({'node': 'semantic', 'status': semantic.get('status'),
                          'evidence': semantic.get('evidence')}, indent=2), flush=True)
        if semantic.get('ok') is True:
            print(f'Baseline prebuild passed. Dispatching {len(selected)}/{len(registry)} selected enabled Linux cases; no Jasper claims.', flush=True)
            result = get(enabled_linux.chia_remote(config, str(args.evidence_dir), str(args.repo), semantic,
                                                   _chia_tag='enabled-feature-linux'))
            graph.append(result)
            print(json.dumps(result, indent=2), flush=True)
    finally:
        (args.evidence_dir / 'graph.json').write_text(json.dumps(graph, indent=2) + '\n')
        ray.shutdown()
    if len(graph) != 2 or any(report.get('ok') is not True for report in graph):
        raise SystemExit('Enabled-feature Linux verification incomplete; inspect retained evidence.')
    print(linux_summary(graph[-1]), flush=True)


if __name__ == '__main__':
    main()
