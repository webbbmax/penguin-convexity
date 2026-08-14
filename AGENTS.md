# AGENTS.md

## 协作对象与表达

- 用户是投资与产品负责人，不要求其理解代码。默认用简体中文和产品语言说明：完成了什么、从哪里打开、能看到什么、如何验证、还有什么风险、下一优先级是什么。
- 每次最终回复必须明确写出“你下一步要做什么”或“当前最高优先级建议”。如果当前无需用户操作，也要明确写“当前无需你操作”并给出最高优先级。模型由用户自行决定；可以给出一次非强制建议，但不得审核模型或反复提醒切换 Sol/Luna。
- 结论优先，事实、项目方陈述与推断分开；涉及最新市场、监管、价格、流动性、部署和治理信息时必须联网核验。
- 不以社交热度、涨幅、低市值或项目宣传替代投资价值判断。

## 项目身份与边界

- 本项目唯一正式可写根目录：`F:\codex项目\企鹅投研\凸性`。D0 已冻结未来代码工作区根目录 `F:\codex项目\企鹅投研\凸性-worktrees`，但只有用户另行明确授权“开始开发体系重置0”后才能创建或写入；该目录只允许代码 worktree，不得保存正式数据库、凭据或运行状态。
- 产品名：`企鹅投研-凸性`；迁移版本：`M1.0`；数据主干版本：`C1.7`；当前已发布桌面与网页业务版本：`C2.4 普通用户体验与机会发布重构`，完整继承 C2.3 的 `.NET 10＋WPF＋WebView2` 桌面容器和 C2.2 业务主干；C2.4 已于 2026-08-13 完成 79/79 独立终验并发布，需求锁为 `docs/C2.4_REQUIREMENTS_LOCK.json`；默认端口：`8766`。
- 历史来源 `F:\codex项目\区块链\convexity-system`、旧整合壳 `F:\codex项目\区块链\project-radar-site` 和旧整合根目录只读，不得写入、删除或作为运行时依赖。
- `F:\codex项目\企鹅投研\RWA` 只读且业务完全独立；本项目不得读取其脚本、数据、路由、任务、状态或文案，也不得建立任何运行时引用。
- 公共 API 资源库只允许共享资源目录、文档、凭据位置和总额度说明。密钥不得明文写入代码、文档、快照或日志；启用状态、游标、采集配置、健康状态、运行日志、原始数据、去重和任务状态必须保存在本项目。

## 产品冻结规则

- 当前已发布 C2.2 桌面软件首页直接进入凸性机会中心；前台导航只包含：机会首页、全部机会、重要变化、我们如何判断。项目详情是上下文页面，工作台通过右上角低干扰入口进入。
- 不建立通用“今日结论”。“当前结论”属于凸性机会中心内部。
- C1.7 机会中心严格保持 C1.6-06 七段顺序：当前结论、为什么不能行动、最近变化、催化路径、项目类别、跟踪任务、全部项目。
- C1.8 已获用户明确授权重做展示结构：七类业务语义继续保留，但不再要求全部铺在单页长滚动中；具体范围只以 `docs/C1.8_PRD.md` 为准。
- Logo、口号“洞见共识之外的价值”和南极蓝视觉保持不变。
- L0-L5、风险、剩余凸性、交易性、点火距离、当前动作是独立维度，不得相互替代。
- 行动语言只使用：普通建仓、极限试仓、只观察、反身性管理、失效/排除。系统只给研究建议，不自动交易。
- 早期、OG、潜力项目是生命周期研究路线，不是 L0-L5、风险或动作的同义词。

## 前后台产品边界

- 用户于 2026-08-03 明确：凸性机会中心是面向普通用户、未来可能公开上线的前台展示产品；凸性工作台是仅用于管理、维护、更新、证据、来源、异常和配置的后台产品。
- 前台与后台不得复用导航、页面壳、页脚、管理控件和责任语言。前台只解释判断、机会、变化、风险、催化和数据时间；任务、来源、重试、调度、Watcher、孤儿证据、日志和模型工具只属于后台。
- 不同业务对象或不同更新流水线即使同属一个导航分组，也必须使用独立页面和独立脚本入口；不得为了少一个页面把状态、按钮和任务逻辑挤在同页。类似功能默认以页面边界降低表达歧义和运行耦合，只有用户明确要求合并时才能同页。
- 项目详情是从结论、机会、变化或列表进入的上下文页面，不是顶级导航栏目；返回时必须恢复原入口、筛选、页码和滚动位置。
- C1.9 以信息架构、栏目、用语、浅色 UI、交互和长任务进度可见性为核心，不新增研究数据能力，不改评分、结论、动作、L0-L5或跨业务边界。
- 面向用户可见的业务版本号只在后台工作台浅色左侧栏底部固定显示一次；机会中心、项目详情、桌面宿主、页眉、页脚和普通栏目不得显示版本号。内部接口、日志和文档可保留机器版本字段。
- C1.9 已由用户于 2026-08-03 明确冻结并由 Sol 终验发布，当前阶段为 `sol_final_acceptance_complete_released`。不得借维护改写其冻结范围；下一版本需用户重新授权。
- 用户已于 2026-08-03 明确授权“按已确认方案冻结 C2.0”。C2.0 正式范围只以 `docs/C2.0_REQUIREMENTS_LOCK.json` 所列五份文件为准，已由 Sol 终验发布，当前阶段为 `sol_final_acceptance_complete_released`；不得借维护改写冻结范围，C2.1 或投资体系变化需用户重新授权。
- 自 C2.0 起，每个产品版本除主功能外都必须包含一组可验收的 UI 优化和一组可验收的 UX 优化；不得只改后台逻辑而让用户体验停滞，也不得用纯换色代替体验改进。
- 用户于 2026-08-04 确认重写长期产品路线：未来定位为完全自动运行的“新发代币项目发现与可信度筛选”，以官方代币首次向普通公众开放领取、转让或交易的可验证时间作为 T0；新发池只保留 0—30、31—90 天，第 91 天退出，不得设置时间权重、年龄分或由时间暗示投资质量。A 新项目新币、B 老项目新币、C 项目关系未确认、D 只有代币四类必须分开；D 不进入前台，C 不得冒充身份已确认。该路线只以 `docs/PRODUCT_ROADMAP.md` 为准，不改变当前 C2.0。
- 未来新路线的产品运行中不设置逐项目人工内容复核、通过、退回或发布确认；证据不足必须自动放弃判断。软件开发自测、Sol 独立终验、固定历史样本和回滚验证仍是强制发布责任，不能被“零人工复核”取消。
- C2.1 已由用户明确回复“按冻结稿冻结C2.1”正式冻结。正式范围只以 `docs/C2.1_REQUIREMENTS_LOCK.json` 所列八份文件为准：全部通过宽硬门槛的 A/B/C 项目进入前台；GitHub 可作为首版产品证据但必须显示“仅代码证据”边界；真实历史从 T0 回溯，发布不足 14 天使用真实可观察窗口且未来日期不计零；动态贝叶斯总分只用于排序、变化与后续校准，不直接定义凸性线索。前台固定五种互斥状态，手动/自动共用隐藏可恢复流水线，凸性线索至少两条强证据路径且必须包含交易与流动性。C2.1 只交付 Windows 桌面产品，不考虑移动端适配。冻结文件未经用户明确解冻不得修改。
- C2.2 已由用户明确回复“按冻结稿冻结C2.2”正式冻结，并于 2026-08-11 完成第二轮独立终验、冻结范围修复和发布。正式范围只以 `docs/C2.2_REQUIREMENTS_LOCK.json` 所列八份文件为准：以后所有版本以当前产品为唯一主干，未获明确批准删除的已有功能默认继承；C2.1 新币筛选与 C1.7/C1.8 凸性跟踪是两个独立可恢复作业，由唯一 Windows 调度器协调并通过稳定 `assetId` 和原子快照交接；生命周期与凸性跟踪状态分离，前台不再把 `data_limited` 作为项目分类；固定加权替换为确定性分层经验贝叶斯证据更新，但综合分不能单独形成凸性线索，也不自动调参。发布证据为 `docs/C2.2_ACCEPTANCE_MANIFEST.json`、`docs/C2.2_FINAL_ACCEPTANCE.md` 和 `docs/C2.2_RELEASE_MANIFEST.json`。
- C2.3 已由用户于 2026-08-12 明确冻结、授权整版开发，并完成独立终验与发布。正式范围只以 `docs/C2.3_REQUIREMENTS_LOCK.json` 所列八份文件为准：使用 `.NET 10 LTS＋WPF＋WebView2 Evergreen` 替换 PowerShell 调起普通 Edge 的桌面容器；C2.2 业务、页面、数据、任务和规则零删除继承；C# 只负责桌面生命周期和恢复，不形成第二业务通道；测试按实际风险分层，不默认重跑全部历史套件。发布证据为 `docs/C2.3_ACCEPTANCE_MANIFEST.json`、`docs/C2.3_FINAL_ACCEPTANCE.md` 和 `docs/C2.3_RELEASE_MANIFEST.json`。
- C2.4 已由用户于 2026-08-13 明确冻结、授权整版开发、独立终验并发布。正式范围只以 `docs/C2.4_REQUIREMENTS_LOCK.json` 所列十份文件为准：第一关宽筛选、第二关完整公开快照、四条动态比较路径、三个公开状态、两个生命周期池、同链首页最多 10 个、全部机会完整保留、29 个既有路由零删除继承，以及普通用户图表化 UI/UX。独立终验发现并修复动态比较组未生效和 GitHub 归因污染，最终 79/79 通过。发布证据为 `docs/C2.4_ACCEPTANCE_MANIFEST.json`、`docs/C2.4_FINAL_ACCEPTANCE.md` 和 `docs/C2.4_RELEASE_MANIFEST.json`。当前是单机单目录发布，没有隔离的测试环境与正式环境；发布后维护不得借机改写冻结范围。
- C2.4 已由用户于 2026-08-13 明确回复“可以按冻结稿冻结C2.4”正式冻结，当前等待另行开发授权。正式范围只以 `docs/C2.4_REQUIREMENTS_LOCK.json` 所列十份文件为准：第一关“90 天候选初筛”只用 T0/身份方向/真实买卖/硬交易阻断四项宽进，第二关完成公开底线、四路径与贝叶斯跟踪，机会中心只读第二关完整公开快照；首页每链合计最多 10 个只是展示切片，全部公开项目仍持续跟踪并进入“全部机会”；第 91 天只迁移曾在 0—90 天完成两关并达到公开条件的同一 `assetId`；29 个既有路由零批准删除并逐项登记；测试必须先按 `assetId` 独立重算核心集合，低优先级通过数不能抵消核心失败。冻结动作不授权修改产品代码、数据库、作业或正式入口。
- 开发体系重置0（D0）已由用户于 2026-08-14 明确回复“按冻结稿冻结开发体系重置0”正式冻结。D0 是工程治理阶段，不是 C2.5，当前产品版本继续为 C2.4；正式范围只以 `docs/D0_REQUIREMENTS_LOCK.json` 所列三份文件为准。冻结动作不授权执行现状分类、清理、创建 worktree、改造项目记忆结构、提交、合并、打标签或推送 GitHub；只有用户另行明确回复“开始开发体系重置0”后才能执行。D0 不得修改六链、筛选、贝叶斯、UI、业务数据库、现役任务、唯一调度器、快捷方式或用户设置。
- 用户于 2026-08-08 以“按原流程纠正 Gate 0”冻结 Gate 0 工具范围。正式范围只以 `docs/GATE0_REQUIREMENTS_LOCK.json` 所列文件为准；该工具已经完成实现、独立后台扫描、终验修复和放行。该冻结只授权 Gate 0 独立工具，不授权 C2.1 产品编码。

## 数据与可恢复性

- 主数据库：`data/convexity.db`。M1.0 不可变迁移基线为 585 个项目和 585 个候选记录；C1.7-00 开发基线为 589 个项目和 590 个机会案例；C1.9 发布时为 596 个项目和 598 个机会案例；C2.0 发布时为 600 个项目和 602 个机会案例。当前数量必须现场核验并写入当前版本验收清单，不得把历史基线写成实时数量。
- C1.7 的 Event Schema v2、来源游标、健康状态、断档、孤儿证据、实体图谱和 Watcher 全部保存在本项目；不得把登记的 Watcher 数量冒充已采集事件，也不得把真实零结果写成采集成功有数据。
- 原始事件、来源发现、证据、溯源、弱线索、监控目标和催化路径必须保留；迁移、重建和修复不得用空白或合成数据覆盖历史。
- 正式变更前先备份；恢复时先保留当前数据库副本，再从明确选定的备份恢复。
- 日志、缓存、窗口状态和备份均使用本项目内目录；不得用跨目录引用节省复制。
- 跟踪来源状态必须按项目归因；项目无适用来源是 `no_data`，不得继承来源全局部分失败。任何更新写入原始事件后，任务结束前必须完成 Event Schema v2 本地增量规范化并同步页面快照。

## 开发准则

1. 先写清目标、假设、缺口和可验证成功标准。
2. 只修改用户目标需要的内容，不顺手重构相邻代码，不加入推测性功能。
3. 每行改动应能追溯到产品要求；新改动造成的无用导入或孤立代码要清理。
4. 优先写能复现问题的测试，再修复并循环到通过。
5. 对业务数据的验收保持只读；真实点击测试不得触发更新、复核或写入操作。

## 临时产物与磁盘保留

- 开发、修复和验收产生的大体积临时副本必须放在 `runtime/temp-artifacts/<单任务目录>`，并通过 `scripts/temp_artifact_retention.py` 或其 `managed_temp_artifact` 上下文登记用途、所属任务、占用进程和最晚清理时间；不得再把完整数据库临时副本散放到其他 `runtime` 目录。
- 任务正常结束或失败都必须封存登记；默认结束后只保留 24 小时，确有排错需要时可明确延长，但最长 30 天。失败测试优先保留小型复现样本、日志、SQL 和结果清单，不长期保留整库。
- 自动清理只允许删除 `runtime/temp-artifacts` 的已登记直属子目录；`data`、`backups`、正式 Gate 0、发布程序、当前运行状态、缓存、锁、正式验收和任何未登记路径一律禁止自动删除。具体边界以 `docs/TEMP_ARTIFACT_RETENTION_POLICY.json` 为准。
- 现有唯一 Windows 调度器每天最多执行一次轻量清理检查，不新增第二个长期任务；清理失败必须记录并继续现有业务更新，不能改写用户暂停设置。
- 开发自测、独立终验或发布报告完成前必须检查临时产物状态；发现到期但被阻止的产物时，报告具体路径和原因，不得静默遗留，也不得绕过保护规则强删。

## 发布门槛

- 按当前版本冻结的风险分层策略执行测试：改动功能、直接依赖、继承契约和真实用户主路径必须通过；只有共享核心、规则、数据库、调度或影响边界不明时才要求全量 Python 回归，不再把全部历史套件作为每次小修改的默认动作。
- C2.0 的 29 个唯一角色路由和 31 个本地页面/资源全部返回正确内容；不接受只有静态文件存在的替代验证。
- SQLite `integrity_check=ok`、外键检查无异常；项目与机会案例数量必须与当前版本验收清单一致，并同时保留 M1.0 与 C1.7-00 基线备份。
- 真实用户路径通过：启动、当前版本首页结构、详情进入与返回、筛选/分页记忆、窗口记忆、重复启动单实例；C1.8 还必须验证软件关闭时无人值守调度。
- 桌面快捷方式“企鹅投研-凸性”必须可用；不得删除、覆盖或擅自重建其他产品和历史入口。
- 交付前更新 `docs/STATUS.md` 和迁移清单。

## 版本禁区

- `C1.7 最大漏斗数据主干一期` 已由用户明确确认并完成，不得借维护 C1.7 改写凸性评分、结论、动作或 C1.6-06 七段顺序。
- `C1.8 机会中心决策体验与自动跟踪闭环` 已由 Sol 终验通过并发布；不得借维护改写其冻结产品范围或投资体系。
- 2026-08-02 的 Sol/Luna 固定分工已由用户在 2026-08-10 取消。后续使用什么模型由用户决定；助手可以建议但不审核、不阻断，也不反复提醒切换。开发自测、独立终验和冻结范围约束仍保留，但不再绑定特定模型。
- 需求范围、验收和交接只以 `docs/C1.8_PRD.md`、`docs/C1.8_ACCEPTANCE_PLAN.md`、`docs/C1.8_HANDOFF.md` 和需求锁为准；没有用户明确解冻不得修改。
- `C1.9 前后台分离与全产品体验重构` 已由 Sol 终验并发布；冻结范围只以 `docs/C1.9_REQUIREMENTS_LOCK.json` 所列正式文件为准。不得擅自改需求锁、借维护扩大范围或开始其他新版本。
- `C2.0 机会信号质量与可解释决策` 已由 Sol 终验发布。阅读优先级只能决定前台先看什么，不能变成新的投资动作、评分、仓位或 L0-L5 替代物；架构为只读派生双快照，不新增 SQLite 业务表、列或迁移，不得改写原始历史或用模板补足真实零信号。
- `docs/PRODUCT_ROADMAP.md` 是 2026-08-04 用户确认后的长期规划基线，不是已发布功能或冻结需求。Gate 0 已完成；只有单版本正式设计经用户确认并生成需求锁后，才能把路线图中的 T0、A/B/C/D、90 天窗口、宽硬门槛或零人工运行写入产品。
- Gate 0 长任务只有在具备分片、原子检查点、可恢复游标、单实例锁和可见进度后才能启动；已经接受的 29 天和正式 90 天结果不得重跑。全量运行由独立 Python 进程完成；是否在对话中持续等待由用户决定，任何情况下不得重复启动或从头重跑。既有 `PenguinConvexity-C1.8-Scheduler`、用户暂停设置、C2.0 产品代码和 `data/convexity.db` 均为禁区。
- `C2.3 稳定桌面容器与浏览器隔离` 已独立终验并发布；旧 PowerShell/Edge 启动器只作为隐藏回滚资产，未经用户明确要求不得恢复正式指向。未来网页版、采集库归档压缩和业务功能变化均不属于 C2.3。

## 模型选择与阶段门禁

1. 使用什么模型、何时切换完全由用户决定；助手不读取或审核模型元数据，不因模型名称或推理档位阻断任务。
2. 助手可以在能力、成本或长任务风险存在明显差异时给出一次非强制建议，但不得把建议变成短口令、固定结尾或反复提醒。
3. 版本规划、需求冻结、实现、开发自测、最终验收和发布仍是不同责任阶段；取消模型门禁不等于取消阶段门禁。
4. C2.4 已发布；业务或页面维护前必须核验 C2.4 需求锁及 C2.2/C2.3 继承依赖哈希，桌面容器维护还必须核验 C2.3 需求锁。开发自测不能替代独立终验，任何范围变化或下一版本仍需用户明确授权。
5. C2.4 已冻结但未授权开发；只有用户另行明确授权“开始 C2.4 整版开发”后，才能建立实现基线并修改冻结范围内产品。实现和终验都必须先执行 Tier 0 核心集合对账，再扩展到规则、恢复、真实桌面和边缘功能。
6. 最终验收必须独立执行当前冻结版本按风险定义的完整验收，不相信实现报告本身；局部修复先定点重验，只有跨越影响边界时才扩大回归，冻结范围缺陷可直接修复并重验，不要求更换模型。
7. 任何模型的修改都只能追溯到用户确认的需求或明确验收问题；范围变化、评分变化、动作变化和新版本仍需用户授权。
8. 只有完整验收通过后，才能更新最终清单、`docs/STATUS.md` 和发布状态。

## 项目记忆读取顺序

1. `AGENTS.md`
   - D0 完成后，继续优先读取 `docs/CURRENT_STATE.md`、`docs/PRODUCT_BASELINE.md`、`docs/DEVELOPMENT_WORKFLOW.md` 和 `docs/HISTORY_INDEX.md`，再按下列原有顺序追溯历史；当前真相文件不删除或改写历史记录。
2. `docs/C1.8_PHASE.json`（执行 C1.8 时）
3. `docs/C1.8_REQUIREMENTS_LOCK.json`（执行 C1.8 时）
4. `docs/STATUS.md`
5. `docs/PROJECT_MEMORY.md`
6. `docs/DECISIONS.md`
7. `docs/RUNBOOK.md`
8. `docs/MIGRATION_MANIFEST.json`
9. `docs/SOURCE_THREAD_INDEX.md`
10. `docs/C1.8_PRD.md`、`docs/C1.8_ACCEPTANCE_PLAN.md`、`docs/C1.8_HANDOFF.md`（执行 C1.8 时必须完整读取）
11. `docs/C1.9_PHASE.json`、`docs/C1.9_REQUIREMENTS_LOCK.json`、`docs/C1.9_PRD.md`、`docs/C1.9_PAGE_INVENTORY.md`、`docs/C1.9_DESIGN_SPEC.md`、`docs/C1.9_COPY_DICTIONARY.md`、`docs/C1.9_ACCEPTANCE_PLAN.md`、`docs/C1.9_HANDOFF.md`（执行 C1.9 时必须完整读取并核验哈希）
12. `docs/C1.9_PLANNING_STATUS.json`、`docs/C1.9_EXPERIENCE_AUDIT.md`（追溯 C1.9 规划依据时）
13. `docs/MODEL_HANDOFF_PROTOCOL.md`（任何版本规划、实现、终验或修复交接前必须读取）
14. `docs/C2.0_PLANNING_STATUS.json`、`docs/C2.0_PLANNING_DRAFT.md`（追溯 C2.0 规划依据时）
15. `docs/C2.0_PHASE.json`、`docs/C2.0_REQUIREMENTS_LOCK.json`、`docs/C2.0_PRD.md`、`docs/C2.0_PAGE_DATA_CONTRACT.md`、`docs/C2.0_DESIGN_SPEC.md`、`docs/C2.0_COPY_DICTIONARY.md`、`docs/C2.0_ACCEPTANCE_PLAN.md`、`docs/C2.0_HANDOFF.md`（执行 C2.0 时必须完整读取并核验哈希）
16. `docs/GATE0_PHASE.json`、`docs/GATE0_REQUIREMENTS_LOCK.json`、`docs/GATE0_PRD.md`、`docs/GATE0_DATA_CONTRACT.md`、`docs/GATE0_DESIGN_SPEC.md`、`docs/GATE0_ACCEPTANCE_PLAN.md`、`docs/GATE0_BASELINE_MANIFEST.json`、`docs/GATE0_HANDOFF.md`（执行 Gate 0 规划、Luna 实现、后台启动或 Sol 终验时必须完整读取并核验哈希）
17. `docs/C2.1_PHASE.json`、`docs/C2.1_REQUIREMENTS_LOCK.json`、`docs/C2.1_PRD.md`、`docs/C2.1_PAGE_DATA_CONTRACT.md`、`docs/C2.1_DESIGN_SPEC.md`、`docs/C2.1_COPY_DICTIONARY.md`、`docs/C2.1_RULE_CONFIG.json`、`docs/C2.1_RULE_REGRESSION_MANIFEST.json`、`docs/C2.1_ACCEPTANCE_PLAN.md`、`docs/C2.1_HANDOFF.md`（执行 C2.1 实现、终验、修复或发布时必须完整读取并核验哈希）
18. `docs/C2.2_PHASE.json`、`docs/C2.2_REQUIREMENTS_LOCK.json`、`docs/C2.2_PRD.md`、`docs/C2.2_PAGE_DATA_CONTRACT.md`、`docs/C2.2_DESIGN_SPEC.md`、`docs/C2.2_COPY_DICTIONARY.md`、`docs/C2.2_BAYES_SPEC.md`、`docs/C2.2_INHERITANCE_MANIFEST.json`、`docs/C2.2_ACCEPTANCE_PLAN.md`、`docs/C2.2_HANDOFF.md`（执行 C2.2 实现、终验、修复或发布时必须完整读取并核验哈希）
19. `docs/C2.3_PHASE.json`、`docs/C2.3_REQUIREMENTS_LOCK.json`、`docs/C2.3_PRD.md`、`docs/C2.3_DESKTOP_CONTAINER_CONTRACT.md`、`docs/C2.3_DESIGN_SPEC.md`、`docs/C2.3_COPY_DICTIONARY.md`、`docs/C2.3_INHERITANCE_MANIFEST.json`、`docs/C2.3_TEST_STRATEGY.md`、`docs/C2.3_ACCEPTANCE_PLAN.md`、`docs/C2.3_HANDOFF.md`（执行 C2.3 开发、终验、修复或发布时必须完整读取并核验哈希）
20. `docs/C2.4_PHASE.json`、`docs/C2.4_REQUIREMENTS_LOCK.json`、`docs/C2.4_PRD.md`、`docs/C2.4_PAGE_DATA_CONTRACT.md`、`docs/C2.4_DESIGN_SPEC.md`、`docs/C2.4_COPY_DICTIONARY.md`、`docs/C2.4_RULE_CONFIG.json`、`docs/C2.4_RULE_REGRESSION_MANIFEST.json`、`docs/C2.4_INHERITANCE_MANIFEST.json`、`docs/C2.4_TEST_STRATEGY.md`、`docs/C2.4_ACCEPTANCE_PLAN.md`、`docs/C2.4_HANDOFF.md`（执行 C2.4 发布后维护、复验或回滚时必须完整读取并核验哈希）
20. `docs/C2.4_PHASE.json`、`docs/C2.4_REQUIREMENTS_LOCK.json`、`docs/C2.4_PRD.md`、`docs/C2.4_PAGE_DATA_CONTRACT.md`、`docs/C2.4_DESIGN_SPEC.md`、`docs/C2.4_COPY_DICTIONARY.md`、`docs/C2.4_RULE_CONFIG.json`、`docs/C2.4_RULE_REGRESSION_MANIFEST.json`、`docs/C2.4_INHERITANCE_MANIFEST.json`、`docs/C2.4_TEST_STRATEGY.md`、`docs/C2.4_ACCEPTANCE_PLAN.md`、`docs/C2.4_HANDOFF.md`（执行 C2.4 开发、终验、修复或发布时必须完整读取并核验哈希）
21. `docs/D0_PHASE.json`、`docs/D0_REQUIREMENTS_LOCK.json`、`docs/D0_FREEZE_DRAFT.md`、`docs/D0_DEVELOPMENT_WORKFLOW.md`、`docs/D0_ACCEPTANCE_PLAN.md`（执行 D0 或规划其后的版本、核心修复和发布流程时必须完整读取并核验哈希）
