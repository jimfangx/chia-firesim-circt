#!/usr/bin/env python3
"""Build real guest/peer/driver code and validate NIC execution tooling.

These are bounded host-side checks. They do not claim Linux guest execution,
FPGA operation, SFC equivalence or formal signoff. All child processes are joined.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import mmap
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import time
import uuid

import nic_check

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]


def record(path):
    return dict(path=str(path.resolve()), sha256=hashlib.sha256(path.read_bytes()).hexdigest())


TOKEN_SEQUENCE_MASK = (1 << 43) - 1


def tokens(flits, count, first_sequence=0):
    result = bytearray(count * 64)
    for beat in range(count):
        control = (first_sequence + beat) & TOKEN_SEQUENCE_MASK
        for slot in range(7):
            bits = 2
            if flits:
                word, last = flits.pop(0)
                struct.pack_into('<Q', result, beat * 64 + (slot + 1) * 8, word)
                bits |= 1 | (4 if last else 0)
            control |= bits << (43 + slot * 3)
        struct.pack_into('<Q', result, beat * 64, control)
    return result


def flits_for(widget, packets, corrupt=False):
    result = []
    for sequence in range(packets):
        # Padding before the Ethernet header is unspecified on outgoing DMA.
        wire = bytearray(b'\x81\xf9' + nic_check.frame(widget, sequence, 0))
        if corrupt and sequence == 0: wire[60] ^= 1
        result.extend((struct.unpack_from('<Q', wire, offset)[0], offset + 8 == len(wire))
                      for offset in range(0, len(wire), 8))
    return result


def peer_test(binary, directory, widgets=(0, 1), corrupt=False, sequence_fault=None, sequence_start=None):
    directory.mkdir()
    ports = {widget: 'chia_peer_test_' + uuid.uuid4().hex for widget in widgets}
    paths = [Path('/dev/shm') / ('port_' + direction + port + '_' + str(round_))
             for port in ports.values() for direction in ('nts', 'stn') for round_ in (0, 1)]
    log, err = directory / 'stdout.log', directory / 'stderr.log'
    stop = directory / 'stop'
    argv = [str(binary), '14', '8', '30', str(stop), *[str(widget) + ':' + port for widget, port in ports.items()]]
    mappings = {}
    with log.open('w') as stdout, err.open('w') as stderr:
        process = subprocess.Popen(argv, stdout=stdout, stderr=stderr)
        try:
            deadline = time.monotonic() + 10
            while 'CIRCT_NIC_PEER_READY' not in log.read_text():
                if process.poll() is not None: raise AssertionError(err.read_text())
                if time.monotonic() > deadline: raise AssertionError('peer startup timeout')
                time.sleep(.01)
            for widget, port in ports.items():
                for direction in ('nts', 'stn'):
                    for round_ in (0, 1):
                        path = Path('/dev/shm') / ('port_' + direction + port + '_' + str(round_))
                        with path.open('r+b') as stream: mappings[widget, direction, round_] = mmap.mmap(stream.fileno(), 129)
            outgoing = {widget: flits_for(widget, 8, corrupt) for widget in widgets}
            received, current, round_index = {w: [] for w in widgets}, {w: bytearray() for w in widgets}, {w: 0 for w in widgets}
            first_sequences = {w: (sequence_start if sequence_start is not None else
                                      37 if i == 0 else TOKEN_SEQUENCE_MASK - 3)
                               for i, w in enumerate(widgets)}
            sent_beats = {w: 0 for w in widgets}
            ticks = 0
            while any(outgoing.values()) or any(len(received[w]) < 8 for w in widgets):
                # Hold widget1 independently while widget0 continues; then
                # alternate and drain. An extra global barrier would deadlock.
                selected = [widgets[0]] if ticks < 6 else list(reversed(widgets))
                for widget in selected:
                    round_ = round_index[widget]
                    tx, rx = mappings[widget, 'nts', round_], mappings[widget, 'stn', round_]
                    assert tx[128] == 0 and rx[128] == 0
                    sequence = (first_sequences[widget] + sent_beats[widget]) & TOKEN_SEQUENCE_MASK
                    expected_sequence = sequence
                    inject_fault = sequence_fault is not None and ticks == 2 and widget == widgets[0]
                    if inject_fault:
                        assert sequence_fault in ('duplicate', 'skip')
                        sequence = (sequence + (-1 if sequence_fault == 'duplicate' else 1)) & TOKEN_SEQUENCE_MASK
                    tx[:128] = tokens(outgoing[widget], 2, sequence)
                    tx[128] = 1
                    sent_beats[widget] += 2
                    deadline = time.monotonic() + 5
                    while not rx[128]:
                        if process.poll() is not None:
                            if inject_fault and process.returncode == 1:
                                diagnostic = [json.loads(line.split(' ', 1)[1]) for line in err.read_text().splitlines()
                                              if line.startswith('CIRCT_NIC_TOKEN_SEQUENCE_FAILURE ')]
                                assert len(diagnostic) == 1 and diagnostic[0]['widget'] == widget
                                assert diagnostic[0]['expected'] == expected_sequence and diagnostic[0]['actual'] == sequence
                                assert diagnostic[0]['token_count'] == sent_beats[widget] - 2
                                assert all(not path.exists() for path in paths), 'peer leaked shared memory on sequence failure'
                                return dict(passed=True, negative_control='host peer rejects ' + sequence_fault + ' outgoing token',
                                            diagnostic=diagnostic[0], shared_memory_cleanup=True, argv=argv)
                            if corrupt and process.returncode == 1 and 'payload/order/widget mismatch' in err.read_text():
                                return dict(passed=True, negative_control='host peer rejects corrupted live packet', argv=argv)
                            raise AssertionError(err.read_text())
                        if time.monotonic() > deadline: raise AssertionError('peer response timeout')
                        time.sleep(.0001)
                    for beat in range(2):
                        control, = struct.unpack_from('<Q', rx, beat * 64)
                        for slot in range(7):
                            bits = control >> (43 + slot * 3) & 7
                            if bits & 1:
                                current[widget] += rx[beat * 64 + (slot + 1) * 8: beat * 64 + (slot + 2) * 8]
                                if bits & 4:
                                    sequence = len(received[widget])
                                    assert current[widget] == b'\0\0' + nic_check.frame(widget, sequence, 1)
                                    received[widget].append(bytes(current[widget])); current[widget].clear()
                    rx[128] = 0
                    round_index[widget] ^= 1
                ticks += 1
                assert ticks < 1000
            assert not corrupt and sequence_fault is None
            stop.touch()
            assert process.wait(timeout=5) == 0, err.read_text()
            assert all(not path.exists() for path in paths), 'peer leaked shared memory'
            summaries = [json.loads(line.split(' ', 1)[1]) for line in log.read_text().splitlines()
                         if line.startswith('CIRCT_NIC_PEER_RESULT ')]
            assert len(summaries) == len(widgets)
            for summary in summaries:
                widget = summary['widget']
                assert summary['passed'] and summary['token_sequence_observed']
                assert summary['token_first'] == first_sequences[widget]
                assert summary['token_count'] == sent_beats[widget]
                assert summary['token_last'] == (first_sequences[widget] + sent_beats[widget] - 1) & TOKEN_SEQUENCE_MASK
            return dict(passed=True, widgets=list(widgets), packets_per_widget=8,
                        independent_stalls=True, checked_exact_reply_bytes=True, shared_memory_cleanup=True,
                        token_sequences=summaries,
                        sequence_rollover_exercised=any(first_sequences[w] + sent_beats[w] > TOKEN_SEQUENCE_MASK for w in widgets),
                        argv=argv)
        finally:
            if process.poll() is None:
                process.terminate()
                try: process.wait(timeout=5)
                except subprocess.TimeoutExpired: process.kill(); process.wait()
            for mapping in mappings.values(): mapping.close()
            assert all(not path.exists() for path in paths), 'peer leaked owned shared memory'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--cc', default='gcc')
    parser.add_argument('--cxx', default='g++')
    parser.add_argument('--riscv-cc', default='riscv64-unknown-linux-gnu-gcc')
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    commands = []
    def run(argv, name, expected_returncode=0):
        argv = list(map(str, argv))
        completed = subprocess.run(argv, capture_output=True, timeout=90)
        stdout, stderr = output / (name + '.stdout'), output / (name + '.stderr')
        stdout.write_bytes(completed.stdout); stderr.write_bytes(completed.stderr)
        commands.append(dict(argv=argv, returncode=completed.returncode, timed_out=False, stdout=record(stdout), stderr=record(stderr)))
        (output / 'commands.json').write_text(json.dumps(commands, indent=2) + '\n')
        if completed.returncode != expected_returncode: raise AssertionError(name + ': ' + completed.stderr.decode(errors='replace'))
        return completed
    result = dict(passed=False, scope='bounded execution-tool validation only; no FPGA/Linux acceptance')
    try:
        native = output / 'nic_packets'
        guest = output / 'circt-nic-packets'
        peer = output / 'nic_peer'
        run([args.cc, '-O2', '-std=c11', '-Wall', '-Wextra', '-Werror', HERE / 'nic_packets.c', '-o', native], 'native-workload-build')
        run([args.riscv_cc, '-O2', '-std=c11', '-Wall', '-Wextra', '-Werror', '-static', HERE / 'nic_packets.c', '-o', guest], 'riscv-workload-build')
        run([args.cxx, '-O2', '-std=c++17', '-static', '-Wall', '-Wextra', '-Werror', HERE / 'nic_peer.cc', '-o', peer], 'peer-build')
        run(['sh', '-n', HERE / 'nic_guest.sh'], 'guest-script-syntax')
        source = output / 'codec.c'
        source.write_text('#include "nic_protocol.h"\n#include <stdio.h>\nint main(void){unsigned char f[NIC_FRAME_MAX];for(unsigned w=0;w<4;w++)for(unsigned s=0;s<8;s++)for(unsigned d=0;d<2;d++){unsigned n=nic_frame(f,w,s,d);printf("%u %u %u %u %u\\n",w,s,d,n,nic_hash(f,n));}return 0;}\n')
        run([args.cc, '-O2', '-Wall', '-Werror', '-I', HERE, source, '-o', output / 'codec'], 'codec-build')
        codec = run([output / 'codec'], 'codec-run')
        for line in codec.stdout.decode().splitlines():
            widget, seq, direction, length, hashed = map(int, line.split())
            data = nic_check.frame(widget, seq, direction)
            assert (len(data), nic_check.fnv(data)) == (length, hashed)
        core = REPO / 'sims/firesim/sim/midas/src/main/cc'
        bridge = REPO / 'generators/firechip/bridgestubs/src/main/cc'
        driver = output / 'nic_driver_test'
        run([args.cxx, '-O2', '-std=c++17', '-ffunction-sections', '-fdata-sections', '-I', core, '-I', bridge,
             HERE / 'nic_driver_test.cc', bridge / 'bridges/simplenic.cc',
             *[core / ('core/' + name + '.cc') for name in ('widget', 'bridge_driver', 'stream_engine')],
             '-Wl,--gc-sections', '-o', driver], 'production-driver-build')
        run([driver], 'production-driver-run')
        pressure = output / 'nic_backpressure_test'
        run([args.cxx, '-O2', '-std=c++17', '-ffunction-sections', '-fdata-sections', '-I', core, '-I', bridge,
             HERE / 'nic_backpressure_test.cc', bridge / 'bridges/simplenic.cc',
             core / 'bridges/cpu_managed_stream.cc',
             *[core / ('core/' + name + '.cc') for name in ('widget', 'bridge_driver', 'stream_engine')],
             '-Wl,--gc-sections', '-o', pressure], 'production-backpressure-build')
        run([pressure], 'production-backpressure-run')
        short = run([pressure, '--short-write'], 'production-short-write-rejection', expected_returncode=1)
        if b'actually wrote in 64 bytes, wanted 58560 bytes' not in short.stdout:
            raise AssertionError('NIC did not reject a nonzero incomplete stream write')
        short_pull = run([pressure, '--short-pull'], 'production-short-pull-rejection', expected_returncode=1)
        if b'NIC incomplete pull: stream=0 actual=29280 expected=58560' not in short_pull.stderr:
            raise AssertionError('NIC did not reject an incomplete pull before peer publication')
        run([sys.executable, HERE / 'nic_manager_test.py', '--peer', peer, '--output-dir', output / 'manager-lifecycle'], 'manager-lifecycle')
        result['peer_two_widgets'] = peer_test(peer, output / 'peer-two-widgets')
        result['peer_corruption'] = peer_test(peer, output / 'peer-corruption', (0,), True)
        result['peer_sequence_rollover'] = peer_test(peer, output / 'peer-sequence-rollover', (0,), sequence_start=TOKEN_SEQUENCE_MASK - 1)
        assert result['peer_sequence_rollover']['sequence_rollover_exercised']
        result['peer_sequence_duplicate'] = peer_test(peer, output / 'peer-sequence-duplicate', (0,), sequence_fault='duplicate')
        result['peer_sequence_skip'] = peer_test(peer, output / 'peer-sequence-skip', (0,), sequence_fault='skip')
        result['checker'] = nic_check.self_test()
        result['artifacts'] = {name: record(path) for name, path in [('guest', guest), ('peer', peer), ('driver_test', driver), ('backpressure_test', pressure)]}
        result['passed'] = True
    except (ValueError, OSError, AssertionError, subprocess.SubprocessError) as error:
        result['error'] = str(error)
    result['sources'] = [record(path) for path in sorted(HERE.glob('nic_*')) if path.is_file()]
    (output / 'result.json').write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
