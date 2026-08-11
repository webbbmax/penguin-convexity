# Gate 0｜Luna 整版实现交接包

状态：已冻结，等待 Luna  
实现模型：`gpt-5.6-luna`，推理强度 `max`

## 1. 接手顺序

Luna 必须完整读取：

1. `AGENTS.md`
2. `docs/GATE0_PHASE.json`
3. `docs/GATE0_REQUIREMENTS_LOCK.json`
4. `docs/GATE0_PRD.md`
5. `docs/GATE0_DATA_CONTRACT.md`
6. `docs/GATE0_DESIGN_SPEC.md`
7. `docs/GATE0_ACCEPTANCE_PLAN.md`
8. `docs/GATE0_BASELINE_MANIFEST.json`
9. `docs/C2.1_PLANNING_STATUS.json`
10. `docs/MODEL_HANDOFF_PROTOCOL.md`

读取后先核验 Luna/max 双元数据；不满足时暂停常规编码。

## 2. 单一任务目标

在一个 Luna 任务内完成：

- 可恢复分片与原子检查点；
- 既有 29 天接受结果的只读复用；
- 独立后台运行和电脑重启恢复；
- 固定进度页；
- 六类状态与自动重试；
- 开发自测、小范围真实恢复演练；
- 只启动一次完整后台运行，然后结束模型任务。

不得把实现拆成多次 Luna 任务，也不得在启动后持续等待全量结果。

## 3. 允许修改

- `scripts/gate0_dex_factory_backfill.py`：只允许修改运行、分片、输出和恢复机制；协议解码语义须保持回归一致。
- `scripts/gate0_shadow_preflight.py`、`scripts/build_gate0_analysis_notebook.py`、`scripts/build_gate0_report.py`：只允许修复冻结测试与新进度引用需要的兼容问题。
- 现有 Gate 0 测试与新增 `scripts/test_gate0_backfill_recovery.py`。
- 新增 `scripts/gate0_backfill_background.py`、`scripts/install_gate0_backfill_task.ps1`、`scripts/build_gate0_backfill_progress.py`。
- `config/gate0-dex-backfill.json`：只允许增加恢复、分片、状态和后台任务参数，不改变协议字段语义。
- `runtime/gate0-shadow/backfill/background/` 与 `reports/gate0-backfill-progress/` 新输出。
- Gate 0 阶段、实现报告和自测报告。

## 4. 禁止修改

- C2.0 产品代码、正式需求锁、页面和快照。
- `data/convexity.db`。
- `PenguinConvexity-C1.8-Scheduler`、其脚本和用户暂停设置。
- RWA、旧整合项目和旧凸性源目录。
- 已接受运行 `dex-backfill-20260805T0630474393727Z` 中任何文件。
- 未完成运行 `dex-backfill-20260805T071018869628Z` 中任何文件。
- `runtime/gate0-shadow/backfill/schema-registry.json` 中的协议语义。
- 18 个未确认 EVM 标签的结构推断或新协议设计。
- C2.1 前后台产品编码、算法阈值和页面。

## 5. 实现步骤与验证

1. 先运行现有三组 Gate 0 测试，记录当前报告测试失败基线。
2. 写失败测试覆盖报告缺少 `requestSummary.total`、进程中断、恢复、分片损坏、双实例、额度等待和完成幂等。
3. 在现有解码器外围加入分片接口；固定样本输出必须逐字段不变。
4. 实现计划、检查点、锁、状态与分片完成清单。
5. 实现后台任务安装器；只创建 `PenguinConvexity-Gate0-Backfill`，核验旧调度器前后快照一致。
6. 实现进度页并完成四个视口和陈旧心跳测试。
7. 用模拟来源与小范围真实区间完成中断恢复演练，不启动完整扫描。
8. 全部开发测试通过后，固定一次完整窗口、启动后台任务、确认进度页出现有效心跳，然后结束模型任务。

## 6. Luna 完成状态

启动后台任务后，将阶段写为：

```text
luna_self_test_complete_backfill_running_no_model_wait
```

实现报告必须列出：修改文件、测试结果、后台任务名、运行 ID、进度页路径、固定窗口、如何判断完成、旧调度器未变证据和仍未支持的协议。

Luna 不等待数小时扫描结束，不宣称 Gate 0 通过，不要求用户继续同一任务。完整运行显示 `completed` 后，用户切换 Sol 并回复既有短口令“已切换 Sol，开始终验修复发布”。

