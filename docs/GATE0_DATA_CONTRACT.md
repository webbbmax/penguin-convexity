# Gate 0 可恢复回扫数据契约

状态：已冻结

## 1. 目录与文件

新后台运行只写入：

```text
runtime/gate0-shadow/backfill/background/
  latest.json
  lock.json
  runs/<runId>/
    run-plan.json
    checkpoints/
    partitions/<networkId>/<schemaId>/<partitionId>.jsonl
    partitions/<networkId>/<schemaId>/<partitionId>.complete.json
    failures.jsonl
    request-ledger.jsonl
    validation.json
    summary.json

reports/gate0-backfill-progress/
  report.html
  artifact.json
```

不得在 Sol 终验前覆盖：

- `runtime/gate0-shadow/backfill/latest.json`；
- `runtime/gate0-shadow/backfill/coverage-rollup.json`；
- 已接受 29 天运行目录；
- 未完成 90 天临时运行目录。

## 2. `run-plan.json`

计划创建后不可变，至少包含：

```text
schemaVersion
runId
createdAt
windowStart
windowEnd
windowDays
selectedNetworks[]
selectedSchemas[]
unsupportedDexLabels[]
configHashes{}
baselineRunIds[]
baselineFileHashes{}
partitions[]
```

每个 `partitions[]` 固定包含：

```text
partitionId
networkId
schemaId
source
fromBlockOrSlot
toBlockOrSlot
weight
state
```

运行中可以把分片拆小，但父分片总权重必须守恒，不能改变目标窗口或协议范围。

## 3. 分片完成清单

只有同时满足以下条件才生成 `.complete.json`：

```text
partitionId
completedAt
sourceState: success | no_data
rowCount
eventIdentityUnique
decodeFailures
minimumBlockOrSlot
maximumBlockOrSlot
minimumTimestamp
maximumTimestamp
sha256
requestCount
```

- `success` 可以包含大于 0 行；成功查询但真实为零必须记录 `no_data`。
- 数据文件先写临时文件、刷新并校验，再原子改名；完成清单最后写入。
- 存在完成清单且文件哈希一致的分片在恢复时不得重跑。

## 4. 检查点与游标

每个工作单元独立保存：

```text
workerId
partitionId
nextBlockOrSlot
lastSuccessfulAt
lastHeartbeatAt
requests
events
retryCount
recoveryCount
state
lastFailure{}
```

检查点采用原子替换。进程退出后，恢复从 `nextBlockOrSlot` 开始；不得只依据文件大小猜测游标。

## 5. 状态分类

所有请求、分片和运行状态必须映射到以下明确类别：

| 状态 | 含义 |
|---|---|
| `success` | 请求成功且返回可解析结果 |
| `no_data` | 请求成功，目标范围真实没有数据 |
| `quota_limited` | 429、日/月额度、并发或计费配额限制 |
| `source_failure` | 上游 5xx、超时、断线、响应截断或暂时不可用 |
| `unsupported` | 链、协议、计划权限或确定性解码结构不受支持 |
| `configuration_missing` | 凭据、端点或必需本地配置缺失/无效 |
| `program_failure` | 程序异常、断言失败、文件损坏或内部契约不成立 |

不得把额度、来源故障、不支持或程序失败统一写成“资料不足”。

## 6. `latest.json`

```text
schemaVersion
runId
state: preparing | running | quota_wait | retrying | paused | completed | failed
stage
startedAt
updatedAt
lastHeartbeatAt
windowStart
windowEnd
partitionProgress{}
networkProgress[]
schemaCoverage{}
requests{}
events
candidateTokens
currentWork{}
lastCheckpoint{}
recoveryCount
failureSummary{}
eta{}
```

`partitionProgress` 必须给出 `completedWeight/totalWeight` 与 `completedCount/totalCount`。结构不支持数量单列在 `schemaCoverage`，不得塞进扫描百分比。

## 7. 已接受 29 天结果复用

- 仅在文件哈希、结构哈希、网络、区间和事件身份校验全部一致时，登记为只读已完成覆盖。
- 新运行固定窗口后，只扫描该窗口相对既有接受区间的缺口；不得再次请求重叠区间。
- 合并时按 `(network, block/slot, transaction, instruction/log identity, schema)` 去重。
- 复用不改变原文件，不把原文件移动、分割或改写。

## 8. 完成条件

运行只能进入 `completed`，当且仅当：

- 冻结支持范围所有目标分片都有有效完成清单；
- 失败或待重试分片为 0；
- 行级身份重复、越界时间、未来时间、缺失池/代币和未完成解码均通过冻结验收；
- 候选代币最早事件可从分片重新计算且一致；
- `validation.json` 和 `summary.json` 原子生成。

完成仍不得自动覆盖官方 `backfill/latest.json`；只有 Sol 终验通过后才能提升为正式 Gate 0 结果。

