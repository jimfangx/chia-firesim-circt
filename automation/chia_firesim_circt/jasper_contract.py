"""Explicit AXI observation contract; no memory initialization or X waiver.

Qualification is by producer valid, including stalled transactions. Ready and
valid remain unconditional obligations. The accepted F2 boundary is deliberately
small: generic `_bits` naming alone never authorizes qualification.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
from jasper_memory_layout import MODULE, _clean, _ports

FIELDS = {
    'aw': {'addr', 'len', 'size', 'burst', 'lock', 'cache', 'prot', 'qos', 'region', 'id', 'user'},
    'ar': {'addr', 'len', 'size', 'burst', 'lock', 'cache', 'prot', 'qos', 'region', 'id', 'user'},
    'w': {'data', 'last', 'id', 'strb', 'user'},
    'b': {'resp', 'id', 'user'},
    'r': {'resp', 'data', 'last', 'id', 'user'},
}
PAYLOAD = re.compile(r'^(io_(?:master|pcis|pcim|slave_[0-9]+))_(aw|ar|w|b|r)_bits_([a-z]+)$')


def top_ports(rtl, top='F1Shim'):
    matches = [m for m in MODULE.finditer(_clean(Path(rtl).read_text())) if m[1] == top]
    if len(matches) != 1:
        raise ValueError('missing or duplicate contract top')
    return _ports(matches[0][2])


def boundary_contract(spec, imp, top='F1Shim'):
    sp, ip = top_ports(spec, top), top_ports(imp, top)
    if sp != ip:
        raise ValueError('protocol contract requires identical named, typed boundary ports')
    qualified, strict = [], []
    for name, (direction, width) in sorted(sp.items()):
        if direction != 'output':
            continue
        match = PAYLOAD.fullmatch(name)
        if match:
            interface, channel, field = match.groups()
            prefix = interface + '_' + channel
            if field not in FIELDS[channel]:
                raise ValueError('unclassified AXI payload field: ' + name)
            valid, ready = prefix + '_valid', prefix + '_ready'
            if sp.get(valid) != ('output', 1) or sp.get(ready) != ('input', 1):
                raise ValueError('missing or malformed producer handshake: ' + prefix)
            qualified.append({'spec': name, 'imp': name, 'condition': valid, 'width': width,
                              'protocol': 'AXI4', 'observable': 'producer valid, including valid && !ready'})
        else:
            # Unknown outputs remain strict; naming similarity does not waive them.
            strict.append(name)
    return {'schema': 1, 'top': top, 'qualified_outputs': qualified, 'strict_outputs': strict,
            'zero_out_dram': False,
            'initialized_regions': {'status': 'not_inferred_from_RTL', 'regions': [],
                                    'requirement': 'Bind actual image/load writes for a loaded-memory abstraction.'},
            'initial_memory_relation': {
                'status': 'no_initial_memory_mapping_added',
                'relation': 'For a classified unwritten logical byte a, both representations denote the same '
                            'abstract nondeterministic M0[a]. Masked writes update enabled bytes only; '
                            'loaded/explicitly initialized bytes have their specified values. This relation '
                            'must be established by checked mapping/refinement before it can be assumed.',
                'scope': 'No memory data is masked while valid; no independent arbitrary live samples, '
                         'zero initialization, blanket reset-X or helper-equality assumptions.'},
            'x_reset_policy': 'Every Jasper X/undriven/reset abstraction signoff obligation remains required; '
                              'unclassified sources and incomplete signoff block acceptance.'}


def stage_boundary_contract(spec, imp, flow, directory, top='F1Shim', initial_mappings=()):
    contract = boundary_contract(spec, imp, top)
    mappings = [list(pair) for pair in initial_mappings]
    if any(len(pair) != 2 or any(not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*(?:\[[0-9]+(?::[0-9]+)?\])*', name) for name in pair) for pair in mappings):
        raise ValueError('unsupported explicit initial mapping')
    contract['explicit_initial_mappings'] = mappings
    if mappings:
        contract['initial_memory_relation']['status'] = 'explicit_checked_relation_required'
    directory = Path(directory)
    conditions = directory / 'protocol-output-conditions.txt'
    conditions.write_text(''.join(f"{top} ^{q['spec']}$ {q['condition']}\n"
                                  for q in contract['qualified_outputs']))
    path = directory / 'protocol-memory-contract.json'
    path.write_text(json.dumps(contract, indent=2) + '\n')
    # Patch only the private staged flow. Unsupported flow revisions fail closed.
    library = Path(flow) / 'tcl/sec_lib.tcl'
    text = library.read_text()
    old = '''    sec_flow_log "could not qualify $spec by $cond: [string map {\\n { }} $err]; restoring plain mapping"
    catch {check_sec -map -spec [list $spec] -imp [list $imp] -global -return none}
    return 0'''
    new = '''    error "CHIA contract qualification failed for $spec by $cond: $err"'''
    if text.count(old) != 1:
        raise ValueError('unsupported staged qualification procedure; refusing silent fallback')
    mapping_report = r'''
rename sec_flow_signoff_summary sec_flow_contract_original_signoff_summary
proc sec_flow_signoff_summary {dir} {
  sec_flow_contract_original_signoff_summary $dir
  set f [open $dir/initial_mappings.txt w]
  foreach m [check_sec -list -mapping -mapping_type init_all -global] { puts $f $m }
  close $f
}
'''
    library.write_text(text.replace(old, new) + mapping_report)
    driver = Path(flow) / 'run_sec.py'
    code = driver.read_text()
    anchor = '    w("check_sec -setup")'
    config_anchor = '    cfg = dict(vars(a))'
    if code.count(anchor) != 1 or code.count(config_anchor) != 1:
        raise ValueError('unsupported staged setup/config driver; refusing automatic reset-X assumptions')
    replacement = '    w("check_sec -setup -exclude_undriven_mapping")\n    w("check_sec -auto_map_reset_x_values off")'
    for sp, ip in mappings:
        replacement += '\n    w(' + repr('check_sec -map -spec {' + sp + '} -imp {' + top + '_imp.' + ip + '} -init -global -return none') + ')'
    code = code.replace(anchor, replacement)
    code = code.replace(config_anchor, config_anchor +
        "\n    cfg.update(chia_auto_map_reset_x_values='off', chia_exclude_undriven_mapping=True, chia_explicit_initial_mappings=" + repr(mappings) + ")")
    driver.write_text(code)
    return contract, conditions, path


def validate_configuration(config, contract):
    if config.get('chia_auto_map_reset_x_values') != 'off':
        raise ValueError('automatic reset-X initialization assumptions are forbidden')
    if config.get('chia_exclude_undriven_mapping') is not True:
        raise ValueError('unclassified undriven mappings are forbidden')
    if config.get('chia_explicit_initial_mappings') != contract.get('explicit_initial_mappings', []):
        raise ValueError('explicit initial mappings differ from established relation inventory')
    expected = sorted((q['spec'], q['imp'], q['condition']) for q in contract['qualified_outputs'])
    actual = config.get('valid_qualified')
    if not isinstance(actual, list) or any(not isinstance(q, list) or len(q) != 3 for q in actual):
        raise ValueError('missing or malformed actual qualification inventory')
    if sorted(tuple(q) for q in actual) != expected:
        raise ValueError('actual qualifications differ from explicit protocol contract')
    if not config.get('no_valid_qualify') or not config.get('no_init_map') or not config.get('no_assume_spec_asserts'):
        raise ValueError('automatic qualification, memory initialization or spec-assert assumptions are forbidden')
    if config.get('chisel_asserts_as_assumptions'):
        raise ValueError('embedded assertion assumptions are forbidden')
    return {'qualified_count': len(expected), 'strict_output_count': len(contract['strict_outputs']),
            'qualification': 'explicit AXI producer-valid only; controls and all live payload bits strict',
            'initial_mapping_count': len(contract.get('explicit_initial_mappings', [])),
            'initial_mapping': 'Only the enumerated checked shared abstract-memory relation is assumed.'
                               if contract.get('explicit_initial_mappings') else 'none; no new initial-memory equality assumed'}


class AbstractMemory:
    """Finite executable specification for contract controls, not a formal proof.

    M0 supplies one shared abstract byte map. Both implementations must realize
    this relation; accepting arbitrary unequal live reads is deliberately absent.
    """
    def __init__(self, initial, loaded=None):
        self.data = dict(initial)
        self.written = set()
        self.loaded = dict(loaded or {})
        self.data.update(self.loaded)

    def write(self, address, data, mask):
        for lane, byte in enumerate(data):
            if mask & (1 << lane):
                self.data[address + lane] = byte
                self.written.add(address + lane)

    def read(self, address, length):
        return tuple(self.data[address + lane] for lane in range(length))


def compare_observation(spec, imp):
    """Strict controls and valid payload even when the consumer stalls."""
    if spec['valid'] != imp['valid'] or spec['ready'] != imp['ready']:
        return False
    return not spec['valid'] or spec['payload'] == imp['payload']


def audit_initial_relation(report, relation):
    """Audit an explicitly established reset-X relation, never name-only maps.

    Caller supplies a checked, hash-bound construction/refinement certificate.
    Production currently supplies none: its unclassified reset-X stays blocking.
    Every raw RESET map must be listed, every relation assertion must be proven,
    and no unrelated X/undriven/out-of-range source can be hidden by this path.
    """
    report = Path(report)
    if not isinstance(relation, dict) or relation.get('established_by') != 'checked_shared_symbolic_seed_construction':
        raise ValueError('missing checked initial-memory relation')
    files = relation.get('rtl_files', [])
    if len(files) != 2:
        raise ValueError('initial relation must bind both actual RTL files')
    import hashlib
    for item in files:
        if hashlib.sha256(Path(item['path']).read_bytes()).hexdigest() != item['sha256']:
            raise ValueError('initial relation RTL hash mismatch')
    expected = relation.get('reset_mappings')
    actual = [line.strip() for line in (report / 'initial_mappings.txt').read_text().splitlines() if line.strip()]
    if not expected or sorted(actual) != sorted(expected) or len(set(actual)) != len(actual):
        raise ValueError('unclassified automatic reset/X mapping')
    # Zero unmatched X sources on BOTH sides. Missing reports/count schemas reject.
    inventory = (report / 'interface_unmapped.txt').read_text().splitlines()
    counts = {}
    for line in inventory:
        match = re.fullmatch(r'## (spec|imp) ([a-z_]+): ([0-9]+)', line)
        if match:
            key = (match[1], match[2])
            if key in counts: raise ValueError('duplicate X inventory category')
            counts[key] = int(match[3])
        elif line.strip():
            raise ValueError('unclassified unmatched source in initial relation')
    kinds = ('undriven', 'internal_undriven', 'x_assignment', 'x_index_out_of_range',
             'x_divide_by_zero', 'x_misc_undriven', 'reset_x')
    if any(counts.values()) or any(counts.get((side, kind)) != 0 for side in ('spec', 'imp') for kind in kinds):
        raise ValueError('unclassified X/undriven source')
    rows = [line.split('\t') for line in (report / 'status.txt').read_text().splitlines()]
    proven = {row[2] for row in rows if len(row) == 3 and row[0] == 'proven'}
    properties = relation.get('relation_properties')
    if not properties or not set(properties) <= proven:
        raise ValueError('initial-memory relation preservation is unproven')
    if not relation.get('loaded_regions') or not relation.get('shared_abstract_regions'):
        raise ValueError('initial-memory region classification missing')
    return {'status': 'classified_shared_initial_memory', 'relation': relation,
            'raw_X_signoff': 'Incomplete', 'contract_status': 'complete_for_exhaustively_classified_relation',
            'qualification': 'Only the exhaustively listed automatic reset-X mappings are related; '
                             'all unmatched sources reject and every preservation property is proven.'}
