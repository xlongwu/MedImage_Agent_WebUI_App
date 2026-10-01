# 任务 10A：交互基线与 Agent Task 投影层

> 状态：Source implemented and automated tests recorded；packaged UI 复验延期。
>
> 本文正文保留实施前要求；其中 `Current Behavior`、清单和 Stop Conditions 是历史方案，
> 当前结果以 `README.md` 和 `evidence/阶段十_E2E验收记录.md` 为准。
>
> 任务模式：Feature Bundle Mode（只读 API）
>
> 交付目标：建立可复现的当前交互基线和不持久化第二状态的 Agent Task read model。

## 1. Background and Current Behavior

当前 UI 已有 Projects、六个 primary workspace、Runs、Settings、Assistant Sheet 和通用 task stream，但标准任务状态由多个页面和前端启发式组合：

- workspace 枚举与默认 Overview：`src/frontend/src/features/navigation/workspaceModel.ts:3-30`；
- shell 分别渲染 Data/Plan/Preprocessing/QC/Results：`src/frontend/src/features/app/AppShellView.tsx:454-563`；
- task controller 通过 task 名称猜测 preprocessing run：`src/frontend/src/features/tasks/useTaskController.ts:140-154`；
- lifecycle read API 已返回 canonical record 和 events：`src/backend/app/api/agent_lifecycle_routes.py:127-147`。

本任务不改变执行、不新增用户写操作，只建立可信的统一读取面。

## 2. Required Behavior

### 2.1 基线审计

对以下流程在当前打包或开发 UI 中逐步记录：

1. converted BIDS → FC；
2. raw DICOM → conversion → native preprocessing → FC；
3. 单受试者失败 → diagnosis → approved recovery。

每一步记录：页面、主动作、次动作、表单字段、科学决策、审批、人工 refresh、技术概念和失败排查动作。交付一张机器可读 JSON fixture 和一份 Markdown 摘要；不得把录屏、日志或用户数据提交仓库。

### 2.2 只读投影

`AgentTaskReadModel` 必须：

- 以 project-scoped lifecycle 为入口；
- 读取 reviewed plan、ticket、run link、observation、goal evaluation、diagnosis、recovery proposal/approval/attempt；
- 纯计算 public state、current action、next action、macro progress、result summary 和 evidence links；
- 缺证据时保守返回 `preparing` 或 `needs_attention`，不得推断完成；
- 不读取全局 `mock_store`，通过 `ProjectStore` Protocol 注入；
- 不写数据库、不创建事件、不执行文件系统写入。

### 2.3 API 范围

首轮只实现：

```text
GET /api/projects/{project_id}/agent/tasks
GET /api/projects/{project_id}/agent/tasks/{task_id}
GET /api/projects/{project_id}/agent/tasks/{task_id}/events?after=<cursor>&limit=<n>
```

`events` 合并多个 canonical ledger 后按稳定 `(occurred_at, source_order, event_id)` 排序；cursor 必须防重复且不能跨 project/task 使用。

## 3. Files

### Create

- `src/backend/app/schemas/agent_task.py`
- `src/backend/app/services/agent_task_read_model.py`
- `src/backend/app/api/agent_task_routes.py`
- `tests/unit/test_agent_task_read_model.py`
- `tests/unit/test_agent_task_api.py`
- `src/frontend/src/lib/types/agentTask.ts`
- `src/frontend/src/lib/api/agentTasks.ts`
- `src/frontend/src/lib/api/__tests__/agentTasks.test.ts`
- `specs/阶段记录/阶段十/evidence/交互基线.md`（只包含无隐私的步骤统计）
- `specs/阶段记录/阶段十/evidence/交互基线.json`（只包含步骤类别和计数）

### Modify

- `src/backend/app/main.py`：注册独立 router；
- `src/backend/app/api/dependencies.py`：仅在 read model 确需新的只读查询方法时扩展 Protocol；
- `src/frontend/src/lib/api/index.ts`：导出新领域 API；
- `specs/阶段记录/阶段十/README.md`：记录 10A gate 结果。

### Read Only

- `src/backend/app/schemas/agent_lifecycle.py`
- `src/backend/app/services/agent_orchestrator.py`
- `src/backend/app/api/agent_lifecycle_routes.py`
- `src/backend/app/schemas/observation.py`
- `src/backend/app/schemas/goal_contract.py`
- `src/backend/app/schemas/recovery.py`
- `src/backend/app/schemas/recovery_attempt.py`
- `src/frontend/src/features/app/AppShellView.tsx`
- `src/frontend/src/features/workspaces/PreprocessingReviewedFlow.tsx`

## 4. Detailed Tasks

1. 固化 public enum、next action enum、progress phase 和 evidence link type。
2. 建立完整 lifecycle mapping table；未知 future state 返回安全的 `needs_attention` 并保留 internal state 证据。
3. 对每种状态定义唯一主 action，禁止同一响应出现两个 `primary=true` action。
4. 结果摘要只接受 `GoalEvaluationRecord.status == satisfied` 且关联 observation/artifact evidence 完整。
5. failed/partial subject count 优先来自 observation；缺失时返回 `null`，不以零代替未知。
6. 将绝对路径转换为 project-relative evidence identifier；技术详情 endpoint 仍可按权限返回本地路径。
7. events cursor 使用 opaque 编码并验证 project/task binding。
8. 完成交互基线并给出目标减少比例，不提前篡改基线数字。

## 5. Acceptance Criteria

- [ ] read model 对同一 evidence 重复调用输出字节级稳定（时间字段除外；首版不得生成读取时间）。
- [ ] GET 前后 lifecycle/event/ticket/DB 行数和文件清单不变。
- [ ] cross-project task 和 cursor 返回 404/安全错误。
- [ ] missing observation、conflicting evidence、partial run 不显示 `completed`。
- [ ] event pagination 无重复、无漏序、可从 cursor 恢复。
- [ ] 前端 API 类型不使用 `Record<string, unknown>` 替代核心契约。
- [ ] 三条 baseline 均有页面/点击/字段/审批/概念/排查计数。

## 6. Hazards and Tests

| Hazard | Test |
|---|---|
| H10-01 第二状态机 | source test 断言 AgentTask projection 没有 store add/update 方法；DB schema 无 agent_task_status |
| H10-06 假成功 | missing artifact、partial、indeterminate goal fixtures |
| H10-07 GET 写入 | DB/file/event snapshot before/after |
| H10-10 cursor 重复 | pagination、project switch、stale cursor |
| H10-13 路径泄漏 | portable response 不含本机 root |
| H10-14 名称启发式 | 同名 task 和明确 lifecycle binding fixtures |

## 7. Validation Commands

```text
python -m pytest tests/unit/test_agent_task_read_model.py tests/unit/test_agent_task_api.py --tb=short --basetemp=.pytest_tmp
python -m pytest tests/unit/test_agent_lifecycle.py tests/unit/test_goal_evaluator.py tests/unit/test_recovery_proposal_engine.py --tb=short --basetemp=.pytest_tmp
npm --prefix src/frontend run typecheck
npm --prefix src/frontend run test -- agentTasks
npm --prefix src/frontend run build
```

每次 pytest 完成后按 `AGENTS.md` 清理并验证仓库根目录没有 `.pytest_cache/` 或 `.pytest_tmp*`。

## 8. Stop Conditions

- 需要修改 execution gateway、ticket、runner、scientific kernel 或 DICOM execute 时停止；这些不属于 10A。
- 发现无法仅从 canonical records 计算某字段时，把字段设为 `null` 并记录 10B 契约缺口，不得创建平行状态。
- Phase 9 未关闭或工作树存在同文件并行编辑时不开始实现。

## 9. Completion Report Format

按 `AGENTS.md` 报告 Task、Files changed、Behavior delivered、方案逐项对照、Validation、Risks/unfinished items。额外附：baseline 六类指标表、projection state coverage 表、GET side-effect 证明和未覆盖 canonical state 列表。
