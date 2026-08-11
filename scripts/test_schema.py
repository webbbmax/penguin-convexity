#!/usr/bin/env python3
import importlib.util
import json
import sqlite3
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
INIT_PATH = PROJECT_ROOT / "scripts" / "init_db.py"
DICTIONARY_PATH = PROJECT_ROOT / "storage" / "data-dictionary.json"


def load_init_module():
    spec = importlib.util.spec_from_file_location("convexity_init_db", INIT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"PASS {message}")


def main():
    init_db = load_init_module()
    dictionary = json.loads(DICTIONARY_PATH.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as temporary:
        temp_root = Path(temporary)
        db_path = temp_root / "convexity-test.db"
        snapshot_path = temp_root / "runtime-snapshot.js"
        first = init_db.initialize_database(db_path, snapshot_path, backup=False)
        second = init_db.initialize_database(db_path, snapshot_path, backup=False)
        backed_up = init_db.initialize_database(
            db_path,
            snapshot_path,
            backup=True,
            backup_dir=temp_root / "backups",
        )

        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        try:
            names = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            }
            documented = set(dictionary["tables"])
            assert_true(names == documented, "数据字典覆盖全部数据库表")
            assert_true(first["tables"] == second["tables"] == len(names), "重复初始化不会重复建表")
            assert_true(
                backed_up["backup"] and Path(backed_up["backup"]).exists(),
                "已有数据库升级前会生成可恢复备份",
            )
            assert_true(
                connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 17,
                "数据库迁移记录保持幂等",
            )
            assert_true(
                "project_identity_aliases" in names,
                "项目身份别名账本已进入数据底座",
            )
            assert_true(
                "evidence_lineage" in names,
                "原始证据溯源账本已进入数据底座",
            )
            assert_true(
                "source_adapter_records" in names,
                "采集主干适配审计已进入数据底座",
            )
            assert_true(
                "project_monitoring_targets" in names,
                "项目监控目标注册表已进入数据底座",
            )
            assert_true(
                {
                    "normalized_events_v2",
                    "source_cursors_v2",
                    "event_replay_runs",
                    "source_health_v2",
                    "orphan_events_v2",
                    "event_attribution_history",
                    "entity_nodes",
                    "entity_edges",
                    "watcher_definitions",
                }.issubset(names),
                "C1.7 最大漏斗数据主干已经进入独立数据库",
            )
            assert_true(
                connection.execute("SELECT COUNT(*) FROM runs WHERE run_id='convexity-bootstrap-v1'").fetchone()[0] == 1,
                "初始化运行记录不会重复",
            )

            run_columns = {
                row[1] for row in connection.execute("PRAGMA table_info('runs')")
            }
            required_feedback = {
                "collected_count",
                "duplicate_count",
                "filtered_count",
                "shadow_added_count",
                "active_added_count",
                "zero_result_class",
                "zero_result_explanation",
                "error_count",
            }
            assert_true(required_feedback.issubset(run_columns), "运行反馈包含数量、失败和零结果解释")

            field_labels = dictionary["fieldLabels"]
            unlabeled = []
            for table in names:
                for row in connection.execute(f'PRAGMA table_info("{table}")'):
                    if row[1] not in field_labels:
                        unlabeled.append(f"{table}.{row[1]}")
            assert_true(not unlabeled, f"全部字段都有中文说明：{', '.join(unlabeled)}")

            try:
                connection.execute(
                    """
                    INSERT INTO candidate_cases (
                      case_id, title, workflow_state, rule_version, created_at, updated_at
                    )
                    VALUES ('invalid-case', 'invalid', 'not-a-state', 'v1', 'now', 'now')
                    """
                )
                connection.commit()
                raise AssertionError("非法工作流状态未被数据库拦截")
            except sqlite3.IntegrityError:
                connection.rollback()
                print("PASS 非法工作流状态会被数据库拦截")

            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            assert_true(integrity == "ok", "SQLite 完整性检查通过")
        finally:
            connection.close()

        snapshot_text = snapshot_path.read_text(encoding="utf-8")
        assert_true(
            "PENGUIN_CONVEXITY_FOUNDATION" in snapshot_text
            and "数据库初始化，本次没有执行采集" in snapshot_text,
            "页面快照能够解释初始化零结果",
        )

    html = (PROJECT_ROOT / "app" / "data-dictionary.html").read_text(encoding="utf-8")
    app_js = (PROJECT_ROOT / "app" / "app.js").read_text(encoding="utf-8")
    assert_true("运行与反馈" in html and "data-table-search" in html, "数据字典页面具备运行反馈与检索入口")
    assert_true("zeroResultLabels" in app_js and "latestSourceStats" in app_js, "界面会展示零结果原因和单来源反馈")
    print("\n凸性数据底座测试全部通过。")


if __name__ == "__main__":
    main()
