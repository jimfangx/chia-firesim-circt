#!/usr/bin/env python3
"""Bounded tiny Jasper contract controls; never baseline/feature acceptance.

Runs actual separate state_matching (LEC) and proof (SEC) strategies, retaining
all inputs/reports. Positive results require complete signoff; negatives require
an actual boundary counterexample, not timeout/error/inconclusive status.
"""
import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tarfile
import uuid
from downstream_common import record
from jasper_contract import stage_boundary_contract, validate_configuration
from run_jasper import collect_proof_archive, signoff, stage_lec_reporter

CASES = ('invalid_unwritten_difference', 'written_corruption', 'initialized_corruption',
         'wrong_valid', 'wrong_ready', 'lost_transaction', 'reordered_transaction',
         'wrong_byte_mask', 'independent_live_unwritten')


def rtl(case, imp=False):
    mask0 = 'mask[1]' if imp and case == 'wrong_byte_mask' else 'mask[0]'
    mask1 = 'mask[0]' if imp and case == 'wrong_byte_mask' else 'mask[1]'
    low = 'wdata[7:0] ^ 8\'h01' if imp and case == 'written_corruption' else 'wdata[7:0]'
    initialized = "16'hcaff" if imp and case == 'initialized_corruption' else "16'hcafe"
    init0 = "abstract_initial[15:0] ^ 16'h0001" if imp and case == 'independent_live_unwritten' else 'abstract_initial[15:0]'
    valid = '!pending' if imp and case == 'wrong_valid' else 'pending'
    ready = 'pending && !io_master_r_ready' if imp and case == 'wrong_ready' else '!pending || io_master_r_ready'
    accept = 'request && io_master_aw_ready'
    if imp and case == 'lost_transaction': accept += ' && !address'
    addr = '!address' if imp and case == 'reordered_transaction' else 'address'
    invalid = "16'habcd" if imp else "16'h1234"
    return f'''module F1Shim(
 input clock, input reset, input [31:0] abstract_initial,
 input request, input write_enable, input address, input [15:0] wdata, input [1:0] mask,
 output io_master_aw_ready, input io_master_r_ready, output io_master_r_valid,
 output [15:0] io_master_r_bits_data);
 reg [15:0] memory [0:1];
 reg pending;
 reg [15:0] response;
 assign io_master_aw_ready = {ready};
 assign io_master_r_valid = {valid};
 assign io_master_r_bits_data = pending ? response : {invalid};
 always @(posedge clock) begin
   if (reset) begin
     memory[0] <= {init0};
     memory[1] <= {initialized};
     pending <= 1'b0;
     response <= 16'b0;
   end else begin
     if (io_master_r_ready) pending <= 1'b0;
     if ({accept}) begin
       if (write_enable) begin
         if ({mask0}) memory[address][7:0] <= {low};
         if ({mask1}) memory[address][15:8] <= wdata[15:8];
       end else begin
         pending <= 1'b1;
         response <= memory[{addr}];
       end
     end
   end
 end
endmodule
'''


def control_relation(bundle, case):
    # Exact checked source construction, not name/shape matching.
    if (bundle / 'spec.sv').read_text() != rtl(case) or (bundle / 'imp.sv').read_text() != rtl(case, True):
        raise ValueError('control relation source construction changed')
    if case != 'invalid_unwritten_difference':
        raise ValueError('no initial relation certificate for corrupted controls')
    return {
        'established_by': 'checked_shared_symbolic_seed_construction',
        'rtl_files': [record(bundle / x) for x in ('spec.sv', 'imp.sv')],
        'reset_mappings': ['memory[0][15:0] -> F1Shim_imp.memory[0][15:0] RESET'],
        'relation_properties': ['<embedded>::memory[0:1]'],
        'shared_abstract_regions': [{'address': 0, 'bytes': 2, 'relation':
            'Both memory[0] words equal the same arbitrary abstract_initial[15:0] sampled at reset.'}],
        'loaded_regions': [{'address': 1, 'bytes': 2, 'value': '0xcafe'}],
        'write_relation': 'Both lane writes use identical address, enable, mask and byte data; '
                         'proven memory equality establishes preservation, including later live reads.'}


def reanalyze(directory):
    """Recheck retained tool reports; preserves original run verdicts/strategies."""
    path = directory / 'result.json'
    result = json.loads(path.read_text())
    for item in result['runs']:
        run = directory / item['case'] / item['mode']
        if not (run / 'result-as-run.json').exists():
            shutil.copy2(run / 'result.json', run / 'result-as-run.json')
        item['passed'] = False
        item.pop('error', None)
        try:
            from jasper_contract import boundary_contract
            bundle, report = run / 'bundle', run / 'proof/reports'
            cfg = json.loads((run / 'proof/config.json').read_text())
            item['configuration_validation'] = validate_configuration(cfg,
                json.loads((bundle / 'protocol-memory-contract.json').read_text()))
            item['actual_strategy'] = cfg['strategy']
            targets = set((report / 'targets.txt').read_text().splitlines())
            statuses = [line.split('\t') for line in (report / 'status.txt').read_text().splitlines()]
            item['all_counterexamples'] = [r[2] for r in statuses if len(r) == 3 and r[0] == 'cex']
            item['boundary_counterexamples'] = [r[2] for r in statuses if len(r) == 3 and r[0] == 'cex' and r[2] in targets]
            if item['case'] == 'invalid_unwritten_difference':
                item['signoff'] = signoff(run / 'proof', item['mode'], control_relation(bundle, item['case']))
                item['passed'] = item['launcher_returncode'] == 0
            else:
                item['passed'] = bool(item['all_counterexamples'] if item['mode'] == 'lec' else item['boundary_counterexamples'])
        except Exception as error: item['error'] = str(error)
        item['assessment'] = 'Retained actual reports rechecked; no proof rerun or strategy relabeling.'
        (run / 'result.json').write_text(json.dumps(item, indent=2) + '\n')
        print(item['case'], item['mode'], 'PASS' if item['passed'] else 'INCOMPLETE', flush=True)
    result['passed'] = bool(result['runs']) and all(x['passed'] for x in result['runs'])
    result['assessment_sources'] = [record(Path(__file__)), record(Path(__file__).with_name('jasper_contract.py')),
                                    record(Path(__file__).with_name('run_jasper.py'))]
    path.write_text(json.dumps(result, indent=2) + '\n')
    return 0 if result['passed'] else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--directory', type=Path, required=True)
    ap.add_argument('--flow', type=Path, default=Path('/scratch/jfx/circt_compare/sec_flow'))
    ap.add_argument('--host', default='eda')
    ap.add_argument('--cases', nargs='+', choices=CASES, default=CASES)
    ap.add_argument('--modes', nargs='+', choices=('lec', 'sec'), default=('lec', 'sec'))
    ap.add_argument('--sec-strategy', choices=('basic', 'proof'), default='proof')
    ap.add_argument('--reanalyze', action='store_true', help='recheck existing reports without starting Jasper')
    a = ap.parse_args()
    if a.reanalyze: return reanalyze(a.directory)
    a.directory.mkdir(parents=True, exist_ok=False)
    shutil.copy2(__file__, a.directory / 'runner.py')
    ssh = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15', a.host]
    result = {'scope': 'Tiny executable contract controls only; not baseline or feature acceptance.',
              'initial_relation': 'The same arbitrary abstract_initial input initializes logical address 0 on both sides; '
                                  'address 1 is explicitly initialized to 0xcafe. One checked shared-word initial mapping, no zero clearing, '
                                  'All masked writes and all live reads are strict. This is an explicit fixture '
                                  'representation relation, not a relation established for production memories.',
              'jobs': 1, 'runs': [], 'passed': False}
    for case in a.cases:
        for mode in a.modes:
            strategy = 'state_matching' if mode == 'lec' else a.sec_strategy
            jobs = 4 if strategy == 'proof' else 1
            directory = a.directory / case / mode
            directory.mkdir(parents=True)
            bundle = directory / 'bundle'; bundle.mkdir()
            shutil.copytree(a.flow, bundle / 'sec_flow', ignore=shutil.ignore_patterns('__pycache__', '*.pyc', 'runs'))
            if mode == 'lec': stage_lec_reporter(bundle / 'sec_flow')
            (bundle / 'spec.sv').write_text(rtl(case))
            (bundle / 'imp.sv').write_text(rtl(case, True))
            initial_maps = ()
            if case == 'invalid_unwritten_difference':
                control_relation(bundle, case)  # Check exact construction before permitting this relation.
                initial_maps = (('memory[0][15:0]', 'memory[0][15:0]'),)
            contract, _, _ = stage_boundary_contract(bundle / 'spec.sv', bundle / 'imp.sv', bundle / 'sec_flow', bundle, initial_mappings=initial_maps)
            remote = '/scratch/jfx/chia-contract-controls/' + uuid.uuid4().hex
            archive = directory / 'inputs.tar'
            with tarfile.open(archive, 'w') as tar: tar.add(bundle, arcname='bundle')
            subprocess.run(ssh + ['mkdir -p ' + shlex.quote(remote)], check=True, timeout=25)
            with archive.open('rb') as stream:
                subprocess.run(ssh + ['tar -xf - -C ' + shlex.quote(remote)], stdin=stream, check=True, timeout=60)
            command = ['python3', 'sec_flow/run_sec.py', '--spec', 'spec.sv', '--imp', 'imp.sv', '--top', 'F1Shim',
                       '--jg', '/tools/Cadence/jasper_2026.06p002/bin/jg', '--strategy', strategy,
                       '--out', 'proof', '--jobs', str(jobs), '--max-mem-gb', '8', '--prove-time', '30s',
                       '--no-init-map', '--no-valid-qualify', '--output-conditions', 'protocol-output-conditions.txt',
                       '--no-assume-spec-asserts']
            shell = 'source /tools/flexlm/flexlm.sh && cd ' + shlex.quote(remote + '/bundle') + ' && ' + shlex.join(
                ['timeout', '--signal=TERM', '--kill-after=10s', '120', *command])
            with (directory / 'launcher.log').open('w') as log:
                done = subprocess.run(ssh + ['bash -lc ' + shlex.quote(shell)], stdout=log, stderr=subprocess.STDOUT, timeout=160)
            out = directory / 'results.tar'
            with out.open('wb') as stream:
                subprocess.run(ssh + ['tar -cf - -C ' + shlex.quote(remote + '/bundle') + ' proof'],
                               stdout=stream, check=True, timeout=60)
            collect_proof_archive(out, directory)
            item = {'case': case, 'mode': mode, 'strategy': strategy, 'remote': remote,
                    'command': command, 'jobs': jobs, 'launcher_returncode': done.returncode,
                    'input_files': [record(bundle / x) for x in ('spec.sv', 'imp.sv', 'protocol-memory-contract.json',
                                                               'protocol-output-conditions.txt')], 'passed': False}
            try:
                cfg = json.loads((directory / 'proof/config.json').read_text())
                item['configuration_validation'] = validate_configuration(cfg, contract)
                report = directory / 'proof/reports'
                targets = set((report / 'targets.txt').read_text().splitlines())
                statuses = [line.split('\t') for line in (report / 'status.txt').read_text().splitlines()]
                item['boundary_counterexamples'] = [r[2] for r in statuses if len(r) == 3 and r[0] == 'cex' and r[2] in targets]
                if case == 'invalid_unwritten_difference':
                    item['signoff'] = signoff(directory / 'proof', mode, control_relation(bundle, case))
                    item['passed'] = done.returncode == 0
                else:
                    item['all_counterexamples'] = [r[2] for r in statuses if len(r) == 3 and r[0] == 'cex']
                    # State-matching LEC must also discharge its actual state
                    # cutpoint guarantees. In this same-encoding fixture a
                    # memory/register CP counterexample is a strict LEC failure,
                    # even before failure extension reaches the boundary.
                    item['passed'] = bool(item['all_counterexamples'] if mode == 'lec' else item['boundary_counterexamples'])
                item['report_files'] = [record(report / x) for x in ('status.txt', 'signoff_summary.txt', 'targets.txt')]
            except Exception as error: item['error'] = str(error)
            (directory / 'result.json').write_text(json.dumps(item, indent=2) + '\n')
            result['runs'].append(item)
            (a.directory / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
            print(case, mode, 'PASS' if item['passed'] else 'INCOMPLETE', flush=True)
    result['passed'] = bool(result['runs']) and all(x['passed'] for x in result['runs'])
    result['sources'] = [record(a.directory / 'runner.py'), record(Path(__file__).with_name('jasper_contract.py')),
                         record(Path(__file__).with_name('run_jasper.py'))]
    (a.directory / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    return 0 if result['passed'] else 1


if __name__ == '__main__': raise SystemExit(main())
