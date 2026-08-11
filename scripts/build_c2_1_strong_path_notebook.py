import argparse
from pathlib import Path

import simple_notebook as nbf


DEFAULT_OUTPUT = Path(
    "reports/c2.1-strong-path-input-probe/strong-path-input-analysis.ipynb"
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
            """# C2.1 四条强证据路径真实输入补测

## 结论先行

- 本次只补测用户已接受的 **P60 + 2,000/3,000/5,000 美元护栏**所形成的 49 个市场侧影子样本；没有冻结 C2.1，没有修改 C2.0 或生产数据库。
- 标准卖出定义为：按 CoinGecko Onchain 主池现价计算约 100 美元代币，向真实路由器询问 ExactIn 卖出至 USDC/USDT 的只读报价；报价不执行，也不保证成交。
- 另外三条路径不因“有一个当前值”就算成立：产品使用需要项目身份映射和至少两个真实时间点；供应改善需要至少两个持仓/供应快照；活动超过估值需要可比时间窗，并处理供应变化与池子范围。
- `no_data`、`unsupported`、`configuration_missing`、`source_failure` 和项目风险严格分开，缺失不补零。
"""
        ),
        nbf.v4.new_code_cell(
            """import json, math, statistics
from collections import Counter
from pathlib import Path
Markdown = str
display = print

REPORT_DIR = Path.cwd()
if REPORT_DIR.name != 'c2.1-strong-path-input-probe':
    REPORT_DIR = Path('reports/c2.1-strong-path-input-probe')

def latest(name):
    result = {}
    for line in (REPORT_DIR / name).read_text(encoding='utf-8').splitlines():
        if line:
            row = json.loads(line)
            result[(row['networkId'], row['tokenAddress'])] = row
    return result

def q(values, probability):
    clean = sorted(float(value) for value in values if value is not None and math.isfinite(float(value)))
    if not clean:
        return None
    position = (len(clean) - 1) * probability
    low = int(position); high = min(low + 1, len(clean) - 1); fraction = position - low
    return clean[low] * (1 - fraction) + clean[high] * fraction

def table(headers, rows):
    lines = ['| ' + ' | '.join(headers) + ' |', '| ' + ' | '.join(['---'] * len(headers)) + ' |']
    lines.extend('| ' + ' | '.join(str(value) for value in row) + ' |' for row in rows)
    return '\\n'.join(lines)

profile = json.loads((REPORT_DIR / 'sample-profile.json').read_text(encoding='utf-8'))
market = latest('market-inputs.jsonl')
quotes = latest('quote-observations.jsonl')
supply = latest('supply-inputs.jsonl')
helius = latest('helius-supply-inputs.jsonl')
product = latest('product-inputs.jsonl')
history_capability = json.loads((REPORT_DIR / 'supply-history-capability.json').read_text(encoding='utf-8'))
BANDS = ['age_0_2','age_3_6','age_7_13','age_14_30','age_31_90']
BAND_LABELS = {'age_0_2':'0-2天','age_3_6':'3-6天','age_7_13':'7-13天','age_14_30':'14-30天','age_31_90':'31-90天'}
"""
        ),
        nbf.v4.new_markdown_cell("## 1. 样本与输入完整性"),
        nbf.v4.new_code_cell(
            """quality = [
    ['影子样本', profile['selectedRows']],
    ['五个日龄层', ' / '.join(str(profile['selectedByAge'].get(band, 0)) for band in BANDS)],
    ['市场/日线输入', f\"{len(market)}/{profile['selectedRows']}\"],
    ['标准卖出报价记录', f\"{len(quotes)}/{profile['selectedRows']}\"],
    ['GoPlus供应输入', f\"{len(supply)}/{profile['selectedRows']}\"],
    ['产品使用映射输入', f\"{len(product)}/{profile['selectedRows']}\"],
]
display(Markdown(table(['检查项','结果'], quality)))
assert profile['selectedRows'] == 49
assert all(profile['selectedByAge'].get(band, 0) > 0 for band in BANDS)
assert len(market) == len(quotes) == len(supply) == len(product) == 49
"""
        ),
        nbf.v4.new_markdown_cell("## 2. 标准卖出报价：真实路由覆盖与数据异常"),
        nbf.v4.new_code_cell(
            """quote_states = Counter(row['state'] for row in quotes.values())
quote_by_network = []
for network in sorted({row['networkId'] for row in quotes.values()}):
    rows = [row for row in quotes.values() if row['networkId'] == network]
    states = Counter(row['state'] for row in rows)
    quote_by_network.append([network, len(rows), states.get('success',0), states.get('no_data',0), states.get('unsupported',0), states.get('source_failure',0)])
display(Markdown(table(['网络','影子样本','真实报价','无路线/无数据','当前不支持','来源失败'], quote_by_network)))

quote_success = [row for row in quotes.values() if row['state'] == 'success']
losses = [row.get('quoteLossPct') for row in quote_success]
mismatches = [row.get('sourceValuationMismatchPct') for row in quote_success if row.get('sourceValuationMismatchPct') is not None]
quote_metrics = [
    ['真实路由报价', len(quote_success)],
    ['无路线/无数据', quote_states.get('no_data',0)],
    ['当前无已验证路由器', quote_states.get('unsupported',0)],
    ['100美元卖出损耗中位数', f\"{q(losses,.50):.2f}%\"],
    ['100美元卖出损耗P75', f\"{q(losses,.75):.2f}%\"],
    ['100美元卖出损耗最大值', f\"{max(losses):.2f}%\"],
    ['两种价格源偏差绝对值>25%', sum(abs(value) > 25 for value in mismatches)],
]
display(Markdown(table(['指标','结果'], quote_metrics)))
"""
        ),
        nbf.v4.new_markdown_cell(
            """### 解释

- 34/49 能取得真实卖出路线；12 个返回 `no_data`，表示当前没有可用路线或路由器未覆盖，不能算作零滑点；Robinhood 3 个为 `unsupported`，因为当前资源库没有经过验证的真实路由聚合器。
- 卖出损耗使用“100 美元目标金额 − 实际稳定币输出”计算。路由器自身对输入代币的美元估值只用于检查价格源冲突，不能当作损耗分母。
- 价格源冲突较大时，程序可以把它列为数据异常并拒绝形成完整第一路径；本次不据此冻结 25% 或任何正式阈值。
- 报价证明“询价时存在路线”，不证明最终交易一定成功；仍需 GoPlus 的蜜罐、卖出税、不可全部卖出等风险字段共同约束。
"""
        ),
        nbf.v4.new_markdown_cell("## 3. 真实产品使用扩张"),
        nbf.v4.new_code_cell(
            """product_states = Counter(row['state'] for row in product.values())
real_product_series = [row for row in product.values() if row['state'] == 'success']
usable_product = [row for row in real_product_series if row['identityState'] == 'verified_local_contract_mapping']
provisional_product = [row for row in real_product_series if row['identityState'] == 'provisional_platform_domain_mapping']
product_rows = [
    ['真实业务序列且身份映射已确认', len(usable_product)],
    ['真实业务序列但身份仅为平台域名线索', len(provisional_product)],
    ['未建立确定映射或无产品序列', product_states.get('no_data',0)],
]
display(Markdown(table(['输入状态','项目数'], product_rows)))
if real_product_series:
    display(Markdown(table(
        ['网络','协议数据源标识','真实时间点','身份状态','证据边界'],
        [[row['networkId'], row['defiLlamaSlug'], len(row['tvl']), row['identityState'], 'TVL是产品金融使用证据，不等于全部产品使用'] for row in real_product_series]
    )))
"""
        ),
        nbf.v4.new_markdown_cell(
            """49 个影子样本中只有 1 个通过聚合平台展示的官网域名匹配到 DefiLlama，并取得 66 个真实 TVL 时间点；但本地没有已确认的合约—项目映射，所以它仍只是产品证据线索，不能算第二条强路径。其余 48 个是 `no_data`，不是产品没有使用，而是程序还不能确定“代币—项目—产品数据”的身份关系。

因此第二条路径的程序上限很明确：**一旦存在确定身份映射和真实业务时间序列，增长可以自动计算；没有映射时必须放弃判断。** GitHub 仓库只能证明代码存在，不能替代真实产品使用。
"""
        ),
        nbf.v4.new_markdown_cell("## 4. 供应与需求结构改善"),
        nbf.v4.new_code_cell(
            """market_holder_count = sum(row.get('holderCount') is not None for row in market.values())
goplus_success = [row for row in supply.values() if row['state'] == 'success']
top_comparisons = []
holder_comparisons = []
percent_over_100 = 0
for identity, row in market.items():
    other = supply.get(identity) or {}
    try:
        top_comparisons.append(float(other['reportedTopHolderPct']) - float(row['holderDistributionPct']['top_10']))
        if float(other['reportedTopHolderPct']) > 100.0001:
            percent_over_100 += 1
    except (KeyError, TypeError, ValueError):
        pass
    try:
        holder_comparisons.append((float(other['holderCount']) / float(row['holderCount']) - 1) * 100)
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        pass
supply_rows = [
    ['CoinGecko当前持有人快照', market_holder_count],
    ['GoPlus当前供应/头部持仓快照', len(goplus_success)],
    ['Helius Solana当前供应与前20代币账户', len([row for row in helius.values() if row['state']=='success'])],
    ['两来源头部集中度可比较', len(top_comparisons)],
    ['GoPlus头部比例合计超过100%的源数据异常', percent_over_100],
    ['历史持有人序列', 0],
]
display(Markdown(table(['输入','项目数'], supply_rows)))
"""
        ),
        nbf.v4.new_markdown_cell(
            """当前供应横截面可以自动取得，而且 EVM 有 CoinGecko + GoPlus 两类来源，Solana 有 CoinGecko + Helius。GoPlus 返回的两条样本中，头部账户比例合计超过 100%，已保留为来源异常，不能自动裁成 100%。

但“改善”必须比较至少两个真实时间点。当前 CoinGecko 历史持有人接口状态为 `unsupported`，原因是当前套餐不包含该接口。因此本次只能建立真实基线，**不能把供应与需求结构改善判为已成立**。程序后续可以保存增量快照并自动比较；若要求对刚收录的老项目立即回看历史，则仍需另行验证历史重建成本或增加可返回历史的来源。
"""
        ),
        nbf.v4.new_markdown_cell("## 5. 活动超过估值变化"),
        nbf.v4.new_code_cell(
            """window_days = {'age_0_2':1,'age_3_6':1,'age_7_13':3,'age_14_30':3,'age_31_90':7}
activity = []
for identity, row in market.items():
    candles = sorted(row.get('ohlcv') or [], key=lambda item: item[0])
    width = window_days[row['effectiveAgeBand']]
    if len(candles) < 2 * width:
        continue
    previous = candles[-2*width:-width]
    current = candles[-width:]
    previous_volume = sum(float(item[5]) for item in previous) / width
    current_volume = sum(float(item[5]) for item in current) / width
    previous_close = float(previous[-1][4]); current_close = float(current[-1][4])
    volume_log_change = math.log((current_volume + 1) / (previous_volume + 1))
    price_log_return = math.log(current_close / previous_close) if current_close > 0 and previous_close > 0 else None
    activity.append({
        'identity': identity,
        'ageBand': row['effectiveAgeBand'],
        'volumeLogChange': volume_log_change,
        'priceLogReturn': price_log_return,
        'activitySurplusCandidate': volume_log_change - abs(price_log_return) if price_log_return is not None else None,
    })
activity_by_age = Counter(row['ageBand'] for row in activity)
display(Markdown(table(
    ['日龄','比较窗口','可计算项目'],
    [[BAND_LABELS[band], f\"前后各{window_days[band]}天\", activity_by_age.get(band,0)] for band in BANDS]
)))
"""
        ),
        nbf.v4.new_markdown_cell(
            """49/49 都有足以形成相邻窗口的真实主池日线。程序可以采用对数变化压缩极端成交额，例如：

`活动变化 = ln((本窗口日均成交额 + 1) / (前窗口日均成交额 + 1))`

`估值变化候选 = |ln(本窗口末价格 / 前窗口末价格)|`

但本次只能叫“候选输入”，不能冻结成第四条完整路径，原因有两个：

1. OHLCV 只来自当前选定主池，尚未汇总代币全部池子；迁池可能造成假变化。
2. 当前没有历史总供应序列，价格变化不一定等于 FDV/市值变化；可增发或销毁时尤其不能替代。

因此程序已经能计算“主池活动相对价格变化”，但还不能对所有项目科学地宣称“活动超过估值变化”。
"""
        ),
        nbf.v4.new_markdown_cell("## 6. 可冻结与不可冻结边界"),
        nbf.v4.new_code_cell(
            """summary = {
    'schemaVersion': 'c2.1-strong-path-input-analysis-v0.1',
    'status': 'planning_probe_not_frozen_not_product_coded',
    'sample': profile,
    'standardSellQuote': {
        'notionalUsd': 100,
        'success': quote_states.get('success',0),
        'noData': quote_states.get('no_data',0),
        'unsupported': quote_states.get('unsupported',0),
        'sourceFailure': quote_states.get('source_failure',0),
        'medianQuoteLossPct': q(losses,.50),
        'p75QuoteLossPct': q(losses,.75),
        'maxQuoteLossPct': max(losses),
        'priceSourceMismatchAbsGt25CountDiagnosticOnly': sum(abs(value) > 25 for value in mismatches),
    },
    'productUsage': {
        'verifiedMappedSeries': len(usable_product),
        'provisionalPlatformDomainSeries': len(provisional_product),
        'noData': product_states.get('no_data',0),
    },
    'supplyDemand': {
        'currentCoinGeckoSnapshots': market_holder_count,
        'currentGoPlusSnapshots': len(goplus_success),
        'currentHeliusSnapshots': len([row for row in helius.values() if row['state']=='success']),
        'historicalImprovementComparable': 0,
        'historicalHolderEndpoint': history_capability,
        'sourcePercentOver100Anomalies': percent_over_100,
    },
    'activityVsValuation': {
        'primaryPoolWindowComparable': len(activity),
        'tokenWideAggregated': 0,
        'historicalSupplyAdjusted': 0,
    },
    'decision': {
        'path1MarketPrefilter': 'accepted_shadow_only',
        'path1StandardQuoteInput': 'feasible_on_5_networks_with_no_data_and_robinhood_unsupported_visible',
        'path2ProductUsage': 'program_feasible_only_when_deterministic_identity_mapping_and_real_series_exist',
        'path3SupplyDemandImprovement': 'current_baseline_feasible_historical_improvement_not_yet_proven',
        'path4ActivityOutpacesValuation': 'primary_pool_price_proxy_feasible_full_token_and_supply_adjustment_not_yet_proven',
        'formalThresholdsFrozen': False,
    },
}
(REPORT_DIR / 'analysis-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\\n',encoding='utf-8')
print(json.dumps(summary['decision'],ensure_ascii=False,indent=2))
"""
        ),
        nbf.v4.new_markdown_cell(
            """### 当前建议

1. 可以把“100 美元真实卖出报价”的字段、状态和价格源冲突规则写进 C2.1 正式数据契约，但滑点/损耗数值门槛仍需讨论，不能由这 34 条成功报价直接拍板。
2. 产品使用路径保留，但必须采用“确定身份映射 + 真实业务序列；否则放弃判断”的窄口径。
3. 供应改善暂不作为首日可判定路径；首日只建立当前基线。若产品定位要求所有年龄项目立刻有历史判断，应先解决历史供应数据源。
4. 活动超过估值路径可先定义数据契约，正式算法必须补“全池汇总”和“供应变化修正/不可修正时放弃”。
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
