import argparse
import json
import sqlite3
from pathlib import Path


DEFAULT_REPORT_DIR = Path("reports/c2.1-path4-full-pool-supply-probe")


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def build(report_dir: Path):
    summary = read_json(report_dir / "analysis-summary.json")
    generated_at = summary["generatedAt"]
    age_rows = summary["sample"]["ageCoverage"]
    category_labels = {
        "exact_stable": "供应完全不变",
        "near_stable_le_0_1pct": "变化不超过 0.1%",
        "minor_change_le_1pct": "变化 0.1%—1%",
        "material_change_gt_1pct": "变化超过 1%",
    }
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute(
        "CREATE TABLE age_coverage(age_band TEXT, age_label TEXT, samples INTEGER, indexed_complete INTEGER, all_discovered_complete INTEGER, unindexed_pools INTEGER, window_unit TEXT, window_width INTEGER)"
    )
    connection.executemany(
        "INSERT INTO age_coverage VALUES(?,?,?,?,?,?,?,?)",
        [
            (
                row["ageBand"], row["ageLabel"], row["samples"], row["indexedComplete"],
                row["allDiscoveredComplete"], row["unindexedDiscoveredPools"],
                row["windowUnit"], row["windowWidth"],
            )
            for row in age_rows
        ],
    )
    connection.execute("CREATE TABLE supply_stability(category TEXT, projects INTEGER, meaning TEXT)")
    connection.executemany(
        "INSERT INTO supply_stability VALUES(?,?,?)",
        [
            (category_labels[key], summary["historicalSupply"]["categories"].get(key, 0), "仅解释，不作门槛")
            for key in category_labels
        ],
    )
    connection.execute(
        "CREATE TABLE headline(sample_projects INTEGER, indexed_complete_projects INTEGER, all_discovered_complete_projects INTEGER, unindexed_discovered_pools INTEGER, historical_supply_comparable INTEGER)"
    )
    connection.execute(
        "INSERT INTO headline VALUES(?,?,?,?,?)",
        (
            49,
            summary["poolOhlcv"]["indexedCoverageCompleteProjects"],
            summary["poolOhlcv"]["allDiscoveredCoverageCompleteProjects"],
            summary["poolOhlcv"]["noData"],
            summary["historicalSupply"]["unitScaleStable"],
        ),
    )
    coverage_sql = """SELECT age_band AS ageBand, age_label AS ageLabel, samples,
       unindexed_pools AS unindexedDiscoveredPools, window_unit AS windowUnit,
       window_width AS windowWidth, indexed_complete AS indexedCompleteProjects,
       all_discovered_complete AS completeProjects,
       CAST(all_discovered_complete AS REAL) / samples AS coverageRate
FROM age_coverage
ORDER BY ageBand"""
    supply_sql = "SELECT category, projects, meaning FROM supply_stability ORDER BY projects DESC, category"
    headline_sql = """SELECT sample_projects AS sampleProjects,
       indexed_complete_projects AS indexedCompleteProjects,
       all_discovered_complete_projects AS allDiscoveredCompleteProjects,
       unindexed_discovered_pools AS unindexedDiscoveredPools,
       historical_supply_comparable AS historicalSupplyComparable
FROM headline"""
    coverage_chart = [dict(row) for row in connection.execute(coverage_sql)]
    supply_rows = [dict(row) for row in connection.execute(supply_sql)]
    headline = [dict(row) for row in connection.execute(headline_sql)]
    connection.close()

    base_query = {
        "engine": "sqlite",
        "language": "sql",
        "executed_at": generated_at,
        "filters": [
            "49 个 P60 + 2,000/3,000/5,000 美元护栏影子样本",
            "真实年龄层：0—2、3—6、7—13、14—30、31—90 天",
            "排除当前未完成小时或自然日",
            "缺失池不写 0",
        ],
    }
    headline_source = {
        "id": "headline_sql",
        "label": "第四路径关键覆盖指标",
        "path": "analysis-summary.json",
        "query": {**base_query, "sql": headline_sql, "description": "从已执行探针摘要读取关键覆盖计数。", "tables_used": ["headline"]},
    }
    coverage_source = {
        "id": "coverage_sql",
        "label": "按真实年龄的池覆盖完整度",
        "path": "analysis-summary.json",
        "query": {
            **base_query,
            "sql": coverage_sql,
            "description": "按真实年龄显示所有已发现池均有 OHLCV 的项目数，同时保留已索引池完整数作为审计字段。",
            "tables_used": ["age_coverage"],
            "metric_definitions": {
                "indexedCoverage": "CoinGecko 已枚举池全部有 OHLCV，且枚举未触及上游分页上限",
                "strictCoverage": "Gate 0 已发现池与 CoinGecko 已索引池并集中，每个池均有 OHLCV",
            },
        },
    }
    supply_source = {
        "id": "supply_sql",
        "label": "历史供应稳定性分布",
        "path": "analysis-summary.json",
        "query": {
            **base_query,
            "sql": supply_sql,
            "description": "汇总两个历史时点的供应变化标签。",
            "tables_used": ["supply_stability"],
            "metric_definitions": {
                "valuationLogChange": "ln((当前窗口末价格×当前历史总供应)/(前一窗口末价格×前一历史总供应))"
            },
        },
    }
    summary_source = {"id": "path4_summary", "label": "第四路径完整探针摘要", "path": "analysis-summary.json"}
    title = "C2.1 第四路径数据边界"
    manifest = {
        "version": 1,
        "surface": "report",
        "title": title,
        "description": "已索引池成交汇总、严格全池缺口与历史供应稳定性验证。",
        "generatedAt": generated_at,
        "cards": [
            {
                "id": "indexed_complete",
                "description": "49 个样本中，CoinGecko 已枚举池全部取得 OHLCV 的项目数。",
                "dataset": "headline",
                "sourceId": "headline_sql",
                "metrics": [{"label": "已索引池完整项目", "field": "indexedCompleteProjects", "format": "number"}],
            },
            {
                "id": "strict_complete",
                "description": "49 个样本中，所有已发现池均取得 OHLCV 的项目数。",
                "dataset": "headline",
                "sourceId": "headline_sql",
                "metrics": [{"label": "严格全池完整项目", "field": "allDiscoveredCompleteProjects", "format": "number"}],
            },
            {
                "id": "unindexed_pools",
                "description": "Gate 0 已发现但当前市场数据源未索引、不能补成零成交的池。",
                "dataset": "headline",
                "sourceId": "headline_sql",
                "metrics": [{"label": "未索引已发现池", "field": "unindexedDiscoveredPools", "format": "number"}],
            },
            {
                "id": "supply_comparable",
                "description": "历史供应成功且两个时点的代币计量单位一致的项目数。",
                "dataset": "headline",
                "sourceId": "headline_sql",
                "metrics": [{"label": "供应可比较项目", "field": "historicalSupplyComparable", "format": "number"}],
            },
        ],
        "charts": [
            {
                "id": "coverage_by_age",
                "title": "所有已发现池完整项目数（按真实年龄）",
                "subtitle": "柱高为严格完整项目数；每个年龄层的已索引池均为全样本完整。",
                "type": "bar",
                "dataset": "coverage_by_age",
                "sourceId": "coverage_sql",
                "encodings": {
                    "x": {"field": "ageLabel", "type": "ordinal", "label": "真实年龄"},
                    "y": {"field": "completeProjects", "type": "quantitative", "label": "完整项目数", "format": "number"},
                },
                "yAxisTitle": "完整项目数",
                "valueFormat": "number",
                "layout": "full",
            }
        ],
        "tables": [
            {
                "id": "supply_stability",
                "title": "历史供应变化分布",
                "subtitle": "分类仅用于解释供应变化；真实供应已直接进入估值比较。",
                "dataset": "supply_stability",
                "sourceId": "supply_sql",
                "defaultSort": {"field": "projects", "direction": "desc"},
                "columns": [
                    {"field": "category", "label": "供应变化", "type": "text"},
                    {"field": "projects", "label": "项目数", "format": "number"},
                    {"field": "meaning", "label": "用途", "type": "text"},
                ],
            }
        ],
        "sources": [headline_source, coverage_source, supply_source, summary_source],
        "blocks": [
            {"id": "title", "type": "markdown", "body": f"# {title}"},
            {
                "id": "executive_summary",
                "type": "markdown",
                "sourceId": "path4_summary",
                "body": """## Executive Summary（结论摘要）

**结论与证据：** 历史供应 49/49、已索引池 49/49；严格全池 25/49。289 个已索引池全部成功；121 个池未索引；45 个供应不变，4 个变化不超过 1%。

**建议与边界：** 名称使用“已索引池活动超过供应修正估值变化”；池历史、供应和单位必须同时可用。未索引池是否停用仍待确认。OHLCV 不能去除刷量；阈值未冻结。""",
            },
            {"id": "coverage_chart", "type": "chart", "chartId": "coverage_by_age", "layout": "full"},
        ],
    }
    artifact = {
        "surface": "report",
        "manifest": manifest,
        "snapshot": {
            "version": 1,
            "generatedAt": generated_at,
            "status": "ready",
            "datasets": {
                "headline": headline,
                "coverage_by_age": coverage_chart,
                "supply_stability": supply_rows,
            },
        },
        "sources": [headline_source, coverage_source, supply_source, summary_source],
        "package_info": {"root": ".", "manifestPath": "artifact.json", "snapshotPath": "artifact.json"},
    }
    (report_dir / "artifact.json").write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    notes = """# C2.1 第四路径报告构建说明

## 图表契约

- 问题：五个真实年龄层中，严格“所有已发现池完整”的项目数如何分布？
- 结论：严格并集口径 25/49，且 31—90 天为 0/3；已索引池完整 49/49 在相邻指标卡展示。
- 图形：单系列柱状图；横轴为年龄层，纵轴为严格完整项目数。
- 数据：5 行，保留样本分母、已索引池完整数、未索引池数和比较窗口，便于审计。
- 颜色：单一蓝色根，不使用冗余图例；轴标签同时承担类别识别。
- QA：由 canonical portable artifact builder 验证增强阅读器、窄屏和语义回退。

## 数据与边界

- 所有数字来自本目录的本地独立探针产物；不读取或修改 C2.0 产品代码与主数据库。
- 池级 OHLCV 是总成交，不是去重后的钱包或用户成交。
- 121 个 `no_data` 池全部属于 Gate 0 发现但 CoinGecko 未枚举的池；缺失不补零。
- 枚举物理记录 82、唯一项目 49；33 条重复尝试不影响 latest-by-identity 结果，但暴露一次性探针缺少单实例锁。
"""
    (report_dir / "source-notes.md").write_text(notes, encoding="utf-8")
    print(report_dir / "artifact.json")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    args = parser.parse_args()
    build(args.report_dir.resolve())


if __name__ == "__main__":
    main()
