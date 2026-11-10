"""Mutation checks for actual driver CSV/Print format and bounded expectations."""
import copy
import unittest

from check_instrumentation import check


class InstrumentationOutputTest(unittest.TestCase):
    def setUp(self):
        self.expected = {"schema_version": 1, "clock": {"name": "target", "multiplier": 1, "divisor": 2},
                         "sample_period_base": 8, "min_samples": 3,
                         "counters": [{"label": "local_cycle", "description": "local cycles", "type": "Accumulate",
                                       "event_width": 1, "accumulator_width": 64, "first": 4, "step": 4},
                                      {"label": "events", "description": 'bounded, "event"', "type": "Accumulate",
                                       "event_width": 1, "accumulator_width": 64, "first": (1 << 64) - 2, "step": 2}],
                         "prints": [{"cycle": 4, "payload": "signed=-1 hex=ff\n"},
                                    {"cycle": 12, "payload": "events=2\n"}]}
        self.csv = ('version,1\nClock Domain Name, target, Base Multiplier, 1, Base Divisor, 2\n'
                    'label,local_cycle,events\ndescription,local cycles,"bounded, ""event"""\n'
                    'type,Accumulate,Accumulate\nevent width,1,1\naccumulator width,64,64\n'
                    '8,4,18446744073709551614\n16,8,0\n24,12,2\n')
        self.prints = ('# Clock Domain: target, Relative Frequency: 1/2 of Base Clock\n'
                       'CYCLE:            4 signed=-1 hex=ff\nCYCLE:           12 events=2\n')

    def test_actual_driver_format_and_rollover(self):
        result = check(self.expected, self.csv, self.prints)
        self.assertEqual((result["samples"], result["prints"]), (3, 2))

    def test_counter_corruption_empty_reorder_clock_and_width_fail(self):
        for corrupted in (self.csv.replace('16,8,0', '16,8,1'),
                          '\n'.join(self.csv.splitlines()[:7]) + '\n',
                          self.csv.replace('16,8,0\n24,12,2', '24,12,2\n16,8,0'),
                          self.csv.replace('Base Divisor, 2', 'Base Divisor, 3'),
                          self.csv.replace('accumulator width,64,64', 'accumulator width,64,32'),
                          self.csv.replace('16,8,0', '16,8,-1')):
            with self.subTest(corrupted=corrupted), self.assertRaises(ValueError):
                check(self.expected, corrupted, self.prints)

    def test_print_format_payload_loss_order_and_cycle_fail(self):
        for corrupted in (self.prints.replace('signed=-1', 'signed=255'),
                          self.prints.replace('events=2\n', ''),
                          self.prints.replace('           12', '           13'),
                          self.prints + 'unexpected\n',
                          self.prints.splitlines(True)[0]):
            with self.subTest(corrupted=corrupted), self.assertRaises(ValueError):
                check(self.expected, self.csv, corrupted)

    def test_fractional_or_idle_contract_cannot_pass(self):
        expected = copy.deepcopy(self.expected)
        expected["sample_period_base"] = 3
        with self.assertRaises(ValueError):
            check(expected, self.csv, self.prints)
        expected = copy.deepcopy(self.expected)
        for counter in expected["counters"]:
            counter["step"] = 0
        with self.assertRaises(ValueError):
            check(expected, self.csv, self.prints)


if __name__ == "__main__":
    unittest.main()
