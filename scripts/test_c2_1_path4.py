#!/usr/bin/env python3

import math
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import c2_1_path4
from c2_1_db import initialize_database, open_pipeline_db


class FakeClient:
    def __init__(self, network):
        self.network = network
        self.calls = 0

    def request(self, source, url, **_kwargs):
        self.calls += 1
        if "/tokens/" in url and "/pools?" in url:
            return "success", {"data": [{"attributes": {"address": "0xpool"}, "relationships": {"base_token": {"data": {"id": "eth_0xtoken"}}, "quote_token": {"data": {"id": "eth_0xquote"}}}}]}, 200, []
        now = int(datetime.now(timezone.utc).timestamp())
        day = 86400
        last = now // day * day - day
        candles = [[last - index * day, 1, 1, 1, 1 + index / 100, 100 + index] for index in range(14)]
        return "success", {"data": {"attributes": {"ohlcv_list": candles}}}, 200, []


def insert_candidate(connection):
    now = datetime.now(timezone.utc)
    t0 = (now - timedelta(days=20)).isoformat().replace("+00:00", "Z")
    cursor = connection.execute(
        """INSERT INTO candidates(network_id,token_address,token_address_normalized,gate0_t0,effective_t0,t0_status,t0_evidence_type,
        source_run_id,first_seen_at,continuity_status,relationship_class,identity_status,local_stage,created_at,updated_at)
        VALUES('ethereum-mainnet','0xtoken','0xtoken',?,?,'verified_in_supported_scope','test','test',?,'candidate_asset','C','verified','test',?,?)""",
        (t0, t0, t0, t0, t0),
    )
    candidate_id = cursor.lastrowid
    connection.execute(
        """INSERT INTO evaluations(evaluation_id,candidate_id,evaluation_window_id,evaluated_at,rule_version,rule_config_hash,
        cohort_snapshot_id,cohort_scope,cohort_sample_size,age_days,age_band,hard_gate_status,hard_gate_json,display_state,
        display_reason,paths_json,factor_directions_json,confidence_json,threshold_context_json,market_snapshot_json,
        source_impact_json,sort_score,sort_reason,is_current)
        VALUES('eval',?,'window',?,'c2.1-rules-v1','hash','fallback','fallback',0,20,'age_14_30','pass','{}',
        'continuous_observation','test','[]','[]','{}','{}','{}','{}',0,'test',1)""",
        (candidate_id, t0),
    )
    connection.commit()
    return candidate_id


def main():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "pipeline.db"
        initialize_database(path)
        connection = open_pipeline_db(path)
        candidate_id = insert_candidate(connection)
        original = c2_1_path4.supply_history
        c2_1_path4.supply_history = lambda *_args, **_kwargs: {
            "state": "success", "unitScaleStable": True, "previousSupplyRaw": 1_000_000,
            "currentSupplyRaw": 1_000_000, "previousDecimals": 18, "currentDecimals": 18,
            "previousBlock": {"blockNumber": 1}, "currentBlock": {"blockNumber": 2},
        }
        try:
            network = {"id": "ethereum-mainnet", "chainType": "EVM", "geckoTerminalId": "eth", "alchemyHost": "example"}
            config = {"sources": {"geckoterminal": {"authenticatedBaseUrl": "https://example", "credentialEnv": "", "fallbackCredentialEnv": "", "credentialHeader": "x-key", "minimumRequestIntervalSeconds": 0}}}
            fake = FakeClient(network)
            result = c2_1_path4.collect_path4(connection, fake, config, {"ethereum-mainnet": network})
            first_calls = fake.calls
            resumed = c2_1_path4.collect_path4(connection, fake, config, {"ethereum-mainnet": network})
        finally:
            c2_1_path4.supply_history = original
        assert result["states"] == {"success": 1}, result
        assert resumed["completed"] == 0 and resumed["skippedCandidates"] == 1 and fake.calls == first_calls
        row = connection.execute("SELECT * FROM pool_window_observations WHERE candidate_id=?", (candidate_id,)).fetchone()
        assert row and row["ohlcv_success_count"] == 1 and row["relative_expansion"] is not None
        assert connection.execute("SELECT COUNT(*) FROM supply_observations WHERE candidate_id=?", (candidate_id,)).fetchone()[0] == 2
        connection.close()
    assert c2_1_path4.weighted_median([(1, 1), (10, 9)]) == 10
    print("C2.1 path4 tests passed")


if __name__ == "__main__":
    main()
