import unittest
from decimal import Decimal

from c2_1_strong_path_input_probe import (
    normalized_domain,
    quote_input_amount,
    top_percent,
)


class StrongPathProbeTest(unittest.TestCase):
    def test_standard_quote_amount_uses_price_and_token_decimals(self):
        self.assertEqual(quote_input_amount("2", 6), "50000000")
        self.assertEqual(quote_input_amount(Decimal("0.25"), 2), "40000")

    def test_invalid_quote_input_does_not_become_zero(self):
        self.assertIsNone(quote_input_amount(None, 18))
        self.assertIsNone(quote_input_amount(0, 18))

    def test_domains_are_normalized_for_exact_matching(self):
        self.assertEqual(normalized_domain("https://www.Example.com/path"), "example.com")

    def test_missing_holder_percent_is_not_zero(self):
        self.assertIsNone(top_percent([{"percent": None}]))
        self.assertAlmostEqual(top_percent([{"percent": "0.2"}, {"percent": "0.3"}]), 50.0)


if __name__ == "__main__":
    unittest.main()
