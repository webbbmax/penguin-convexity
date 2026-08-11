# 企鹅投研-凸性｜运行与恢复手册

## 1. 用户如何打开

双击 Windows 桌面的“企鹅投研-凸性”。软件首页直接进入凸性机会中心。重复双击只会激活已有窗口，不会再开一个本软件窗口；它可以与其他独立软件同时运行。

如果桌面快捷方式缺失，运行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "F:\codex项目\企鹅投研\凸性\scripts\install-desktop-shortcut.ps1"
```

## 2. 快速健康检查

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "F:\codex项目\企鹅投研\凸性\scripts\launch-convexity.ps1" -HealthCheck
```

预期显示本软件已就绪，地址为 `http://127.0.0.1:8766/desktop/index.html`。也可访问 `http://127.0.0.1:8766/api/health`，应看到产品名、M1.0、C2.1 体验、端口 8766；C2.1 发布时主库为 620 个项目、622 个案例，实时数量需和数据库、`docs/C2.1_ACCEPTANCE_MANIFEST.json` 核对。访问 `http://127.0.0.1:8766/api/c2.1/status` 查看 C2.1 更新频率、暂停、当前阶段、最近完成和下次新周期；`existingTaskMigration` 应为 `released_same_task`。`startupRebuild.state` 为 `running` 时表示页面已经可用、后台正在只读重建页面快照；为 `success` 表示本轮完成；为 `failed` 时继续使用旧快照并先查看服务日志。

## 3. 重要位置

- 数据库：`data/convexity.db`
- 数据库历史备份：`backups/`、`data/*.db` 和归档内的版本副本
- 桌面壳：`desktop/`
- 页面：`app/`
- 运行日志：`runtime/logs/server.stdout.log`、`runtime/logs/server.stderr.log`
- 缓存：`runtime/cache/`
- 窗口位置：`runtime/window-state.json`
- 采集游标：`data/source-discovery-cursors.json`
- 更新任务与守护状态：`data/update-runtime-status.json`
- C1.8 调度配置：`runtime/c1.8-scheduler.json`
- C1.8 调度状态：`runtime/c1.8-scheduler-state.json`
- C1.8 调度锁：`runtime/locks/c1.8-scheduler.lock`
- C1.8 调度安装脚本：`scripts/install-c1.8-scheduler.ps1`
- C1.8 无窗口调度启动器：`scripts/run-c1-8-scheduler-hidden.vbs`
- C2.1 独立数据库：`data/c2.1-pipeline.db`
- C2.1 更新配置：`runtime/c2.1/update-config.json`
- C2.1 调度与流水线状态：`runtime/c2.1/scheduler-state.json`、`runtime/c2.1/pipeline-status.json`
- C2.1 运行日志：`runtime/c2.1/logs/update-runner.log`
- C2.1 正式调度安装脚本：`scripts/install-c2.1-scheduler.ps1`
- C2.1 零窗口启动器：`scripts/run-c2-1-update-hidden.vbs`
- C1.7 数据主干快照：`app/data-backbone-snapshot.js`
- C1.7 基线备份：`backups/c1.7-00-baseline-20260801T042722Z/`
- 公共 API 无密钥目录快照：`config/shared-api-catalog.json`

## 4. 手工启动与停止

通常只用桌面快捷方式。开发检查时可直接运行：

```powershell
python "F:\codex项目\企鹅投研\凸性\scripts\serve_local.py" --port 8766
```

停止时只终止命令行中明确包含本项目 `scripts\serve_local.py` 绝对路径的进程；不得按 Python 名称批量终止，也不得影响旧项目或其他业务服务。

## 5. 公共 API 资源目录同步

只有公共资源目录、文档、凭据位置和额度说明发生变化时才运行：

```powershell
python "F:\codex项目\企鹅投研\凸性\scripts\sync_shared_api_catalog.py"
```

同步后检查 `config/shared-api-catalog.json` 不含 API 密钥、启用状态、游标、健康结果、日志、原始数据或任务状态。实际密钥只通过约定的环境变量/凭据位置读取。

## 6. 发布验收

```powershell
python "F:\codex项目\企鹅投研\凸性\scripts\test_complete_user_flow.py" --live
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "F:\codex项目\企鹅投研\凸性\scripts\test-convexity-window-state.ps1"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "F:\codex项目\企鹅投研\凸性\scripts\test-convexity-window-integration.ps1"
```

全量 Python 测试需运行 `scripts/test_*.py`，C2.1 发布时为 75/75 个测试文件退出码 0。主库和 `data/c2.1-pipeline.db` 都必须执行 `PRAGMA integrity_check`与`PRAGMA foreign_key_check`；发布时主库为 620/622，C2.1 为 4,587,762 条候选、157 个当前评估对象、12/12 宽硬门槛通过并前台可见。还需运行 `scripts/test_c2_1_release.py`、`scripts/test_c2_1_acceptance.py`、`scripts/test_complete_user_flow.py --live` 与 `scripts/test_c1_9_experience.py --live`；当前 31/31 个本地页面/资源通过。

真实点击验收必须只读。普通用户路径：机会首页 → 全部机会筛选分页 → 项目详情 → 返回恢复筛选和页码 → 重要变化 → 我们如何判断。维护者路径：前台右上角管理工作台 → 工作台概览 → 更新中心 → 聚合失败 → 返回机会中心。不要点击会触发更新、扫描、复核、状态回写或重建的按钮。

## 7. C2.1 自动增量与恢复调度

软件窗口关闭后，由本项目唯一 Windows 计划任务运行；任务名继续为 `PenguinConvexity-C1.8-Scheduler`，没有新增第二个长期任务。C2.1 发布迁移保留了用户的自动开启状态，并把旧“每日全量”准确映射为每 24 小时启动一次 C2.1 新周期。

计划任务每 15 分钟唤醒一次，只检查“是否到达用户选定的新周期时间”或“是否有已到冷却时间的未恢复失败”。这不等于每 15 分钟全量采集。已被后续完成结果覆盖的旧失败不再触发；尚未恢复的对象仍继续冷却重试。

计划任务必须由 `wscript.exe` 调用本项目隐藏启动器，不能直接把 `python.exe` 登记为任务动作。这样登录后补跑或整点运行时不会显示黑色控制台，同时仍保留调度脚本退出码和本项目状态记录。

先查看 C2.1 迁移计划，不注册任务：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "F:\codex项目\企鹅投研\凸性\scripts\install-c2.1-scheduler.ps1" -Install -DryRun
```

有 Windows 任务计划权限时注册、取消或手工运行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "F:\codex项目\企鹅投研\凸性\scripts\install-c2.1-scheduler.ps1" -Install
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "F:\codex项目\企鹅投研\凸性\scripts\install-c2.1-scheduler.ps1" -RunOnce -DryRun
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "F:\codex项目\企鹅投研\凸性\scripts\install-c2.1-scheduler.ps1" -Uninstall
```

也可在“凸性工作台 → 更新与任务”页顶部选择立即更新、仅手动、每小时、每3/6/12小时或每天，也可暂停自动更新或暂停当前任务。手动和自动使用同一数据库、游标、规则和原子快照。

回滚为旧 C1.8 入口属于有风险操作，必须先保留当前配置、状态、任务 XML 和数据库副本，并由用户明确授权后才可运行 `scripts/install-c1.8-scheduler.ps1 -Install`。

### 7.1 跟踪来源执行链健康标准

- 跟踪任务必须实际登记 `market-coingecko`、`market-dexscreener`、`contract-identity-mapping`、`security-goplus` 和 `chain-robinhood-blockscout` 的本轮状态。
- 单个项目没有适用来源时显示 `no_data`，不算失败；只有该项目自己的来源调用失败才能标记部分完成或失败。不要把来源全局失败数量复制给全部项目。
- 跟踪完成后，`app/tracking-tasks-snapshot.js` 中该项目应从 `due` 变为 `open`，并出现新的 `nextReviewAt`。
- `raw_events`、`normalized_events_v2` 和带有 `raw_locator`/`content_hash` 的可溯源事件数量必须一致；不一致时先运行本地增量数据主干重放，禁止删除原始事件。
- 长任务运行时 `data/update-runtime-status.json` 的心跳应约每 10 秒更新，阶段编号不得减小。外部来源等待可以保持当前阶段，但不能停止心跳。
- 2026-08-03 修复证据、真实运行 ID、数据库哈希和恢复点见 `docs/C1.9_TRACKING_SOURCE_CHAIN_FIX.md`。

## 8. C1.7 数据主干更新与检查

日常使用优先从“凸性工作台 → 更新中心 → 最大漏斗数据主干”运行。该任务会更新本项目内的标准事件、游标、来源健康、孤儿证据归属、实体关系和 Watcher，不会修改评分、结论或动作。

开发或恢复检查可运行：

```powershell
python "F:\codex项目\企鹅投研\凸性\scripts\data_backbone.py" --mode incremental
```

需要同时采集 GitHub 发布和包清单时使用 `--collect-software`；程序只从约定环境变量读取凭据，不在输出、日志或快照中记录密钥。`--mode replay` 用于幂等重放，`--mode gap_recovery` 只用于明确的断档恢复。执行写操作前先按下一节备份数据库。

数据主干验收应同时检查：原始数等于标准事件数、可追溯覆盖完整、积压为 0、开放断档为 0、真实零结果与异常分开、孤儿证据仍可定位、五条主线的登记/可运行/真实事件没有混写。

## 9. 备份

在数据模型、正式更新或恢复前：

1. 确认服务没有正在写数据库。
2. 把 `data/convexity.db` 复制到 `backups/` 下新的时间戳文件，不覆盖旧备份。
3. 记录数据库 SHA-256、字节数、项目数、证据数和溯源数。
4. 用只读连接执行 SQLite 完整性和外键检查。

## 10. 恢复

恢复是有风险操作，必须由用户明确指定备份：

1. 停止且只停止本项目服务。
2. 先把当前 `data/convexity.db` 复制为新的恢复前备份。
3. 对选定备份做哈希、完整性、外键和该备份对应版本的项目/机会案例数量核验；M1.0 是 585/585，C1.7-00 是 589/590。
4. 将选定备份复制到 `data/convexity.db`，不要删除原备份。
5. 启动本软件，执行健康检查、30 路由、数据库和真实点击验收。

不得通过移动旧项目文件或建立跨目录链接完成恢复。

## 11. 常见问题

- 桌面打不开：先运行健康检查，再查看 `runtime/logs/server.stderr.log`。
- 8766 被占用：先确认占用进程是否为本项目；若不是，不要强行终止，记录冲突并调整环境后重试。
- 页面有数据但导航无响应：检查浏览器控制台、iframe 当前 URL 和本地 30 路由；不能只靠静态文件判断。
- 数据主干显示“尚待判断”：表示该来源缺少足够运行记录，不等于来源正常，也不等于真实零事件；先检查更新中心任务与来源健康诊断。
- 孤儿证据数量不为零：只表示暂时缺少可靠身份锚点，原始记录仍在；不得按名称猜测归属或删除原始记录。
- 筛选丢失：检查机会中心自己的本地状态键与桌面壳状态是否相互独立。
- 窗口跑到屏幕外：删除或修复 `runtime/window-state.json`；启动器会把无效位置收回当前显示器。
- 第一次打开等待：先检查 `/api/health` 的 `startupRebuild`。C2.1 中该字段保留兼容名称，实际只校验已原子发布的 C2.1 前后台快照，不重建旧 C1.x/C2.0 快照，不运行旧守护恢复，不得写 `data/convexity.db`。
- 桌面弹出“不能对 Null 值表达式调用方法”：检查 `scripts/launch-convexity.ps1` 是否按 `experienceRelease=C2.1` 和 `c2-1-front.js` 判定健康，并确认空 `server.stderr.log` 使用 `IsNullOrWhiteSpace` 处理；修复后必须从正式桌面 `.lnk` 重跑冷启动、单实例和窗口恢复，不得只跑脚本静态断言。
- 自动运行未安装：在更新中心看到“Windows 自动任务未安装”时，先运行第 7 节 C2.1 安装脚本的 `-Install -DryRun`，确认范围后再执行 `-Install`。用 `Get-ScheduledTask PenguinConvexity-C1.8-Scheduler` 验证状态为 Ready；不要把 dry-run 当作已安装。
- 自动运行弹出标题为 Python 路径的黑框：这是任务动作直接执行 `python.exe`，不是产品窗口。先等待已经开始的任务结束，再重新运行 C2.1 `-Install`；随后任务动作应为 `wscript.exe`和 `run-c2-1-update-hidden.vbs`，不得按 Python 进程名批量关闭。
- 15 分钟内反复启动更新：先检查 `runtime/c2.1/scheduler-state.json` 的 `nextRunAt` 和 `data/c2.1-pipeline.db` 中尚未被新完成结果覆盖的失败游标。已覆盖的旧失败不应触发；不得为了停止误触发而删除游标或失败记录。
- 跟踪任务显示大量失败：先在更新中心确认失败是否都来自同一批所需来源。本轮 2026-08-03 14:00 的 104 项失败是行情、合约身份或安全来源未在该轮执行；旧结论仍被保留。不要直接重跑全部更新或把失败改成成功，应先确认来源任务可执行，再按聚合范围单项重试。修复来源执行链属于独立任务，不能借机修改评分或研究规则。

## 12. 版本阶段与模型选择

C2.1 已发布，最终阶段保存在 `docs/C2.1_PHASE.json`，完整放行证据在 `docs/C2.1_ACCEPTANCE_MANIFEST.json` 和 `docs/C2.1_RELEASE_MANIFEST.json`。C2.0 继续作为上一冻结回滚基线。自 2026-08-10 起，使用什么模型、何时切换由用户决定；模型名称不再构成阶段门禁。

- 需求规划、正式冻结、产品实现、开发自测、独立终验和发布仍是不同阶段。
- 用户明确冻结前不得开始产品编码；冻结后必须先核验需求锁哈希。
- 开发自测不能代替独立完整终验；终验中修复缺陷后必须重跑受影响验收和完整发布门槛。
- 任何阶段都不得借实现或修复扩大范围、改变评分/动作/L0-L5或擅自开始新版本。

C1.8、C1.9、C2.0 和 C2.1 维护必须分别验证对应需求锁。哈希不一致时停止，等待用户明确解冻，不得按被修改的需求继续。

## 13. C2.0 上一冻结版本与兼容入口

- 前台入口：`candidate-pool.html`。四个导航分别是机会首页、全部机会、重要变化、我们如何判断；项目详情为 `project-detail.html` 上下文页面。
- 后台入口：`workbench.html`。七个一级栏目由 `workbench-nav.js` 统一生成，版本号只允许在浅色左栏底部出现一次。
- 角色清单：`docs/C2.0_ROUTE_ROLES.json`。未来大众构建必须排除所有 `admin` 页面和本地管理入口。
- C2.0 前台与后台快照：`app/decision-signals-snapshot.js` 和 `app/decision-quality-snapshot.js`；两者必须由 `scripts/build_decision_quality_snapshots.py` 同一构建原子生成，并共享构建 ID、生成时间、来源快照时间和输入运行 ID。
- 判断质量入口：`decision-quality.html`，属于“模型与规则”。它只读显示七类漏斗、质量指标、阻断排行和闭环队列，不修改评分、动作、仓位或 L0-L5。
- 长任务状态：`data/update-runtime-status.json`。七阶段遥测只描述运行状态，不改变数据库提交、采集结果或研究结论；遥测写入失败只记后台警告。
- 页面显示“可能卡住”时，先比较最近心跳和系统时间，再查看 `runtime/logs/`；不要因为总量未知而推算伪百分比。
- 页面显示“本轮失败”或“部分完成”时，先查看聚合来源、影响范围和保留的上次有效结果，再决定单项重试；不得从历史失败总量推导当前影响范围。

## 14. C2.1 正式运行边界

C2.1 已完成 58/58 项独立验收并发布，阶段为 `independent_full_acceptance_complete_released`。需求集 SHA-256 为 `f8784fb3ee03df42d56171f97dfb37707107f50fcbceba4647764a98a16b6696`；维护前必须重算需求锁中八份正式文件哈希。

- C2.1 新写入库只能是 `data/c2.1-pipeline.db`；`data/convexity.db` 对 C2.1 只读。软件启动只校验 C2.1 原子快照，不构建 C1.7/C2.0 兼容快照，不调用旧更新守护恢复，不得因启动刷新主库运行记录。
- 不重跑 Gate 0 已接受 90 天区间，不恢复一次性 Gate 0 任务，不新建第二个长期调度器。
- 当前自动开启、未暂停、新周期每 24 小时一次。任务每 15 分钟唤醒只是到期/恢复检查，不得写成每 15 分钟采集。
- 发布不足 14 天的项目使用从真实 T0 到当前的已发生历史；尚未发生日期不计缺失、不计零、不要求等待。
- 合格前台项目为 A/B/C；D 只有代币不进前台，C 不得冒充身份已确认。综合分只用于排序、变化和后续校准，不单独定义凸性线索。
- 程序只能形成自动研究线索，不能证明项目靠谱、市场低估、未来上涨或一定能退出，不自动交易。

## 15. C2.1 发布后观察与恢复

- 当前无需用户操作。首要任务是只读观察第一个自然每日增量周期，不是项目观察期，不阻塞任何项目或开发。
- 核对 `pipeline-status.json` 为完成、计划任务 LastTaskResult 为 0、候选增量和快照构建 ID 发生预期变化；无数据、额度受限、来源失败、不支持、缺配置和程序失败必须分开。
- 任务失败时先保留旧快照和已提交游标；不得从 Gate 0 90 天起点重跑，不得删除失败记录来停止重试。
- 回滚必须有用户明确授权。使用 `backups/c2.1-release-migration-20260810T2033` 前先备份当前 C2.1 数据库、配置、状态和任务 XML；不得为了哈希一致回滚真实数据。
