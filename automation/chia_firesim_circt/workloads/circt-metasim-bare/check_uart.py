#!/usr/bin/env python3
"""Validate FAME completion, ordered test results, and optional golden signatures."""
import argparse
import json
import re
from pathlib import Path

GROUPS = ["integer", "memory", "control", "atomics", "csr", "traps", "vm",
          "interrupts", "fp", "icache", "workloads", "uart"]
START = re.compile(r"^CIRCT_FAME: BEGIN version=1 mask=(\d+)$", re.M)
TEST = re.compile(r"^FAME_TEST: (\w+) PASS checks=(\d+) signature=(0x[0-9a-f]{16})$", re.M)
BEGIN = re.compile(r"^FAME_TEST: (\w+) BEGIN$", re.M)
END = re.compile(r"^CIRCT_FAME: PASS tests=(\d+) checks=(\d+) failures=0 signature=(0x[0-9a-f]{16})$", re.M)
UART_DATA = [f"FAME_UART: DATA {i} 0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz\n" for i in range(8)]


def aggregate(tests):
    mask64 = (1 << 64) - 1
    value = 0xcbf29ce484222325
    for test in tests:
        value ^= int(test["signature"], 16) ^ test["checks"]
        value = ((value ^ (value >> 30)) * 0xbf58476d1ce4e5b9) & mask64
        value = ((value ^ (value >> 27)) * 0x94d049bb133111eb) & mask64
        value ^= value >> 31
    return f"0x{value:016x}"


def validate(output, reference=None):
    output = output.replace("\r", "")
    if re.search(r"FAME_FAIL:|CIRCT_FAME: (?:FAIL|FATAL)|FAME_TEST: \w+ FAIL", output):
        raise ValueError("failure or fatal marker present")
    starts, finishes = list(START.finditer(output)), list(END.finditer(output))
    if len(starts) != 1 or len(finishes) != 1:
        raise ValueError("require exactly one BEGIN and one complete aggregate PASS")
    start, finish = starts[0], finishes[0]
    if finish.start() <= start.end():
        raise ValueError("aggregate PASS precedes BEGIN")
    mask = int(start[1])
    if not mask or mask & ~4095:
        raise ValueError("invalid suite mask")
    expected = [name for i, name in enumerate(GROUPS) if mask & (1 << i)]
    events = sorted([(m.start(), "begin", m[1]) for m in BEGIN.finditer(output)] +
                    [(m.start(), "pass", m[1]) for m in TEST.finditer(output)])
    wanted = [(kind, name) for name in expected for kind in ("begin", "pass")]
    if [(kind, name) for _, kind, name in events] != wanted:
        raise ValueError("test events are missing, duplicated, or out of order")
    if any(not start.end() < pos < finish.start() for pos, _, _ in events):
        raise ValueError("test events outside aggregate boundaries")
    matches = list(TEST.finditer(output))
    tests = [{"name": m[1], "checks": int(m[2]), "signature": m[3]} for m in matches]
    if any(t["checks"] == 0 for t in tests):
        raise ValueError("test group made no checks")
    checks = sum(t["checks"] for t in tests)
    if int(finish[1]) != len(tests) or int(finish[2]) != checks:
        raise ValueError("aggregate test/check count mismatch")
    if finish[3] != aggregate(tests):
        raise ValueError("aggregate signature does not match test signatures")
    if "uart" in expected:
        if any(output.count(line) != 1 for line in UART_DATA) or output.count("FAME_UART: END\n") != 1:
            raise ValueError("missing or duplicated direct UART payload")
        positions = [output.index(line) for line in UART_DATA] + [output.index("FAME_UART: END\n")]
        if positions != sorted(positions):
            raise ValueError("direct UART bytes reordered")
        uart_begin = next(m for m in BEGIN.finditer(output) if m[1] == "uart")
        uart_end = next(m for m in TEST.finditer(output) if m[1] == "uart")
        if not all(uart_begin.end() < pos < uart_end.start() for pos in positions):
            raise ValueError("direct UART payload outside the UART test")
    result = {"mask": mask, "checks": checks, "signature": finish[3], "tests": tests}
    if reference is not None and result != reference:
        raise ValueError("result differs from reference counts/signatures")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("uartlog", type=Path)
    parser.add_argument("--reference", type=Path, help="Spike or SFC expected-results JSON")
    parser.add_argument("--binary", default="circt-fame-suite")
    parser.add_argument("--write-reference", type=Path, help="save a validated SFC result for later comparison")
    args = parser.parse_args()
    reference = None
    if args.reference:
        reference = json.loads(args.reference.read_text())["binaries"][args.binary]
    try:
        result = validate(args.uartlog.read_text(errors="replace"), reference)
    except ValueError as error:
        parser.exit(1, f"FAIL: {error}\n")
    print(f"PASS: {len(result['tests'])} test groups, {result['checks']} checks, {result['signature']}")
    if args.write_reference:
        args.write_reference.write_text(json.dumps({"binaries": {args.binary: result}}, indent=2) + "\n")


if __name__ == "__main__":
    main()
