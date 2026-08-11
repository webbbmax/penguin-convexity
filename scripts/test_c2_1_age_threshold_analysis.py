import unittest

from c2_1_age_threshold_analysis import summarize_pair


PAIR = {
    "pairAddress": "0xpool",
    "dexId": "test-dex",
    "baseToken": {"address": "0xabc"},
    "quoteToken": {"address": "0xdef"},
    "liquidity": {"usd": 12000},
    "fdv": 500000,
    "marketCap": 400000,
    "volume": {"h24": 1000},
    "txns": {"h24": {"buys": 4, "sells": 3}},
    "priceChange": {"h24": 12.5},
    "info": {"websites": [{"url": "https://example.com"}], "socials": []},
}


class PairOrientationTest(unittest.TestCase):
    def test_base_token_keeps_token_specific_metrics(self):
        summary = summarize_pair(PAIR, "0xabc", "EVM")
        self.assertEqual(summary["tokenSide"], "base")
        self.assertEqual(summary["marketCapUsd"], 400000)
        self.assertEqual(summary["priceChangeH24Pct"], 12.5)
        self.assertEqual(summary["websiteCount"], 1)

    def test_quote_token_does_not_borrow_base_token_metrics(self):
        summary = summarize_pair(PAIR, "0xdef", "EVM")
        self.assertEqual(summary["tokenSide"], "quote")
        self.assertIsNone(summary["marketCapUsd"])
        self.assertIsNone(summary["fdvUsd"])
        self.assertIsNone(summary["priceChangeH24Pct"])
        self.assertIsNone(summary["websiteCount"])
        self.assertEqual(summary["liquidityUsd"], 12000)
        self.assertEqual(summary["volumeH24Usd"], 1000)


if __name__ == "__main__":
    unittest.main()
