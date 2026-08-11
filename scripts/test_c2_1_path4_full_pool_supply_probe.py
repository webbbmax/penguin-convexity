import unittest

from c2_1_path4_full_pool_supply_probe import (
    event_raw_amount,
    historical_uint,
    positive_price,
    supply_category,
    trailing_contiguous,
    weighted_median,
)


class Path4FullPoolSupplyProbeTest(unittest.TestCase):
    def test_historical_uint_accepts_zero_decimals(self):
        class Rpc:
            @staticmethod
            def call(url, method, params):
                return "success", {"result": "0x0"}

        self.assertEqual(historical_uint(Rpc(), "url", "token", 1, "selector"), ("success", 0))

    def test_weighted_median_uses_pool_volume(self):
        self.assertEqual(weighted_median([(1, 10), (2, 80), (100, 10)]), 2)

    def test_weighted_median_falls_back_when_all_weights_zero(self):
        self.assertEqual(weighted_median([(1, 0), (3, 0)]), 2)

    def test_trailing_contiguous_stops_at_latest_gap(self):
        self.assertEqual(trailing_contiguous({0: {}, 3600: {}, 10800: {}, 14400: {}}, 3600), [10800, 14400])

    def test_positive_price_rejects_missing_and_nonpositive(self):
        self.assertIsNone(positive_price(None))
        self.assertIsNone(positive_price(0))
        self.assertIsNone(positive_price(-1))
        self.assertEqual(positive_price("1.25"), 1.25)

    def test_event_raw_amount_only_sums_selected_mint(self):
        event = {
            "tokenTransfers": [
                {"mint": "mint-a", "tokenAmount": 1.25},
                {"mint": "mint-a", "tokenAmount": "0.75"},
                {"mint": "mint-b", "tokenAmount": 99},
            ]
        }
        self.assertEqual(event_raw_amount(event, "mint-a", 6), 2_000_000)

    def test_supply_stability_categories_are_diagnostic(self):
        self.assertEqual(supply_category(0), "exact_stable")
        self.assertEqual(supply_category(0.1), "near_stable_le_0_1pct")
        self.assertEqual(supply_category(-1), "minor_change_le_1pct")
        self.assertEqual(supply_category(1.01), "material_change_gt_1pct")


if __name__ == "__main__":
    unittest.main()
