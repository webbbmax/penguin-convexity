# Gate 0｜Sol 重复扫描纠正与加速记录

状态：`superseded_by_gate0_sol_final_acceptance`  
修复时间：2026-08-09 07:55（Asia/Shanghai）  
模型门禁：`gpt-5.6-sol / xhigh` 双元数据一致

> 终态更新：纠正运行已完成，最终结果由 `gate0-solfinal-20260809T045924Z-f7bbd2` 承接并通过 Sol 独立终验。当前状态以 `docs/GATE0_PHASE.json`、`docs/GATE0_ACCEPTANCE_MANIFEST.json` 和 `docs/GATE0_SOL_FINAL_ACCEPTANCE.md` 为准；本文件保留为修复过程记录。

## 1. 已确认缺陷

- 原后台运行 `gate0-backfill-20260808T140710Z-a2588321` 没有复用已接受的 29 天 Solana 结果，已向重叠区间发送网络请求，不能通过 C03。
- 原进度页的区块权重百分比被误当成耗时百分比，造成“需要十多天”的错误观感。
- 原请求账本没有保存网络、schema 和请求区间，无法直接审计重叠请求。

原运行已在 70/290 分片、14,210 次请求、2,441,211 条事件处停止。运行目录、分片、检查点和账本全部保留，不删除、不覆盖、不提升为正式结果。

## 2. 修复结果

新运行：`gate0-backfill-20260808T235243Z-8929b37d`。

- 固定窗口保持 `2026-05-10T14:07:10Z` 至 `2026-08-08T14:07:10Z`，没有因修复漂移。
- 65 个已验证分片以只读方式复用，其中包括冻结接受的 29 天 Solana 结果和原运行中完全位于非重叠区间的完成分片。
- Solana 实际联网缺口缩减为 4 个分片：
  - `409332669—409383766`
  - `415272800—415472799`
  - `415472800—415672799`
  - `415672800—415853249`
- 接受区间 `409383767—415272799` 的新运行网络请求数为 0。
- 每次新请求写入网络、schema、分片、范围和状态；最终校验会把接受区间重叠请求数大于 0 直接判为 `program_failure`。
- 只读文件哈希不一致时直接阻断，禁止降级为联网重扫。
- 进度页同时显示“全部覆盖、仍需联网、只读复用”，并明确区块权重不是 ETA。

本次没有增加后台并发。正确复用已把剩余慢速 Solana 分片从约 27 个降为 4 个；此时改造共享账本、锁和状态聚合的并发风险高于预计节省，保留串行恢复语义更稳妥。历史速度下预计后台联网阶段约 1—3 小时，但上游速度不作保证。

## 3. 验证

以下短测试全部通过：

```text
python scripts/test_gate0_dex_factory_backfill.py
python scripts/test_gate0_shadow_preflight.py
python scripts/test_build_gate0_report.py
python scripts/test_gate0_backfill_recovery.py
python -m py_compile scripts/gate0_backfill_background.py scripts/build_gate0_backfill_progress.py scripts/test_gate0_backfill_recovery.py
```

真实启动检查：

- Windows 独立任务已启动；
- 65/65 只读复用分片有效；
- 新账本首批 Base 请求带完整范围；
- 接受 29 天区间重叠请求为 0；
- 固定进度页心跳正常。

## 4. 保护边界与剩余事项

- C2.0 需求锁、已接受 Solana 事件文件、摘要和验证文件哈希不变。
- `PenguinConvexity-C1.8-Scheduler` 保持启用、每小时运行、动作不变。
- `data/convexity.db` 当前哈希与 2026-08-08 21:36 的 Gate 0 冻结快照不同；数据库最后写入为 2026-08-08 22:21，早于本次 Sol 修复。Gate 0 工具没有读写或恢复数据库，最终验收必须把它识别为冻结后业务调度变化，不能归因于本次回扫，也不能擅自回滚。
- 旧 Codex `gate-0` 小时心跳自动化已删除；独立 Python 任务和本地进度页继续运行，不再消耗模型轮询额度。
- 完整运行尚未结束，当前不得宣称 Gate 0 通过或发布完成。

## 5. Robinhood 跨分片查询跨度修复

2026-08-09 09:50（Asia/Shanghai）确认：Robinhood 上游默认为已认证的 Blockscout Pro，其能执行大区间历史日志查询；Alchemy 免费层在同一实测中限制 `eth_getLogs` 为最多 10 个区块，不适合 90 天回扫，因此没有切换 API。真正的性能缺陷是：每个 200 万区块父分片都会从大跨度重新试错，没有继承前一分片已学到的安全跨度。

修复后：

- 按 `networkId + schemaId` 原子保存已验证查询跨度，新父分片从该值继续，不再每次从 200 万区块起步。
- 返回量触发上游截断时立即缩小并保存；低返回量时可逐步增长，交通故障缩小后同样保存。
- 运行时状态增加 `learnedBlockSpan`，可直接看到当前学习值。
- 原运行 `gate0-backfill-20260808T235243Z-8929b37d` 从 162/262 分片恢复，未更换 runId，未回退进度。
- Bankr 规则首次一次性收敛到 23,437 区块，Pons 规则从既有请求证据继承 31,250 区块；后续同规则分片直接复用。
- 同一时间只有 1 个独立 Python 工作进程，Windows 任务处于运行状态。

回归测试新增“同网络同规则跨父分片继承”和“上下限夹取”检查；四项 Gate 0 短测试及 Python 语法检查全部通过。
