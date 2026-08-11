#!/usr/bin/env python3
import sqlite3
import tempfile
from pathlib import Path

from init_db import initialize_database
from build_manual_review_snapshot import build_manual_review_snapshot
from manage_manual_review import execute_manual_review_action
from sync_thread_candidates import import_candidates, load_fixture


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def run():
    with tempfile.TemporaryDirectory() as temporary_directory:
        temporary_root = Path(temporary_directory)
        db_path = temporary_root / "manual-review.db"
        runtime_path = temporary_root / "runtime.js"
        initialize_database(db_path, runtime_path, backup=False)
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        import_candidates(connection, load_fixture())
        connection.execute(
            """
            UPDATE assets
            SET contract_address = '5yC9BM8KUsJTPbWPLfA2N8qH1s9V8DQ3Vcw1G6Jdpump'
            WHERE project_id = 'agenc'
            """
        )
        now = "2026-07-29T00:00:00Z"
        agenc_asset_id = connection.execute(
            "SELECT asset_id FROM assets WHERE project_id = 'agenc' LIMIT 1"
        ).fetchone()[0]
        connection.execute(
            """
            INSERT INTO market_snapshots (
              snapshot_id, asset_id, observed_at, price_usd, liquidity_usd,
              volume_24h_usd, market_cap_usd, fdv_usd, definition_note
            )
            VALUES (
              'market-test-agenc', ?, ?, 0.012, 32000, 48000,
              4200000, 9000000, '人工复核行情筛选自动测试'
            )
            """,
            (agenc_asset_id, now),
        )
        connection.execute(
            """
            INSERT INTO network_discoveries (
              discovery_id, network_id, contract_address, token_name, symbol,
              first_seen_at, last_seen_at, source_conflict_risk,
              liquidity_usd, volume_24h_usd, market_cap_usd,
              queue_status, status_reason, created_at, updated_at
            )
            VALUES (
              'test-discovery', 'base-mainnet',
              '0x0000000000000000000000000000000000000001',
              'Unverified Token', 'UVT', ?, ?, 'high', 21000, 26000, 750000,
              'identity_pending',
              '仅用于验证线索不能直接升格。', ?, ?
            )
            """,
            (now, now, now, now),
        )
        connection.commit()
        connection.close()

        common = {
            "targetKey": "project:agenc",
            "classification": "ordinary_candidate",
            "priority": "P1",
            "maturity": "L2",
            "riskLevel": "medium",
            "convexitySource": "产品采用凸性",
            "researchRouteOverride": "startup",
            "researchRouteReason": "项目仍处于L2，先完成基础资料核验。",
            "identityConfirmed": False,
            "note": "自动测试：确认人工复核记录可以保存和追溯。",
        }
        saved = execute_manual_review_action(
            {"operation": "save_review", **common},
            db_path=db_path,
            runtime_snapshot_path=runtime_path,
            rebuild=False,
        )
        assert_true(
            saved["target"]["manualClassification"] == "ordinary_candidate",
            "人工分类没有保存",
        )
        assert_true(
            saved["target"]["researchRouteOverride"] == "startup",
            "人工研究路线没有保存",
        )
        assert_true(saved["target"]["promotionEligible"], "符合条件的项目未允许升格")
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        market_snapshot = build_manual_review_snapshot(connection)
        connection.close()
        agenc_target = next(
            item
            for item in market_snapshot["targets"]
            if item["masterId"] == "project:agenc"
        )
        assert_true(
            agenc_target["marketCapUsd"] == 4200000,
            "正式项目没有带入最新流通市值",
        )
        assert_true(
            agenc_target["fdvUsd"] == 9000000,
            "正式项目没有带入最新FDV",
        )
        assert_true(
            market_snapshot["counts"]["withMarketData"] >= 2,
            "人工复核快照没有统计行情覆盖记录",
        )

        revised = execute_manual_review_action(
            {
                "operation": "save_review",
                **common,
                "priority": "P0",
                "note": "自动测试：修改后的人工复核，旧版本必须保留。",
            },
            db_path=db_path,
            runtime_snapshot_path=runtime_path,
            rebuild=False,
        )
        assert_true(
            revised["target"]["promotionEligible"],
            "修改复核后升格条件发生错误",
        )

        promoted = execute_manual_review_action(
            {
                "operation": "promote",
                "targetKey": "project:agenc",
                "note": "自动测试：升格。",
            },
            db_path=db_path,
            runtime_snapshot_path=runtime_path,
            rebuild=False,
        )
        assert_true(
            promoted["target"]["publicationStatus"] == "published",
            "升格后发布状态不正确",
        )

        withdrawn = execute_manual_review_action(
            {
                "operation": "withdraw_publication",
                "targetKey": "project:agenc",
                "note": "自动测试：撤回发布。",
            },
            db_path=db_path,
            runtime_snapshot_path=runtime_path,
            rebuild=False,
        )
        assert_true(
            withdrawn["target"]["publicationStatus"] == "withdrawn",
            "撤回后发布状态不正确",
        )

        review_withdrawn = execute_manual_review_action(
            {
                "operation": "withdraw_review",
                "targetKey": "project:agenc",
                "note": "自动测试：撤回人工标注。",
            },
            db_path=db_path,
            runtime_snapshot_path=runtime_path,
            rebuild=False,
        )
        assert_true(
            review_withdrawn["counts"]["reviewed"] == 0,
            "撤回后仍被计为有效人工复核",
        )

        combined = execute_manual_review_action(
            {
                "operation": "save_and_promote",
                **common,
                "classification": "watch_embryo",
                "note": "自动测试：人工标注和发布必须在一次操作中同时成功。",
            },
            db_path=db_path,
            runtime_snapshot_path=runtime_path,
            rebuild=False,
        )
        assert_true(
            combined["target"]["manualClassification"] == "watch_embryo",
            "保存并发布没有写入人工标注",
        )
        assert_true(
            combined["target"]["publicationStatus"] == "published",
            "保存并发布没有形成真实发布状态",
        )

        execute_manual_review_action(
            {
                "operation": "save_review",
                "targetKey": "discovery:test-discovery",
                "classification": "extreme_candidate",
                "priority": "P0",
                "maturity": "L1",
                "riskLevel": "high",
                "convexitySource": "流动性凸性",
                "identityConfirmed": True,
                "note": "自动测试：即使人工确认，纯发现也不能直接发布。",
            },
            db_path=db_path,
            runtime_snapshot_path=runtime_path,
            rebuild=False,
        )
        try:
            execute_manual_review_action(
                {
                    "operation": "promote",
                    "targetKey": "discovery:test-discovery",
                },
                db_path=db_path,
                runtime_snapshot_path=runtime_path,
                rebuild=False,
            )
        except ValueError as error:
            assert_true(
                "尚未建立项目主体" in str(error),
                "纯发现升格没有返回明确阻断原因",
            )
        else:
            raise AssertionError("纯发现被错误升格到机会中心")

        try:
            execute_manual_review_action(
                {
                    "operation": "save_and_promote",
                    "targetKey": "discovery:test-discovery",
                    "classification": "extreme_candidate",
                    "priority": "P0",
                    "maturity": "L1",
                    "riskLevel": "high",
                    "convexitySource": "流动性凸性",
                    "identityConfirmed": True,
                    "note": "这条内容必须因为发布失败而整体回滚。",
                },
                db_path=db_path,
                runtime_snapshot_path=runtime_path,
                rebuild=False,
            )
        except ValueError as error:
            assert_true(
                "本次操作没有写入任何更改" in str(error),
                "组合操作失败时没有说明原子回滚",
            )
        else:
            raise AssertionError("纯发现通过组合操作被错误发布")

        connection = sqlite3.connect(db_path)
        publication_history = connection.execute(
            "SELECT COUNT(*) FROM publication_records WHERE project_id = 'agenc'"
        ).fetchone()[0]
        audit_history = connection.execute(
            "SELECT COUNT(*) FROM manual_annotations"
        ).fetchone()[0]
        discovery_note = connection.execute(
            """
            SELECT note
            FROM manual_annotations
            WHERE discovery_id = 'test-discovery'
              AND field_name = 'manual_review'
              AND status = 'active'
            ORDER BY updated_at DESC
            LIMIT 1
            """
        ).fetchone()[0]
        connection.close()
        assert_true(publication_history == 3, "组合发布没有保留独立发布记录")
        assert_true(audit_history == 5, "人工标注历史数量不正确")
        assert_true(
            discovery_note == "自动测试：即使人工确认，纯发现也不能直接发布。",
            "组合发布失败后仍然覆盖了原有人工标注",
        )

        html = (PROJECT_ROOT / "app" / "manual-review.html").read_text(
            encoding="utf-8"
        )
        script = (PROJECT_ROOT / "app" / "manual-review.js").read_text(
            encoding="utf-8"
        )
        server = (PROJECT_ROOT / "scripts" / "serve_local.py").read_text(
            encoding="utf-8"
        )
        assert_true("人工复核与升格" in html, "人工复核页面缺少主标题")
        for filter_id in (
            "reviewMarketStatusFilter",
            "reviewMarketCapPreset",
            "reviewFdvPreset",
            "reviewLiquidityPreset",
            "reviewVolumePreset",
        ):
            assert_true(filter_id in html, f"人工复核页面缺少行情筛选：{filter_id}")
        assert_true("metricMatchesPreset" in script, "前端缺少行情预设筛选逻辑")
        assert_true(
            'value === null || value === undefined || value === ""' in script,
            "前端没有区分行情缺失与真实数值0",
        )
        assert_true("暂无行情" in html, "人工复核页面无法单独识别暂无行情记录")
        assert_true("save_review" in script, "前端缺少保存人工复核动作")
        assert_true("save_and_promote" in script, "前端缺少保存并发布组合动作")
        assert_true("保存标注并发布" in script, "页面没有明确的组合操作入口")
        assert_true("data-review-preset" in html, "人工复核页面缺少快捷队列")
        for queue_id in ("must_handle", "worth_review", "low_priority"):
            assert_true(queue_id in html, f"人工复核页面缺少明确队列：{queue_id}")
        assert_true("reviewQueue" in script, "前端缺少复核队列判定逻辑")
        assert_true("queueRecommendation" in script, "项目没有显示建议下一步")
        assert_true("manual-review-advanced" in html, "复杂筛选没有收进高级筛选")
        assert_true("researchRouteOverride" in script, "前端缺少人工研究路线")
        assert_true("reviewRouteFilter" in html, "人工复核缺少研究路线筛选")
        assert_true("/api/manual-review" in server, "本地服务缺少人工复核接口")
        assert_true(
            '"/api/manual-review"' in server,
            "企鹅投研-凸性服务缺少人工复核接口",
        )

    print("C1.2-07 人工标注、组合发布、回滚与审计测试通过。")


if __name__ == "__main__":
    run()
