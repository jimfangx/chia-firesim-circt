#!/usr/bin/env python3
"""Check every logical tile's actual pinned Linux arithmetic/memory ledger."""
import argparse
import json
from pathlib import Path
import re

MASK = (1 << 64) - 1

def step(value):
    return (value * 6364136223846793005 + 1442695040888963407) & MASK

def digest(ident):
    result = 0
    for epoch in range(5):
        state = 0x123456789abcdef ^ (ident << 32) ^ epoch
        memory = [step(state ^ i) for i in range(4093)]
        for round in range(1024):
            index = (round * 97 + ident * 31) % len(memory)
            memory[index] = step(memory[index])
            state = step(state ^ memory[index])
        for value in memory:
            result = step(result ^ value)
        result = step(result ^ state)
    return f'{result:016x}'

def check(text, threads, shutdown=True):
    if type(threads) is not int or not 1 <= threads <= 64:
        raise ValueError('thread count must be an integer from one through 64')
    rows = [json.loads(line.split('CIRCT_THREAD_STATE ', 1)[1]) for line in text.splitlines()
            if 'CIRCT_THREAD_STATE ' in line]
    expected = [dict(thread=i, cpu=i, epochs=5, words=4093, rounds=1024, digest=digest(i)) for i in range(threads)]
    # CPU IDs are selected from the actual affinity mask. Require distinct CPUs;
    # the fixed Linux representative additionally binds CPU0..N-1 below.
    fields = {'thread', 'cpu', 'epochs', 'words', 'rounds', 'digest'}
    if any(not isinstance(row, dict) or set(row) != fields
           or any(type(row[key]) is not int or row[key] < 0 for key in fields - {'digest'})
           or not isinstance(row['digest'], str) or re.fullmatch('[0-9a-f]{16}', row['digest']) is None
           for row in rows):
        raise ValueError('malformed thread state ledger')
    if len(rows) != threads or len({r['cpu'] for r in rows}) != threads:
        raise ValueError('missing logical thread or duplicate CPU')
    for row, want in zip(rows, expected):
        if {k:v for k,v in row.items() if k != 'cpu'} != {k:v for k,v in want.items() if k != 'cpu'}:
            raise ValueError('thread state/memory result differs')
    result = re.findall(r'^CIRCT_THREAD_RESULT: (.+)$', text, re.MULTILINE)
    if result != [f'PASS threads={threads}'] or re.search(r'CIRCT_(?:THREAD|LINUX)_[^\n]*FAIL', text):
        raise ValueError('thread execution did not complete successfully')
    if shutdown:
        boot = text.find('CIRCT_LINUX_BOOT: PASS')
        data = text.find('CIRCT_THREAD_STATE ')
        done = text.find('CIRCT_THREAD_RESULT: PASS')
        power = text.find('CIRCT_LINUX_POWEROFF: REQUESTED')
        if ([r['cpu'] for r in rows] != list(range(threads)) or not 0 <= boot < data < done < power
                or not re.search('reboot: Power down', text[power:])):
            raise ValueError('Linux CPU binding/ordered boot-exercise-poweroff missing')
    return {'passed': True, 'threads': threads, 'digests': [r['digest'] for r in rows],
            'observed_checks': ['distinct_pinned_logical_cpus', 'per_thread_initialized_memory',
                                'deterministic_arithmetic_and_written_state', 'five_independent_software_epochs'],
            'scope': 'initialized/written software state per pinned logical tile; no claim of target reset proof'}

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--uart', type=Path, required=True)
    parser.add_argument('--threads', type=int, default=4)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(json.dumps(check(args.uart.read_text(), args.threads), indent=2) + '\n')
