"""Strict memory packing inventory for native enabled read-data captures.

This scanner recognizes a small emitted-SV grammar, not arbitrary Verilog.
It recovers packing metadata only. Its output may propose helper assertions;
it establishes neither equality nor any initial-state relation.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shutil

ID = r"[A-Za-z_][A-Za-z_0-9]*"
MODULE = re.compile(r"^\s*module\s+(" + ID + r")\s*\((.*?)\)\s*;(.*?)^\s*endmodule", re.M | re.S)
TOKEN = re.compile(r"[A-Za-z_$][A-Za-z_$0-9]*|\d+'[sS]?[bBdDhHoO][0-9a-fA-F_xXzZ?]+|<=|[^\s]")


def _clean(text):
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def _norm(text):
    return re.sub(r"\s+", "", text)


def _ports(header):
    result = {}
    direction = None
    width = 1
    for token in header.split(","):
        match = re.fullmatch(r"\s*(?:(input|output)\s+)?(?:wire\s+|reg\s+|logic\s+)?"
                             r"(?:\[(\d+):0\]\s*)?(" + ID + r")\s*", token)
        if not match:
            raise ValueError("unsupported port declaration")
        d, high, name = match.groups()
        if d:
            direction, width = d, int(high) + 1 if high is not None else 1
        elif high is not None:
            width = int(high) + 1
        if direction is None or name in result:
            raise ValueError("ambiguous port declaration")
        result[name] = (direction, width)
    return result


def _balanced(text, pos):
    """Return contents and end index for one parenthesized expression."""
    if text[pos] != "(":
        raise ValueError("expected opening parenthesis")
    depth = 1
    for end in range(pos + 1, len(text)):
        depth += (text[end] == "(") - (text[end] == ")")
        if not depth:
            return text[pos + 1:end], end + 1
    raise ValueError("unbalanced parentheses")


def _connections(text):
    result = {}
    pos = 0
    while pos < len(text):
        match = re.match(r"\s*\.(" + ID + r")\s*", text[pos:])
        if not match:
            raise ValueError("unsupported named connection")
        name = match.group(1)
        pos += match.end()
        value, pos = _balanced(text, pos)
        if name in result:
            raise ValueError("duplicate named connection")
        result[name] = _norm(value)
        rest = re.match(r"\s*(,?)\s*", text[pos:])
        pos += rest.end()
        if not rest.group(1) and pos != len(text):
            raise ValueError("missing connection separator")
    return result


def _statements(tokens, pos=0, conditional=False):
    """Parse begin/if/semicolon statements; reject other control constructs."""
    if tokens[pos] == "begin":
        pos += 1
        leaves = []
        while pos < len(tokens) and tokens[pos] != "end":
            pos, child = _statements(tokens, pos, conditional)
            leaves += child
        if pos >= len(tokens):
            raise ValueError("unterminated begin")
        return pos + 1, leaves
    if tokens[pos] == "if":
        pos += 1
        if tokens[pos] != "(":
            raise ValueError("unsupported condition")
        depth = 1
        pos += 1
        while pos < len(tokens) and depth:
            depth += (tokens[pos] == "(") - (tokens[pos] == ")")
            pos += 1
        if depth:
            raise ValueError("unterminated condition")
        pos, leaves = _statements(tokens, pos, True)
        if pos < len(tokens) and tokens[pos] == "else":
            pos, child = _statements(tokens, pos + 1, True)
            leaves += child
        return pos, leaves
    start = pos
    while pos < len(tokens) and tokens[pos] != ";":
        if tokens[pos] in {"begin", "end", "case", "casez", "casex", "for", "while", "repeat"}:
            raise ValueError("unsupported procedural control")
        pos += 1
    if pos >= len(tokens):
        raise ValueError("unterminated statement")
    return pos + 1, [(tokens[start:pos], conditional)]


def _captures(body):
    captures = []
    for match in re.finditer(r"\balways\s*@\s*\(\s*posedge\s+(" + ID + r")\s*\)\s*", body):
        tokens = TOKEN.findall(body[match.end():])
        try:
            _, leaves = _statements(tokens)
        except (ValueError, IndexError):
            continue
        for statement, conditional in leaves:
            if len(statement) == 3 and statement[1] == "<=" and not conditional:
                captures.append((statement[0], statement[2], match.group(1)))
    return captures


def _leaf(memory, ports, body):
    expected = {"R0_addr", "R0_en", "R0_clk", "R0_data", "W0_addr", "W0_en", "W0_clk", "W0_data"}
    if ports.keys() != expected:
        raise ValueError("not a sole-reader/sole-writer RAM")
    width, addr = ports["R0_data"][1], ports["R0_addr"][1]
    if ports != {key: ("output" if key == "R0_data" else "input",
                       width if key in {"R0_data", "W0_data"} else addr if key.endswith("addr") else 1)
                 for key in expected}:
        raise ValueError("inconsistent memory port widths")
    declaration = re.findall(r"\breg\s*\[(\d+):0\]\s*(" + ID + r")\s*\[0:(\d+)\]\s*;", body)
    if len(declaration) != 1:
        raise ValueError("ambiguous memory array")
    high, storage, last = declaration[0]
    depth = int(last) + 1
    if int(high) + 1 != width or depth != 1 << addr:
        raise ValueError("inconsistent memory geometry")
    assignments = re.findall(r"\bassign\s+R0_data\s*=\s*([^;]+);", body)
    if len(assignments) != 1 or _norm(assignments[0]) != f"R0_en?{storage}[R0_addr]:{width}'bx":
        raise ValueError("read address/data is not a direct asynchronous array read")
    if len(re.findall(r"\balways(?:_ff|_comb|_latch)?\b", body)) != 1 or re.search(r"\bR0_data\s*<=", body):
        raise ValueError("unsupported extra procedural memory driver")
    writers = re.findall(r"\balways\s*@\s*\(\s*posedge\s+W0_clk\s*\)\s*begin\s*"
                         r"if\s*\(\s*(W0_en(?:\s*&\s*1'h1)?)\s*\)\s*"
                         + re.escape(storage) + r"\s*\[\s*W0_addr\s*\]\s*<=\s*W0_data\s*;\s*end\b", body)
    if len(writers) != 1 or len(re.findall(re.escape(storage) + r"\s*\[[^;]*?<=", body)) != 1:
        raise ValueError("write clock/address/data is not a direct enabled write")
    return width, depth, storage


def recover_capture_layouts(paths):
    """Return supplemental matcher layouts and checked structural records.

    Unsupported candidates are omitted. No existing matcher layout is changed.
    Same read/write clock is required; read and write addresses may be independent.
    Only unconditional captures of always-enabled asynchronous reads are recognized.
    """
    modules = {}
    for path in paths:
        path = Path(path)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        for match in MODULE.finditer(_clean(path.read_text())):
            name, header, body = match.groups()
            if name in modules:
                raise ValueError("duplicate module definition: " + name)
            modules[name] = (header, body, str(path), digest)
    layouts, records = {}, []
    for parent, (header, body, path, digest) in modules.items():
        if not re.search(r"\w+\s*<=\s*_\w+_ext_R0_data\s*;", body):
            continue
        try:
            ports = _ports(header)
        except ValueError:
            continue
        for match in re.finditer(r"^\s*(" + ID + r")\s+(" + ID + r"_ext)\s*\(", body, re.M):
            memory, instance = match.groups()
            if memory not in modules:
                continue
            try:
                args, _ = _balanced(body, match.end() - 1)
                connections = _connections(args)
                leaf_header, leaf_body, _, _ = modules[memory]
                width, depth, storage = _leaf(memory, _ports(leaf_header), leaf_body)
                if connections.keys() != {"R0_addr", "R0_en", "R0_clk", "R0_data", "W0_addr", "W0_en", "W0_clk", "W0_data"}:
                    raise ValueError("unexpected RAM instance connections")
                read = "_" + instance + "_R0_data"
                if connections["R0_data"] != read or connections["R0_en"] != "1'h1":
                    raise ValueError("read is not the expected always-enabled memory wire")
                if connections["R0_clk"] != connections["W0_clk"] or not re.fullmatch(ID, connections["R0_clk"]):
                    raise ValueError("read/write clocks differ or are expressions")
                candidates = [(capture, clock) for capture, source, clock in _captures(body) if source == read]
                if len(candidates) != 1:
                    raise ValueError("missing or ambiguous unconditional capture")
                capture, clock = candidates[0]
                if clock != connections["R0_clk"] or ports.get(clock) != ("input", 1):
                    raise ValueError("capture clock differs from memory clock")
                for kind, name in [("wire", read), ("reg", capture)]:
                    if len(re.findall(r"\b" + kind + r"\s*\[" + str(width - 1) + r":0\]\s+" + name + r"\s*;", body)) != 1:
                        raise ValueError("capture/read declaration width differs")
                if len(re.findall(r"\b" + capture + r"\s*<=", body)) != 1 or re.search(r"\bassign\s+" + capture + r"\b", body):
                    raise ValueError("capture has another driver")
                reads = re.findall(r"^\s*assign\s+(" + ID + r")\s*=\s*" + capture + r"\[(\d+)(?::(\d+))?\]\s*;", body, re.M)
                if len(reads) < 2:
                    raise ValueError("not an aggregate field packing")
                fields, used = [], set()
                for output, high, low in reads:
                    high, low = int(high), int(low if low else high)
                    field = re.fullmatch(r"deq_bits_(" + ID + r")", output)
                    if not field or high < low or high >= width or ports.get(output) != ("output", high - low + 1):
                        raise ValueError("output field range/width differs")
                    if len(re.findall(r"\bassign\s+" + re.escape(output) + r"\s*=", body)) != 1 or re.search(r"\b" + re.escape(output) + r"\s*<=", body):
                        raise ValueError("output field has multiple drivers")
                    bits = set(range(low, high + 1))
                    if used & bits:
                        raise ValueError("overlapping read slices")
                    used |= bits
                    fields.append([field.group(1), high, low])
                if used != set(range(width)) or len({f[0] for f in fields}) != len(fields):
                    raise ValueError("incomplete or ambiguous read packing")
                writer = re.fullmatch(r"\{(" + ID + r"(?:," + ID + r")+)\}", connections["W0_data"])
                if not writer:
                    raise ValueError("writer is not a simple field concatenation")
                writer_fields, bit = [], width
                expected_fields = []
                for signal in writer.group(1).split(","):
                    field = re.fullmatch(r"enq_bits_(" + ID + r")", signal)
                    direction, field_width = ports.get(signal, (None, None))
                    if not field or direction != "input":
                        raise ValueError("writer field is not a named input")
                    writer_fields.append([signal, field_width])
                    expected_fields.append([field.group(1), bit - 1, bit - field_width])
                    bit -= field_width
                if bit != 0 or sorted(expected_fields) != sorted(fields):
                    raise ValueError("writer concatenation and reader slices disagree")
                fields.sort(key=lambda f: -f[1])
                layouts.setdefault(parent, {})[instance] = fields
                records.append(dict(parent_module=parent, memory_instance=instance, memory_module=memory,
                                    storage=storage, width=width, depth=depth, capture_register=capture,
                                    capture_clock=clock, read_signal=read, read_address=connections["R0_addr"],
                                    write_address=connections["W0_addr"], fields=fields, writer_fields=writer_fields,
                                    source_file=path, source_sha256=digest))
            except (ValueError, IndexError):
                continue
    return layouts, records


def merge_layouts(original, supplement):
    """Preserve original entries and reject conflicting supplemental packing."""
    result = {module: dict(instances) for module, instances in original.items()}
    for module, instances in supplement.items():
        for instance, fields in instances.items():
            previous = result.setdefault(module, {}).get(instance)
            if previous is not None and sorted(map(tuple, previous)) != sorted(map(tuple, fields)):
                raise ValueError(f"conflicting memory field packing: {module}.{instance}")
            if previous is None:
                result[module][instance] = fields
    return result


def _instance_paths(paths, root, target):
    """Resolve native non-generated instance paths from actual module bodies."""
    modules = {}
    for path in paths:
        for match in MODULE.finditer(_clean(Path(path).read_text())):
            name, _, body = match.groups()
            if name in modules:
                raise ValueError("duplicate module in instance-path validation")
            modules[name] = body
    if root not in modules:
        raise ValueError("compiler instance-path root is absent from emitted RTL")
    instances = {}
    for module, body in modules.items():
        children = {}
        for match in re.finditer(r"^\s*(" + ID + r")\s+(" + ID + r")\s*\(", body, re.M):
            child, instance = match.groups()
            if child not in modules:
                continue
            if instance in children:
                raise ValueError("ambiguous emitted instance name")
            children[instance] = child
        instances[module] = children
    result = []
    def visit(module, path, active):
        if module in active:
            raise ValueError("cyclic emitted module hierarchy")
        if module == target:
            result.append(path)
        for instance, child in instances[module].items():
            visit(child, path + "." + instance, active | {module})
    visit(root, root, set())
    return sorted(result)


def validate_origins(paths, origins, records):
    """Bind recovered layouts to the compiler's descriptive, hash-bound sidecar.

    The candidate receipt independently binds the entire RTL. This binding
    covers its exact compiler-emitted prefix, before frozen resources append.
    No correspondence with SFC state or initial equality follows from this join.
    """
    if len(paths) != 1 or origins.get("schema_version") != 1 or isinstance(origins.get("schema_version"), bool) or origins.get("kind") != "goldengate.native-memory-origins":
        raise ValueError("unsupported native memory provenance schema")
    binding = origins.get("binding", {})
    size, digest = binding.get("bytes"), binding.get("sha256")
    if binding.get("scope") != "compiler_emitted_prefix" or type(size) is not int or size <= 0 or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("invalid compiler-prefix binding")
    data = Path(paths[0]).read_bytes()
    if size > len(data) or hashlib.sha256(data[:size]).hexdigest() != digest:
        raise ValueError("native memory provenance compiler-prefix hash mismatch")
    memories = origins.get("memories")
    if not isinstance(memories, list):
        raise ValueError("missing memory provenance inventory")
    by_key = {}
    for memory in memories:
        if not isinstance(memory, dict) or not isinstance(memory.get("lowered"), dict):
            raise ValueError("invalid memory provenance record")
        lowered = memory["lowered"]
        key = (lowered.get("module"), lowered.get("memory_instance"))
        if key in by_key or any(not isinstance(x, str) or not x for x in key):
            raise ValueError("duplicate or invalid memory provenance identity")
        by_key[key] = memory
    matched = set()
    for record in records:
        key = (record["parent_module"], record["memory_instance"])
        memory = by_key.get(key)
        if memory is None or memory.get("origin") != "FASEDReadBuffer":
            raise ValueError("recognized packing lacks compiler origin: " + ".".join(key))
        lowered, source = memory["lowered"], memory.get("source", {})
        fields = memory.get("fields")
        if not isinstance(fields, list) or any(not isinstance(f, dict) or set(f) != {"name", "hi", "lo"}
                                              or not isinstance(f["name"], str) or type(f["hi"]) is not int or type(f["lo"]) is not int for f in fields):
            raise ValueError("invalid compiler field inventory")
        if sorted([f["name"], f["hi"], f["lo"]] for f in fields) != sorted(record["fields"]):
            raise ValueError("compiler field packing disagrees with emitted RTL")
        expected = {"module": record["parent_module"], "memory_instance": record["memory_instance"],
                    "memory_module": record["memory_module"], "storage": record["storage"],
                    "read_capture": record["capture_register"]}
        if not isinstance(source, dict) or not isinstance(source.get("memory"), str) or any(lowered.get(k) != v for k, v in expected.items()) or source.get("module") != record["parent_module"] or source.get("memory") + "_ext" != record["memory_instance"] or source.get("width") != record["width"] or source.get("depth") != record["depth"] or source.get("read_port") != "read" or source.get("write_port") != "write":
            raise ValueError("compiler memory geometry or lowered identity disagrees with emitted RTL")
        capture = memory.get("read_capture")
        if not isinstance(capture, dict) or type(capture.get("latency")) is not int or capture != {"latency": 1, "enable": "constant_one", "clock": record["capture_clock"],
                       "reset": "none", "disabled": "hold", "collision": "pre_edge_data"}:
            raise ValueError("compiler capture policy disagrees with recognized RTL")
        paths_record = memory.get("instance_paths")
        if not isinstance(paths_record, list) or not paths_record or any(not isinstance(p, str) or not p for p in paths_record) or len(set(paths_record)) != len(paths_record):
            raise ValueError("invalid compiler instance-path inventory")
        roots = {path.split(".", 1)[0] for path in paths_record}
        if len(roots) != 1 or sorted(paths_record) != _instance_paths(paths, next(iter(roots)), record["parent_module"]):
            raise ValueError("compiler instance paths disagree with emitted hierarchy")
        matched.add(key)
    if any(memory.get("origin") == "FASEDReadBuffer" and key not in matched for key, memory in by_key.items()):
        raise ValueError("compiler-marked memory has no recognized emitted structure")
    return {"binding": binding, "joined_records": len(matched)}


def stage_memory_layout_adapter(flow, origins_path, evidence_path="proof/memory_layout_supplement.json"):
    """Wrap only a staged scanner and require the compiler-origin join remotely."""
    scripts = Path(flow) / "scripts"
    shutil.copy2(__file__, scripts / "chia_memory_layout.py")
    shutil.copy2(origins_path, scripts / "chia_memory_origins.json")
    scanner = scripts / "svinfo.py"
    hook = '\n# CHIA: checked descriptive field packing; no state assumptions.\n'
    hook += 'from chia_memory_layout import recover_capture_layouts as _chia_recover, merge_layouts as _chia_merge, validate_origins as _chia_validate\n'
    hook += 'import json as _chia_json\nfrom pathlib import Path as _ChiaPath\n'
    hook += '_chia_original_merged_layouts = merged_layouts\n'
    hook += 'def merged_layouts(paths):\n'
    hook += '    original = _chia_original_merged_layouts(paths)\n'
    hook += '    supplement, records = _chia_recover(paths)\n'
    hook += '    provenance = _chia_json.loads((_ChiaPath(__file__).parent / "chia_memory_origins.json").read_text())\n'
    hook += '    binding = _chia_validate(paths, provenance, records)\n'
    hook += f'    evidence = _ChiaPath({str(evidence_path)!r})\n'
    hook += '    evidence.parent.mkdir(parents=True, exist_ok=True)\n'
    hook += '    evidence.write_text(_chia_json.dumps({"records": records, "supplement": supplement, "provenance": binding, "scope": "packing inventory only; helper proposals, no initial equality"}, indent=2) + "\\n")\n'
    hook += '    return _chia_merge(original, supplement)\n'
    with scanner.open("a") as stream:
        stream.write(hook)


HELPER_CEX_HOOK = r'''
rename sec_flow_save_cex chia_original_save_cex
proc sec_flow_save_cex {dir max} {
  chia_original_save_cex $dir $max
  file mkdir $dir/helper_cex
  set f [open $dir/helper_cex/inventory.tsv w]
  puts $f "status\tproperty\tfile\terror"
  if {[catch {set ids [check_sec -list -mapping -mapping_type helper -global -return id]} err]} {
    puts $f "inventory_error\t\t\t[string map {\n { } \t { }} $err]"
    close $f
    return
  }
  set helpers {}
  foreach id $ids {
    if {[catch {set properties [check_sec -mapping_info -id $id -list property]} err]} {
      puts $f "inventory_error\t$id\t\t[string map {\n { } \t { }} $err]"
      continue
    }
    foreach p $properties { lappend helpers $p }
  }
  if {![llength $helpers]} {
    puts $f "inventory_empty\t\t\tno helper properties returned"
  }
  set n 0
  foreach p [get_property_list -include {status cex}] {
    if {[lsearch -exact $helpers $p] < 0} continue
    if {$n >= 8} break
    set output [format "helper_%02d.vcd" $n]
    if {[catch {
      visualize -violation -property $p -window chia_helper_cex -batch -silent
      visualize -save -vcd $dir/helper_cex/$output -force -window chia_helper_cex
    } err]} {
      puts $f "export_error\t$p\t$output\t[string map {\n { } \t { }} $err]"
      sec_flow_log "could not save helper trace for $p: $err"
    } else {
      puts $f "exported\t$p\t$output\t"
    }
    incr n
  }
  close $f
}
'''


CUTPOINT_CEX_HOOK = r'''
# The original boundary exporter can spend the entire remaining wall budget
# materializing large traces. Export actual automatic cutpoints first, with a
# separate diagnostic budget. This recognizes the task/property
# spelling in the retained 2026.06p002 status report; it adds no proof commands.
rename sec_flow_save_cex chia_save_boundary_and_helper_cex
proc sec_flow_save_cex {dir max} {
  file mkdir $dir/auto_cutpoint_cex
  set f [open $dir/auto_cutpoint_cex/inventory.tsv w]
  puts $f "status\tproperty\tfile\terror"
  if {[catch {set properties [get_property_list -include {status cex}]} err]} {
    puts $f "inventory_error\t\t\t[string map {\n { } \t { }} $err]"
    close $f
    chia_save_boundary_and_helper_cex $dir $max
    return
  }
  set n 0
  foreach p $properties {
    if {![regexp {(^|::)AutoProveCutPointsAssertions:.*_CP_assert$} $p]} continue
    if {$n >= 8} {
      puts $f "deferred\t$p\t\tdiagnostic export budget exhausted"
      continue
    }
    set output [format "cutpoint_%02d.vcd" $n]
    puts $f "export_started\t$p\t$output\t"
    flush $f
    sec_flow_log "saving automatic cutpoint trace for $p to $output"
    if {[catch {
      visualize -violation -property $p -window chia_cutpoint_cex -batch -silent
      visualize -save -vcd $dir/auto_cutpoint_cex/$output -force -window chia_cutpoint_cex
    } err]} {
      puts $f "export_error\t$p\t$output\t[string map {\n { } \t { }} $err]"
      sec_flow_log "could not save automatic cutpoint trace for $p: $err"
    } else {
      puts $f "exported\t$p\t$output\t"
    }
    flush $f
    incr n
  }
  if {!$n} { puts $f "inventory_empty\t\t\tno failing automatic cutpoint assertions returned" }
  close $f
  chia_save_boundary_and_helper_cex $dir $max
}
'''


def stage_helper_cex_reporter(flow):
    """Export existing helper/cutpoint failures with separate diagnostic budgets."""
    with (Path(flow) / "tcl/sec_lib.tcl").open("a") as stream:
        stream.write("\n# CHIA: separately bounded diagnostic helper counterexamples.\n" + HELPER_CEX_HOOK)
        stream.write("\n# CHIA: retain actual automatic-cutpoint counterexamples too.\n" + CUTPOINT_CEX_HOOK)
