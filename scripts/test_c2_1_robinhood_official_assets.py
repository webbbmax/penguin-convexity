#!/usr/bin/env python3

import tempfile
import unittest
from pathlib import Path

from c2_1_db import initialize_database, open_pipeline_db
from c2_1_enrichment import collect_robinhood_official_assets


MATCHED = "0x1111111111111111111111111111111111111111"
UNMATCHED = "0x2222222222222222222222222222222222222222"
NOW = "2026-08-13T00:00:00Z"


class FakeClient:
    def request(self, source, url, **_kwargs):
        self.source = source
        self.url = url
        return (
            "success",
            {
                "assets": [
                    {
                        "id": "0x" + "ab" * 32,
                        "tokenSymbol": "TEST",
                        "tokenName": "Test Company Robinhood Token",
                        "deployments": [{"contractAddress": MATCHED, "chainId": 4663}],
                        "currentMultiplier": "1.000000000000000000",
                        "tradingCapabilities": {"fractionalTradability": "tradable"},
                        "status": "ASSET_STATUS_ACTIVE",
                    }
                ]
            },
            200,
            [{"attempt": 1, "state": "success"}],
        )


class RobinhoodOfficialAssetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "pipeline.db"
        initialize_database(self.database)
        self.connection = open_pipeline_db(self.database)
        for address in (MATCHED, UNMATCHED):
            self.connection.execute(
                """INSERT INTO candidates(
                  network_id,token_address,token_address_normalized,gate0_t0,effective_t0,t0_status,
                  t0_evidence_type,source_run_id,first_seen_at,continuity_status,relationship_class,
                  identity_status,created_at,updated_at
                ) VALUES('robinhood-mainnet',?,?,?,?,'verified_in_supported_scope','factory_event',
                  'run',?,'candidate_asset','D','market_matched',?,?)""",
                (address, address.lower(), NOW, NOW, NOW, NOW, NOW),
            )
        self.connection.commit()

    def tearDown(self):
        self.connection.close()
        self.temp.cleanup()

    def test_only_exact_official_chain_and_contract_match_is_promoted(self):
        result = collect_robinhood_official_assets(
            self.connection, client=FakeClient(), force_recheck=True
        )
        self.assertEqual(result["matchedCandidates"], 1)
        matched = self.connection.execute(
            "SELECT * FROM candidates WHERE token_address_normalized=?", (MATCHED,)
        ).fetchone()
        unmatched = self.connection.execute(
            "SELECT * FROM candidates WHERE token_address_normalized=?", (UNMATCHED,)
        ).fetchone()
        self.assertEqual((matched["relationship_class"], matched["identity_status"], matched["symbol"]), ("B", "verified", "TEST"))
        self.assertEqual(unmatched["relationship_class"], "D")
        evidence = self.connection.execute(
            "SELECT evidence_type,status,identity_status FROM product_evidence WHERE candidate_id=?",
            (matched["candidate_id"],),
        ).fetchone()
        self.assertEqual(tuple(evidence), ("deployed_product", "qualifying", "verified"))
        risk = self.connection.execute(
            "SELECT source_status,hard_trade_block,severe_anomaly FROM risk_observations WHERE candidate_id=?",
            (matched["candidate_id"],),
        ).fetchone()
        self.assertEqual(tuple(risk), ("success", 0, 0))


if __name__ == "__main__":
    unittest.main()
