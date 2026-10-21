#!/usr/bin/env python3
"""Count annotation classes and check a FIRRTL file's embedded handoff."""

import argparse
from collections import Counter
import json
from pathlib import Path


def load_annotations(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as source:
        annotations = json.load(source)
    if not isinstance(annotations, list):
        raise ValueError("expected a JSON array of annotations")
    for index, annotation in enumerate(annotations):
        if not isinstance(annotation, dict) or not isinstance(annotation.get("class"), str):
            raise ValueError(f"annotation at index {index} has no string 'class' field")
    return annotations


def load_embedded_annotations(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as source:
        source.readline()  # FIRRTL version
        if not source.readline().rstrip().endswith(":%[["):
            raise ValueError(f"{path}: expected circuit-level embedded annotations")
        lines = []
        for line in source:
            if line.rstrip() == "]]":
                break
            lines.append(line)
        else:
            raise ValueError(f"{path}: unterminated embedded annotations")
    annotations = json.loads("[" + "".join(lines) + "]")
    if not isinstance(annotations, list):
        raise ValueError(f"{path}: expected an embedded annotation array")
    for index, annotation in enumerate(annotations):
        if not isinstance(annotation, dict) or not isinstance(annotation.get("class"), str):
            raise ValueError(f"{path}: embedded annotation {index} has no string 'class' field")
    return annotations


def count_classes(annotations: list[dict]) -> Counter[str]:
    classes: Counter[str] = Counter()
    for annotation in annotations:
        classes[annotation["class"]] += 1
    return classes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("annotations", nargs="+", type=Path, help="annotation JSON files")
    parser.add_argument("--json", action="store_true", help="emit machine-readable counts")
    parser.add_argument(
        "--check-embedded",
        type=Path,
        metavar="FIRRTL",
        help="require the FIRRTL embedded annotations to exactly match one JSON input",
    )
    args = parser.parse_args()
    if args.check_embedded and len(args.annotations) != 1:
        parser.error("--check-embedded requires exactly one annotation JSON file")

    try:
        loaded = {str(path): load_annotations(path) for path in args.annotations}
        if args.check_embedded:
            embedded = load_embedded_annotations(args.check_embedded)
            external = next(iter(loaded.values()))
            if embedded != external:
                first_difference = next(
                    (index for index, pair in enumerate(zip(embedded, external)) if pair[0] != pair[1]),
                    min(len(embedded), len(external)),
                )
                raise ValueError(
                    f"{args.check_embedded}: embedded annotations differ from JSON at index "
                    f"{first_difference} (embedded={len(embedded)}, JSON={len(external)})"
                )
        inventories = {path: count_classes(annotations) for path, annotations in loaded.items()}
    except (OSError, ValueError, json.JSONDecodeError) as error:
        parser.error(str(error))

    if args.json:
        print(json.dumps(inventories, indent=2, sort_keys=True))
        return

    for path, classes in inventories.items():
        if len(inventories) > 1:
            print(f"{path}:")
        width = max(len("annotation class"), *(len(name) for name in classes))
        print(f"{'annotation class':<{width}}  count")
        print(f"{'-' * width}  -----")
        for name, count in sorted(classes.items()):
            print(f"{name:<{width}}  {count}")
        print(f"{'total':<{width}}  {sum(classes.values())}")


if __name__ == "__main__":
    main()
