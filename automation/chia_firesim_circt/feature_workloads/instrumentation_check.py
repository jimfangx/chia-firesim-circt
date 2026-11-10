#!/usr/bin/env python3
"""Validate actual CIRCTFeatureMonitor CSV/Print files and guest completion.

The manifest declares domains, clock/reset acquisition, a base sample period and
minimum sample/print counts. It is an input, never synthesized from observations.
Checks local cycle, separate identity/accumulation reset prefixes, narrow
identity rollover and complete ordered Print records in every actual domain.
The caller separately requires Linux boot/poweroff and successful driver exit.
"""
import argparse
import csv
import hashlib
import io
import json
from pathlib import Path
import re

PERIOD = 1 << 20
MARKER = 'CHIA_FEATURE_INSTRUMENTATION: PASS sum=8390656 bytes=65536'


def require(value, message):
    if not value:
        raise ValueError(message)


def reset_acquisition(base_reset_cycles, multiplier, divisor):
    """Phase-zero ClockBridge/ResetPulse source-token acquisition contract.

    Reset is held through base edge R-1. Target registers therefore see
    1+floor((R-1)*m/d) asserted edges. FAME exposes an initial masked source
    token and captures gated-edge results on the following step, giving
    1+ceil((R-1)*m/d) masked source tokens. These are separate quantities:
    identity uses the former, accumulation the latter. The same prefix also
    determines whether the monitor's count-zero Print is globally masked.
    This is not an offset fitted to observed CSV or Print data.
    """
    require(all(type(value) is int and value > 0
                for value in (base_reset_cycles, multiplier, divisor)),
            'reset acquisition needs positive integer reset length and clock ratio')
    require(multiplier <= divisor, 'monitor frequency exceeds the declared base clock')
    last_reset_edge = (base_reset_cycles - 1) * multiplier
    target_reset_cycles = 1 + last_reset_edge // divisor
    masked_source_tokens = 1 + (last_reset_edge + divisor - 1) // divisor
    first_counter = masked_source_tokens - target_reset_cycles
    return dict(reset_cycles=target_reset_cycles, masked_source_tokens=masked_source_tokens,
                first_print_cycle=((first_counter + PERIOD - 1) // PERIOD) * PERIOD)


def check(manifest, csv_texts, print_texts, uart, require_shutdown=False):
    schema = manifest['schema_version']
    require(type(schema) is int and schema in (1, 2), 'unsupported monitor manifest')
    if schema == 2:
        require(manifest.get('clock_phase') == 'all-countdowns-zero',
                'monitor needs the generated initial ClockBridge phase')
    require(uart.count(MARKER) == 1, 'guest arithmetic/memory workload did not complete exactly once')
    require(re.search(r'(?:CHIA_FEATURE_INSTRUMENTATION[: ]|CIRCT_LINUX_)[^\n]*FAIL', uart) is None,
            'guest workload failed')
    if require_shutdown:
        boot, done, power = (uart.find(marker) for marker in
                            ('CIRCT_LINUX_BOOT: PASS', MARKER, 'CIRCT_LINUX_POWEROFF: REQUESTED'))
        require(0 <= boot < done < power and re.search(r'reboot: Power down', uart[power:]),
                'ordered Linux boot, instrumentation workload and kernel poweroff are required')
    require(type(manifest.get('min_samples', 2)) is int and manifest.get('min_samples', 2) >= 2
            and type(manifest.get('min_prints', 2)) is int and manifest.get('min_prints', 2) >= 2,
            'at least two samples and prints per domain are required')
    domains = manifest['domains']
    require(domains and len({d['id'] for d in domains}) == len(domains), 'missing/duplicate domains')
    period = manifest['sample_period_base']
    require(type(period) is int and period > 0, 'invalid base sample period')
    csv_domains = {}
    for text in csv_texts:
        rows = list(csv.reader(io.StringIO(text), strict=True))
        require(len(rows) >= 7 and rows[0] == ['version', '1'], 'invalid AutoCounter CSV')
        require(len(rows[1]) >= 6 and rows[1][0] == 'Clock Domain Name', 'missing clock metadata')
        # The unchanged ClockInfo driver emits joined clock aliases without
        # CSV quoting. Read its fixed four-field frequency suffix from the
        # right, preserving every comma-separated alias in the domain name.
        name = ','.join(rows[1][1:-4]).strip()
        require(name not in csv_domains, 'duplicate CSV domain')
        csv_domains[name] = rows
    print_domains = {}
    for text in print_texts:
        lines = text.splitlines()
        require(lines, 'empty Print output')
        header = re.fullmatch(r'# Clock Domain: (.+), Relative Frequency: ([0-9]+)/([0-9]+) of Base Clock', lines[0])
        require(header, 'invalid Print clock header')
        name = header[1]
        require(name not in print_domains, 'duplicate Print domain')
        events = []
        for line in lines[1:]:
            if '[CIRCT_FEATURE]' not in line:
                continue
            event = re.fullmatch(r'CYCLE:\s*([0-9]+) \[CIRCT_FEATURE\] domain=([0-9]+) cycles=\s*([0-9]+) wrap=\s*([0-9]+)', line)
            require(event, 'malformed monitor Print payload')
            events.append(tuple(map(int, event.groups())))
        print_domains[name] = (int(header[2]), int(header[3]), events)
    results = []
    for domain in domains:
        ident, name, mult, div, reset = (domain[k] for k in ('id', 'name', 'multiplier', 'divisor', 'reset_cycles'))
        require(all(type(v) is int and v >= 0 for v in (ident, reset)) and
                all(type(v) is int and v > 0 for v in (mult, div)), 'invalid declared domain')
        if schema == 2:
            acquisition = reset_acquisition(manifest.get('reset_base_cycles'), mult, div)
            require(all(type(domain.get(key)) is int and domain[key] == value
                        for key, value in acquisition.items()),
                    'declared reset acquisition differs from clock/reset contract')
            masked, first_print = acquisition['masked_source_tokens'], acquisition['first_print_cycle']
        else:
            require(mult == div, 'multiclock observations require the generated reset acquisition contract')
            masked, first_print = reset, 0
        require(period * mult % div == 0, 'fractional local sampling period')
        local_period = period * mult // div
        require(name in csv_domains and name in print_domains, 'missing active CSV/Print domain ' + name)
        rows = csv_domains[name]
        require(rows[1] == ['Clock Domain Name', *(' ' + name).split(','), ' Base Multiplier', ' ' + str(mult),
                            ' Base Divisor', ' ' + str(div)], 'CSV domain ownership differs')
        require([row[0] for row in rows[2:7]] == ['label', 'description', 'type', 'event width', 'accumulator width'],
                'invalid CSV metadata row order')
        require(all(len(row) == len(rows[2]) for row in rows[2:7]), 'CSV metadata column count differs')
        labels = rows[2][1:]
        require(len(set(labels)) == len(labels) and labels[0] == 'local_cycle', 'invalid counter identity')
        indices = {}
        for suffix in ('active', 'cycles', 'wrap'):
            matching = [i for i, label in enumerate(labels) if label.endswith('feature_domain%d_%s' % (ident, suffix))]
            require(len(matching) == 1, 'missing/duplicate monitor counter ' + suffix)
            indices[suffix] = matching[0]
        require(rows[3][1] == 'Clock cycles elapsed in the local domain.', 'local_cycle description differs')
        require(rows[4][1] == 'Accumulate' and rows[5][1] == '1' and rows[6][1] == '64', 'local_cycle ABI differs')
        descriptions = {'active': 'Cycles outside local reset.',
                        'cycles': 'Target domain cycles since local reset release.',
                        'wrap': 'Low four bits of target domain cycles.'}
        for suffix, index in indices.items():
            require(rows[3][index + 1] == descriptions[suffix], 'monitor counter description differs')
            require(rows[4][index + 1] == ('Accumulate' if suffix == 'active' else 'Identity') and
                    rows[5][index + 1] == str({'active': 1, 'cycles': 64, 'wrap': 4}[suffix]) and
                    rows[6][index + 1] == '64', 'monitor type/width differs')
        require(len(rows) - 7 >= manifest.get('min_samples', 2), 'too few actual monitor samples')
        for index, row in enumerate(rows[7:]):
            require(len(row) == len(labels) + 1 and all(v.isascii() and v.isdecimal() for v in row), 'malformed sample')
            values = list(map(int, row))
            timestamp, local = (index + 1) * period, (index + 1) * local_period
            require(values[0] == timestamp and values[1] == local, 'sample cycle lost/reordered or rational ratio differs')
            since_reset = max(0, local - reset)
            expected_active = max(0, local - masked)
            require(values[indices['active'] + 1] == expected_active,
                    f'domain {ident} ({name}), sample {index + 1}, base cycle {timestamp}: '
                    f'accumulation/reset differs (expected {expected_active}, got {values[indices["active"] + 1]})')
            require(values[indices['cycles'] + 1] == since_reset, 'target identity/reset differs')
            require(values[indices['wrap'] + 1] == since_reset % 16, 'narrow identity rollover differs')
        pmult, pdiv, events = print_domains[name]
        require((pmult, pdiv) == (mult, div), 'Print rational domain differs')
        require(len(events) >= manifest.get('min_prints', 2), 'too few actual monitor prints')
        for i, (cycle, did, count, wrap) in enumerate(events):
            require(did == ident and count == first_print + i * PERIOD and wrap == count % 16,
                    'monitor Print missing/reordered/corrupt')
            require(cycle == count + reset, 'Print accepted cycle/reset relation differs')
        results.append({'id': ident, 'name': name, 'samples': len(rows) - 7, 'prints': len(events)})
    result = {'passed': True, 'domains': results, 'workload_marker_present': True,
            'observed_checks': ['active_counter_samples', 'reset_offset_and_accumulation',
                                'narrow_identity_wrap', 'rational_domain_cycle_progress',
                                'ordered_print_payloads', 'guest_arithmetic_and_written_memory'],
            'scope': 'Actual enabled monitor observations and guest work; formal/FPGA acceptance requires caller receipts.'}
    if schema == 2:
        result['reset_acquisition_contract'] = dict(reset_base_cycles=manifest['reset_base_cycles'],
            clock_phase=manifest['clock_phase'], domains=[dict(id=d['id'], **reset_acquisition(
                manifest['reset_base_cycles'], d['multiplier'], d['divisor'])) for d in domains])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--csv', type=Path, nargs='+', required=True)
    parser.add_argument('--prints', type=Path, nargs='+', required=True)
    parser.add_argument('--uart', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    paths = [args.manifest, *args.csv, *args.prints, args.uart]
    bind = lambda: {str(p.resolve()): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    before = bind()
    try:
        result = check(json.loads(args.manifest.read_text()), [p.read_text() for p in args.csv],
                       [p.read_text() for p in args.prints], args.uart.read_text(), require_shutdown=True)
    except (ValueError, KeyError, TypeError, csv.Error) as error:
        result = {'passed': False, 'error': str(error)}
    require(before == bind(), 'monitor inputs changed while checking')
    result['inputs'] = before
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
