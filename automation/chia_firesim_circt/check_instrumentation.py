#!/usr/bin/env python3
"""Check bounded enabled-case AutoCounter/Print driver output against stimulus.

The expectation JSON must come from the enabled case's declared stimulus, not
the output being checked. This checker does not launch hardware or grant any
semantic/formal/Linux acceptance. The F2 adapter must separately bind these
observations, the expectation, the workload and the actual checker command.

Schema 1 requires clock:{name,multiplier,divisor}, sample_period_base,
min_samples (>=2), counters:[{label,description,type,event_width,
accumulator_width,first,step}], and prints:[{cycle,payload}]. Counter samples
are checked exactly against first + sample_index*step modulo accumulator width;
cycle is the decoded local target cycle and payload is the complete printf
text, including newlines. Print output must retain the standard cycle prefix.
This bounded deterministic contract can check rollover, formatting and order
when the enabled stimulus actually produces them; it cannot infer reset or
backpressure coverage from an idle Linux boot.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
from pathlib import Path


def require(ok, message):
    if not ok:
        raise ValueError(message)


def integer(value, label, minimum=0, maximum=(1 << 64) - 1):
    require(type(value) is int and minimum <= value <= maximum, label + " is out of range")
    return value


def binding(path):
    path = Path(path).resolve()
    data = path.read_bytes()
    return {"path": str(path), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def check(expected, csv_text, print_text):
    require(expected.get("schema_version") == 1, "unsupported instrumentation expectation schema")
    clock = expected["clock"]
    require(isinstance(clock["name"], str) and clock["name"] and
            not any(c in clock["name"] for c in "\r\n\x00"), "invalid clock domain name")
    multiplier = integer(clock["multiplier"], "clock multiplier", 1, (1 << 32) - 1)
    divisor = integer(clock["divisor"], "clock divisor", 1, (1 << 32) - 1)
    period = integer(expected["sample_period_base"], "sample period", 1)
    require(period * multiplier % divisor == 0, "sample period is fractional in the declared domain")
    minimum = integer(expected["min_samples"], "minimum samples", 2)
    counters = expected["counters"]
    require(isinstance(counters, list) and counters, "expected counter metadata is empty")
    labels = [counter["label"] for counter in counters]
    require(all(isinstance(label, str) and label for label in labels) and
            len(set(labels)) == len(labels) and labels[0] == "local_cycle", "counter labels omit local_cycle or repeat")
    for counter in counters:
        require(counter["type"] in ("Accumulate", "Identity"), "unsupported counter operation")
        require(isinstance(counter["description"], str), "counter description must be a string")
        integer(counter["event_width"], "event width", 1, 1 << 20)
        width = integer(counter["accumulator_width"], "accumulator width", 1, 64)
        integer(counter["first"], "counter first", 0, (1 << width) - 1)
        integer(counter["step"], "counter step", 0, (1 << width) - 1)
    require(any(counter["step"] for counter in counters), "bounded stimulus must require actual counter activity")
    rows = list(csv.reader(io.StringIO(csv_text), strict=True))
    require(len(rows) >= 7 + minimum, "AutoCounter output has too few actual samples")
    require(rows[0] == ["version", "1"], "AutoCounter CSV version differs")
    require(rows[1] == ["Clock Domain Name", " " + clock["name"], " Base Multiplier", " " + str(multiplier),
                        " Base Divisor", " " + str(divisor)], "AutoCounter clock metadata differs")
    metadata = [
        ["label", *labels],
        ["description", *(counter["description"] for counter in counters)],
        ["type", *(counter["type"] for counter in counters)],
        ["event width", *(str(counter["event_width"]) for counter in counters)],
        ["accumulator width", *(str(counter["accumulator_width"]) for counter in counters)],
    ]
    require(rows[2:7] == metadata, "AutoCounter labels/descriptions/operations/widths differ from stimulus")
    for index, row in enumerate(rows[7:]):
        require(len(row) == len(counters) + 1, "AutoCounter sample column count differs")
        require(all(value.isascii() and value.isdecimal() for value in row), "AutoCounter sample is not unsigned decimal")
        require(int(row[0]) == (index + 1) * period, "AutoCounter sample timestamps are lost, duplicated or reordered")
        for counter, value in zip(counters, row[1:]):
            predicted = (counter["first"] + index * counter["step"]) % (1 << counter["accumulator_width"])
            require(int(value) == predicted,
                    "AutoCounter value differs at sample %d counter %s" % (index, counter["label"]))
    prints = expected["prints"]
    require(isinstance(prints, list) and prints, "expected prints are empty")
    body = []
    previous = 0
    for event in prints:
        cycle = integer(event["cycle"], "print target cycle")
        require(cycle >= previous, "expected prints reorder target cycles")
        previous = cycle
        require(isinstance(event["payload"], str) and event["payload"], "expected print payload is empty")
        body.append("CYCLE:%13d " % cycle + event["payload"])
    header = "# Clock Domain: %s, Relative Frequency: %d/%d of Base Clock\n" % (clock["name"], multiplier, divisor)
    require(print_text == header + "".join(body), "Print clock, accepted cycles, formatting, values or order differ")
    return {"samples": len(rows) - 7, "prints": len(prints), "counter_labels": labels,
            "clock": clock, "sample_period_base": period,
            "scope": "Exact bounded driver CSV metadata/values/timestamps and Print payload/cycle/order; hardware execution and other acceptance stages are separate."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected", type=Path, required=True)
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--prints", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    observed = {"expectation": binding(args.expected), "csv": binding(args.csv), "prints": binding(args.prints)}
    try:
        result = check(json.loads(args.expected.read_text()), args.csv.read_text(), args.prints.read_text())
        result.update(passed=True)
    except (ValueError, KeyError, TypeError, csv.Error) as error:
        result = {"passed": False, "error": str(error)}
    require(observed == {"expectation": binding(args.expected), "csv": binding(args.csv), "prints": binding(args.prints)},
            "instrumentation observation changed during validation")
    result["observations"] = observed
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
