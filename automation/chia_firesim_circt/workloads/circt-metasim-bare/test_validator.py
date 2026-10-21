"""Ensure the log checker cannot accept incomplete or contradictory results."""
import unittest
from check_uart import UART_DATA, aggregate, validate


def transcript(mask=3):
    tests = ([{"name": "integer", "checks": 2, "signature": "0x0000000000000001"},
              {"name": "memory", "checks": 3, "signature": "0x0000000000000002"}]
             if mask == 3 else [{"name": "uart", "checks": 664, "signature": "0x0000000000000001"}])
    lines = [f"CIRCT_FAME: BEGIN version=1 mask={mask}\n"]
    for test in tests:
        lines.append(f"FAME_TEST: {test['name']} BEGIN\n")
        if test["name"] == "uart":
            lines.extend(UART_DATA + ["FAME_UART: END\n"])
        lines.append(f"FAME_TEST: {test['name']} PASS checks={test['checks']} signature={test['signature']}\n")
    lines.append(f"CIRCT_FAME: PASS tests={len(tests)} checks={sum(t['checks'] for t in tests)} "
                 f"failures=0 signature={aggregate(tests)}\n")
    return "".join(lines)


class ValidatorTests(unittest.TestCase):
    def reject(self, text):
        with self.assertRaises(ValueError):
            validate(text)

    def test_complete_and_crlf(self):
        self.assertEqual(validate(transcript())["checks"], 5)
        self.assertEqual(validate(transcript()), validate(transcript().replace("\n", "\r\n")))

    def test_truncated(self):
        self.reject(transcript().rsplit("CIRCT_FAME: PASS", 1)[0])

    def test_duplicate_suite(self):
        self.reject(transcript() * 2)

    def test_failure_before_pass(self):
        self.reject("FAME_FAIL: broken\n" + transcript())
        self.reject("CIRCT_FAME: FATAL trap\n" + transcript())

    def test_duplicate_test(self):
        self.reject(transcript().replace("FAME_TEST: memory BEGIN\n", "FAME_TEST: integer BEGIN\nFAME_TEST: memory BEGIN\n"))

    def test_out_of_order(self):
        self.reject(transcript().replace("FAME_TEST: integer BEGIN", "FAME_TEST: memory BEGIN", 1))

    def test_bad_counts(self):
        self.reject(transcript().replace("tests=2 checks=5", "tests=2 checks=6"))

    def test_bad_signature(self):
        text = transcript()
        self.reject(text.replace(aggregate(validate(text)["tests"]), "0x0000000000000000"))

    def test_reference_difference(self):
        text = transcript()
        reference = validate(text)
        reference["signature"] = "0x0000000000000000"
        with self.assertRaises(ValueError):
            validate(text, reference)

    def test_uart_payload(self):
        self.assertEqual(validate(transcript(2048))["checks"], 664)
        self.reject(transcript(2048).replace(UART_DATA[3], ""))
        self.reject(transcript(2048).replace(UART_DATA[3], UART_DATA[3] * 2))
        self.reject(transcript(2048).replace(UART_DATA[0] + UART_DATA[1], UART_DATA[1] + UART_DATA[0]))


if __name__ == "__main__":
    unittest.main()
