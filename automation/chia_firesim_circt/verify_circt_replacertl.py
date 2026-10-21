#!/usr/bin/env python3
"""Require evidence that replacertl invoked the CIRCT Golden Gate compiler.

The FireSim manager can succeed using cached Scala Golden Gate RTL. Its exit
status alone does not prove that the CIRCT compiler ran in this iteration.
"""

import argparse
from pathlib import Path
import re


COMPILER_COMMAND = re.compile(
    r"^\[localhost\] out:\s*(?:[A-Za-z_][A-Za-z_0-9]*=\S+\s+)*"
    r"(?:\S*/)?goldengate-circt(?:\s|$)",
    re.MULTILINE,
)


def verify_log(output: str) -> None:
    if "midas.stage.GoldenGateMain" in output:
        raise ValueError("replacertl invoked Scala GoldenGateMain")
    if not COMPILER_COMMAND.search(output):
        raise ValueError(
            "replacertl did not log a goldengate-circt compiler invocation; "
            "cached Scala RTL or an unselected CIRCT path cannot pass this gate"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stdout_log", type=Path, help="complete replacertl stdout log")
    args = parser.parse_args()
    try:
        verify_log(args.stdout_log.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(f"Confirmed CIRCT Golden Gate invocation in {args.stdout_log}")


if __name__ == "__main__":
    main()
