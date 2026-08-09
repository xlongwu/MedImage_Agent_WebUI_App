# Agent 简洁优化实施方案

> 状态：Draft，待人工 Review。
> 任务模式：Architecture / Refactor + Feature Bundle。
> 本文职责：收敛 Agent 默认交互和后台推进方式，作为后续开发的直接实施依据；本文不授权修改生产代码。
> 方案关系：`00_Agent改造总体方案.md` 至 `04_项目证据收集与科学决策自动化方案.md` 提供背景和候选设计。本文选择其中当前必须完成的部分，并明确删除或延期的内容；在本文获批前，不改变已有方案状态。

## 1. 目标

当前项目已经有 Agent Task、Agent Lifecycle、Reviewed Plan、Approval Gate、Execution Ticket、Execution Gateway、Pipeline Runtime、Observation、Goal Evaluation 和 Recovery Proposal。主要问题不是能力不足，而是：

1. 默认导航和 Agent 页面仍暴露过多工作区、卡片和内部概念；
2. create/answer 仍在 HTTP 请求内同步推进，Harness 每次又只处理一步；
3. 前端通过英文摘要和 artifact 类型推断业务状态；
4. 阶段十二候选方案同时引入循环、Action handler、EvidenceSnapshot、Context v2、ModelCallRecord、Skills、Replay 和多 Agent，首期范围过大。

修改完成后，标准流程应为：

```text
选择项目
-> 输入一个研究目标
-> 后台自动读取已登记证据并准备方案
-> 仅在必要时集中询问科学决定
-> 用户审批稳定的执行摘要
-> 后台执行、监控和验证
-> 页面显示结果或一条需要人工处理的建议
```

普通用户默认只看到：输入、当前状态、唯一主动作、结果和“查看详情”。

### 1.1 本次必须完成

- 顶层导航收敛为 Projects、Agent、Runs、Settings；
- Agent 页面收敛为一个主要任务区域，不并列展示 Harness 和多张流程卡；
- 后端提供结构化字段，删除前端字符串解析和 artifact 类型推断；
- create/answer 快速返回，由后台 owner 有界推进到用户输入、审批、运行或终态；
- 多个必要决定一次提交；
- 执行完成后自动生成 Observation、Goal Evaluation 和真实结果摘要；
- rawdata、审批、执行、路径和科学真实性边界保持不变。

### 1.2 不属于本次范围

- 通用 Tool Worker、任意 shell、Python、网络或文件工具；
- 新建第二套 Lifecycle、Pipeline、artifact registry 或恢复状态机；
- Redis、Celery、外部消息队列或分布式 worker；
- 完整 Replay engine、Prompt cache、Skills 注册系统、多 Agent 和 SDK；
- 修改科学算法、能力等级、外部工具开关或版本号；
- 自动批准科学决定、执行或恢复；
- 删除旧 Data、Plan、Preprocessing、QC、Results 页面。

## 2. 当前实现分析

### 2.1 当前调用链

```text
POST /api/projects/{project_id}/agent/tasks
-> agent_task_routes.py:create_agent_task()
-> AgentTaskCommandService.create()
-> AgentOrchestrator.create()
-> AgentTaskCommandService._harness_or_plan()
   -> Harness 关闭：_plan()
   -> Harness 开启：ensure_attempt() -> run_one()
-> AgentTaskReadModel.get()
-> HTTP 返回 AgentTaskResponse
```

审批后调用链已经符合项目边界：

```text
AgentTaskCommandService.approve()
-> 校验 Approval Summary/hash
-> post-approval dry-run
-> Execution Ticket
-> reviewed execution service
-> Execution Gateway
-> Pipeline Runtime / registered runner
-> AgentTaskReconciler
-> Observation / Goal Evaluation / Recovery Proposal
```

该执行链继续复用，不由 Harness 或新 scheduler 重写。

### 2.2 可直接复用的实现

| 现有实现 | 位置 | 本方案用途 |
|---|---|---|
| Agent Lifecycle | `schemas/agent_lifecycle.py`、`services/agent_orchestrator.py` | 用户任务唯一状态源 |
| Agent Task 投影 | `schemas/agent_task.py`、`services/agent_task_read_model.py` | 生成前端统一状态、动作和结果 |
| Harness attempt/step/context | `schemas/agent_harness.py`、`services/mock_store.py` | 保存有限规划步骤、预算、lease 和审计 |
| 单步 Harness | `services/agent_harness_service.py:run_one()` | 继续作为一个可恢复步骤的最小单元 |
| 启动恢复 | `runtime/agent_harness_scheduler.py` | 改造成应用生命周期内的后台推进 owner |
| 执行终态协调 | `services/agent_task_reconciler.py` | 继续负责 run 终态、观察、评价和恢复建议 |
| Agent Workspace | `src/frontend/src/features/agent/` | 收敛为任务驱动的默认页面 |
| Runs 和兼容工作区 | `features/workspaces/`、`RunsWorkspace.tsx` | 承载二级结果和三级技术证据 |

### 2.3 已确认问题

| ID | 当前实现 | 影响 | 修改方向 |
|---|---|---|---|
| G-01 | `GlobalNavigationRail.tsx` 直接列出 10 个入口 | 用户仍需理解内部工作流 | 默认只显示 4 个入口，旧页面从详情进入 |
| G-02 | `AgentWorkspace.tsx` 同时渲染 Project Summary、Current Action、Harness、Progress、Next Action、Result、Details | 状态被拆成多张卡片 | 合并为一个 `TaskCard`，结果和详情按需展开 |
| G-03 | `NextActionCard.tsx` 用英文正则解析 subject/node 数量 | 后端文案变化会破坏 UI | Approval Summary 增加结构化计数字段 |
| G-04 | `ResultSummaryCard.tsx` 用英文句子表做 i18n | 前端依赖后端英文措辞 | 后端返回稳定 `summary_code`，前端只映射 code |
| G-05 | 前端通过 `reviewed_plan` artifact 推断 plan-only | 业务状态可能被误判 | 后端直接返回 `task_kind` 和 `execution_performed` |
| G-06 | create/answer 在请求内调用 `_plan()` 或 `run_one()` | 增加多步后请求时间会继续增长 | 命令只持久化并唤醒后台 owner |
| G-07 | `AgentHarnessScheduler` 只在启动时每个 lifecycle 跑一步 | 新任务不会持续自动推进 | 增加运行期 wake 和有界 batch |
| G-08 | `read_evidence`、`explain_result`、`finish` 是 Harness Action，但已有确定性服务可完成对应工作 | Action 名称增加了模型和 handler 复杂度 | 从模型 Action 中删除，改由确定性流程处理 |
| G-09 | `PendingDecision` 一次只能保存一个问题 | atlas、TR、template 等可能反复打断 | 改为一个批次，一次提交全部必要答案 |
| G-10 | Assistant 和 Goal Composer 都像输入入口 | 用户不易区分“提任务”和“问问题” | Goal Composer 保持唯一任务入口；Assistant 降为“解释当前任务” |

## 3. 总体修改思路

### 3.1 选择

使用“确定性任务主流程 + 可选 LLM 规划建议”的结构：

```text
Agent Workspace
-> Agent Task API
-> Agent Lifecycle + 后台 wake
-> 项目证据收集
-> 确定性 Planner 或可选 Harness 规划
-> Reviewed Plan / Approval Summary
-> 人工审批
-> Execution Gateway / Pipeline Runtime
-> Observation / Goal Evaluation
-> Agent Task 投影
-> Agent Workspace / Runs
```

模型只允许建议“需要用户回答什么”或“生成候选计划”。证据读取、任务完成判断、结果摘要、恢复候选和执行全部由现有确定性服务负责。

### 3.2 原因

- 项目已有完整的确定性执行和结果判断链，不需要模型再次选择工具或判断是否成功；
- `AgentLifecycleRecord` 已能作为唯一状态源；
- Harness 已有 step、budget、lease 和 SQLite 持久化，可以直接扩展有限推进；
- 限制模型 Action 数量可以减少 handler、Prompt、测试和审计面。

### 3.3 不采用的方案

| 方案 | 不采用原因 |
|---|---|
| 为六种 Action 全部新增 handler | `read_evidence`、`explain_result`、`finish` 可由现有确定性服务完成，模型参与没有必要 |
| 新增独立 `EvidenceSnapshot` 表 | `AgentHarnessContext` 已按 hash 持久化项目快照；首期只需扩展其结构化证据字段 |
| 新增 `ModelCallRecord` 表 | attempt 和 step 已能记录 provider、调用数、输入/输出 hash 和错误；首期扩展 step 字段即可 |
| 新增通用后台任务表 | Lifecycle、attempt 和 run 已持久化；启动扫描可以恢复遗漏 wake |
| 引入外部队列 | 当前是单机 desktop sidecar，现有 SQLite 和 lifespan owner 足够 |
| 在默认 UI 展示 Harness 预算和 provider | 这些是三级审计信息，不是用户完成任务所需信息 |

## 4. 目标状态和信息层级

### 4.1 公共状态

继续使用现有五个公共状态，不增加 `retrying`、`observing` 或 `evaluating`：

| 公共状态 | 用户含义 | 典型内部状态 |
|---|---|---|
| `preparing` | 正在读取项目、准备方案或准备 dispatch | CREATED、CONTEXT_READY、PLAN_DRAFTED、PLAN_VALIDATED、APPROVED、EXECUTION_READY |
| `waiting_for_user` | 需要回答、审批或确认恢复 | WAITING_FOR_INPUT、WAITING_FOR_SCIENCE_DECISION、WAITING_FOR_APPROVAL、WAITING_FOR_RETRY_APPROVAL、RECOVERY_PROPOSED、WAITING_FOR_RECOVERY_APPROVAL |
| `running` | 已批准任务正在执行或验证 | RUNNING、OBSERVING、EVALUATING、RETRYING、RECOVERING |
| `needs_attention` | 无法安全自动继续 | FAILED、DIAGNOSING、RETRY_PROPOSED、RECOVERY_READY、HUMAN_HANDOFF |
| `completed` | 有真实结果、明确完成 plan-only，或已取消 | GOAL_SATISFIED、SUCCEEDED、CANCELED |

内部状态仍完整保留，用于审批、重启恢复和审计。前端只根据公共状态、`phase`、`next_action.type` 和 `outcome` 显示页面。

### 4.2 三层信息

| 层级 | 默认可见性 | 内容 |
|---|---|---|
| Level 1：任务 | 默认显示 | 目标、当前状态、一句话说明、真实进度、唯一主动作 |
| Level 2：结果 | 完成或用户展开 | 结果摘要、完成/失败数量、限制、产物入口 |
| Level 3：详情 | 用户主动展开 | Reviewed Plan、run、artifact、validation、audit、Harness step、hash、provider、node、路径 |

Level 3 继续复用 Runs、Task Details 和兼容工作区，不删除证据。

## 5. 详细修改方案

### 5.1 收敛顶层导航

**当前情况**

`GlobalNavigationRail.tsx` 的 `items` 同时包含 Agent 和六个旧工作区；测试也明确查找 Overview、Results。

**修改方式**

修改：

- `src/frontend/src/features/navigation/GlobalNavigationRail.tsx`
- `src/frontend/src/features/navigation/__tests__/GlobalNavigationRail.test.tsx`
- `src/frontend/src/features/app/AppShellView.tsx`

默认 `items` 只保留：

```text
Projects / Agent / Runs / Settings
```

旧工作区继续使用 `src/frontend/src/features/navigation/workspaceModel.ts:legacyLocationForProject()` 和现有 outlet，不出现在 rail。入口保留在 `TaskDetails` 和 Runs 的证据链接中。

**验证**

- rail 只有四个按钮；
- 选择项目仍默认打开 Agent；
- Task Details 可以进入 Data、Plan、Preprocessing、QC、Results；
- deep-link 和项目切换不丢失。

### 5.2 合并 Agent Workspace

**当前情况**

`AgentWorkspace.tsx` 把一个任务拆成多张同级卡片，并为每张卡显示 01～04 编号。Harness 状态也在普通页面直接展示。

**修改方式**

新增：

`src/frontend/src/features/agent/components/TaskCard.tsx`

`TaskCard` 是唯一视觉卡片。现有子组件继续保持小职责，但移除各自的 `Card` 外壳并按内容命名：

- `MacroProgress.tsx` -> `TaskProgress.tsx`；
- `NextActionCard.tsx` -> `TaskActionPanel.tsx`；
- `RecoveryActionCard.tsx` -> `TaskRecoveryPanel.tsx`；
- `ResultSummaryCard.tsx` -> `TaskResultPanel.tsx`。

删除 `ProjectSummaryCard.tsx`；项目名由现有 Shell/Sidebar 提供，数据准备状态作为 Goal Composer 或 `TaskCard` 顶部的一行上下文保留，不再占用独立卡片。删除默认页的 `HarnessStatusCard.tsx`，其脱敏内容移入 `TaskDetails`。`CurrentAction.tsx` 的简短标题逻辑合并到 `TaskCard`。

`TaskCard` 根据状态呈现不同内容，但始终只产生一个 `data-primary-action="true"`：

```text
preparing        -> 状态 + 当前阶段 + 无主动作
waiting_for_user -> 状态 + Decision/Approval + 一个提交按钮
running          -> 状态 + 真实 subject 进度 + 无主动作
needs_attention  -> 问题摘要 + 一个安全处理动作
completed        -> Result Summary + 查看产物
```

上述 Panel 不创建独立 Card，也不决定任务状态，只根据 props 渲染 `TaskCard` 内部内容。`TaskDetails` 保持折叠，并承载 Harness、技术证据和兼容入口。

**边界情况**

- planning 没有真实百分比时显示不确定进度，不绘制伪进度条；
- lifecycle 已终态但缺少可辩护结果时，由 Read Model 保守投影为 `needs_attention`；这不是新增 lifecycle 状态迁移；
- internal `CANCELED` 继续映射公共 `completed`，同时返回 `outcome="canceled"`，使用终态样式且不显示“查看结果”主动作；
- 新任务确认继续保留，避免误丢当前任务视图。

### 5.3 简化 Agent Task 公共合同

**当前情况**

前端从 `dataset_summary`、`execution_summary`、英文结果文案和 artifacts 推断状态。

**修改方式**

修改：

- `src/backend/app/schemas/agent_task.py`
- `src/backend/app/services/agent_task_read_model.py`
- `src/frontend/src/lib/types/agentTask.ts`
- `src/frontend/src/lib/api/agentTasks.ts`

使用单一新合同，同步更新所有当前消费者，不保留旧字段解析。

`AgentTaskResponse` 新增：

- `task_kind`；
- `execution_performed`；
- `status_code`。

`execution_performed` 是非空布尔值：当且仅当当前 lifecycle 已经通过唯一 Execution Gateway dispatch 并绑定 `run_id` 时为 `true`，即使该 run 后续失败也保持 `true`；plan-only、审批前取消和从未 dispatch 的失败均为 `false`。

`status_code` 是稳定 Literal，替换并删除当前可自由变化的 `current_action` 文案；`next_action.type` 继续决定按钮类型。`AgentTaskNextAction` 删除 `title`、`description` 和单项 `decision_id`，保留 `type`、`requires_user`，并增加可选 `batch_id`、`disabled_reason_code`。前端只按 type/code 映射 i18n。首期 status code 固定为：

| code | 使用条件 |
|---|---|
| `preparing.project_context` | 正在收集受控项目上下文 |
| `preparing.reviewed_plan` | 正在生成或校验 Reviewed Plan |
| `preparing.dispatch` | 已批准，正在准备唯一 Gateway dispatch |
| `waiting.decision` | 等待决定批次 |
| `waiting.approval` | 等待当前 Approval Summary 的审批 |
| `waiting.recovery_approval` | 等待重试或恢复审批 |
| `waiting.recovery_proposal` | 已生成恢复建议，等待用户选择 |
| `running.execution` | 已 dispatch 且 run 未终态 |
| `running.validation` | 正在收集 Observation 或评估目标 |
| `running.recovery` | 已批准的重试或恢复正在运行 |
| `attention.failure` | 失败、诊断或人工移交 |
| `attention.evidence` | 终态但结果证据不足或不可重载 |
| `attention.recovery` | 恢复准备失败或需要人工处理 |
| `completed.result` | 有可辩护结果 |
| `completed.plan_only` | 仅生成计划，未执行 |
| `completed.canceled` | 已取消 |

`next_action.type` 继续使用并仅允许：`none`、`provide_input`、`revise_goal`、`answer_science_decision`、`approve_execution`、`approve_recovery`、`review_results`、`view_attention`、`contact_support`。每个公共投影最多一个非 `none` 主动作；恢复审批仍由后端现有状态和 approval hash 决定。

权威 `schemas/approval_summary.py:ApprovalSummary` 与公共 `AgentTaskApprovalSummary` 同步增加结构化范围，替换可解析英文：

- `registered_subject_count`；
- `selected_subject_ids` 和 `selected_subject_count`；
- `reviewed_node_ids` 和 `reviewed_node_count`。

这些字段由 `ApprovalSummaryService` 从实际 Reviewed Plan 和项目索引产生，并纳入稳定 `summary_hash`。UI 显示“已选择 X / 已登记 Y”；不得只显示登记总数来暗示所有 subject 都会执行。公共投影必须从已持久化的权威 Approval Summary 复制字段，不能重新计算。

`AgentTaskResultSummary` 增加稳定 Literal `summary_code`，首期固定为 `result.succeeded`、`result.partial`、`result.failed`、`result.indeterminate` 和 `result.plan_only`，前端按 code 映射 i18n。顶层 `status_code` 只决定任务卡标题；存在结果时，`result_summary.summary_code` 只决定结果区文案，两者不得互相覆盖。后端动态限制和科学警告仍使用结构化列表，不拼接为待解析句子。

现有 `outcome` 保持 `succeeded | partial | failed | indeterminate | canceled`；它描述任务结果，不描述科学能力。`capability_level` 继续逐 artifact 表示 `unavailable | scaffolded | metadata_only | computed | validated`。Read Model 是公共 `state`、`outcome` 和结果可见性的唯一权威，前端不得组合这些字段推导新状态。

删除前端：

- subject/node 英文正则；
- `RESULT_MESSAGE_KEYS` 英文句子表；
- `RunsWorkspace` 对 `getAgentResultMessageKey`、后端英文 `current_action` 的依赖；
- 通过 `reviewed_plan` artifact 判断 plan-only 的逻辑。

实现前全仓搜索上述 helper、旧组件导入和旧字段消费者；所有当前消费者改为共享 code catalog 后再删除旧实现。

**验证**

- 修改后端英文摘要不会改变前端状态；
- plan-only 明确返回 `task_kind="plan_only"`、`execution_performed=false`；
- 中文和英文都只依赖 `summary_code`；
- list/detail 对同一 lifecycle 产生相同字段。

### 5.4 缩减 Harness Action

**当前情况**

`ActionEnvelope.kind` 有六种。`read_evidence` 只使 attempt 回到 READY；`explain_result` 和 `finish` 只结束 attempt；`propose_recovery` 依赖可选 callback。

**选择**

模型协议只保留：

```text
request_decision
draft_plan
```

**修改方式**

修改：

- `src/backend/app/schemas/agent_harness.py:AgentHarnessActionKind`
- `src/backend/app/services/agent_harness_service.py:_apply()`
- `src/backend/app/runtime/agent_capability_catalog.py`
- `src/backend/app/planner/agent_model_adapter.py`
- 对应 replay fixtures 和单元测试

处理规则：

- 项目证据在调用模型前由唯一 context builder 读取；
- 结果摘要由 `AgentTaskResultSummaryService` 生成；
- Recovery Proposal 由 `AgentTaskReconciler` 和现有 recovery policy 生成；
- attempt 是否结束由 lifecycle 状态和循环停止条件决定，不接受模型 `finish`；
- 未知或已删除的 action fail closed，不做兼容解析。

这样不再需要六个 payload handler、通用 handler registry 或 `ActionExecutionResult`。

### 5.5 扩展项目证据上下文，不新增证据存储

**当前情况**

`HarnessContextBuilder._safe_project_evidence()` 主要读取 `project.metadata`，缺少明确的 run、artifact、Observation 和配置能力摘要。

**修改方式**

修改：

- `src/backend/app/services/agent_harness_context_service.py`
- `src/backend/app/schemas/agent_harness.py:AgentHarnessContext`
- `src/backend/app/api/dependencies.py:ProjectStore`

Context builder 只读取：

1. 项目和 dataset index 摘要；
2. 已登记输入和 artifact ID；
3. 当前 Reviewed Plan、run 和 Observation 引用；
4. ConfigService、Tool Catalog 和 Node Catalog 的启用状态；
5. 已允许的 MemoryContext 建议。

现有读取锚点优先复用 `ProjectStore.get_project()`、`get_dataset_summary()`、`get_reviewed_plan()`、`list_run_links()`、`get_execution_ticket()`、`get_observation()` 和对应 list 方法。若注册 artifact 摘要没有现成 Protocol 方法，只在 `api/dependencies.py:ProjectStore` 增加一个只读、结构化查询并在 store 实现；不得从路径扫描或 rawdata 读取补齐。

新增 `evidence_refs`，记录使用了哪些稳定 ID。继续使用现有 `project_snapshot_hash` 和 `context_hash`，不新增 `EvidenceSnapshot` 表。

同时把 hash 语义收紧：`project_snapshot_hash` 只覆盖规范化 project/dataset 摘要；`context_hash` 覆盖 schema version、prompt template version、project snapshot hash、排序后的 evidence refs、Reviewed Plan/run/Observation 引用、catalog/config 能力摘要和已允许的 MemoryContext hash。序列化使用排序 key 和稳定数组顺序。Context、Step 与 Prompt 版本同步提升到 2，防止新证据集合命中旧 `INSERT OR IGNORE` 记录。

禁止读取 rawdata/NIfTI/DICOM 正文、完整日志、环境变量值、provider key 和项目外路径。

### 5.6 后台有限推进

**当前情况**

`AgentHarnessScheduler.recover_once_on_startup()` 只在启动时扫描；create/answer 同步调用 `_harness_or_plan()`。

**选择**

将 `AgentHarnessScheduler` 替换为 `AgentTaskScheduler`。它是 planning/Harness 的后台 owner，不负责 Pipeline Runtime。

**修改方式**

新增或重命名：

`src/backend/app/runtime/agent_task_scheduler.py`

提供：

```text
wake(project_id, lifecycle_id, reason)
run_pending_batch()
recover_once_on_startup()
shutdown()
```

`main.py` lifespan 创建唯一 scheduler，保存在 `app.state`，启动一个后台 owner，并在退出时调用 `shutdown()`。`api/dependencies.py` 提供 `get_agent_task_scheduler()`；Route 将 scheduler 的 `wake` callable 注入 `AgentTaskCommandService`，测试可注入 fake callable。Command Service 不读取 FastAPI `app.state`。

修改 `AgentTaskCommandService`：

- `create()`：创建 lifecycle；Harness 开启时再创建 attempt；登记 wake 后返回；
- `answer()`：原子保存答案、清除决定批次、登记 wake 后返回；
- `approve()`：审批和 dispatch 顺序不变；
- GET/list 不调用 `wake()`。

Scheduler 每次重新从 SQLite 读取 lifecycle，不使用旧内存对象。Harness 开启时循环调用 `run_one()`；Harness 关闭时调用从现有 `_plan()` 提取的公共确定性 `advance_planning()` 一次。遇到以下条件立即停止：

- 等待用户输入或审批；
- lifecycle 进入 RUNNING/RECOVERING；
- lifecycle 终态；
- 达到本次 wakeup 步数上限；
- budget、lease、schema 或安全策略拒绝。

首期使用进程内有界队列作为 wake 提示。队列丢失不丢任务：启动扫描和低频运行期扫描都以 SQLite 中 `CREATED`、`CONTEXT_READY`、`PLAN_DRAFTED`、`PLAN_VALIDATED` 以及 Harness `READY`、lease 已过期的 `RUNNING` attempt 为准重新登记。`wake()` 合并同一 lifecycle 的重复提示；队列满、线程安全登记失败或当前 batch 运行期间收到新 wake 时设置 `rescan_required`，batch 结束后再次扫描 SQLite。低频扫描只检查这些可推进状态，不轮询 run、不触发 GET 副作用，也不替代启动恢复。

从 `AgentTaskCommandService._plan()` 抽出窄职责 `AgentPlanningService.advance_planning()`，保持 Reviewed Plan、validator 和 Approval Summary 单一 owner。该 service 只依赖 Planner、context/plan/approval store 和 lifecycle orchestrator，不注入 `ReviewedExecutionService`、Execution Gateway、Pipeline Runtime 或 runner。Command Service 负责命令边界并调用/唤醒它，scheduler 只持有这个窄 collaborator，不能通过 bound Command Service 间接到达执行依赖。

并发和退出语义固定如下：

- dedupe key 使用 `(project_id, lifecycle_id)`；reason 只进入审计，不扩大并发 key；
- lifespan owner 运行在应用事件循环中；现有同步 Route 只能通过 `loop.call_soon_threadsafe()` 登记 wake，不得从 FastAPI worker thread 直接操作 async queue；
- Harness 开启时继续使用 attempt lease/step key；Harness 关闭时在 `AgentLifecycleRecord` 增加 `planning_lease_owner`、`planning_lease_expires_at` 和 `planning_generation`，并由 store 提供 expected-state/revision 的原子 `claim_planning()`。任何 Planner 调用前必须取得对应持久 claim；进程内锁不作为正确性依据；
- planning idempotency key 使用 `lifecycle_id + expected_state + planning_generation + context_hash`；数据库事务不得跨模型调用或多个 step；
- 达到 `max_steps_per_wakeup` 而 lifecycle 仍可推进时，将该 lifecycle 重新登记；队列满时设置 `rescan_required`，不得静默停在 preparing；
- `shutdown()` 先停止新 claim，再等待当前单步完成并释放 lease；不得在退出时启动下一步；
- 仅 provider 不可用、传输失败或一次 schema repair 后仍无效时显式回退确定性 Planner；能力缺失、预算、状态冲突、安全和审批错误必须结构化停止，不得回退绕过；
- scheduler 的依赖图不得包含 Execution Gateway、Pipeline Runtime、runner 或外部命令执行器。

逐状态恢复规则固定为：

| SQLite 状态/事实 | 恢复动作 | 禁止动作 |
|---|---|---|
| `CREATED`，Harness 开启但无 attempt | idempotent `ensure_attempt()`，再构建 context | 不得把缺 attempt 当 provider fallback |
| `CREATED`，Harness 关闭 | 取得 lifecycle planning claim，构建 context并转 `CONTEXT_READY` | 不创建 Harness attempt |
| `CONTEXT_READY` | 取得 attempt lease 或 lifecycle planning claim，生成 candidate plan | 不得并发调用 Planner |
| `PLAN_DRAFTED` 且无 Reviewed Plan | 取得 claim，按同一 context 重新生成 candidate 并继续校验/保存 | 不得假设草案正文已持久化 |
| `PLAN_VALIDATED`，execution | 读取已持久化 Reviewed Plan/Approval Summary，校验 plan/summary hash 后转 `WAITING_FOR_APPROVAL` | 不重新规划、不重写 summary |
| `PLAN_VALIDATED`，plan-only | 读取已持久化 plan-only 证据后幂等转 `SUCCEEDED` | 不创建审批、ticket 或 run |
| `PLAN_VALIDATED` 但缺 plan/summary | 记录 invariant error 并转 `HUMAN_HANDOFF` | 不从残缺状态猜测或重新 dispatch |

永久性 budget、模型输出 schema、未知 action、safety、batch limit 和状态 invariant 错误统一记录稳定 `blocking_error_code` 并转现有 `HUMAN_HANDOFF`；持久 payload 版本不支持则在反序列化边界返回 `AGENT_SCHEMA_VERSION_UNSUPPORTED`。lease 冲突只让出，provider/transport/schema repair 的 fallback 按上文规则处理。这样运行期 rescan 不会反复调用同一永久失败步骤。

**配置**

新增：

`MEDIMAGE_AGENT_TASK_MAX_STEPS_PER_WAKEUP=3`
`MEDIMAGE_AGENT_TASK_RESCAN_SECONDS=5`

每次 wakeup 步数硬上限 6；rescan 允许 1～60 秒。复用现有模型调用、proposal、wall time、lease 和 lifecycle retry quota，不新增第二套恢复预算。

### 5.7 集中用户决定

**当前情况**

`PendingDecision`、Answer API 和 `NextActionCard` 一次只处理一个问题。

**修改方式**

用 `PendingDecisionBatch` 替换 `PendingDecision`：

- lifecycle 最多只有一个未解决批次；
- 一个批次最多 8 个必需项；
- 用户一次提交全部 required answers；
- 任一答案无效时不消费批次；
- atlas、TR、template、GSR、subject scope、overwrite 和 experimental backend 不得超时自动接受；
- 可由项目索引确定的值不得询问用户。

前端在 `TaskCard` 内按 item 渲染一个表单和一个提交按钮，不为每个 item 创建独立卡片。

持久结构固定为：

```text
PendingDecisionBatch
  batch_id
  project_id
  lifecycle_id
  context_hash
  plan_hash_before
  items[]
  created_at

DecisionItem
  item_id
  kind
  prompt_code
  prompt_params
  answer_type       choice | multi_choice | text | number | boolean
  required
  options[]
  impact_code

DecisionOption
  id
  label_code
  description_code
  recommended
```

`item_id` 在批次内唯一；choice/multi_choice 的答案必须来自 options，number/text/boolean 由 schema 按 kind 再校验。Answer API 只返回以下稳定错误 code：`DECISION_BATCH_STALE`、`DECISION_BATCH_INCOMPLETE`、`DECISION_ITEM_UNKNOWN`、`DECISION_ITEM_DUPLICATE`、`DECISION_ANSWER_INVALID`；collector 还可返回 `DECISION_BATCH_LIMIT_EXCEEDED`。错误包含 `item_id`（适用时）和 i18n 参数，不返回需要前端解析的英文句子。成功后在同一事务内消费批次、写入答案和 lifecycle transition，随后登记 wake；旧 batch 重放不得改变状态。

`kind` 固定为 `missing_input`、`goal_revision`、`subject_scope`、`atlas`、`global_signal_regression`、`repetition_time`、`template`、`overwrite`、`experimental_backend`、`other`；旧 `subject_id` 当前消费者同步切换后删除，不保留别名。

计划生成前的输入批次允许 `plan_hash_before=null`，但必须绑定 `context_hash`；Reviewed Plan 已存在后的科学决定必须同时绑定 `context_hash` 和 `plan_hash_before`。任一绑定变化都返回 stale。答案消费后废弃旧 plan/hash，由下一次 `advance_planning()` 基于答案生成并持久化新的 Reviewed Plan 和 Approval Summary。

确定性 Planner 先运行 decision collector，一次收集当前证据下全部已知缺口，再生成一个 batch；按固定 kind 顺序排序，以 `(kind, item_id)` 去重。Memory 只能提供建议或推荐项，不能直接满足必需决定。Harness 的 `request_decision` 改为有类型 `RequestDecisionBatchPayload(items=...)`，与确定性缺口合并后再做同一套去重和校验。超过 8 项时返回 `DECISION_BATCH_LIMIT_EXCEEDED` 并进入 `needs_attention`，不得拆成多个连续弹窗或截断。`subject_scope` 使用 `multi_choice` 和 JSON 数组，布尔项使用 JSON boolean，数值项使用 JSON number；Answer 示例中的字符串仅适用于 choice/text。

### 5.8 结果和恢复保持确定性

**当前情况**

`AgentTaskReconciler` 已能在 run 终态后收集 Observation、执行 Goal Evaluation 并产生 Recovery Proposal；`AgentTaskResultSummaryService` 已根据真实 artifact 和 reload 状态生成结果。

**修改方式**

不把结果解释和恢复选择接入模型循环。保持顺序：

```text
run terminal
-> Observation Collector
-> Goal Evaluator
-> Result Summary Service
-> satisfied: completed
-> not satisfied: deterministic Recovery Proposal
-> 需要重新执行或改计划: waiting_for_user
```

该顺序已经由现有 Reconciler 和 Read Model 承担，本阶段只补回归测试和前端呈现，不增加 Harness wake。模型解释改为用户点击“解释此结果”后调用现有只读 Assistant。解释不能修改 outcome、capability、artifact、plan 或 recovery。

### 5.9 降低 Assistant 的入口权重

**当前情况**

TopBar 中的 Assistant 和 Agent 页 Goal Composer 都提供文本输入，但 Assistant 只读，不能创建任务。

**修改方式**

- Goal Composer 保持唯一任务创建入口；
- TopBar 的 Assistant 更名为“解释当前任务”或等价文案；
- 没有当前项目或任务时显示只读边界，不显示类似“开始任务”的建议；
- Assistant 请求继续使用 `/api/assistant/chat`，不得转发为 create/answer/approve 命令；
- `TaskCard` 的“解释”按钮只预填当前 task/run/evidence context。

### 5.10 审计和详情

**修改方式**

保留现有 `AgentHarnessAttempt`、`AgentHarnessStep`、lifecycle events 和 Agent Task events。`AgentHarnessStep` 只增加实施所需字段：

- `wake_reason`；
- `provider_ref`；
- `action_result_code`。

不新增 `ModelCallRecord` 表或完整 transcript。Prompt 内容、provider key、源影像和完整日志不得写入普通审计。

Harness 摘要从 Agent 默认页面移入 `TaskDetails`。Advanced Mode 只控制技术证据可见性，不改变任何后端行为。

## 6. 数据结构变化

### 6.1 后端 schema

| 结构/字段 | 变化 | 产生位置 | 使用位置 |
|---|---|---|---|
| `AgentTaskResponse.task_kind` | 新增：`execution` / `plan_only` | Read Model | Agent Workspace、Runs |
| `AgentTaskResponse.execution_performed` | 新增非空布尔值；是否已由 Gateway dispatch | Read Model | 结果和 plan-only UI |
| `AgentTaskResponse.status_code` | 新增稳定 code，替换自由 `current_action` 文案 | Read Model | i18n |
| `AgentTaskNextAction` | 删除自由 title/description/decision_id；保留 type 并增加 batch_id/disabled_reason_code | Read Model | 唯一主动作和 i18n |
| `ApprovalSummary.registered_subject_count` | 新增并进入 summary hash | Approval Summary Service | 权威审批合同、公共投影 |
| `ApprovalSummary.selected_subject_ids/count` | 新增并进入 summary hash | Approval Summary Service | 实际执行范围、Approval UI |
| `ApprovalSummary.reviewed_node_ids/count` | 新增并进入 summary hash | Approval Summary Service | Reviewed Plan 范围、Approval UI |
| `AgentTaskResultSummary.summary_code` | 新增稳定 code | Result Summary Service | Result UI、Runs |
| `AgentLifecycleRecord.pending_decision_batch` | 新增，替换 `pending_decision` | Planner / Command Service | Answer API、Read Model |
| `AgentLifecycleRecord.planning_lease_* / planning_generation` | Harness 关闭时的持久 planning claim | Scheduler / Store | 双 owner 去重和崩溃接管 |
| `AgentLifecycleRecord.blocking_error_code` | 永久 planning 错误 | Planning Service | `HUMAN_HANDOFF` 投影和防重复 rescan |
| `AgentHarnessContext.evidence_refs` | 新增稳定引用 | Context Builder | Prompt、step 审计 |
| `AgentHarnessStep.wake_reason` | 新增 | Scheduler / Harness | 详情和审计 |
| `AgentHarnessStep.provider_ref` | 新增 | Harness | 详情和审计 |
| `AgentHarnessStep.action_result_code` | 新增 | Harness | 详情和错误分析 |

持久格式切换使用单一新 schema version。同步更新当前消费者和 SQLite payload tests，不保留旧字段 fallback、双写或兼容 parser。

### 6.2 Answer API

请求改为：

```json
{
  "batch_id": "decision_batch_xxx",
  "answers": [
    {"item_id": "atlas", "value": "aal"},
    {"item_id": "repetition_time", "value": 2.0}
  ],
  "command_id": "answer:...",
  "actor": "desktop-user"
}
```

后端一次校验 batch、project、lifecycle、plan hash、item 唯一性、必填项和 option。失败时返回字段级结构化错误，原批次保持不变。

`POST /api/projects/{project_id}/agent/tasks` 和 Answer API 统一返回 `202 Accepted`，响应体仍是持久化后的 `AgentTaskResponse` 投影；这表示命令已接受，不表示规划或执行完成。同步更新 route、OpenAPI contract、前端 client 和当前测试，不保留 200/202 双合同。approve/cancel 等现有同步命令的返回码不在本方案中改变。

### 6.3 Schema cutover 和重启恢复

这是一次单格式切换，不实现旧格式迁移、双读或 fallback。目标版本一次确定为：

| 持久/公共面 | 目标版本 |
|---|---|
| `AgentLifecycleRecord` | 4 |
| `ApprovalSummary` | 2 |
| `ActionEnvelope`、Harness attempt/step/context | 2 |
| Harness `prompt_template_version` | 2 |
| `AgentTaskResponse`、list response、event payload | 2 |

所有上述 Pydantic model 使用 `extra="forbid"` 或等价严格校验；store 在构造 model 前检查精确版本。遇到旧版本返回 `AGENT_SCHEMA_VERSION_UNSUPPORTED`，不得静默忽略旧 `pending_decision` 后继续投影。

实施和自动测试只使用隔离的新数据库；“重启恢复”验收专指新版本记录在服务重启后的恢复。进入真实桌面升级或 Release 前，必须清点数据库中的全部 lifecycle、Harness attempt/context/step、Reviewed Plan 内 Approval Summary 和相关公共投影，而不只是活动任务。只要存在任一旧版本记录就不得切换，因为 list/detail 会读取历史终态记录。旧数据的保留、导出或清除必须由单独批准的 Release/数据处置任务决定；本方案不删除、转换、静默隐藏或伪装用户历史记录。

## 7. 文件修改清单

### 7.1 后端

| 文件 | 修改内容 |
|---|---|
| `schemas/agent_task.py` | 精简公共合同，增加 task kind、execution、status/summary code 和批量 answer |
| `schemas/agent_lifecycle.py` | `PendingDecisionBatch`、blocking error、deterministic planning lease/generation、schema version |
| `schemas/agent_harness.py` | 缩减 Action kind；增加 `RequestDecisionBatchPayload`、context/step 最小审计字段 |
| `schemas/approval_summary.py:ApprovalSummary` | 增加 subject/node 结构化范围并进入稳定 hash |
| `services/agent_task_command_service.py` | create/answer 改为持久化后 wake；删除单决定和内联 planning 路径；同步 Reviewed Plan 公共 Approval Summary 字段 allowlist |
| `services/agent_planning_service.py:AgentPlanningService.advance_planning()` | 新增窄 planning collaborator；不依赖任何执行 service/gateway |
| `services/agent_task_read_model.py:AgentTaskReadModel` | 生成全部结构化 UI 字段，删除英文可解析语义 |
| `services/agent_harness_service.py` | 只处理 request_decision/draft_plan，保留 `run_one()` |
| `services/agent_harness_context_service.py` | 读取允许的结构化项目证据和 refs |
| `runtime/agent_task_scheduler.py` | 新增；替换 startup-only Harness scheduler |
| `runtime/agent_harness_scheduler.py` | 删除 |
| `runtime/agent_capability_catalog.py` | 缩减 Action/state 矩阵 |
| `planner/agent_model_adapter.py` | 缩减模型输出 schema 和 Prompt |
| `services/agent_task_reconciler.py:AgentTaskReconciler` | 检查并补回归测试；现有终态协调不接入 Harness wake |
| `services/observation_collector.py`、`services/goal_evaluator.py`、`services/recovery_policy_service.py` | 保持结果、评估和恢复的确定性 owner，只补当前调用链回归 |
| `services/agent_task_result_summary.py:AgentTaskResultSummaryService.build()` | 增加稳定 summary code，保持 artifact/reload 真实性 |
| `services/approval_summary_service.py:ApprovalSummaryService` | 产生结构化 subject/node 范围，保持 summary hash 绑定 |
| `services/mock_store.py` | schema payload 更新和 scheduler 所需查询 |
| `api/dependencies.py` | 同步 ProjectStore Protocol |
| `api/agent_task_routes.py` | create/answer 返回新的任务投影 |
| `core/config_schema.py`、`.env.example` | 增加每次 wakeup 步数和运行期 rescan 间隔配置 |
| `main.py` | lifespan 启动和关闭 AgentTaskScheduler |

### 7.2 前端

| 文件 | 修改内容 |
|---|---|
| `features/navigation/GlobalNavigationRail.tsx` | 只显示四个顶层入口 |
| `features/app/AppShellView.tsx` | 旧 workspace 仅作为详情 outlet |
| `features/agent/AgentWorkspace.tsx` | 使用单一 TaskCard |
| `features/agent/components/TaskCard.tsx` | 新增；统一状态、决定、审批、进度、结果和恢复 |
| `features/agent/components/TaskDetails.tsx` | 承载 Harness、技术证据和兼容入口 |
| `features/agent/components/CurrentAction.tsx` | 删除；标题逻辑移入 TaskCard |
| `features/agent/components/HarnessStatusCard.tsx` | 删除；内容移入 TaskDetails |
| `features/agent/components/MacroProgress.tsx` | 重命名为 `TaskProgress.tsx`，移除独立容器 |
| `features/agent/components/ProjectSummaryCard.tsx` | 删除 |
| `features/agent/components/NextActionCard.tsx` | 重命名为 `TaskActionPanel.tsx`，移除独立 Card |
| `features/agent/components/RecoveryActionCard.tsx` | 重命名为 `TaskRecoveryPanel.tsx`，移除独立 Card |
| `features/agent/components/ResultSummaryCard.tsx` | 重命名为 `TaskResultPanel.tsx`，移除独立 Card |
| `features/workspaces/RunsWorkspace.tsx` | 改用共享 `summary_code` 映射，删除对旧 ResultSummaryCard helper 和后端英文 `current_action` 的依赖 |
| `features/agent/useAgentTaskController.ts` | 批量 answer、新合同、active-only polling |
| `lib/types/agentTask.ts`、`lib/api/agentTasks.ts` | 同步新 API |
| `features/tools/AssistantSheet.tsx`、`AssistantDock.tsx` | 降为任务解释入口 |
| `i18n/messages/en.ts`、`zh-CN.ts` | 使用 summary/action code，不映射英文句子 |

### 7.3 测试和文档

| 文件/范围 | 修改内容 |
|---|---|
| `tests/unit/test_agent_harness_*.py` | 两种 Action、有限推进、lease、预算和执行边界 |
| `tests/integration/test_agent_harness_lifecycle.py` | create 到阻塞点、重启恢复、fallback |
| `tests/unit/test_agent_task_commands.py` | 异步 create/answer、决定批次、审批顺序、plan-only |
| `tests/unit/test_agent_planning_service.py` | 持久 planning claim、逐状态恢复、永久错误收敛和执行依赖隔离 |
| `tests/unit/test_agent_task_read_model.py` | 新结构化字段、无英文推断、GET 纯读 |
| `tests/unit/test_agent_task_reconciler.py` | 终态协调顺序，并证明不调用 planning scheduler |
| `tests/unit/test_agent_task_api.py` | create/answer 202、结构化错误和 list/detail contract |
| `features/navigation/__tests__/GlobalNavigationRail.test.tsx`、`workspaceModel.test.ts` | 四入口、旧 workspace deep-link |
| `features/agent/__tests__/AgentWorkspace.test.tsx` | 单 TaskCard、单 primary、决定批次和结果真实性 |
| `features/agent/__tests__/useAgentTaskController.test.tsx` | active-only polling、project switch、批量 answer |
| `lib/api/__tests__/agentTasks.test.ts` | 202 合同和新增结构化字段 |
| `features/workspaces/RunsWorkspace.test.tsx` | summary code、result outcome、Agent Task/project run 合并和去重 |
| `docs/规划与运行时/受控单AgentHarness.md` | 更新为两种模型 Action 和后台推进合同 |
| `docs/架构与决策/系统架构.md` | 更新 Agent Task API、scheduler 和信息分层 |
| `docs/安全与审批/安全边界.md` | 明确后台自动推进不扩大审批权限 |
| `PROJECT_STATE.md` | 只在源码、测试和产品入口验证后更新 |

## 8. 风险和处理

| ID | 风险 | 处理 | 验证 |
|---|---|---|---|
| H-01 | 隐藏旧导航后证据不可达 | TaskDetails 和 Runs 保留稳定入口 | deep-link 和详情导航测试 |
| H-02 | 后台 wake 丢失导致任务长期 preparing | SQLite 状态为权威，运行期/启动扫描重新登记 | queue-full、丢 wake、kill/restart 测试 |
| H-03 | 双 owner 重复调用模型或规划 | expected status、lease、step key、command ID | 并发 claim 和重复 wake 测试 |
| H-04 | create 返回后后台立即失败但 UI 不刷新 | preparing/running 才 polling，结构化 error 投影 | provider failure UI 测试 |
| H-05 | 删除 Action 后 Prompt 或 fixture 仍生成旧 kind | schema fail closed，全仓删除旧 kind | provider、replay、catalog 测试 |
| H-06 | 批量回答部分写入 | 全批次校验通过后单次 lifecycle transition | 缺项/非法项时 DB 不变测试 |
| H-07 | summary code 和 i18n 漏配 | 后端 Literal + 前端 exhaustiveness test | en/zh-CN contract 测试 |
| H-08 | plan-only 被显示为真实执行 | `task_kind` 和 `execution_performed` 后端权威 | ticket/gateway/runner 零调用测试 |
| H-09 | 模型解释提升科学结果等级 | 解释只读，outcome 来自 evaluator | metadata-only/reload failure 测试 |
| H-10 | Scheduler 变成第二执行入口 | 它只能调用规划服务，依赖中不注入 gateway/runner | spy 和依赖边界测试 |
| H-11 | Advanced Mode 改变后端行为 | 只作为前端可见性偏好 | on/off 相同 API 命令测试 |
| H-12 | 文档宣称超过实际验证 | source、test、packaging、release 分开记录 | 最终文档和 Git 基线检查 |
| H-13 | 任一旧历史 payload 使 list/detail 在 schema cutover 后不可用 | 精确版本检查、extra forbid、全部旧记录阻断切换 | 旧 lifecycle/Harness/Approval payload fail-closed 和历史 list 测试 |
| H-14 | Context 新证据复用旧 hash | canonical context hash 包含 refs、catalog、memory 和版本 | 改任一输入即改变 hash 的参数化测试 |

## 9. 测试与验收

### 9.1 后端单元测试

必须覆盖：

1. create 只持久化 lifecycle、登记 wake 并返回 202；用 spy 证明请求调用链没有模型、Planner 或 Harness step，避免依赖机器性能的毫秒阈值；
2. 一个 wakeup 能连续推进安全 planning step，并在用户输入、审批、运行和终态停止；
3. 超过 `max_steps_per_wakeup` 时让出、重新登记或触发 rescan，不标记失败且最终继续推进；
4. Harness 开/关两条路径的重复 wake、命令重放和双 scheduler 都不产生第二个 plan 或模型调用；确定性路径必须验证持久 planning claim/过期接管；
5. provider 不可用时显式回退到确定性 Planner；
6. 删除的 Action 全部 fail closed，且不能调用任何执行依赖；
7. 多个决定一次回答，缺项时不消费批次；
8. Approval Summary/hash 变化阻止旧审批；
9. plan-only 不创建 dry-run、ticket、run 或数值产物；
10. GET/list 不调用 scheduler、reconcile 或模型；
11. run 终态后按 Observation -> Goal Evaluation -> Result/Recovery 顺序推进；
12. 队列满、跨线程 wake、丢 wake、`PLAN_VALIDATED` 中断和过期 `RUNNING` lease 均能由 rescan 恢复；
13. decision collector 收集/排序/去重全部缺口，超过 8 项 fail closed，choice/数组/number/boolean 类型校验正确；
14. v3 lifecycle 和旧 Harness payload 以 `AGENT_SCHEMA_VERSION_UNSUPPORTED` fail closed，新版本记录重启恢复；
15. 任一 evidence ref、catalog、MemoryContext 或 prompt version 变化都会改变 context hash；
16. 逐状态恢复表全部覆盖，永久错误只转一次 `HUMAN_HANDOFF`，rescan 不重复调用；
17. scheduler 只注入窄 `AgentPlanningService`，依赖图和 spy 证明不可达执行 service/gateway；
18. rawdata、路径、ticket、gateway 和 artifact truthfulness 回归通过。

### 9.2 前端测试

必须覆盖：

1. 顶层只有 Projects、Agent、Runs、Settings；
2. 无任务时只显示 Goal Composer，不显示执行工具按钮；
3. 每个任务状态最多一个 primary action；
4. planning 无真实百分比时不显示伪进度；
5. 决定批次一次提交，字段错误定位到对应 item；
6. approval 直接使用结构化 subject/node 数量；
7. plan-only、partial、failed、canceled 不显示成数值执行成功；
8. Harness、hash、provider 和兼容页面只在详情中出现；
9. project switch 立即清空旧 task 并中止旧请求；
10. active task 自动 polling，等待用户和终态停止 polling；
11. Assistant 不产生 create/answer/approve 请求；
12. en、zh-CN 和 keyboard/a11y 通过。

### 9.3 推荐验证命令

实施期间先运行 focused tests：

```powershell
python -m pytest tests/unit/test_agent_harness_service.py tests/unit/test_agent_harness_context.py tests/unit/test_agent_harness_lease.py tests/unit/test_agent_harness_execution_boundary.py tests/integration/test_agent_harness_lifecycle.py --tb=short --basetemp=.pytest_tmp
python -m pytest tests/unit/test_agent_task_commands.py tests/unit/test_agent_planning_service.py tests/unit/test_agent_task_read_model.py tests/unit/test_agent_task_reconciler.py tests/unit/test_agent_task_api.py tests/unit/test_approval_summary.py --tb=short --basetemp=.pytest_tmp
npm --prefix src/frontend run format:check
npm --prefix src/frontend run typecheck
npm --prefix src/frontend run test
npm --prefix src/frontend run test:project-runs
npm --prefix src/frontend run build
```

共享 lifecycle、store、审批或 runtime 发生变化后运行后端完整测试：

```powershell
python -m pytest --tb=short --basetemp=.pytest_tmp
```

桌面主进程未修改时无需在每个子任务重复打包。最终集成和产品入口验收运行：

```powershell
npm --prefix desktop/electron run check
```

该命令只证明 Electron contract，不等于 GUI 验证。若任务明确包含已有 unpacked 产物验证，则按 `docs/桌面与前端/桌面应用打包.md` 使用隔离 workspace/userData、`MEDIMAGE_DESKTOP_SMOKE=1` 和绝对 `MEDIMAGE_DESKTOP_SMOKE_RESULT` 执行真实 unpacked smoke；否则最终报告必须明确 packaged GUI 未验证。Harness 默认开关不依赖该 smoke 自动改变。

pytest 后按 `AGENTS.md` 只清理仓库根直接子项 `.pytest_cache/` 和 `.pytest_tmp*`，并复查 `git status --short`。

### 9.4 人工验收

- 选择项目后直接进入 Agent；
- 普通用户不需要理解 Data、Plan、Preprocessing、QC、Results、Harness 或 node；
- 无科学歧义时，只需“提交目标 + 审批执行”；
- 有多个科学问题时，只额外提交一次决定批次；
- 一次审批后不需要点击 monitor、validate、refresh 或 report；
- 结果页能明确区分 computed、partial、metadata-only、failed 和 plan-only；
- 所有技术证据仍能从 Runs 或 Task Details 到达；
- 页面刷新、服务重启和重复命令不产生重复执行；
- rawdata 内容、大小和修改时间保持不变。

## 10. 实施顺序和 Gate

### Phase 1：公共合同和导航

内容：

- 增加结构化 Agent Task 字段；
- 同步权威 Approval Summary、公共投影和 202 create/answer 合同；
- 删除前端英文/artifact 推断；
- 顶层导航收敛为四项；
- 保留旧 workspace deep-link。

退出条件：contract tests、四入口导航和兼容入口测试通过。

### Phase 2：单 TaskCard

内容：

- 合并 Agent Workspace 卡片；
- Harness 和技术证据移入详情；
- Assistant 降为解释入口；
- 完成双语和 a11y 测试。

退出条件：所有 fixture 最多一个 primary action，普通页面无内部标识和手动执行工具。

### Phase 3：决定批次和后台有限推进

内容：

- 先切换 `PendingDecisionBatch`、批量 Answer API 和结构化错误合同；
- 增加 AgentTaskScheduler；
- 抽出窄 `AgentPlanningService`，实现 Harness 关闭路径的持久 planning claim 和逐状态恢复；
- create/answer 改为持久化后 wake；
- 实现有限 batch、lease、幂等、shutdown 和启动恢复；
- Harness Action 缩减为两种；
- 扩展 `AgentHarnessContext.evidence_refs`，补齐 step 最小审计字段。

退出条件：202 contract、批次原子性、HTTP 零模型调用、重启/并发/重复 wake 测试通过，Execution Gateway 不可从 scheduler 到达。

### Phase 4：执行后结果和端到端交互

内容：

- run 终态后的结果/恢复自动投影；
- plan-only、partial、failure、canceled 和 recovery E2E；
- AgentWorkspace/Controller 双语与 a11y 回归。

自动 Gate：`test_agent_task_reconciler.py`、`AgentWorkspace.test.tsx`、`useAgentTaskController.test.tsx`、`agentTasks.test.ts` 和 `npm --prefix src/frontend run test:project-runs` 全部通过。人工退出条件：标准任务操作收敛为“目标 + 可选一次决定批次 + 一次审批”，失败恢复仍需新审批。

### Phase 5：完整回归和文档

内容：

- backend 全量、frontend 全套、desktop check；
- 文档影响检查；
- source/test/package/release 状态分级记录。

退出条件：本方案第 13 节和 `AGENTS.md` 第 8 节 Definition of Done 全部通过。未通过时不得提升发布或科学能力声明。

## 11. 文档影响

实施完成后必须检查并按实际结果更新：

- `docs/规划与运行时/受控单AgentHarness.md`；
- `docs/架构与决策/系统架构.md`；
- `docs/安全与审批/安全边界.md`；
- `docs/文档索引.md`；
- `README.md`、`README_CN.md`；
- `.env.example`；
- `PROJECT_STATE.md`；
- 阶段十 Agent-first 文档中“顶层四入口”的完成状态；
- 阶段十二 00～04 中被本文缩减或延期的内容。

`AGENTS.md` 现有规则已经覆盖单一状态源、GET 纯读、审批顺序、plan-only、rawdata 和结果真实性。只有实施中发现新的可复发问题时才更新，不为本方案重复追加规则。

## 12. 已确定的实施决策

1. Harness 在本方案实施前后都保持默认 `false`。是否默认开启属于后续独立产品/Release 决策，本任务不自动改变。
2. create 和 answer 使用单一 `202 Accepted` 合同，不保留 200 fallback。
3. 主要任务组件固定为 `TaskCard`；内部可以拆小组件，但不得恢复多张同级主卡片。
4. 旧工作区本方案只隐藏、不删除。只有 Runs/Details 覆盖全部当前消费者且另有明确删除任务时才处理。

当前没有阻塞实施设计的待选方案。实施前仍需用全仓搜索确认旧 helper/字段的完整消费者清单；真实 provider 延迟、桌面 GUI 行为和数据库中全部旧版本 payload 数量属于必须实测的环境事实，不能在本文中预设。若任一事实与本文前提冲突，应暂停对应 Phase、更新方案并重新 Review，不得增加兼容 fallback 绕过。

## 13. 完成标准

本节必须与 `AGENTS.md` 第 8 节 Definition of Done 同时满足；任一项未通过都不能声明实现完成。

- 顶层导航和 Agent 页面围绕任务，而不是工具、stage 或内部服务；
- 前端不解析英文摘要，不通过 artifact 类型推断 task kind；
- create/answer 快速返回，后台有界推进且可重启恢复；
- 模型 Action 只有当前必要的两种，不能触达任何执行能力；
- 多个必要问题一次提交，科学选择仍由用户确认；
- 审批、Execution Ticket、Execution Gateway、Pipeline Runtime 和 rawdata 只读边界未改变；
- 结果只由 Observation、Goal Evaluation 和可重载 artifact 决定；
- 普通页面信息维持 Level 1，结果和技术证据按需展开；
- focused、full backend、frontend、desktop 和人工验收按实际影响完成；
- 所有受影响文档同步，`git diff` 和 `git status` 无无关改动、秘密、研究数据或生成物。
