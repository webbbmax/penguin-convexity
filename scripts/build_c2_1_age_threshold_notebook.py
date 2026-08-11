import argparse
from pathlib import Path

import simple_notebook as nbf


DEFAULT_OUTPUT = Path("reports/c2.1-age-threshold-analysis/age-threshold-analysis.ipynb")


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
            """# C2.1 年龄分层门槛分析

## 结论先行

- 本分析从 Gate 0 已验收的 4,585,955 条 90 天候选中，按 6 链 × 5 个 Gate 0 池子事件日龄层确定性抽样；不是重新回扫链上历史。
- `no_data` 只表示当前市场接口未返回可计算交易对，不等于项目失败，也不参与数值分位数。
- Gate 0 的池子事件日龄只是 T0 下界；市场接口发现更早交易对时，必须改用更早时间分层，超过 90 天的样本不参与新项目影子门槛。
- 当前样本可以校准“交易与流动性形成”的宽松影子门槛；不能校准产品使用、持仓供应和多日防抖，因为 Gate 0 没有这些输入。
- 年龄只决定比较组和观测窗口，不形成年龄分、质量加权或前台资格门槛。
"""
        ),
        nbf.v4.new_code_cell(
            """import json, math
from collections import Counter, defaultdict
from pathlib import Path
Markdown = str
display = print

REPORT_DIR = Path.cwd()
if REPORT_DIR.name != 'c2.1-age-threshold-analysis':
    REPORT_DIR = Path('reports/c2.1-age-threshold-analysis')
PROFILE_PATH = REPORT_DIR / 'sample-profile.json'
OBSERVATION_PATH = REPORT_DIR / 'market-observations.jsonl'

profile = json.loads(PROFILE_PATH.read_text(encoding='utf-8'))
latest = {}
for line in OBSERVATION_PATH.read_text(encoding='utf-8').splitlines():
    if line:
        row = json.loads(line)
        latest[(row['networkId'], row['tokenAddress'])] = row
rows = list(latest.values())
BANDS = ['age_0_2', 'age_3_6', 'age_7_13', 'age_14_30', 'age_31_90']
success_rows = [
    row for row in rows
    if row['state'] == 'success' and row.get('effectiveAgeBand') in BANDS
]
effective_outside_90 = [
    row for row in rows
    if row['state'] == 'success' and row.get('effectiveAgeBand') not in BANDS
]
BAND_LABELS = {
    'age_0_2': '0-2天', 'age_3_6': '3-6天', 'age_7_13': '7-13天',
    'age_14_30': '14-30天', 'age_31_90': '31-90天'
}
LIQUIDITY_FLOORS = {
    'age_0_2': 2000, 'age_3_6': 2000, 'age_7_13': 3000,
    'age_14_30': 3000, 'age_31_90': 5000
}

def quantile(values, probability):
    clean = sorted(float(value) for value in values if value is not None and math.isfinite(float(value)))
    if not clean:
        return None
    position = (len(clean) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(clean) - 1)
    fraction = position - lower
    return clean[lower] * (1 - fraction) + clean[upper] * fraction

def metric(row, name):
    pair = row['bestPair']
    if name == 'liquidity':
        return pair.get('liquidityUsd')
    if name == 'volume24':
        return pair.get('volumeH24Usd')
    if name == 'tx24':
        return (pair.get('buysH24') or 0) + (pair.get('sellsH24') or 0)
    if name == 'volumeLiquidity24':
        liquidity = pair.get('liquidityUsd')
        volume = pair.get('volumeH24Usd')
        return volume / liquidity if liquidity and volume is not None else None
    raise KeyError(name)

def markdown_table(headers, data):
    lines = ['| ' + ' | '.join(headers) + ' |', '| ' + ' | '.join(['---'] * len(headers)) + ' |']
    lines.extend('| ' + ' | '.join(str(value) for value in row) + ' |' for row in data)
    return '\\n'.join(lines)
"""
        ),
        nbf.v4.new_markdown_cell("## 1. 数据质量与覆盖"),
        nbf.v4.new_code_cell(
            """state_counts = Counter(row['state'] for row in rows)
quality_rows = [
    ['Gate 0候选总数', f\"{profile['candidateRowsSeen']:,}\"],
    ['分层样本数', f\"{profile['selectedRows']:,}\"],
    ['可计算市场样本', f\"{state_counts.get('success', 0):,}\"],
    ['当前接口无数据', f\"{state_counts.get('no_data', 0):,}\"],
    ['更早历史校正后超出90天', f\"{len(effective_outside_90):,}\"],
    ['遗留来源失败', f\"{sum(state_counts.get(s, 0) for s in ('source_failure','quota_limited','configuration_missing','program_failure')):,}\"],
    ['候选文件SHA256', profile['candidateSha256']],
]
display(Markdown(markdown_table(['检查项', '结果'], quality_rows)))
"""
        ),
        nbf.v4.new_code_cell(
            """coverage_table = []
for band in BANDS:
    band_rows = [row for row in rows if row['gate0AgeBand'] == band]
    available = sum(row['state'] == 'success' for row in band_rows)
    coverage_table.append([
        BAND_LABELS[band], f'{len(band_rows):,}', f'{available:,}',
        f'{available / len(band_rows):.1%}' if band_rows else '不可用'
    ])
display(Markdown(markdown_table(['Gate 0池子事件日龄', '抽样数', '可计算数', '当前可计算率'], coverage_table)))
"""
        ),
        nbf.v4.new_markdown_cell(
            """### 数据质量解释

当前可计算率随日龄明显下降，说明随机 Gate 0 候选中大量交易池已经没有聚合市场记录。它不能被解释为“项目质量下降”，也不能用 0 填入流动性或成交。真正生产校准必须在 C2.1 完成 T0、A/B/C/D、买卖事实、硬风险和产品证据筛选后，对全部合资格对象重算。
"""
        ),
        nbf.v4.new_markdown_cell("## 2. 不同日龄的真实市场分布"),
        nbf.v4.new_code_cell(
            """distribution = {}
distribution_table = []
for band in BANDS:
    band_rows = [row for row in success_rows if row['effectiveAgeBand'] == band]
    distribution[band] = {}
    for name in ['liquidity', 'volume24', 'tx24', 'volumeLiquidity24']:
        values = [metric(row, name) for row in band_rows]
        distribution[band][name] = {
            'n': sum(value is not None for value in values),
            'p25': quantile(values, .25),
            'p50': quantile(values, .50),
            'p60': quantile(values, .60),
            'p75': quantile(values, .75),
        }
    distribution_table.append([
        BAND_LABELS[band], len(band_rows),
        f\"${distribution[band]['liquidity']['p25']:,.0f}\" if distribution[band]['liquidity']['p25'] is not None else '不可用',
        f\"${distribution[band]['liquidity']['p50']:,.0f}\" if distribution[band]['liquidity']['p50'] is not None else '不可用',
        f\"${distribution[band]['volume24']['p50']:,.0f}\" if distribution[band]['volume24']['p50'] is not None else '不可用',
        f\"{distribution[band]['tx24']['p50']:.1f}\" if distribution[band]['tx24']['p50'] is not None else '不可用',
    ])
display(Markdown(markdown_table(
    ['真实日龄', '可计算数', '流动性P25', '流动性中位', '24h成交中位', '24h交易中位'],
    distribution_table
)))
"""
        ),
        nbf.v4.new_markdown_cell("## 3. 交易与流动性路径的市场侧宽松预筛"),
        nbf.v4.new_code_cell(
            """def simulate(percentile):
    result = {'percentile': percentile, 'total': 0, 'bands': {}}
    for band in BANDS:
        band_rows = [row for row in success_rows if row['effectiveAgeBand'] == band]
        cuts = {
            name: quantile([metric(row, name) for row in band_rows], percentile)
            for name in ['liquidity', 'volume24', 'tx24', 'volumeLiquidity24']
        }
        selected = []
        for row in band_rows:
            pair = row['bestPair']
            liquidity = metric(row, 'liquidity')
            guard = (
                liquidity is not None
                and liquidity >= LIQUIDITY_FLOORS[band]
                and (pair.get('buysH24') or 0) >= 1
                and (pair.get('sellsH24') or 0) >= 1
            )
            demand_high = sum(
                metric(row, name) is not None and metric(row, name) >= cuts[name]
                for name in ['volume24', 'tx24', 'volumeLiquidity24']
            ) >= 2
            liquidity_high = liquidity is not None and liquidity >= cuts['liquidity']
            if guard and demand_high and liquidity_high:
                selected.append(row)
        result['bands'][band] = {
            'available': len(band_rows), 'selected': len(selected), 'cuts': cuts
        }
        result['total'] += len(selected)
    return result

simulations = [simulate(value) for value in (.50, .55, .60, .65, .70)]
simulation_table = []
for item in simulations:
    simulation_table.append([
        f\"前{(1-item['percentile']):.0%}\", item['total'],
        *[item['bands'][band]['selected'] for band in BANDS]
    ])
display(Markdown(markdown_table(
    ['同组高位口径', '合计', *[BAND_LABELS[band] for band in BANDS]], simulation_table
)))
"""
        ),
        nbf.v4.new_markdown_cell(
            """### 建议采用的影子规则

1. 先过绝对护栏：0—6天流动性不低于 2,000 美元；7—30天不低于 3,000 美元；31—90天不低于 5,000 美元；最近24小时至少 1 笔买入和 1 笔卖出。
2. 流动性达到同链、同五级日龄比较组的 P60；成交额、交易次数、成交额/流动性三项中至少两项达到 P60。
3. 同链同日龄有效对象少于 30 个时，不直接使用小样本分位数：10—29个与同日龄全链分布收缩合并；少于10个时暂用同日龄全链分布，并显示“比较组样本少”。
4. 以上只是第一条强证据路径的市场侧预筛。Gate 0 样本没有标准金额真实卖出报价，因此还必须补齐可卖性和滑点护栏，才能叫完整第一条路径。项目之后还必须满足另一条独立强路径、两类独立来源和严重异常为零，才能显示“凸性线索”。

P60 是相对宽松的高位口径。按当前可核验的更早交易对时间重新分层并排除超过 90 天后，本样本下 P60 市场侧影子预筛形成 49 个。采用 P60 可降低再次筛成零的风险。绝对美元数是影子下限，不是前台资格门槛。
"""
        ),
        nbf.v4.new_markdown_cell("## 4. 年龄分层与进入/退出防抖"),
        nbf.v4.new_markdown_cell(
            """| 日龄阶段 | 路径计算窗口 | 进入防抖建议 | 普通退出防抖建议 |
|---|---|---|---|
| 0—6天 | 可用生命期 + 6h/24h滚动窗口，与相同细日龄比较 | 最近3次中2次满足，观测至少间隔3小时 | 连续3次不满足才撤下路径 |
| 7—30天 | 24h指标 + 可取得的3日历史 | 最近3个日度结果中2次满足 | 连续2个日度结果不满足 |
| 31—90天 | 24h指标 + 可取得的7日历史 | 最近3个日度结果中2次满足 | 连续2个日度结果不满足 |

硬风险、T0越界、D类、明确不可卖或流动性归零不防抖，立即撤下资格。来源失败也不算普通退出：保留最后有效状态并标记过期/数据受限。

这组防抖是可实现的设计建议，不是 Gate 0 已验证结果。当前只有一个时间点，无法统计“2/3”和“连续2次”的真实误触发率；冻结前必须补至少3次间隔采集，或取得接口真实历史回放。
"""
        ),
        nbf.v4.new_markdown_cell("## 5. 另外三条强路径的当前边界"),
        nbf.v4.new_markdown_cell(
            """- **真实产品使用扩张**：Gate 0 没有项目合约映射、成功调用和独立地址时间序列，不能冻结增长百分比与绝对增量。
- **供应与需求结构改善**：Gate 0 没有持仓快照、非尘埃地址、头部集中度和净供应时间序列，不能冻结改善阈值。
- **活动超过估值变化**：当前只有单次 h1/h6/h24 滚动值，且同一聚合接口不构成独立证据，不能冻结相对增长阈值。

因此，下一步不是继续抬高或降低猜测数字，而是用 C2.1 独立预生产数据层对全部通过宽硬门槛的对象采集这些字段，再用同一套五日龄比较组定 P60 和异常分布。程序能够实现采集、分位数、收缩、防抖和复算；不能在输入尚不存在时科学地产生阈值。
"""
        ),
        nbf.v4.new_code_cell(
            """recommended = next(item for item in simulations if item['percentile'] == .60)
summary = {
    'schemaVersion': 'c2.1-age-threshold-analysis-v0.1',
    'asOf': profile['asOf'],
    'candidateRows': profile['candidateRowsSeen'],
    'candidateSha256': profile['candidateSha256'],
    'sampleRows': profile['selectedRows'],
    'states': dict(state_counts),
    'marketAvailableByGate0PoolEventAge': {
        band: {
            'sample': sum(row['gate0AgeBand'] == band for row in rows),
            'success': sum(row['gate0AgeBand'] == band and row['state'] == 'success' for row in rows),
        }
        for band in BANDS
    },
    'effectiveMarketSuccessByAge': {
        band: sum(row.get('effectiveAgeBand') == band and row['state'] == 'success' for row in rows)
        for band in BANDS
    },
    'effectiveOutside90Success': len(effective_outside_90),
    'distribution': distribution,
    'path1Simulations': simulations,
    'recommendedPath1': {
        'status': 'provisional_market_prefilter_not_full_path_not_frozen',
        'percentile': .60,
        'liquidityFloorsUsd': LIQUIDITY_FLOORS,
        'minimumBuysH24': 1,
        'minimumSellsH24': 1,
        'demandMetricsRequired': 2,
        'demandMetricsAvailable': 3,
        'selectedInCurrentMarketSample': recommended['total'],
    },
    'notCalibrated': [
        'standard_exit_quote', 'product_usage_expansion',
        'supply_demand_improvement', 'activity_outpaces_valuation',
        'multi_snapshot_hysteresis'
    ],
    'interpretationBoundary': (
        'Gate 0 pool-event age is only a lower bound; effective age uses the earliest market pair found. '
        'Gate 0 candidates are raw DEX discoveries, not C2.1 front-qualified projects. '
        'no_data is not zero, and this snapshot cannot validate multi-day persistence.'
    ),
}
(REPORT_DIR / 'analysis-summary.json').write_text(
    json.dumps(summary, ensure_ascii=False, indent=2) + '\\n', encoding='utf-8'
)
print(json.dumps({
    'analysisSummary': str(REPORT_DIR / 'analysis-summary.json'),
    'recommendedPath1Count': recommended['total'],
    'notCalibrated': summary['notCalibrated'],
}, ensure_ascii=False, indent=2))
"""
        ),
    ]
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
