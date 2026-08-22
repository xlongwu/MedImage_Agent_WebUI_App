# 任务 8D：受控 Retry 与局部 Replan

> 归档状态：该文档对应的当前阶段范围已完成；仅作为历史实施与审计记录保留。

## Handoff

- **Status**：Implemented / Source Verified（2026-07-15）
- **Task Mode**：Feature Bundle Mode + Protected Change；涉及 Pipeline Executor、Execution Gateway、Approval Gate、票据和状态迁移
- **Goal**：将 8C 的无副作用 Recovery Proposal 转换为经策略、审批、配额和一次性能力约束的 retry/resume/replan，并在执行后自动回到 Observation 与 Goal Evaluation。
- **Background**：原 ticket 一次消费；legacy retry runtime 已拒绝无执行契约调用；当前尚无 child ticket、recovery attempt ledger、failed-subject/resume 执行适配或新 plan 审批分支。
- **Current Behavior**：orchestrator 能提出粗粒度 retry proposal，但没有完整 retry execution path；参数或范围变化会回到 `PLAN_DRAFTED`，尚未形成新 reviewed plan lineage。
- **Required Behavior**：同参数/后端/范围的低风险幂等恢复可按 Approval Policy 执行；任何 reviewed contract 变化都生成新 Reviewed Plan 和新审批；配额耗尽进入人工接管；每个 attempt 后重新观察和评价。
- **Non-goals**：不支持无限后台 worker、多机调度、任意命令、自动安装依赖、自动修改数据或未经审批的 backend switch。

## Files to Read Before Editing

- 8A–8C 全部 schema/service/decision tests
- `src/backend/app/runtime/execution_gateway.py`
- `src/backend/app/runtime/capability_enforcement.py`
- `src/backend/app/runtime/pipeline_executor.py`
- `src/backend/app/schemas/execution_ticket.py`
- `src/backend/app/services/execution_ticket_service.py`
- `src/backend/app/services/agent_orchestrator.py`
- `src/backend/app/planner/reviewed_plan_store.py`
- `src/backend/app/planner/approval_gate.py`
- `src/backend/app/runtime/retry_runtime.py`
- node state/checkpoint/subject scheduling/store/API/frontend tests。

## Exact Anchors

- `ExecutionGateway.dispatch` 与 `current_safe_allowlist_fingerprint`
- `ExecutionTicketService.issue/validate/consume/revoke`
- `ExecutionTicket.retry_policy/canonical_hash`
- `enforce_node_capabilities` 与 Pipeline Executor 的 Runner dispatch 前检查
- `AgentOrchestrator.dispatch_execution/observe/propose_retry` 和 transition table
- `reviewed_plan_identity`、`save_reviewed_plan`、`resolve_reviewed_plan_for_execution`
- 8C Recovery Proposal 的 canonical diff、approval class 和 quota snapshot

## Files to Read Only

- 科学 kernels、golden fixtures、rawdata、用户数据、原 run/state/artifacts。
- 与当前恢复链无关的真实外部工具 wrappers，除非未来独立 implementation-ready handoff 明确纳入。

## Proposed Files to Create / Edit

| 动作 | 文件 | 责任 |
|---|---|---|
| 创建 | `src/backend/app/schemas/recovery_attempt.py` | attempt、parent lineage、scope、approval、ticket、status、quota consumption |
| 创建 | `src/backend/app/services/recovery_policy_service.py` | proposal 可执行性、approval class、quota 的最终权威判定 |
| 创建 | `src/backend/app/services/recovery_execution_service.py` | child ticket 签发、gateway dispatch、attempt 状态和回评协调 |
| 创建 | `src/backend/app/services/replan_service.py` | 对 proposal patch 生成新 candidate/reviewed plan lineage，不自动审批 |
| 修改 | `src/backend/app/schemas/execution_ticket.py` | recovery child ticket binding 或兼容扩展：parent ticket/run、attempt、action、scope |
| 修改 | `src/backend/app/services/execution_ticket_service.py` | 一次性 child ticket 签发、验证、消费、撤销和审计 |
| 修改 | `src/backend/app/runtime/execution_gateway.py` | 识别受控 recovery capability；仍执行全量一致性和路径校验 |
| 修改 | `src/backend/app/runtime/pipeline_executor.py` | 仅在明确 ticket scope 下支持 subject subset/resume；Runner 前强制能力 |
| 修改 | `src/backend/app/services/agent_orchestrator.py` | `WAITING_FOR_RECOVERY_APPROVAL → RECOVERING → OBSERVING → EVALUATING` 闭环 |
| 修改 | API/store/frontend（若纳入） | recovery command/query、审批、attempt timeline、禁用/失败/人工接管状态 |
| 创建 | policy/ticket/runtime/E2E/safety tests | 重试、恢复、重规划、回评、配额、重放和 crash recovery |

## Controlled Recovery Paths

### 1. Safe Retry

必须同时满足：

- proposal action 为 `SAFE_RETRY` 或 `RETRY_FAILED_SUBJECTS`；
- canonical diff 证明参数、backend、roots、Goal Contract 和输出范围未变；subject subset 只能缩小到原范围内失败项；
- Node Contract 声明 retryable，error class 被允许，且 idempotency/output strategy 安全；
- quota 未超；原 approval policy 明确允许该 recovery class。

即使无需“新 Reviewed Plan”，也不得重用已消费 ticket。后端签发一次性 child ticket，绑定 parent ticket、parent run、proposal、attempt、node/subject scope、同一 plan/goal hashes 和新的 audit ID。

### 2. Resume

Resume 必须验证 checkpoint：

- checkpoint 属于同 project/run/plan/contract versions；
- 已完成 node artifacts 仍完整并可重载；
- remaining subgraph 是原 DAG 的合法未完成部分；
- 依赖状态一致，无参数/backend/root 漂移；
- Node Contract 明确支持 resume。

不满足任一条件时不得把 retry 冒充 resume。

### 3. Parameter Change / Backend Switch / Replan

这些 action 只能创建新的 candidate plan：

```text
Recovery Proposal
→ Plan Patch Candidate
→ Plan Validator + Node Contracts
→ New Reviewed Plan ID / Plan Hash / Goal Contract Hash
→ New Approval Context
→ New Execution Ticket
→ Execution Gateway
```

旧 approval、ticket 和 plan hash 不可沿用。局部 replan 可以复用原计划未变节点作为 lineage 信息，但新计划必须完整可验证，不能只保存 patch 并依赖隐含旧状态。

### 4. Human Handoff

以下情况直接进入人工接管：

- quota 达到任一硬限制；
- proposal/observation/evaluation 证据冲突或 root cause unknown；
- 高风险或外部执行没有新审批；
- checkpoint/产物完整性无法证明；
- 审计或持久化失败；
- 重复恢复仍未缩小 goal gap。

Handoff record 必须包含已尝试 attempts、剩余 goal gaps、阻塞原因和安全的后续人工动作，不包含可直接执行的自由文本命令。

## Approval Policy

建议的最小枚举：

| Class | 含义 |
|---|---|
| `within_original_approval` | 原审批显式允许相同契约下的有限 retry；仍需 child ticket 和审计 |
| `explicit_retry_approval` | 用户/维护者确认本 attempt 后签 child ticket |
| `new_plan_approval` | 新 reviewed plan 和完整审批 |
| `not_executable` | 只能人工接管 |

若项目 policy 未明确配置，默认 `explicit_retry_approval`；外部工具、backend switch、参数/节点/范围变化一律 `new_plan_approval`。

这里的默认只决定审批类别，不能补足 quota。五个必需 quota 维度任一缺失或耗尽时，proposal 为 `not_executable` 并进入 `HUMAN_HANDOFF`；显式审批不得越过硬配额。Replan 不重置 lifecycle 总 attempts、累计 recovery wall-clock 或 replan count。

每条 approval record 必须不可变绑定：`project_id`、`lifecycle_id`、`recovery_proposal_id`、candidate hash、action/scope、parent plan/goal/ticket/run hashes、quota snapshot、批准 actor、批准时间、过期时间、撤销状态、command/idempotency ID 和 audit ID。child ticket 签发前再次验证未过期、未撤销且 bindings 未漂移。

当前单机桌面 actor 只能沿用项目既有本地审批身份语义，不能宣称已完成多用户认证或职责分离。若任务要求网络服务或多用户审批，触发 Stop Condition，先单独完成身份、ACL 和审计防篡改设计。

## Lifecycle and Attempt State

建议 lifecycle v2 分支：

```text
DIAGNOSING
  → RECOVERY_PROPOSED
  → WAITING_FOR_RECOVERY_APPROVAL
  → RECOVERY_READY
  → RECOVERING
  → OBSERVING
  → EVALUATING
  → GOAL_SATISFIED | DIAGNOSING | HUMAN_HANDOFF
```

每个 `RecoveryAttemptRecord` 独立持久化：`PROPOSED/APPROVED/TICKET_ISSUED/RUNNING/OBSERVED/EVALUATED/EXECUTION_SUCCEEDED/EXECUTION_FAILED/HANDOFF`。`EXECUTION_SUCCEEDED` 只表示本 attempt 的受控执行结束，不表示用户目标满足；生命周期仍须以 `GOAL_SATISFIED` 为唯一目标完成状态。状态迁移必须事务化或采用可恢复的事件账本；崩溃后不能重复签票或重复执行。

## Quota Enforcement

- quota 在 proposal、approval、ticket issue、gateway dispatch 四个边界重复检查；最终以执行前检查为权威。
- attempt 在成功 dispatch 前后分别记录 reservation/consumption，避免并发请求双花。
- lifecycle、node、subject+node、replan、wall-clock 任一配额超限都拒绝执行并写审计。
- 每次失败和 replan 都不会刷新已消耗 quota。新 plan 不得重置 lifecycle 总 attempts、累计 recovery wall-clock、replan count、node attempts 或 subject+node attempts；新 plan/project policy 只能在剩余额度内进一步收紧，不能扩大或归零重计。

## Output and State Rules

- 原 run、node states 和 artifacts 永不原地修改。
- 每个 recovery attempt 使用独立 run ID、state root 和输出 namespace；复用 artifact 只能通过只读 lineage 引用。
- overwrite policy 必须来自 Node Contract 和 ticket；`fail_if_exists` 是默认。
- attempt 结束后 collector 读取新 run 及允许的父 lineage，生成新 Observation；Goal Evaluator 对同一 Goal Contract（或新 plan 的新 contract）重新评价。
- 不得因为 retry runner 返回 `ok=true` 直接写 `GOAL_SATISFIED`。

## API Shape

API 只暴露意图命令和查询，不暴露裸 runner：

- `POST .../recovery-proposals/{id}/approve`
- `POST .../recovery-proposals/{id}/execute`
- `POST .../recovery-proposals/{id}/create-replan`
- `GET .../recovery-attempts`
- `GET .../recovery-attempts/{id}`

所有命令带 command/idempotency ID，并校验 project/lifecycle/proposal binding。具体路径在 implementation handoff 中按现有 domain router 风格冻结。

## Acceptance Criteria

- [ ] 相同参数、后端、roots、scope 的低风险幂等 retry 只能按明确 Approval Policy 获得 child ticket 并经 Gateway 执行。
- [ ] 原 ticket 重放、child ticket 跨项目/跨 attempt/超 scope/过期/重复消费全部拒绝且审计。
- [ ] failed-subject retry 不重新运行成功被试，不覆盖其 artifact。
- [ ] resume 只从验证 checkpoint 和原 DAG 合法 remaining subgraph 继续。
- [ ] 参数、节点、backend、root、output scope 或 Goal Contract 变化必产生新 Reviewed Plan 和新审批。
- [ ] quota 超限、并发双花、持久化/审计失败均进入 `HUMAN_HANDOFF`，无 runner side effect。
- [ ] 每个 attempt 后自动执行 Observation + Goal Evaluation；只有 evaluation satisfied 才目标完成。
- [ ] crash/restart 后可恢复 attempt 状态且不会重复 dispatch。
- [ ] 外部执行仍默认关闭，rawdata 未修改，原 run/state/artifact 未覆盖。

## Safety Invariants

- 所有真实 recovery dispatch 都必须通过 Execution Gateway；route/service 不得直接调用 runner。
- child ticket 是服务端签发、一次性、不可扩权的能力；客户端 proposal/approval 字段不能替代它。
- Approval Gate、safe paths、allowlist、audit、environment gates 和 rawdata 只读保护不得弱化。
- 审计或状态持久化失败时 fail closed；不得先执行后补记录。
- recovery 成功只表示 attempt 执行完成，不能替代 Goal Evaluation。

## Allowed Commands

- 只读检索/检查、schema/import 检查、focused pytest、受影响 backend suite。
- 若修改前端，运行仓库规定的 typecheck/test/build。
- 仅允许临时目录和合成 fixtures；不得启用真实 MATLAB/SPM/DPABI/DICOM/GPU/GUI 执行。
- 不允许打包、发布、提交、推送或修改用户数据。

## Validation Commands

```powershell
python -m pytest tests/unit/test_execution_ticket.py tests/unit/test_capability_enforcement.py tests/unit/test_agent_lifecycle.py tests/unit/test_execute_reviewed_api.py --tb=short --basetemp=.pytest_tmp
python -m pytest tests/integration/test_reviewed_execution_synthetic_smoke.py tests/integration/test_real_project_run_lifecycle_smoke.py tests/integration/test_node_contract_smoke.py --tb=short --basetemp=.pytest_tmp
```

新增 child-ticket replay、policy matrix、failed-subject、resume checkpoint、new-plan approval、quota concurrency、crash recovery 和完整闭环 E2E tests。若修改前端，执行 `npm --prefix src/frontend run typecheck`、`test`、`build`。pytest 后执行限定清理。

## Stop Conditions

- 如果 Execution Gateway 无法在 Runner 前强制 recovery scope，停止；不得在 route/service 复制检查后直接调用 runner。
- 如果 child ticket 的 parent/attempt/plan/goal 绑定不能不可变持久化，停止签发。
- 如果 retry 会覆盖原产物或无法区分 attempt 输出，保持 proposal/handoff，不执行。
- 如果新 plan 无法完成 validator、approval 和 ticket 全链路，不能只执行 patch。
- 如果外部 backend 的审批、安全路径、环境 gate 或审计不完整，保持禁用。
- 如果需要多用户/网络身份认证或审批人职责分离，停止本任务并先建立独立身份与授权方案。

## Completion Report Requirements

列出恢复 action、approval policy、child ticket fields、Gateway/Runtime 改动、attempt 状态机、quota 和并发语义、输出隔离、replan lineage、API/frontend 影响、安全不变量、所有验证命令及结果、未验证外部路径和剩余风险。
