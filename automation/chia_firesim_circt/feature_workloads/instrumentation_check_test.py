#!/usr/bin/env python3
"""Checker positive/negative controls only, not execution acceptance."""
import copy
import csv
import io
import unittest
from instrumentation_check import check, MARKER, PERIOD


class MonitorOutputChecks(unittest.TestCase):
    def setUp(self):
        self.expected = {'schema_version': 1, 'domains': [
            {'id': 0, 'name': 'domain0', 'multiplier': 1, 'divisor': 1, 'reset_cycles': 50}],
            'sample_period_base': PERIOD + 7, 'min_samples': 3, 'min_prints': 2}
        labels = ['local_cycle', 'top_feature_domain0_active', 'top_feature_domain0_cycles', 'top_feature_domain0_wrap']
        rows = [['version', '1'], ['Clock Domain Name', ' domain0', ' Base Multiplier', ' 1', ' Base Divisor', ' 1'],
                ['label', *labels], ['description', 'Clock cycles elapsed in the local domain.', 'Cycles outside local reset.',
                'Target domain cycles since local reset release.', 'Low four bits of target domain cycles.'],
                ['type', 'Accumulate', 'Accumulate', 'Identity', 'Identity'], ['event width', '1', '1', '64', '4'],
                ['accumulator width', '64', '64', '64', '64']]
        for i in range(1, 4):
            cycle = i * (PERIOD + 7)
            rows.append([str(v) for v in (cycle, cycle, cycle-50, cycle-50, (cycle-50) % 16)])
        stream = io.StringIO(); csv.writer(stream).writerows(rows); self.csv = stream.getvalue()
        self.prints = '# Clock Domain: domain0, Relative Frequency: 1/1 of Base Clock\n' + ''.join(
            'CYCLE:%13d [CIRCT_FEATURE] domain=0 cycles=%d wrap=0\n' % (c + 50, c) for c in (0, PERIOD))

    def test_valid_written_observations(self):
        result = check(self.expected, [self.csv], [self.prints], MARKER)
        self.assertEqual(result['domains'], [{'id': 0, 'name': 'domain0', 'samples': 3, 'prints': 2}])

    def test_corrupt_observations_fail(self):
        cases = [(self.csv, self.prints.replace('wrap=0', 'wrap=1'), MARKER),
                 (self.csv, self.prints.replace('cycles=1048576', 'cycles=1048577'), MARKER),
                 (self.csv, self.prints.replace('domain=0', 'domain=1'), MARKER),
                 (self.csv.replace('1048533', '1048534'), self.prints, MARKER),
                 (self.csv.replace('Identity,Identity', 'Accumulate,Identity'), self.prints, MARKER),
                 (self.csv, self.prints, '')]
        for csv_text, print_text, uart in cases:
            with self.subTest(csv=csv_text[-100:], prints=print_text[-100:], uart=uart):
                with self.assertRaises(ValueError):
                    check(self.expected, [csv_text], [print_text], uart)

    def test_wrong_reset_or_ratio_fails(self):
        for key, value in [('reset_cycles', 49), ('divisor', 3)]:
            altered = copy.deepcopy(self.expected); altered['domains'][0][key] = value
            with self.assertRaises(ValueError):
                check(altered, [self.csv], [self.prints], MARKER)

    def test_unchanged_driver_unquoted_joined_clock_aliases(self):
        name='uart_clock,clock_1000.0MHz,harnessbinder_clock,reference'
        expected=copy.deepcopy(self.expected); expected['domains'][0]['name']=name
        result=check(expected,[self.csv.replace(' domain0, Base Multiplier',' '+name+', Base Multiplier')],
                     [self.prints.replace('Clock Domain: domain0,','Clock Domain: '+name+',')],MARKER)
        self.assertEqual(result['domains'][0]['name'],name)


if __name__ == '__main__':
    unittest.main()
