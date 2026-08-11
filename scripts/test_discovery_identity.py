#!/usr/bin/env python3
import sqlite3
import tempfile
from pathlib import Path

from discover_network_tokens import build_discovery_snapshot
from refresh_candidate_pool import persist_refresh
from resolve_discovery_identities import (
    evaluate_identity_candidate,
    persist_identity_reviews,
)
from sync_thread_candidates import (
    build_pool_snapshot,
    load_fixture,
    stable_id,
    sync_candidates,
)


CONTRACT = "0x1111111111111111111111111111111111111111"


def discovery_record(name="Discovery Test", symbol="DTEST"):
    return {
        "networkId": "robinhood-mainnet",
        "contractAddress": CONTRACT,
        "tokenName": name,
        "symbol": symbol,
        "holdersCount": 128,
        "sourceIds": ["discovery-robinhood-blockscout"],
        "sourceUrls": [
            f"https://robinhoodchain.blockscout.com/address/{CONTRACT}"
        ],
        "discoveryKinds": ["主网代币注册表"],
        "sourceBoundaries": ["主网存在不等于项目归属。"],
        "rawPriceUsd": 0.01,
        "rawVolume24hUsd": 40000,
        "rawMarketCapUsd": 1000000,
        "pair": {},
        "pairError": "",
        "priceUsd": 0.01,
        "liquidityUsd": 30000,
        "volume24hUsd": 40000,
        "marketCapUsd": 1000000,
        "recentBuys24h": 40,
        "recentSells24h": 35,
        "exitNotionalUsd": 100,
        "estimatedExitSlippagePct": 0.6667,
        "contractExistsStatus": "verified",
        "metadataMatchStatus": "match",
        "pairMatchStatus": "match",
        "sellPathStatus": "read_only_verified",
        "contractRisk": "low",
        "preflightStatus": "pass",
        "verificationScope": "只读预检",
        "verificationEvidence": [],
        "discoveryScore": 90,
    }


def settings():
    return {
        "excludedAssetSymbols": ["USDG", "WETH"],
        "excludedNameFragments": ["robinhood token", "wrapped "],
    }


def coin_detail(contract=CONTRACT):
    return {
        "id": "discovery-test",
        "name": "Discovery Test",
        "symbol": "dtest",
        "platforms": {"robinhood": contract},
        "links": {
            "homepage": ["https://discovery.example"],
            "twitter_screen_name": "discoverytest",
            "repos_url": {"github": ["https://github.com/example/discovery"]},
        },
        "description": {"en": "A governance utility token."},
    }


def main():
    record = discovery_record()
    detail = coin_detail()
    review = evaluate_identity_candidate(
        record,
        [{"id": detail["id"], "name": detail["name"], "symbol": detail["symbol"]}],
        {detail["id"]: detail},
        {"status": "accessible", "contractConfirmed": False, "error": ""},
        settings(),
    )
    assert review["resolutionStatus"] == "corroborated"
    assert review["officialContractStatus"] == "registry_matched"
    assert review["promotionEligible"] is True
    assert review["valueCaptureStatus"] == "claimed"

    verified = evaluate_identity_candidate(
        record,
        [{"id": detail["id"], "name": detail["name"], "symbol": detail["symbol"]}],
        {detail["id"]: detail},
        {"status": "accessible", "contractConfirmed": True, "error": ""},
        settings(),
    )
    assert verified["resolutionStatus"] == "verified"
    assert verified["officialContractStatus"] == "confirmed"

    conflict_detail = coin_detail(
        "0x2222222222222222222222222222222222222222"
    )
    conflict = evaluate_identity_candidate(
        record,
        [
            {
                "id": conflict_detail["id"],
                "name": conflict_detail["name"],
                "symbol": conflict_detail["symbol"],
            }
        ],
        {conflict_detail["id"]: conflict_detail},
        {"status": "missing", "contractConfirmed": False, "error": ""},
        settings(),
    )
    assert conflict["resolutionStatus"] == "conflict"

    excluded = evaluate_identity_candidate(
        discovery_record("Global Dollar", "USDG"),
        [],
        {},
        {},
        settings(),
    )
    assert excluded["resolutionStatus"] == "rejected"
    assert excluded["valueCaptureStatus"] == "not_applicable"

    fixture = load_fixture()
    discovery_bundle = {
        "records": [record],
        "sourceStats": {
            "robinhood_registry": {
                "collected": 1,
                "accepted": 1,
                "failed": 0,
            }
        },
        "errors": [],
    }
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        db_path = root / "convexity.db"
        sync_candidates(
            db_path=db_path,
            pool_snapshot_path=root / "pool.js",
            runtime_snapshot_path=root / "runtime.js",
        )
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        try:
            persist_refresh(
                connection,
                fixture,
                [],
                [],
                "identity-review-test",
                discovery_bundle=discovery_bundle,
            )
            summary = persist_identity_reviews(
                connection,
                {
                    "records": [review],
                    "errors": [],
                    "sourceStats": {},
                },
                "identity-review-test",
                stable_id,
            )
            connection.commit()
            assert summary["promoted"] == 1
            assert connection.execute(
                "SELECT queue_status FROM network_discoveries"
            ).fetchone()[0] == "promoted"
            identity_row = connection.execute(
                "SELECT * FROM discovery_identity_reviews"
            ).fetchone()
            assert identity_row["promotion_status"] == "shadow_promoted"
            assert identity_row["official_contract_status"] == "registry_matched"
            case = connection.execute(
                """
                SELECT workflow_state, action_stage, value_capture_grade
                FROM candidate_cases
                WHERE case_id = ?
                """,
                (identity_row["promoted_case_id"],),
            ).fetchone()
            assert dict(case) == {
                "workflow_state": "shadow_signal",
                "action_stage": "只观察",
                "value_capture_grade": "unknown",
            }
            persist_refresh(
                connection,
                fixture,
                [],
                [],
                "identity-review-test-2",
                discovery_bundle=discovery_bundle,
                identity_bundle={
                    "records": [review],
                    "errors": [],
                    "sourceStats": {},
                },
            )
            connection.commit()
            assert connection.execute(
                "SELECT queue_status FROM network_discoveries"
            ).fetchone()[0] == "promoted"
            latest_identity = connection.execute(
                """
                SELECT promotion_status, promoted_case_id
                FROM discovery_identity_reviews
                WHERE run_id = 'identity-review-test-2'
                """
            ).fetchone()
            assert latest_identity["promotion_status"] == "shadow_promoted"
            assert latest_identity["promoted_case_id"] == identity_row["promoted_case_id"]
            assert connection.execute(
                "SELECT COUNT(*) FROM candidate_cases WHERE case_id LIKE 'auto-case-%'"
            ).fetchone()[0] == 1
            discovery_snapshot = build_discovery_snapshot(connection)
            assert discovery_snapshot["counts"]["promoted"] == 1
            assert discovery_snapshot["counts"]["identityReviewed"] == 1
            pool_snapshot = build_pool_snapshot(connection, fixture)
            auto_case = next(
                item
                for item in pool_snapshot["cases"]
                if item["caseId"] == identity_row["promoted_case_id"]
            )
            assert auto_case["pool"] == "embryo"
            assert auto_case["normalizedAction"] == "只观察"
        finally:
            connection.close()

    app_root = Path(__file__).resolve().parent.parent / "app"
    html = (app_root / "network-discovery.html").read_text(encoding="utf-8")
    script = (app_root / "network-discovery.js").read_text(encoding="utf-8")
    candidate_script = (app_root / "candidate-pool.js").read_text(encoding="utf-8")
    assert 'id="discoveryPromoted"' in html
    assert "项目身份与升格" in script
    assert "打开影子研究项目" in script
    assert "item.detailUrl" in candidate_script
    assert ".slice(" not in candidate_script
    print("discovery identity and promotion checks passed")


if __name__ == "__main__":
    main()
