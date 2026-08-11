# Gate 0 Luna 实现与自测交接

状态：`luna_self_test_complete_backfill_running_no_model_wait`

本轮由 `gpt-5.6-luna`、`max` 推理强度完成常规实现和开发自测。Gate 0 仍未宣称通过；完整 90 天扫描由独立 Python 进程继续运行，模型不等待其结束。

## 已完成

- 修复 `scripts/build_gate0_report.py` 在旧/测试摘要缺少 `requestSummary.total` 时的兼容性回归；缺少聚合值时从 `byState` 求和。
- 新增 `scripts/gate0_backfill_background.py`：固定窗口计划、EVM/Solana 分片、原子检查点、断点恢复、单实例锁、有限重试、额度/来源/不支持/配置/程序状态区分、分片清单、候选重算和最终校验。
- 新增 `scripts/build_gate0_backfill_progress.py`：固定进度页和 `artifact.json`；后台心跳每 30 秒以内刷新一次，页面自刷新 30 秒。
- 新增 `scripts/install_gate0_backfill_task.ps1`：只注册独立任务 `PenguinConvexity-Gate0-Backfill`，不触碰旧 C1.8 任务。
- 新增 `scripts/test_gate0_backfill_recovery.py`：三处游标恢复、完成分片跳过、损坏分片隔离、单实例锁、六类状态和真实 Gate 0 EVM 解码器小范围验收。
- 修复一个运行时边界：Blockscout 成功返回但达到分页上限时只缩小当前请求区间，不消耗普通来源失败重试预算。

## 自测结果

以下命令全部通过：

```text
python scripts/test_gate0_dex_factory_backfill.py
python scripts/test_gate0_shadow_preflight.py
python scripts/test_build_gate0_report.py
python scripts/test_gate0_backfill_recovery.py
python -m py_compile scripts/gate0_backfill_background.py scripts/build_gate0_backfill_progress.py scripts/test_gate0_backfill_recovery.py
```

真实计划短验收通过：六链、31 个已确认 schema、290 个分片、18 个明确未支持观察标签；没有规划失败。固定窗口为 `2026-05-10T14:07:10Z` 至 `2026-08-08T14:07:10Z`。

## 独立后台运行

- 任务：`PenguinConvexity-Gate0-Backfill`（Running/Ready 由 Windows 任务计划程序管理）。
- runId：`gate0-backfill-20260808T140710Z-a2588321`。
- 当前记录：`running`，`partition_scan`，启动后心跳持续更新；交接时为 `0/290` 完成分片，当前从 Ethereum `uniswap-v4-ethereum` 分片游标继续。
- 进度页：[reports/gate0-backfill-progress/report.html](../reports/gate0-backfill-progress/report.html)。
- 机器状态：`runtime/gate0-shadow/backfill/background/latest.json`。
- 固定计划：`runtime/gate0-shadow/backfill/background/runs/gate0-backfill-20260808T140710Z-a2588321/run-plan.json`。
- 完成判定：所有目标分片均有有效 `.complete.json`，事件身份无重复、解码失败为 0、无越界/未来时间、候选可由分片重算，且 `validation.json` 通过。未完成或失败分片不会被当作完成结果。

## 保护边界核验

- 未写入 `data/convexity.db`，未修改 C2.0 产品代码、页面或评分。
- 未读取、合并、删除已接受 29 天结果，也未恢复旧不可恢复 `.tmp` 运行。
- 未修改 `PenguinConvexity-C1.8-Scheduler`、其动作、触发器、启用状态或用户暂停设置；新任务独立注册。
- 18 个未确认 EVM 标签继续保持 `unsupported`，没有由 Luna 猜测协议语义。
- 实时稳定性 14 天仍只是并行证据，不是项目观察期，也不阻塞历史回扫或 C2.1 冻结。

## Sol 终验入口

Sol 需要独立核对：保护文件/数据库哈希、旧调度器前后快照、三处恢复与完成幂等、六类状态、进度页对账，以及后台终态 `completed` 或失败分片的真实原因。Sol 可以在 Gate 0 冻结范围内直接修复缺陷，但不得扩大协议、产品或 C2.1 范围。只有 Sol 完整验收通过后才能更新最终状态或发布。
