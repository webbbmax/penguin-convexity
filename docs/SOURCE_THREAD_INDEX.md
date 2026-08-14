# 源任务索引

本文件用于在需要精确追溯需求时定位旧任务，不构成运行时依赖。旧任务与旧项目均可不可用；本项目仍须独立运行。

## 主源任务

- 任务 ID：`019f3a65-a5d0-70f0-b92e-53c120d8d0a5`
- 主题：企鹅投研旧整合任务，包含 C1.0-C1.6 建设、发布验收、C1.7 冻结框架和 M1.0 分离决策。
- 本项目已把持续需要的规则固化到 `AGENTS.md`、`docs/PROJECT_MEMORY.md` 和 `docs/DECISIONS.md`；日常工作不应依赖重新加载该任务。

## 关键回合

- `019fb742-d340-7011-9ee5-5b8c75494113`：冻结 C1.7“最大漏斗数据主干一期”九步框架与验收边界。
- `019fbb40-5002-7401-a70d-2603dcbda5ee`：M1.0 独立迁移、复制校验、桌面壳和端到端验收要求。
- `019fbb46-fab5-74a0-82f2-ff90fc8434fa`：公共 API 资源共享边界；业务启用、游标、健康、日志和原始数据不得共享。
- `019fbb56-d573-7472-a248-47f7d5728dad`：凸性、其他业务与公共 API 资源库拆分为独立任务/目录。
- `019fbb58-4f25-7020-b12a-95354a898315`：用户明确确认继续 C1.7；完成最大漏斗数据主干一期、全量测试、真实页面点击、桌面单实例与窗口记忆验收。
- 2026-08-02 当前项目任务：用户确认冻结 C1.8“机会中心决策体验与自动跟踪闭环”，指定 Luna 实现、Sol 最终独立验收，并要求提供防忘记切换的固定交接提示语。需求已完整固化到下列文件，不依赖重新加载任务历史。
- 2026-08-02 后续职责更新：Luna 只负责普通功能编程和开发自测；Sol 负责问题修复、阻断清理、独立复验和最终发布。C1.8 首轮终验阻断已由 Sol 在冻结范围内修复，最终阶段为 `accepted`。新版本和产品范围变化仍需用户明确授权。
- 2026-08-03 前后台边界更新：用户明确机会中心是未来可能面向大众的前台，工作台是管理维护后台，项目详情不再作为顶级栏目；下一版本只做前后台分离、栏目用语、浅色 UI、交互和长任务进度体验，不新增核心研究数据能力。
- 2026-08-03 模型协作最终确认：Sol 在一个任务内完成规划和设计；Luna 在一个任务内完成全部常规编码与开发自测，不拆成多次 Luna 任务；Luna 结束时必须提醒切回 Sol，由 Sol 独立终验、直接修复冻结范围内问题并发布。
- 2026-08-03 C1.9 规划补充：用户要求每次对话结束明确下一步或最高优先级，并要求可见版本号只固定显示在工作台、不在前台和其他页面散布。Sol 据此完成 C1.9 页面清单、UI/交互规格、用语字典和验收稿，规划阶段随后进入用户冻结确认。
- 2026-08-03 C1.9 正式冻结：用户回复“按当前方案冻结 C1.9”。Sol 生成正式 PRD、正式验收计划、需求 SHA-256 锁、阶段文件和 Luna 一次性交接包；产品代码尚未开始。
- 2026-08-03 C1.9 Luna 实现：用户回复“已切换 Luna，开始整版开发”；Luna 在一个阶段内完成常规实现和开发自测，把阶段改为 `luna_self_test_complete_waiting_sol`。
- 2026-08-03 C1.9 Sol 终验发布：用户回复“已切换 Sol，开始终验修复发布”；Sol 独立复验、修复冻结范围内阻断并完成 34/34 项验收，最终阶段为 `sol_final_acceptance_complete_released`。发布证据不依赖旧任务内容。
- 2026-08-03 C2.0 规划与冻结：在任务 `019fc6be-2cde-7c82-9fb7-65106c84649a` 中，用户明确回复“按已确认方案冻结 C2.0”。Sol 核对双模型元数据后生成正式 PRD、页面与数据契约、设计规格、用语字典、验收计划、需求锁、阶段文件和 Luna 交接包；产品代码尚未开始，阶段为 `requirements_frozen_waiting_luna`。
- 2026-08-04 产品总路线重写：同一任务中，用户确认未来定位改为零人工复核的新发代币项目发现与可信度筛选，以代币首次公开流通 T0、A/B/C/D 四类为核心，最终收窄为 90 天池、第 91 天退出，并明确取消年龄权重。`docs/PRODUCT_ROADMAP.md` 已重写；该决定不解冻 C2.0。
- 2026-08-04 C2.1 规划：用户确认全部宽硬门槛通过项目进入前台、GitHub 首版可作为产品证据、短历史从真实 T0 回溯并按真实窗口补偿、总分不直接定义凸性线索。Sol 核验双元数据均为 `gpt-5.6-sol` / `xhigh`，形成规划、页面数据、设计和验收草案；当前等待 Gate 0 授权，未冻结、未编码。
- 2026-08-04 Gate 0 启动：同一任务中，用户明确授权继续数据预检。Sol 建立独立只读六链全量影子扫描、可复算 notebook 与报告，并创建每日任务 `gate-0`；首个有效观察日为 1/14，C2.1 仍未冻结、未编码。
- 2026-08-04 Gate 0 API 补充：用户提供 Helius Project ID、GeckoTerminal/CoinGecko Onchain Key、GoPlus App Key，并说明 Alchemy 六链已开放。程序完成认证接口与六链实时复测；GoPlus 仍缺 App Secret，使用公共接口回退。用户同时确定后续资源缺失必须先询问其是否已有。
- 2026-08-04 Gate 0 API 完整认证：用户补充 GoPlus App Secret、CoinMarketCap Key 和第二枚满额度 GeckoTerminal Key。程序修复 GoPlus Bearer 前缀适配，接入 CoinMarketCap 用量探针；GeckoTerminal 采用新主 Key、旧 Key 认证故障备用，节流从 7 秒校准为 2.5 秒并通过全量复测。

## 文件证据索引

- 当前业务版本历史：`README.md`
- C1.1 需求树：`C1.1-凸性发现质量升级-需求树.md`
- C1.2 需求树：`C1.2-凸性使用体验重构-需求树.md`
- 规则与仓位提示：`storage/rulebook-v1.json`
- 状态机：`storage/state-machine-v1.json`
- 数据模型：`storage/schema.sql`、`storage/data-dictionary.json`
- 当前运行数据：`data/convexity.db`
- 迁移证据：`docs/MIGRATION_MANIFEST.json`、`docs/migration/`
- 运行与恢复：`docs/RUNBOOK.md`
- 当前完成状态：`docs/STATUS.md`
- C1.8 冻结 PRD：`docs/C1.8_PRD.md`
- C1.8 Sol 验收计划：`docs/C1.8_ACCEPTANCE_PLAN.md`
- C1.8 Luna/Sol 交接：`docs/C1.8_HANDOFF.md`
- C1.8 当前阶段：`docs/C1.8_PHASE.json`
- C1.8 需求哈希锁：`docs/C1.8_REQUIREMENTS_LOCK.json`
- C1.8 最终验收清单：`docs/C1.8_ACCEPTANCE_MANIFEST.json`
- C1.8 首轮阻断与修复追溯：`docs/C1.8_SOL_BLOCKERS.md`
- C1.9 规划状态：`docs/C1.9_PLANNING_STATUS.json`
- C1.9 体验审计：`docs/C1.9_EXPERIENCE_AUDIT.md`
- C1.9 正式 PRD：`docs/C1.9_PRD.md`
- C1.9 页面唯一归属：`docs/C1.9_PAGE_INVENTORY.md`
- C1.9 UI 与交互规格：`docs/C1.9_DESIGN_SPEC.md`
- C1.9 前后台用语字典：`docs/C1.9_COPY_DICTIONARY.md`
- C1.9 正式验收计划：`docs/C1.9_ACCEPTANCE_PLAN.md`
- C1.9 需求哈希锁：`docs/C1.9_REQUIREMENTS_LOCK.json`
- C1.9 当前阶段：`docs/C1.9_PHASE.json`
- C1.9 Luna 一次性交接：`docs/C1.9_HANDOFF.md`
- C1.9 Sol 最终验收：`docs/C1.9_SOL_FINAL_ACCEPTANCE.md`
- C1.9 最终机器清单：`docs/C1.9_ACCEPTANCE_MANIFEST.json`
- C1.9 最终截图清单：`docs/C1.9_SCREENSHOT_MANIFEST.json`
- Sol/Luna 固定交接协议：`docs/MODEL_HANDOFF_PROTOCOL.md`
- C2.0 规划依据：`docs/C2.0_PLANNING_DRAFT.md`、`docs/C2.0_PLANNING_STATUS.json`
- C2.0 正式 PRD：`docs/C2.0_PRD.md`
- C2.0 页面与数据契约：`docs/C2.0_PAGE_DATA_CONTRACT.md`
- C2.0 UI 与交互规格：`docs/C2.0_DESIGN_SPEC.md`
- C2.0 前后台用语字典：`docs/C2.0_COPY_DICTIONARY.md`
- C2.0 正式验收计划：`docs/C2.0_ACCEPTANCE_PLAN.md`
- C2.0 需求哈希锁：`docs/C2.0_REQUIREMENTS_LOCK.json`
- C2.0 当前阶段：`docs/C2.0_PHASE.json`
- C2.0 Luna 一次性交接：`docs/C2.0_HANDOFF.md`
- 产品总路线图：`docs/PRODUCT_ROADMAP.md`
- C2.1 规划状态：`docs/C2.1_PLANNING_STATUS.json`
- C2.1 规划稿：`docs/C2.1_PLANNING_DRAFT.md`
- C2.1 页面数据契约草案：`docs/C2.1_PAGE_DATA_CONTRACT_DRAFT.md`
- C2.1 UI 与交互草案：`docs/C2.1_DESIGN_DRAFT.md`
- C2.1 验收草案：`docs/C2.1_ACCEPTANCE_DRAFT.md`
- Gate 0 影子范围：`config/gate0-shadow-scope.json`
- Gate 0 DEX 90天回扫范围：`config/gate0-dex-backfill.json`
- Gate 0 DEX 90天回扫器：`scripts/gate0_dex_factory_backfill.py`
- Gate 0 DEX 单次最新回扫证据：`runtime/gate0-shadow/backfill/latest.json`
- Gate 0 DEX 已核验事件结构：`runtime/gate0-shadow/backfill/schema-registry.json`
- Gate 0 DEX 六链累计覆盖：`runtime/gate0-shadow/backfill/coverage-rollup.json`
- Gate 0 最新运行证据：`runtime/gate0-shadow/latest.json`
- Gate 0 可复算分析：`reports/gate0-data-preflight/gate0-analysis.ipynb`
- Gate 0 当前报告：`reports/gate0-data-preflight/report.html`

## 边界

只保留“不得与其他业务耦合”的工程边界，不复制其他业务的产品规则、数据结构、任务、路由或文案。
