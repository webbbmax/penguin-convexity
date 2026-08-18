# 企鹅投研-凸性｜历史索引

历史文件保留用于追溯，不代表当前状态。当前结论先看 `CURRENT_STATE.md` 和当前版本需求锁。

| 主题 | 入口 | 用途 |
|---|---|---|
| 当前发布与未完成事项 | `docs/STATUS.md` | 历次状态与发布记录 |
| 项目长期记忆 | `docs/PROJECT_MEMORY.md` | 迁移、版本和关键经验 |
| 用户决定 | `docs/DECISIONS.md` | 决策时间线与边界 |
| 来源任务索引 | `docs/SOURCE_THREAD_INDEX.md` | 旧任务和证据来源定位 |
| 迁移资产 | `docs/MIGRATION_MANIFEST.json` | M1.0 迁移文件与哈希 |
| 当前产品冻结 | `docs/C2.5_REQUIREMENTS_LOCK.json` | C2.5 管理者控制面唯一正式范围 |
| 当前产品发布 | `docs/C2.5_RELEASE_MANIFEST.json` | C2.5 发布证据 |
| 当前产品终验 | `docs/C2.5_ACCEPTANCE_MANIFEST.json`、`docs/C2.5_FINAL_ACCEPTANCE.md` | C2.5 92/92 独立终验证据 |
| 当前产品继承 | `docs/C2.4_INHERITANCE_MANIFEST.json` | 29 个角色路由零删除基线 |
| 上一产品发布 | `docs/C2.4_RELEASE_MANIFEST.json` | C2.4 发布与回滚基线 |
| C2.5任务清单 | `docs/C2.5_TASK_INVENTORY.json` | 现役、旧版、隐藏和按需入口登记 |
| C2.5开发前基线 | `docs/C2.5_DEVELOPMENT_READINESS_BASELINE.json` | 最新C2.4源基线、冻结后修复和C2.6隔离 |
| C2.5任务技术补充 | `docs/C2.5_TASK_INVENTORY_SUPPLEMENT.json` | 20个POST入口和21项任务目录精确映射 |
| C2.5需求追踪 | `docs/C2.5_REQUIREMENT_TRACEABILITY.json` | A01至J07共92项实现、测试和证据槽位 |
| C2.5开发门禁 | `docs/C2.5_GATE_CONTRACT.json` | 92项阶段门禁和开发前就绪产物哈希 |
| C2.5夹具设计 | `docs/C2.5_FIXTURE_DESIGN.json` | 六组隔离夹具场景；设计完成但未实现 |
| C2.5整体设计 | `docs/C2.5_PRODUCT_DESIGN_SYSTEM.md` | 南极蓝、Apple视觉与Windows操作规范 |
| 开发体系冻结 | `docs/D0_REQUIREMENTS_LOCK.json` | D0 唯一正式范围 |
| 开发体系验收 | `docs/D0_ACCEPTANCE_PLAN.md` | A01-H06 验收项目 |

更早版本按 `docs/C1.*_REQUIREMENTS_LOCK.json`、`docs/C2.0_REQUIREMENTS_LOCK.json` 至 `docs/C2.3_REQUIREMENTS_LOCK.json` 定位。版本文档之间发生冲突时，较新且经用户明确冻结的需求锁优先，但不能反向改写历史记录。
