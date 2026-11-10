"""Focused positive/negative RAM/protocol controls, no AWS or full proof jobs."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from jasper_contract import AbstractMemory, compare_observation, boundary_contract, validate_configuration, audit_initial_relation
from run_jasper import choose_strategy, signoff
import test_downstream_adapters as fixtures


class ContractControls(unittest.TestCase):
    def test_invalid_unwritten_payload_is_unobservable(self):
        self.assertTrue(compare_observation({'valid': 0, 'ready': 1, 'payload': 0x1234},
                                            {'valid': 0, 'ready': 1, 'payload': 0xabcd}))

    def test_shared_nondeterministic_initial_memory_is_not_zero(self):
        for seed in range(256):
            initial = {i: (seed + 17 * i) % 256 for i in range(4)}
            a, b = AbstractMemory(initial), AbstractMemory(initial)
            self.assertEqual(a.read(0, 4), b.read(0, 4))
        self.assertNotEqual(AbstractMemory({0: 7}).read(0, 1), (0,))

    def test_live_unwritten_reads_cannot_be_independent(self):
        a, b = AbstractMemory({0: 5}), AbstractMemory({0: 8})
        self.assertFalse(compare_observation({'valid': 1, 'ready': 1, 'payload': a.read(0, 1)},
                                             {'valid': 1, 'ready': 1, 'payload': b.read(0, 1)}))

    def test_written_initialized_and_wrong_byte_masks_fail(self):
        for corruption in ('written', 'initialized', 'mask'):
            a, b = AbstractMemory({0: 7, 1: 11}, {0: 19}), AbstractMemory({0: 7, 1: 11}, {0: 19})
            a.write(0, (29, 31), 2)
            b.write(0, (29, 31), 3 if corruption == 'mask' else 2)
            if corruption == 'written': b.data[1] ^= 1
            if corruption == 'initialized': b.data[0] ^= 1
            self.assertNotEqual(a.read(0, 2), b.read(0, 2), corruption)

    def test_valid_ready_lost_reordered_and_stalled_corruption_fail(self):
        reference = {'valid': 1, 'ready': 0, 'payload': (17, 3)}
        for field in ('valid', 'ready', 'payload'):
            bad = dict(reference)
            bad[field] = {'valid': 0, 'ready': 1, 'payload': (3, 17)}[field]
            self.assertFalse(compare_observation(reference, bad), field)
        good = [{'valid': 1, 'ready': 1, 'payload': x} for x in (17, 23)]
        self.assertFalse(all(map(compare_observation, good, list(reversed(good)))))
        lost = copy.deepcopy(good); lost[0]['valid'] = 0
        self.assertFalse(all(map(compare_observation, good, lost)))

    def fixture(self, root):
        rtl = '''module F1Shim(input clock, input reset,
          input io_master_r_ready, output io_master_r_valid,
          output [7:0] io_master_r_bits_data, output mystery_bits_data);
          endmodule\n'''
        a, b = root / 'spec.sv', root / 'imp.sv'
        a.write_text(rtl); b.write_text(rtl)
        return a, b

    def test_explicit_allowlist_and_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, b = self.fixture(Path(tmp))
            contract = boundary_contract(a, b)
            self.assertEqual(contract['strict_outputs'], ['io_master_r_valid', 'mystery_bits_data'])
            self.assertEqual(len(contract['qualified_outputs']), 1)
            config = {'valid_qualified': [['io_master_r_bits_data', 'io_master_r_bits_data', 'io_master_r_valid']],
                      'no_valid_qualify': True, 'no_init_map': True, 'no_assume_spec_asserts': True,
                      'chia_auto_map_reset_x_values': 'off', 'chia_exclude_undriven_mapping': True, 'chia_explicit_initial_mappings': []}
            validate_configuration(config, contract)
            for mutation in ({'valid_qualified': []}, {'no_init_map': False},
                             {'valid_qualified': [['io_master_r_valid', 'io_master_r_valid', 'io_master_r_ready']]},
                             {'no_valid_qualify': False}, {'chisel_asserts_as_assumptions': True},
                             {'chia_auto_map_reset_x_values': 'on'}, {'chia_explicit_initial_mappings': [['a', 'b']]}):
                bad = dict(config); bad.update(mutation)
                with self.assertRaises(ValueError): validate_configuration(bad, contract)

    def test_unknown_axi_payload_and_bad_handshake_reject(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, b = self.fixture(Path(tmp))
            original = a.read_text()
            for bad in (original.replace('bits_data', 'bits_secret', 1),
                        original.replace('input io_master_r_ready', 'output io_master_r_ready'),
                        original.replace('output io_master_r_valid', 'output [1:0] io_master_r_valid')):
                a.write_text(bad); b.write_text(bad)
                with self.assertRaises(ValueError): boundary_contract(a, b)

    def test_strategy_respects_actual_job_requirement(self):
        for jobs in (1, 2, 3): self.assertEqual(choose_strategy('sec', jobs), 'basic')
        self.assertEqual(choose_strategy('sec', 4), 'proof')
        self.assertEqual(choose_strategy('lec', 1), 'state_matching')
        for jobs in (0, -1):
            with self.assertRaises(ValueError): choose_strategy('sec', jobs)

    def test_output_preconditions_require_actual_covered_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures.JasperSignoffTest().fixture(root)
            (root / 'reports/targets.txt').write_text('output_a\noutput_b\n<embedded>::payload_precondition\n')
            csv = root / 'reports/properties.csv'
            good = 'Task,Name,Type,Result\n<embedded>,payload_precondition,cover(Sec),covered\n'
            csv.write_text(good)
            self.assertEqual(signoff(root, 'sec')['covered_preconditions'], ['<embedded>::payload_precondition'])
            for bad in (good.replace('covered', 'unreachable'), good.replace('cover(Sec)', 'assert(Sec)'),
                        good + good.splitlines()[1] + '\n'):
                csv.write_text(bad)
                with self.assertRaises(ValueError): signoff(root, 'sec')

    def test_initial_relation_rejects_unclassified_sources_or_unproven_preservation(self):
        from downstream_common import record
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a, b = self.fixture(root)
            (root / 'initial_mappings.txt').write_text('memory[0] -> F1Shim_imp.memory[0] RESET\n')
            kinds = ('undriven', 'internal_undriven', 'x_assignment', 'x_index_out_of_range',
                     'x_divide_by_zero', 'x_misc_undriven', 'reset_x')
            clean = ''.join(f'## {side} {kind}: 0\n' for side in ('spec', 'imp') for kind in kinds)
            (root / 'interface_unmapped.txt').write_text(clean)
            (root / 'status.txt').write_text('proven\tinfinite\tmemory_relation\n')
            relation = {'established_by': 'checked_shared_symbolic_seed_construction',
                        'rtl_files': [record(a), record(b)],
                        'reset_mappings': ['memory[0] -> F1Shim_imp.memory[0] RESET'],
                        'relation_properties': ['memory_relation'], 'loaded_regions': [{'address': 1}],
                        'shared_abstract_regions': [{'address': 0}]}
            audit_initial_relation(root, relation)
            for bad in ({'reset_mappings': []}, {'relation_properties': ['unproven']}, {'loaded_regions': []}):
                changed = dict(relation); changed.update(bad)
                with self.assertRaises(ValueError): audit_initial_relation(root, changed)
            (root / 'interface_unmapped.txt').write_text(clean.replace('spec x_assignment: 0', 'spec x_assignment: 1'))
            with self.assertRaises(ValueError): audit_initial_relation(root, relation)
            (root / 'interface_unmapped.txt').write_text(clean)
            b.write_text('corrupted source')
            with self.assertRaises(ValueError): audit_initial_relation(root, relation)


if __name__ == '__main__': unittest.main()
