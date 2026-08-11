#!/usr/bin/env python3
import json
import tempfile
import threading
import urllib.request
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path

import serve_local
from gate_screening import (
    DEFAULT_PRESETS_PATH,
    build_screening_snapshot,
    evaluate_case,
    load_presets,
    load_state,
    save_state,
    validate_state,
)


def sample_case():
    return {
        "caseId": "sample-cowl",
        "projectName": "Cowl Protocol",
        "maturity": "L2",
        "state": "tradeability_pending",
        "riskLevel": "high",
        "remainingConvexity": "high",
        "tradeabilityStatus": "limited",
        "normalizedAction": "只观察",
        "valueCaptureGrade": "B",
        "mismatchScore": 46,
        "assetMapped": True,
        "projectIdentityStatus": "verified",
        "assetIdentityStatus": "verified",
        "sellPathStatus": "unknown",
        "contractRisk": "unknown",
        "hardTracePresent": True,
        "convexityFieldsComplete": True,
        "latestMarket": {
            "liquidityUsd": 21_500,
            "volume24hUsd": 44_000,
            "estimatedExitSlippagePct": 0.95,
        },
    }


def preset_settings(presets, preset_id):
    return next(
        preset["settings"]
        for preset in presets["presets"]
        if preset["id"] == preset_id
    )


def main():
    presets = load_presets()
    assert presets["defaultPresetId"] == "extreme_discovery"
    assert {item["id"] for item in presets["presets"]} == {
        "extreme_action",
        "extreme_discovery",
        "ordinary_action",
        "research_universe",
    }

    cowl = sample_case()
    discovery = evaluate_case(
        cowl, preset_settings(presets, "extreme_discovery")
    )
    assert discovery["included"] is True
    assert discovery["status"] == "pending"
    assert any("待核验" in reason for reason in discovery["pendingReasons"])

    strict = evaluate_case(cowl, preset_settings(presets, "extreme_action"))
    assert strict["included"] is False
    assert strict["status"] == "fail"
    assert any("最高允许 中" in reason for reason in strict["failedReasons"])

    complete = {
        **cowl,
        "riskLevel": "medium",
        "mismatchScore": 72,
        "tradeabilityStatus": "verified",
        "sellPathStatus": "verified",
        "contractRisk": "low",
    }
    complete_result = evaluate_case(
        complete, preset_settings(presets, "extreme_action")
    )
    assert complete_result["included"] is True
    assert complete_result["status"] == "pass"

    invalidated = {
        **complete,
        "state": "invalidated",
        "normalizedAction": "已失去凸性",
    }
    invalidated_result = evaluate_case(
        invalidated, preset_settings(presets, "research_universe")
    )
    assert invalidated_result["included"] is False
    assert invalidated_result["status"] == "fail"

    with tempfile.TemporaryDirectory() as temporary:
        state_path = Path(temporary) / "gate-state.json"
        initial = load_state(state_path, DEFAULT_PRESETS_PATH)
        assert initial["activePresetId"] == "extreme_discovery"

        payload = {
            "activePresetId": "custom",
            "showOnlyPassing": False,
            "settings": {
                **preset_settings(presets, "extreme_discovery"),
                "minimumLiquidityUsd": 12_345,
            },
        }
        saved = save_state(payload, state_path, DEFAULT_PRESETS_PATH)
        loaded = load_state(state_path, DEFAULT_PRESETS_PATH)
        assert saved["settings"]["minimumLiquidityUsd"] == 12_345
        assert loaded["settings"]["minimumLiquidityUsd"] == 12_345
        assert loaded["showOnlyPassing"] is False
        assert not state_path.with_suffix(".json.tmp").exists()

        bad_payload = {
            **payload,
            "settings": {**payload["settings"], "allowedMaturities": []},
        }
        try:
            validate_state(bad_payload, presets)
            raise AssertionError("空成熟度设置应被拒绝")
        except ValueError as error:
            assert "成熟度" in str(error)

        snapshot = build_screening_snapshot(
            [sample_case()],
            state_path=state_path,
            presets_path=DEFAULT_PRESETS_PATH,
        )
        assert snapshot["summary"]["total"] == 1
        assert snapshot["summary"]["included"] == 1
        assert snapshot["summary"]["pending"] == 1

    original_save = serve_local.save_gate_screening_state
    original_rebuild = serve_local.rebuild_pool_snapshot
    serve_local.save_gate_screening_state = lambda payload: {
        **payload,
        "updatedAt": "test",
    }
    serve_local.rebuild_pool_snapshot = lambda: {
        "gateScreening": {
            "summary": {
                "total": 20,
                "included": 3,
                "passed": 0,
                "pending": 3,
                "excluded": 17,
            }
        }
    }
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        partial(serve_local.QuietHandler, directory=str(serve_local.APP_ROOT)),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/api/gate-screening",
            data=json.dumps(
                {
                    "activePresetId": "custom",
                    "showOnlyPassing": True,
                    "settings": preset_settings(presets, "extreme_discovery"),
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = json.load(response)
            assert response.status == 200
        assert payload["status"] == "success"
        assert payload["summary"]["included"] == 3
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        serve_local.save_gate_screening_state = original_save
        serve_local.rebuild_pool_snapshot = original_rebuild

    html = (serve_local.APP_ROOT / "screening-console.html").read_text(encoding="utf-8")
    script = (serve_local.APP_ROOT / "screening-console.js").read_text(encoding="utf-8")
    assert 'id="gateScreeningForm"' in html
    assert 'id="gateMinLiquidity"' in html
    assert 'fetch(apiUrl("gate-screening")' in script
    assert 'location.pathname.startsWith("/convexity/")' in script
    assert "screeningMarkup" in script

    print("gate screening checks passed")


if __name__ == "__main__":
    main()
