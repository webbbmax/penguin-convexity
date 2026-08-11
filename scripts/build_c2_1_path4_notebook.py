import argparse
import contextlib
import io
from pathlib import Path

import simple_notebook as nbf


DEFAULT_OUTPUT = Path(
    "reports/c2.1-path4-full-pool-supply-probe/path4-full-pool-supply-analysis.ipynb"
)


def execute_notebook(notebook):
    namespace = {"__name__": "__notebook__"}
    execution_count = 0
    for cell in notebook["cells"]:
        if cell["cell_type"] != "code":
            continue
        execution_count += 1
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exec(
                compile(cell["source"], f"<notebook cell {execution_count}>", "exec"),
                namespace,
            )
        cell["execution_count"] = execution_count
        cell["outputs"] = []
        if stdout.getvalue():
            cell["outputs"].append(
                {"output_type": "stream", "name": "stdout", "text": stdout.getvalue()}
            )


def build_notebook(output_path: Path):
    notebook = nbf.v4.new_notebook()
    notebook["metadata"]["kernelspec"] = {
        "display_name": "Python 3",
        "language": "python",
        "name": "python3",
    }
    notebook["metadata"]["language_info"] = {"name": "python", "version": "3"}
    notebook["cells"] = [
        nbf.v4.new_markdown_cell(
            """# C2.1 第四路径：已索引池成交汇总与历史供应校验

## 结论先行

- 49 个真实影子样本的历史供应与单位稳定性全部可由程序复算。
- 289 个 CoinGecko 已索引池全部取得 OHLCV；另外 121 个 Gate 0 已发现但尚未被市场数据源索引的池没有 OHLCV，不能补成 0。
- 因此“已索引池成交汇总”在本样本为 49/49 可计算；若坚持“所有已发现池完整”，只有 25/49 可用。
- 本探针不冻结阈值、不定义凸性线索、不修改 C2.0、主数据库或调度任务。"""
        ),
        nbf.v4.new_code_cell(
            """import json, math
from collections import Counter
from pathlib import Path

REPORT_DIR = Path.cwd()
if REPORT_DIR.name != 'c2.1-path4-full-pool-supply-probe':
    REPORT_DIR = Path('reports/c2.1-path4-full-pool-supply-probe')

def rows(name):
    return [json.loads(line) for line in (REPORT_DIR / name).read_text(encoding='utf-8').splitlines() if line]

def latest(name, fields=('networkId','tokenAddress')):
    return {tuple(row[field] for field in fields): row for row in rows(name)}

def q(values, probability):
    clean = sorted(float(value) for value in values if value is not None and math.isfinite(float(value)))
    if not clean:
        return None
    position = (len(clean) - 1) * probability
    low = int(position); high = min(low + 1, len(clean) - 1); fraction = position - low
    return clean[low] * (1 - fraction) + clean[high] * fraction

def table(headers, data):
    result = ['| ' + ' | '.join(headers) + ' |', '| ' + ' | '.join(['---'] * len(headers)) + ' |']
    result.extend('| ' + ' | '.join(str(value) for value in row) + ' |' for row in data)
    return '\\n'.join(result)

observable = rows('observable-pools.jsonl')
gecko_physical = rows('gecko-pool-enumeration.jsonl')
gecko = latest('gecko-pool-enumeration.jsonl')
ohlcv_physical = rows('pool-ohlcv.jsonl')
ohlcv = latest('pool-ohlcv.jsonl', ('networkId','tokenAddress','poolAddress','timeframe'))
activity = rows('activity-aggregate.jsonl')
supply = latest('supply-history.jsonl')
path4 = latest('path4-inputs.jsonl')
BANDS = ['age_0_2','age_3_6','age_7_13','age_14_30','age_31_90']
BAND_LABELS = {'age_0_2':'0—2天','age_3_6':'3—6天','age_7_13':'7—13天','age_14_30':'14—30天','age_31_90':'31—90天'}
"""
        ),
        nbf.v4.new_markdown_cell("## 1. 池范围与来源边界"),
        nbf.v4.new_code_cell(
            """local_summary = json.loads((REPORT_DIR / 'local-pool-scan-summary.json').read_text(encoding='utf-8'))
pool_states = Counter(row['state'] for row in ohlcv.values())
pool_quality = [
    ['Gate 0 分片文件', local_summary['files'], '198/198 完成'],
    ['Gate 0 分片字节', local_summary['bytes'], '只读扫描'],
    ['Gate 0 命中去重池', local_summary['uniqueTokenPools'], '已覆盖工厂事件范围'],
    ['CoinGecko 已索引池', sum(row['geckoTopPoolCount'] for row in observable), '49 个项目均未触及 200 池上限'],
    ['并集池', sum(row['observablePoolCount'] for row in observable), '不是全球所有 DEX'],
    ['OHLCV 成功池', pool_states.get('success',0), '真实历史'],
    ['OHLCV 无数据池', pool_states.get('no_data',0), '不写 0'],
    ['OHLCV 来源失败池', pool_states.get('source_failure',0), '重试后终态'],
]
print(table(['检查项','数量','含义'], pool_quality))
assert local_summary['state'] == 'success' and local_summary['invalidJsonRows'] == 0
assert len(observable) == len(gecko) == 49
assert sum(row['geckoTopPoolCount'] for row in observable) == 289
assert len(ohlcv) == 410 and pool_states == Counter({'success':289,'no_data':121})
"""
        ),
        nbf.v4.new_markdown_cell("## 2. 年龄层覆盖：严格完整与实用口径必须分开"),
        nbf.v4.new_code_cell(
            """coverage_rows = []
for band in BANDS:
    band_activity = [row for row in activity if row['effectiveAgeBand'] == band]
    coverage_rows.append([
        BAND_LABELS[band],
        len(band_activity),
        sum(row['indexedCoverageState'] == 'complete_for_indexed_set' for row in band_activity),
        sum(row['coverageState'] == 'complete_for_observable_set' for row in band_activity),
        sum(row['localOnlyDiscoveredPools'] for row in band_activity),
        f"{band_activity[0]['windowWidth']} {band_activity[0]['windowUnit']}" if band_activity else '',
    ])
print(table(['真实年龄','样本','已索引池完整','所有已发现池完整','未索引池','前后比较窗口'], coverage_rows))
assert sum(row['indexedCoverageState'] == 'complete_for_indexed_set' for row in activity) == 49
assert sum(row['coverageState'] == 'complete_for_observable_set' for row in activity) == 25
"""
        ),
        nbf.v4.new_markdown_cell("## 3. 历史供应与计量单位稳定性"),
        nbf.v4.new_code_cell(
            """supply_states = Counter(row['state'] for row in supply.values())
supply_categories = Counter(row['supplyStabilityCategory'] for row in supply.values())
supply_quality = [
    ['EVM 历史 totalSupply + decimals', sum(row['provider']=='alchemy_archive_eth_call' for row in supply.values())],
    ['Solana Mint/Burn 历史重建', sum(row['provider']=='helius_mint_burn_reconstruction' for row in supply.values())],
    ['供应读取成功', supply_states.get('success',0)],
    ['计量单位前后一致', sum(row.get('unitScaleStable') is True for row in supply.values())],
    ['供应完全不变', supply_categories.get('exact_stable',0)],
    ['变化不超过 0.1%', supply_categories.get('near_stable_le_0_1pct',0)],
    ['变化 0.1%—1%', supply_categories.get('minor_change_le_1pct',0)],
    ['变化超过 1%', supply_categories.get('material_change_gt_1pct',0)],
]
print(table(['供应检查','项目数'], supply_quality))
assert supply_states == Counter({'success':49})
assert all(row.get('valuationComparable') is True for row in supply.values())
"""
        ),
        nbf.v4.new_markdown_cell("## 4. 第四路径可复算输入"),
        nbf.v4.new_code_cell(
            """path_rows = list(path4.values())
metric_rows = []
for field, label in [
    ('activityLogChange','成交活跃对数变化'),
    ('valuationLogChange','供应修正估值对数变化'),
    ('relativeExpansion','成交变化减估值变化'),
    ('riskAdjustedSurplus','成交变化减估值绝对变化'),
    ('topPoolVolumeSharePct','最大池成交占比（%）'),
]:
    values = [row.get(field) for row in path_rows]
    metric_rows.append([label, round(q(values,.25),4), round(q(values,.50),4), round(q(values,.75),4)])
print(table(['候选输入','P25','中位数','P75'], metric_rows))
print('严格所有已发现池可用：', sum(row['path4InputUsable'] for row in path_rows), '/ 49')
print('已索引池口径可用：', sum(row['indexedPoolPath4InputUsable'] for row in path_rows), '/ 49')
assert all(row['state'] == 'success' for row in path_rows)
"""
        ),
        nbf.v4.new_markdown_cell("## 5. 决策结论与程序上限"),
        nbf.v4.new_code_cell(
            """age_coverage = []
for band in BANDS:
    band_rows = [row for row in activity if row['effectiveAgeBand'] == band]
    age_coverage.append({
        'ageBand': band,
        'ageLabel': BAND_LABELS[band],
        'samples': len(band_rows),
        'indexedComplete': sum(row['indexedCoverageState']=='complete_for_indexed_set' for row in band_rows),
        'allDiscoveredComplete': sum(row['coverageState']=='complete_for_observable_set' for row in band_rows),
        'unindexedDiscoveredPools': sum(row['localOnlyDiscoveredPools'] for row in band_rows),
        'windowUnit': band_rows[0]['windowUnit'],
        'windowWidth': band_rows[0]['windowWidth'],
    })

summary = {
    'schemaVersion': 'c2.1-path4-full-pool-supply-analysis-v0.2',
    'status': 'planning_probe_not_frozen_not_product_coded',
    'generatedAt': max(row['calculatedAt'] for row in path_rows),
    'sample': {'projects':49, 'ageCoverage':age_coverage},
    'poolEnumeration': {
        'gate0PartitionFiles': local_summary['files'],
        'gate0PartitionBytes': local_summary['bytes'],
        'gate0MatchedEventRows': local_summary['matchedEventRows'],
        'gate0UniqueTokenPools': local_summary['uniqueTokenPools'],
        'coingeckoUniqueProjects': len(gecko),
        'coingeckoIndexedPools': sum(row['geckoTopPoolCount'] for row in observable),
        'observableUnionPools': sum(row['observablePoolCount'] for row in observable),
        'upstreamTruncatedProjects': sum(row['geckoUpstreamTruncated'] for row in observable),
        'enumerationPhysicalRows': len(gecko_physical),
        'enumerationDuplicateRows': len(gecko_physical) - len(gecko),
        'globalAllPoolsClaimAllowed': False,
    },
    'poolOhlcv': {
        'uniquePools': len(ohlcv),
        'physicalRowsIncludingRetries': len(ohlcv_physical),
        'success': pool_states.get('success',0),
        'noData': pool_states.get('no_data',0),
        'sourceFailure': pool_states.get('source_failure',0),
        'quotaLimited': pool_states.get('quota_limited',0),
        'indexedCoverageCompleteProjects': sum(row['indexedCoverageState']=='complete_for_indexed_set' for row in activity),
        'allDiscoveredCoverageCompleteProjects': sum(row['coverageState']=='complete_for_observable_set' for row in activity),
    },
    'historicalSupply': {
        'success': supply_states.get('success',0),
        'evmDirectHistoricalCalls': sum(row['provider']=='alchemy_archive_eth_call' for row in supply.values()),
        'solanaMintBurnReconstructions': sum(row['provider']=='helius_mint_burn_reconstruction' for row in supply.values()),
        'unitScaleStable': sum(row.get('unitScaleStable') is True for row in supply.values()),
        'categories': dict(supply_categories),
    },
    'path4': {
        'calculatedProjects': len(path_rows),
        'strictAllDiscoveredPoolUsable': sum(row['path4InputUsable'] for row in path_rows),
        'indexedPoolUsable': sum(row['indexedPoolPath4InputUsable'] for row in path_rows),
        'formulaInputs': ['activityLogChange','valuationLogChange','relativeExpansion','riskAdjustedSurplus'],
        'formalThresholdFrozen': False,
    },
    'decision': {
        'historicalSupplyValidation': 'proven_for_49_sample_with_unit_scale_check',
        'indexedPoolAggregation': 'proven_for_49_sample',
        'allDiscoveredPoolAggregation': 'not_proven_121_discovered_pools_unindexed',
        'recommendedProductWording': '已索引池成交汇总',
        'literalFullPoolWordingAllowed': False,
        'recommendedShadowRule': '只有已索引池覆盖完整、历史供应与单位稳定性成功时才计算；同时显示未索引池数量，不把缺失写成0。',
    },
}
(REPORT_DIR / 'analysis-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\\n',encoding='utf-8')
print(json.dumps(summary['decision'],ensure_ascii=False,indent=2))
"""
        ),
        nbf.v4.new_markdown_cell(
            """### 当前建议

1. 第四路径保留，但正式名称改为“已索引池活动超过供应修正估值变化”，不要使用“全球全池”。
2. 项目必须同时满足：已索引池 OHLCV 完整、历史供应成功、计量单位稳定；任一失败即放弃这条路径，不补零。
3. 额外显示“未索引已发现池数量”。这不是扣分项，但会降低独立可信度，并提示本路径仍可能漏掉成交。
4. 0%、0.1%、1% 只作为供应变化说明标签，不是进入或退出门槛。真实供应已经进入估值计算，不需要再用任意供应变化阈值重复惩罚。
5. 具体 `relativeExpansion` 或 `riskAdjustedSurplus` 门槛仍未冻结；应与另外三条路径一起用固定样本做进入/退出防抖后再决定。"""
        ),
    ]
    execute_notebook(notebook)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    nbf.write(notebook, output_path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    build_notebook(args.output.resolve())
    print(args.output.resolve())


if __name__ == "__main__":
    main()
