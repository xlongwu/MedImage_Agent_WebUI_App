# 05：Planner 与版本化重规划改造方案

> 状态：Implemented，已完成代码与回归验证；仍按项目流程接受人工 Review。
> 依赖：04 提供 EvidenceSnapshot 和决定批次；06 的恢复重规划复用本文合同。

## 1. 目标

让首次规划、回答后的重新规划和失败后的局部重规划使用同一条 Planner 链，并能说明每个计划版本由什么证据产生、相对上一版改了什么。任何影响执行内容的变化都必须生成新 plan hash 和新 Approval Summary。

非目标：不让模型直接持久化 Reviewed Plan，不保留多个可同时审批的活动计划，不自动批准重规划，不改变 Pipeline Runtime。

## 2. 当前实现分析

- `AgentTaskCommandService._plan()` 同时处理 MemoryContext、目标分类、科学决定、Planner、validator、Reviewed Plan 和 Approval Summary，职责较集中但已有完整安全顺序。
- `GoalPlanningService.plan()` 在 provider 失败时返回结构化 context error，并调用现有 planner。
- `reviewed_plan_store.py` 根据 normalized plan 和 planning inputs 生成稳定 ID/hash，并校验重载一致性。
- 当前决定包含 `plan_hash_before`，但 Reviewed Plan 没有明确的 `revision_no`、父计划和修订原因投影。
- Recovery 已有 `parent_plan_hash` 和 `ReplanService`，但 Harness 首次/回答后规划未统一表现为版本链。

## 3. 总体修改思路

不新建第二套 Planner。将 `_plan()` 拆成“准备输入、生成候选、验证并持久化、推进 lifecycle”四个内部步骤；所有入口都构造同一个 `PlanningRequest`。版本信息进入 Reviewed Plan identity 和审计，不改变 node 执行合同。

## 4. 详细实施方案

### 4.1 统一规划请求

新增内部 schema `PlanningRequest`：

| 字段 | 来源 |
|---|---|
| `project_id`、`lifecycle_id`、`goal` | lifecycle |
| `evidence_snapshot_hash` | 04 |
| `science_answers` | 已消费决定批次 |
| `memory_context_hash`、refs | 现有 Memory Domain |
| `parent_reviewed_plan_id`、`parent_plan_hash` | 当前 lifecycle/恢复 attempt |
| `revision_reason` | `initial`、`decision_answered`、`goal_revised`、`recovery_replan` |
| `provider_ref`、prompt version | Harness/model config |

Planner 只能消费已绑定、已裁剪的结构化输入，不直接查询 memory DB、rawdata 或前端本地状态。

### 4.2 拆分 `_plan()` 内部步骤

建议保留 `AgentTaskCommandService._plan()` 作为命令入口，内部调用：

1. `_build_planning_request()`；
2. `GoalPlanningService.plan()` 生成 Candidate Plan；
3. `validate_plan()` 和现有科学前提检查；
4. `persist_reviewed_plan()`；
5. `ApprovalSummaryService.build()`；
6. lifecycle 进入等待决定、plan-only 完成或 `WAITING_FOR_APPROVAL`。

不在 Route 或 Harness handler 复制这些步骤。

### 4.3 计划版本

在 Reviewed Plan payload/record 增加：

| 字段 | 含义 |
|---|---|
| `revision_no` | 同 lifecycle 从 1 开始递增 |
| `parent_reviewed_plan_id` | 上一版计划 ID，可空 |
| `parent_plan_hash` | 上一版 plan hash，可空 |
| `revision_reason` | 受限枚举 |
| `planning_inputs_hash` | goal、evidence、answers、memory、provider/prompt 的统一 hash |
| `evidence_snapshot_hash` | 本版使用的项目证据 |

`revision_no` 只用于阅读；安全身份仍以 hash 为准。相同 planning inputs 和 normalized plan 的幂等重放返回同一 Reviewed Plan，不创建空修订。

### 4.4 审批失效规则

以下任一变化必须产生新计划身份：

- goal 或 goal contract；
- EvidenceSnapshot；
- science answers；
- node、backend、params、depends_on、输入或输出 scope；
- MemoryContext；
- recovery candidate；
- 会影响规划结果的 Prompt/Skill/provider 版本。

新版本持久化前不得删除旧记录；lifecycle 只绑定当前版本。旧 Approval Summary、approval actor、dry-run 结果和 ticket 均不能转移到新版本。

### 4.5 自动重规划边界

- 回答决定后可自动重新规划；
- 目标修订后可自动重新规划，但旧计划立即解绑；
- 执行失败后只能在 06 产生 Recovery Proposal；用户批准执行性恢复后才可重规划/执行；
- 相同输入连续产生同一校验失败时，不无限重试，转人工；
- plan-only 可以生成多个修订，但始终零 approval、dry-run、ticket、run。

### 4.6 失败分类

| 类别 | 处理 |
|---|---|
| 缺少输入 | 返回 04 的决定批次 |
| 不支持目标 | 请求 goal revision |
| provider 不可用 | 显式 deterministic fallback 或停止 |
| 输出 schema 错误 | 一次 repair，失败后停止 |
| validator 拒绝 | 记录 issues；可由规则修复一次，否则 handoff |
| 执行前提缺失 | 保持不可审批/不可 dispatch，返回结构化错误 |

## 5. 文件修改清单

| 文件 | 修改内容 |
|---|---|
| `schemas/desktop.py:ReviewedPlanRecord` | 计划修订和 planning inputs 字段 |
| `services/agent_task_command_service.py` | 拆分 `_plan()` 内部步骤、统一请求 |
| `services/goal_planning_service.py` | 接受 `PlanningRequest` 和显式 provider 结果 |
| `planner/reviewed_plan_store.py` | identity、父版本和幂等校验 |
| `services/approval_summary_service.py` | summary identity 绑定 planning inputs/修订 |
| `services/replan_service.py` | 复用统一 PlanningRequest，不复制持久化 |
| `services/agent_task_read_model.py` | 展示当前修订和父版本 |
| 对应 schema/API/frontend types/tests | 同步新结构，删除旧消费者 |

## 6. 风险与处理

| ID | 风险 | 处理 | 测试 |
|---|---|---|---|
| H05-01 | 旧审批批准新计划 | summary/ticket 绑定新 plan/planning inputs hash | 修改任一输入后 approve 拒绝 |
| H05-02 | 幂等重放产生多版本 | canonical identity + command replay | 相同请求返回同 ID |
| H05-03 | 版本号被当安全身份 | 所有 gate 只校验 hash | revision 相同但 hash 不同拒绝 |
| H05-04 | `_plan()` 拆分破坏顺序 | characterization + ordered spy | approval 前无 dry-run |
| H05-05 | 自动重规划无限循环 | 同输入失败 hash + attempt budget | 重复 validator failure handoff |
| H05-06 | plan-only 意外执行 | 专门零调用断言 | ticket/gateway/runner=0 |

## 7. 测试与验收

覆盖首次规划、回答后修订、goal revision、recovery replan、相同输入重放、旧审批失效、Memory/evidence/prompt hash 变化和 plan-only。

```powershell
python -m pytest tests/unit/test_agent_task_commands.py tests/unit/test_approval_summary.py tests/unit/test_recovery_replan.py tests/unit/test_project_history_plans.py tests/unit/test_memory_retrieval.py --tb=short --basetemp=.pytest_tmp
```

人工验收：Reviewer 能从当前任务查看计划版本、父计划、修订原因和输入证据；对旧版本的批准请求在 dry-run 前被拒绝。

## 8. 实施顺序

1. 用测试固定当前规划/审批顺序；
2. 定义 PlanningRequest 和修订字段；
3. 拆分 `_plan()` 但保持行为；
4. 更新 identity、store 和 Approval Summary；
5. 接入 answer/goal revision/recovery；
6. 更新 read model/API/前端；
7. 删除旧分散重规划逻辑；
8. 运行审批、recovery 和 plan-only 回归。
