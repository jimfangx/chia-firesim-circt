#!/usr/bin/env python3
"""Check actual guest UART and normal NIC host-peer traffic before poweroff.

The checker recomputes every expected byte and FNV hash independently. Its
--self-test validates protocol and negative controls only, never FPGA acceptance.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import sys


def frame(widget, sequence, direction):
    length = (126, 254, 510, 1022)[sequence % 4]
    data = bytearray(length)
    data[:6] = b'\xff' * 6
    data[6] = 2
    data[9] = direction
    data[10:12] = struct.pack('>H', widget)
    data[12:14] = b'\x88\xb5'
    data[14:22] = b'CIRCTNIC'
    struct.pack_into('<IIII', data, 22, widget, sequence, direction, length)
    data[38:] = bytes((i * 29 + sequence * 17 + widget * 43 + direction * 101) & 255
                      for i in range(38, length))
    return bytes(data)


def fnv(data):
    value = 2166136261
    for byte in data:
        value = ((value ^ byte) * 16777619) & 0xffffffff
    return value


def events(text, prefix):
    rows = []
    for line in text.splitlines():
        if prefix in line:
            suffix = line.split(prefix, 1)[1].strip()
            rows.append(json.loads(suffix))
    return rows


def expected(widget, sequence, direction):
    data = frame(widget, sequence, direction)
    return dict(widget=widget, sequence=sequence, direction=direction,
                bytes=len(data), fnv1a=fnv(data))


def check(uarts, widgets, mode, packets=128, peer=None, require_shutdown=True,
          require_worker_lifecycle=False):
    if (not isinstance(mode, (str, dict)) or not widgets
            or any(type(widget) is not int or widget < 0 for widget in widgets)
            or len(set(widgets)) != len(widgets)
            or (isinstance(mode, str) and mode not in ('normal', 'loopback'))):
        raise ValueError('distinct expected widgets and a supported mode are required')
    if type(packets) is not int or packets <= 0 or packets % 8:
        raise ValueError('packet count must be positive and divisible by eight')
    modes = {widget: mode for widget in widgets} if isinstance(mode, str) else mode
    if set(modes) != set(widgets) or any(m not in ('normal', 'loopback') for m in modes.values()):
        raise ValueError('mode bindings must match every expected widget')
    rows, summaries, workers = [], [], []
    for text in uarts:
        boot, poweroff = text.find('CIRCT_LINUX_BOOT: PASS'), text.find('CIRCT_LINUX_POWEROFF: REQUESTED')
        if require_shutdown:
            if not 0 <= boot < poweroff or not re.search(r'(?:Power down|Powering off|reboot: Power down)', text[poweroff:]):
                raise ValueError('ordered Linux boot, workload and actual kernel poweroff are required')
            positions = [match.start() for match in re.finditer('CIRCT_NIC_(?:PACKET|RESULT|WORKER) ', text)]
            if not positions or min(positions) < boot or max(positions) > poweroff:
                raise ValueError('packet checks must finish after boot and before poweroff')
        if re.search(r'CIRCT_(?:NIC|LINUX)_[^\n]*FAIL', text):
            raise ValueError('guest failure marker')
        rows.extend(events(text, 'CIRCT_NIC_PACKET '))
        local_summaries = events(text, 'CIRCT_NIC_RESULT ')
        interfaces = [row.get('interface') for row in local_summaries]
        if (any(not isinstance(interface, str) or not interface for interface in interfaces)
                or len(set(interfaces)) != len(interfaces)):
            raise ValueError('independent widgets share a guest interface')
        summaries.extend(local_summaries)
        local_workers = events(text, 'CIRCT_NIC_WORKER ')
        if require_worker_lifecycle:
            local_widgets = [row['widget'] for row in local_summaries]
            # Workers may complete their packet checks in either order; the
            # guest launches in configuration order and joins in that same
            # order, independently of completion. Do not infer hardware stalls.
            actual_launches = local_workers[:len(local_widgets)]
            if (any(type(row.get('widget')) is not int for row in local_workers)
                    or any(type(row.get('returncode')) is not int for row in local_workers[len(local_widgets):])
                    or {row.get('widget') for row in actual_launches} != set(local_widgets)
                    or any(row != dict(widget=row.get('widget'), event='launched') for row in actual_launches)
                    or local_workers[len(local_widgets):] != [
                        dict(widget=row['widget'], event='joined', returncode=0) for row in actual_launches]
                    or len(local_workers) != 2 * len(local_widgets)):
                raise ValueError('all NIC workers must launch before successful joins')
        workers.extend(local_workers)
    if {row['widget'] for row in rows} != set(widgets) or {row['widget'] for row in summaries} != set(widgets):
        raise ValueError('missing or unexpected widget identity')
    for widget in widgets:
        direction = int(modes[widget] == 'normal')
        actual = [row for row in rows if row['widget'] == widget]
        if actual != [expected(widget, sequence, direction) for sequence in range(packets)]:
            raise ValueError('guest packet contents/order/count differ for widget ' + str(widget))
        total = sum(len(frame(widget, sequence, direction)) for sequence in range(packets))
        summary = [row for row in summaries if row['widget'] == widget]
        if len(summary) != 1 or summary[0].get('packets') != packets or summary[0].get('bytes') != total or summary[0].get('mode') != modes[widget] or summary[0].get('passed') is not True or not summary[0].get('interface'):
            raise ValueError('invalid guest summary for widget ' + str(widget))
    normal_widgets = [widget for widget in widgets if modes[widget] == 'normal']
    if normal_widgets:
        if peer is None:
            raise ValueError('normal NIC requires the real host-peer traffic log')
        host = events(peer, 'CIRCT_NIC_HOST ')
        if any(row.get('event') not in ('request', 'reply') for row in host):
            raise ValueError('unexpected host traffic event')
        results = events(peer, 'CIRCT_NIC_PEER_RESULT ')
        if {row['widget'] for row in host} != set(normal_widgets) or {row['widget'] for row in results} != set(normal_widgets):
            raise ValueError('host widget identities differ')
        for widget in normal_widgets:
            for event, d in [('request', 0), ('reply', 1)]:
                actual = [row for row in host if row['widget'] == widget and row['event'] == event]
                wanted = [dict(event=event, **expected(widget, sequence, d)) for sequence in range(packets)]
                if actual != wanted:
                    raise ValueError('host packet contents/order/count differ')
            result = [row for row in results if row['widget'] == widget]
            if len(result) != 1 or result[0].get('requests') != packets or result[0].get('replies') != packets or result[0].get('passed') is not True or result[0].get('rounds', 0) < 1:
                raise ValueError('host peer did not complete')
    return dict(passed=True, widgets=widgets, packets_per_widget=packets,
                mode=mode, checked_payload_bytes=True, checked_order=True,
                worker_lifecycle_checked=require_worker_lifecycle,
                observed_checks=['per_widget_packet_bytes_and_identity', 'per_widget_packet_order_and_count',
                                 'distinct_guest_interfaces', *(['guest_worker_launch_and_join'] if require_worker_lifecycle else []),
                                 *(['real_host_peer_request_reply'] if normal_widgets else [])],
                scope='actual UART/peer output validation; FPGA acceptance additionally requires provenance and driver exit')


def self_test():
    # Roundtrip tests compare the independent Python codec against fixed hashes;
    # C codec and actual shared-memory peer are exercised by nic_validate.py.
    assert len(frame(0, 0, 0)) == 126
    assert frame(1, 3, 1)[22:38] == struct.pack('<IIII', 1, 3, 1, 1022)
    for mode in ('normal', 'loopback'):
        widgets, count = [0, 1], 8
        logs, host = [], ''
        for widget in widgets:
            text = 'CIRCT_LINUX_BOOT: PASS\n'
            for sequence in range(count):
                text += 'CIRCT_NIC_PACKET ' + json.dumps(expected(widget, sequence, int(mode == 'normal'))) + '\n'
                for event, direction in [('request', 0), ('reply', 1)]:
                    host += 'CIRCT_NIC_HOST ' + json.dumps(dict(event=event, **expected(widget, sequence, direction))) + '\n'
            text += 'CIRCT_NIC_RESULT ' + json.dumps(dict(widget=widget, mode=mode, interface='eth0', packets=count, bytes=sum(len(frame(widget, seq, 0)) for seq in range(count)), passed=True)) + '\n'
            logs.append(text + 'CIRCT_LINUX_POWEROFF: REQUESTED\nreboot: Power down\n')
            host += 'CIRCT_NIC_PEER_RESULT ' + json.dumps(dict(widget=widget, requests=count, replies=count, rounds=2, passed=True)) + '\n'
        check(logs, widgets, mode, count, host)
        corruptions = [logs[::-1] + [logs[0]], [logs[0].replace('"sequence": 0', '"sequence": 1', 1), logs[1]],
                       [logs[0].replace('Power down', 'still running'), logs[1]],
                       [logs[0].replace('"passed": true', '"passed": false'), logs[1]],
                       [logs[0].replace('"fnv1a": ', '"fnv1a": 1', 1), logs[1]]]
        for broken in corruptions:
            try: check(broken, widgets, mode, count, host)
            except (ValueError, json.JSONDecodeError): pass
            else: raise AssertionError('negative control accepted')
        if mode == 'normal':
            try: check(logs, widgets, mode, count, None)
            except ValueError: pass
            else: raise AssertionError('normal mode accepted without real host traffic')
    mixed_logs = [logs[0], logs[1].replace('"mode": "loopback"', '"mode": "normal"')]
    # Rebuild widget1's packet hashes for a distinct host reply, leaving widget0
    # in hardware/runtime loopback. The normal peer must contain widget1 only.
    lines = mixed_logs[1].splitlines()
    lines = [('CIRCT_NIC_PACKET ' + json.dumps(expected(1, i, 1))) if line.startswith('CIRCT_NIC_PACKET ') else line
             for i, line in enumerate(lines[1:9])] + lines[9:]
    mixed_logs[1] = 'CIRCT_LINUX_BOOT: PASS\n' + '\n'.join(lines) + '\n'
    filtered_host = '\n'.join(line for line in host.splitlines() if json.loads(line.split(' ', 1)[1])['widget'] == 1)
    check(mixed_logs, widgets, {0: 'loopback', 1: 'normal'}, count, filtered_host)
    return dict(passed=True, scope='checker self-test only; no guest/FPGA execution', negative_controls=11, mixed_widget_modes=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--uart', type=Path, action='append', default=[])
    parser.add_argument('--widget', type=int, action='append', default=[])
    parser.add_argument('--mode', choices=('normal', 'loopback'))
    parser.add_argument('--binding', action='append', default=[], metavar='WIDGET:MODE', help='per-widget mode, e.g. 0:normal 1:loopback')
    parser.add_argument('--packets', type=int, default=128)
    parser.add_argument('--peer', type=Path)
    parser.add_argument('--require-worker-lifecycle', action='store_true')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    try:
        bindings = dict((int(item.split(':', 1)[0]), item.split(':', 1)[1]) for item in args.binding)
        result = self_test() if args.self_test else check([path.read_text(errors='replace') for path in args.uart], list(bindings) if bindings else args.widget, bindings if bindings else args.mode, args.packets, args.peer.read_text() if args.peer else None,
            require_worker_lifecycle=args.require_worker_lifecycle)
        result['inputs'] = [dict(path=str(path.resolve()), sha256=hashlib.sha256(path.read_bytes()).hexdigest()) for path in args.uart + ([args.peer] if args.peer else [])]
    except (ValueError, OSError, KeyError, AssertionError) as error:
        result = dict(passed=False, error=str(error))
    payload = json.dumps(result, indent=2, sort_keys=True) + '\n'
    if args.output: args.output.write_text(payload)
    print(payload, end='')
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
