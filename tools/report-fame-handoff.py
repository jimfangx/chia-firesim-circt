#!/usr/bin/env python3
"""Report the FAME channel interface at SFC's post-InferModelPorts boundary.

This reads the SFC debug annotation JSON without changing it.  The report
records channel endpoints, channel kinds, model port groups, and bridge
constructor metadata for later comparison with a CIRCT implementation.
"""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path


PREFIX = "midas.passes.fame."
CONNECTION = PREFIX + "FAMEChannelConnectionAnnotation"
PORTS = PREFIX + "FAMEChannelPortsAnnotation"
TRANSFORM = PREFIX + "FAMETransformAnnotation"
FANOUT = PREFIX + "FAMEChannelFanoutAnnotation"
BRIDGE = "firesim.lib.bridgeutils.BridgeIOAnnotation"


def target_module(target: str) -> str:
    if not isinstance(target, str) or not target.startswith("~") or "|" not in target:
        raise ValueError(f"invalid FIRRTL target: {target!r}")
    return target.split(">", 1)[0]


def report(path: Path) -> dict:
    data = path.read_bytes()
    annotations = json.loads(data)
    if not isinstance(annotations, list):
        raise ValueError("expected an annotation array")
    if any(not isinstance(anno, dict) or not isinstance(anno.get("class"), str)
           for anno in annotations):
        raise ValueError("every annotation must have a string class")

    connections = []
    port_groups = []
    transforms = []
    fanout_groups = []
    bridges = []
    seen_channels = set()
    seen_port_groups = set()
    seen_bridge_targets = set()
    mapped_bridge_channels = set()
    for anno in annotations:
        kind = anno["class"]
        if kind == CONNECTION:
            name = anno.get("globalName")
            if not isinstance(name, str) or not name or name in seen_channels:
                raise ValueError(f"missing or duplicate global channel name: {name!r}")
            seen_channels.add(name)
            sources = anno.get("sources") or []
            sinks = anno.get("sinks") or []
            if not isinstance(sources, list) or not isinstance(sinks, list):
                raise ValueError(f"channel {name!r} has non-list endpoints")
            if not sources and not sinks:
                raise ValueError(f"channel {name!r} has no endpoints")
            for target in [*sources, *sinks]:
                target_module(target)
            if anno.get("clock") is not None:
                target_module(anno["clock"])
            info = anno.get("channelInfo")
            if not isinstance(info, dict) or not isinstance(info.get("class"), str):
                raise ValueError(f"channel {name!r} has no channelInfo class")
            connections.append({
                "name": name,
                "kind": info["class"],
                "clock": anno.get("clock"),
                "sources": sources,
                "sinks": sinks,
                "channelInfo": info,
            })
        elif kind == PORTS:
            name = anno.get("localName")
            ports = anno.get("ports")
            if not isinstance(name, str) or not name or not isinstance(ports, list) or not ports:
                raise ValueError(f"invalid model port group: {anno!r}")
            modules = {target_module(port) for port in ports}
            if len(modules) != 1:
                raise ValueError(f"model port group {name!r} spans modules: {sorted(modules)}")
            module = modules.pop()
            if anno.get("clockPort") is not None and target_module(anno["clockPort"]) != module:
                raise ValueError(f"model port group {name!r} has a clock on another module")
            key = (module, name)
            if key in seen_port_groups:
                raise ValueError(f"duplicate model port group: {key!r}")
            seen_port_groups.add(key)
            port_groups.append({
                "module": module,
                "name": name,
                "clockPort": anno.get("clockPort"),
                "ports": ports,
            })
        elif kind == TRANSFORM:
            transforms.append(target_module(anno.get("target")))
        elif kind == FANOUT:
            names = anno.get("channelNames")
            if (not isinstance(names, list) or not names
                    or any(not isinstance(name, str) or not name for name in names)
                    or len(set(names)) != len(names)):
                raise ValueError(f"invalid fanout channel names: {names!r}")
            fanout_groups.append(names)
        elif kind == BRIDGE:
            target = anno.get("target")
            target_module(target)
            if target in seen_bridge_targets:
                raise ValueError(f"duplicate bridge target: {target!r}")
            seen_bridge_targets.add(target)
            mapping = anno.get("channelMapping")
            if (not isinstance(mapping, dict) or not mapping
                    or any(not isinstance(key, str) or not key
                           or not isinstance(value, str) or not value
                           for key, value in mapping.items())):
                raise ValueError(f"bridge {target!r} has an invalid channel mapping")
            repeated = set(mapping.values()) & mapped_bridge_channels
            if repeated or len(set(mapping.values())) != len(mapping):
                raise ValueError(f"bridge channel mapped more than once: {target!r}")
            mapped_bridge_channels.update(mapping.values())
            # Keep the original target and constructor payload: BridgeIOAnnotation
            # still names the pre-WrapTop circuit in this SFC handoff.
            bridges.append(anno)

    if not connections or not port_groups or not transforms:
        raise ValueError("missing FAME channel, model port, or transform annotations")
    unknown_bridge_channels = mapped_bridge_channels - seen_channels
    if unknown_bridge_channels:
        raise ValueError(f"bridges reference unknown channels: {sorted(unknown_bridge_channels)}")
    fanout_members = set()
    for names in fanout_groups:
        unknown = set(names) - seen_channels
        if unknown:
            raise ValueError(f"fanout references unknown channels: {sorted(unknown)}")
        repeated = set(names) & fanout_members
        if repeated:
            raise ValueError(f"channels appear in multiple fanout groups: {sorted(repeated)}")
        fanout_members.update(names)
    return {
        "input": str(path),
        "sha256": hashlib.sha256(data).hexdigest(),
        "annotationClasses": dict(sorted(Counter(a["class"] for a in annotations).items())),
        "transformedModules": sorted(transforms),
        "channels": sorted(connections, key=lambda c: c["name"]),
        "bridges": sorted(bridges, key=lambda bridge: bridge["target"]),
        # The first member is the source identity used by SimWrapper.
        "fanoutGroups": sorted(fanout_groups, key=lambda names: names[0]),
        "modelPortGroups": sorted(port_groups, key=lambda p: (p["module"], p["name"])),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("annotations", type=Path, help="SFC post-infer-model-ports.json")
    args = parser.parse_args()
    try:
        result = report(args.annotations)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        parser.error(str(error))
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
