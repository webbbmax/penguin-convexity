#!/usr/bin/env python3

import json
import sqlite3
import unittest

from c2_1_observation_state import confirmed_trade_block, latest_effective_market_row, latest_effective_market_rows


class EffectiveObservationTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.execute(
            """CREATE TABLE market_observations(
              observation_id TEXT PRIMARY KEY,candidate_id INTEGER,observed_at TEXT,
              liquidity_usd REAL,standard_sell_notional_usd REAL,
              standard_sell_quote_state TEXT,standard_sell_quote_loss_pct REAL,payload_json TEXT
            )"""
        )

    def tearDown(self):
        self.connection.close()

    def test_blank_market_refresh_does_not_erase_last_explicit_quote_attempt(self):
        self.connection.execute(
            "INSERT INTO market_observations VALUES('quoted',1,'2026-08-13T00:00:00Z',1000,100,'success',2.5,?)",
            (json.dumps({"quoteProvider": "Jupiter", "quoteAttempts": [{"state": "success"}], "quoteBoundary": "read_only"}),),
        )
        self.connection.execute(
            "INSERT INTO market_observations VALUES('refresh',1,'2026-08-13T01:00:00Z',1500,100,'no_data',NULL,'{}')"
        )
        row = latest_effective_market_row(self.connection, 1)
        self.assertEqual(row["observation_id"], "refresh")
        self.assertEqual(row["liquidity_usd"], 1500)
        self.assertEqual(row["standard_sell_quote_state"], "success")
        self.assertEqual(row["standard_sell_quote_loss_pct"], 2.5)
        self.assertEqual(row["effective_quote_observation_id"], "quoted")

    def test_new_explicit_no_data_quote_supersedes_an_old_success(self):
        self.connection.execute(
            "INSERT INTO market_observations VALUES('success',1,'2026-08-13T00:00:00Z',1000,100,'success',2.5,?)",
            (json.dumps({"quoteAttempts": [{"state": "success"}], "quoteBoundary": "read_only"}),),
        )
        self.connection.execute(
            "INSERT INTO market_observations VALUES('attempt',1,'2026-08-13T01:00:00Z',1500,100,'no_data',NULL,?)",
            (json.dumps({"quoteAttempts": [{"state": "no_data"}], "quoteBoundary": "read_only"}),),
        )
        row = latest_effective_market_row(self.connection, 1)
        self.assertEqual(row["standard_sell_quote_state"], "no_data")
        self.assertEqual(row["effective_quote_observation_id"], "attempt")

    def test_bulk_resolution_keeps_new_market_and_old_quote(self):
        self.connection.execute(
            "INSERT INTO market_observations VALUES('quoted',1,'2026-08-13T00:00:00Z',1000,100,'success',1.0,?)",
            (json.dumps({"quoteProvider": "Jupiter", "quoteBoundary": "read_only"}),),
        )
        self.connection.execute(
            "INSERT INTO market_observations VALUES('refresh',1,'2026-08-13T01:00:00Z',2000,100,'no_data',NULL,'{}')"
        )
        latest, previous = latest_effective_market_rows(self.connection.execute("SELECT * FROM market_observations"))
        self.assertEqual(latest[1]["liquidity_usd"], 2000)
        self.assertEqual(latest[1]["standard_sell_quote_state"], "success")
        self.assertEqual(previous[1]["standard_sell_quote_state"], "success")

    def test_cancelled_percentage_reason_is_not_a_hard_trade_block(self):
        self.assertFalse(confirmed_trade_block({
            "source_status": "success",
            "hard_trade_block": 1,
            "reason_codes_json": '["confirmed_sell_tax_ge_20pct"]',
        }))
        self.assertTrue(confirmed_trade_block({
            "source_status": "success",
            "hard_trade_block": 1,
            "reason_codes_json": '["confirmed_cannot_sell_all"]',
        }))


if __name__ == "__main__":
    unittest.main()
