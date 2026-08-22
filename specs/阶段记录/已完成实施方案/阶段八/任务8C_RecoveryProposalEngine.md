# 任务 8C：Recovery Proposal Engine

> 归档状态：该文档对应的当前阶段范围已完成；仅作为历史实施与审计记录保留。

## Handoff

- **Status**：Implemented / Source Verified（2026-07-15）
- **Task Mode**：Feature Bundle Mode
- **Goal**：根据 Observation、Goal Evaluation gaps、结构化诊断、Node Contract、原 Reviewed Plan/Ticket、checkpoint 和配额生成无副作用的 Recovery Proposal。
- **Background**：当前 error diagnoser 根据状态和日志模式生成 advisory retry plan；orchestrator 的 `RetryProposal` 主要判断 node/backend/root 和粗粒度 risk，尚不能表达失败被试、resume、参数修改、backend switch、局部 replan 与人工接管。
- **Current Behavior**：诊断和 retry plan 是独立 JSON；`SAFE_RETRY` allowlist 与 Node Contract 分离；参数 hash 未与原 plan 做完整 canonical diff。
- **Required Behavior**：引擎先生成 Diagnosis Record，再按确定性决策表产生候选并说明依据、差异、所需审批和不可执行原因；引擎本身不能迁移状态或执行。
- **Non-goals**：不签发 ticket、不调用 Execution Gateway、不自动修改参数、不让 LLM 自由选择风险或审批等级。

## Files to Read Before Editing

- 8A Observation 与 8B Goal Evaluation models
- `src/backend/app/runtime/error_diagnoser.py`
- `src/backend/app/runtime/retry_runtime.py`
- `src/backend/app/schemas/node_contract.py`
- `src/backend/app/runtime/node_contract_registry.py`
- `src/backend/app/schemas/execution_ticket.py`
- `src/backend/app/services/execution_ticket_service.py`
- `src/backend/app/services/agent_orchestrator.py`
- checkpoint/resume、run state timeline、subject execution 和 error classifier tests。

## Exact Anchors

- `diagnose_run`、`_collect_issue_from_state`、`_build_retry_plan`
- `execute_retry_plan` 的 `EXECUTION_CONTRACT_REQUIRED` fail-closed 返回
- `ContractRetryPolicy`、`IdempotencyPolicy`、`NodeContract`
- `ExecutionRetryPolicy`、`ExecutionTicket.retry_policy`
- `AgentOrchestrator.propose_retry`
- 8B `GoalEvaluation` 的 failed/indeterminate criteria 和 8A evidence bindings

## Files to Read Only

- `runtime/pipeline_executor.py`、`runtime/execution_gateway.py` 和 runner plugins（8C 不改执行层）。
- 科学 kernels、rawdata、用户数据、既有 run/state/artifacts。

## Proposed Files to Create / Edit

| 动作 | 文件 | 责任 |
|---|---|---|
| 创建 | `src/backend/app/schemas/recovery.py` | DiagnosisFact、GoalGap、RecoveryCandidate、Proposal、quota/approval decision |
| 创建 | `src/backend/app/services/run_diagnosis_service.py` | 将 observation + goal gaps 转为结构化诊断，替代散落 legacy JSON 语义 |
| 创建 | `src/backend/app/services/recovery_proposal_engine.py` | 纯决策逻辑、候选排序、canonical diff、配额判定 |
| 修改 | `src/backend/app/schemas/node_contract.py` | 细化 retry/resume/subject scope/parameter mutability/output collision policy |
| 修改 | `src/backend/app/runtime/node_contract_registry.py` | 为首批节点填充可验证恢复契约；未知节点 fail closed |
| 修改 | `src/backend/app/runtime/error_diagnoser.py` | 兼容适配到新 diagnosis schema；legacy 报告只读/弃用 |
| 修改 | lifecycle/store/API | 持久化 diagnosis/proposal 引用和只读查询，不执行 |
| 创建 | proposal matrix、no-side-effect、contract/quota tests | 覆盖七类 action 与拒绝原因 |

## Recovery Schema

### `DiagnosisRecord`

- 绑定 goal evaluation、observation、plan/ticket/run；
- `facts[]`：错误类别、scope、node/subject、直接证据、置信来源（规则/明确状态/validator）；
- `goal_gaps[]`：failed/indeterminate criterion、expected/actual、缺失 artifact/subject/validation；
- `root_cause_status`：`known / probable / unknown`；
- `blocking_safety_issues`；
- 不包含未经审阅的执行指令。

### `RecoveryProposal`

- identity/version/hash/lineage；
- 原 plan/ticket/goal/observation/evaluation/diagnosis bindings；
- quota snapshot；
- `candidates[]`：action、scope、target nodes/subjects、canonical diff、risk、idempotency、expected evidence、approval class、reason codes、blocked reasons；
- recommended candidate 只能从可行候选中确定；无安全候选时必须 `HUMAN_HANDOFF`。

## Action Types and Decision Table

| Action | 必要条件 | 审批分类 | 结果 |
|---|---|---|---|
| `SAFE_RETRY` | 同参数/后端/输入输出范围；节点 retryable；低风险；idempotent 或隔离输出；配额内 | 依据原 Approval Policy；默认显式 retry approval | 新 recovery attempt，不能重放原 ticket |
| `RETRY_FAILED_SUBJECTS` | subject-level contract；失败 subjects 可精确识别；其余成功产物不可覆盖；范围为原范围子集 | 同上，且记录 subject subset | 只重试失败被试 |
| `RESUME` | 有已验证 checkpoint；计划/参数/backend/roots 未变；remaining nodes 为原 DAG 合法后缀/子图 | 依据 resume policy；默认显式 approval | 从 checkpoint 继续，保留原 run 不变 |
| `PARAMETER_CHANGE` | 诊断证明参数可能导致 gap；新值满足 contract | **新 Reviewed Plan + 新审批** | 生成 plan patch proposal，不执行 |
| `BACKEND_SWITCH` | 原后端不可用且替代后端有契约；科学等价/差异明确 | **新 Reviewed Plan + 新审批** | 新 plan，记录可重复性影响 |
| `REPLAN` | 需要增删节点、改变依赖、目标/范围/路径或输出 | **新 Reviewed Plan + 新审批** | 生成局部 plan candidate，不执行 |
| `HUMAN_HANDOFF` | 根因未知、证据冲突、高风险、无契约、配额耗尽或外部环境需人工处理 | 人工接管 | 无执行 capability |

### Canonical Diff 必须比较

- normalized params 及其 hash；
- node IDs、contract versions、DAG dependencies；
- backend IDs、precision/device/fallback policy；
- input/output/readonly roots；
- subject/session/output scope；
- artifact types、overwrite/idempotency policy；
- Goal Contract；
- approval context 与安全 allowlist fingerprint。

任一差异未被分类时，候选必须 blocked 或升级为新 plan，不能归为 safe retry。

## Node Contract Extensions

建议将现有 `ContractRetryPolicy`/`IdempotencyPolicy` 扩展为：

- `retryable_error_classes`、`non_retryable_error_classes`；
- `max_attempts`、`backoff_policy`（只允许有限固定策略）；
- `supports_subject_subset`、`supports_resume`、`checkpoint_schema`；
- `mutable_parameters_for_recovery`（仅用于生成新 plan，不代表免审批）；
- `backend_switch_targets` 与 scientific equivalence note；
- `output_collision_policy`、`attempt_output_strategy`；
- `required_pre_retry_validations`、`required_post_retry_validations`。

未显式声明的能力默认 false。

## Candidate Ranking

排序是确定性的：

1. 能修复直接 goal gap 且证据充分；
2. 变更面最小；
3. 风险最低；
4. 不改变 reviewed contract；
5. 资源成本较低；
6. 预期可产生缺失证据；
7. 同分时按稳定 action priority 和 ID 排序。

不得以模型置信度跳过契约、审批或配额。LLM 可生成用户可读解释，但必须引用结构化 reason codes。

## Quota Model

至少同时检查：

- lifecycle 总 recovery attempts；
- 每 node attempt；
- 每 subject+node attempt；
- 累计 wall-clock/资源预算；
- replan 次数。

配额取 ticket、node contract、项目 policy 中的最严格值。任何 quota source 缺失时默认零自动执行；达到任一硬限制即只产生 `HUMAN_HANDOFF`。

所有 quota source 必须显式给出五个维度：`max_lifecycle_recovery_attempts`、`max_node_attempts`、`max_subject_node_attempts`、`max_replans`、`max_recovery_wall_seconds`。任一维度缺失时，该维度按 `0` 处理，候选可被生成但标记 `not_executable`；显式 retry approval 不能覆盖缺失或已耗尽的硬配额。维护者必须先通过新的、可审计的 policy 变更补齐额度，再重新生成 proposal。

Replan 不重置 lifecycle 总 attempts、累计 wall-clock 或已消耗的 replan 次数；新 plan 的 node/subject 配额只能进一步收紧剩余额度。达到任一维度时立即只产生 `HUMAN_HANDOFF`。

## Acceptance Criteria

- [ ] 七类 action 均有 schema、生成条件、拒绝原因和测试。
- [ ] Proposal Engine 是纯决策层；测试证明不会写 artifact/state、调用 runner/subprocess/gateway 或签票。
- [ ] failed-subject retry 只在准确识别 subset 且 contract 允许时可行。
- [ ] 参数、backend、node、DAG、root、scope、goal 变化全部升级为新 Reviewed Plan。
- [ ] 未知 contract、未知错误、证据冲突、高风险和配额耗尽均 `HUMAN_HANDOFF`。
- [ ] proposal 持久化不可变、hash 稳定、可关联全部输入证据。
- [ ] legacy advisory retry plan 有明确兼容/弃用路径，不再成为执行授权来源。

## Safety Invariants

- Recovery Proposal 不等于 approval，也不等于 ticket。
- 原 run/state/artifacts 不修改、不删除；proposal 不创建输出目录。
- 外部执行与 backend switch 默认高风险并要求新审批。
- rawdata 永久只读；任何建议修改 rawdata 的候选必须拒绝。

## Allowed Commands

- 只读检索、schema/import 检查、纯函数/持久化 focused pytest 和受影响 backend suite。
- 测试可使用临时 SQLite/project fixtures，但不得调用真实 runner、subprocess 或外部后端。
- 不允许执行 legacy retry plan，不允许生成/消费 execution ticket。

## Validation Commands

```powershell
python -m pytest tests/unit/test_agent_lifecycle.py tests/unit/test_node_contract_registry.py tests/unit/test_run_state_timeline.py --tb=short --basetemp=.pytest_tmp
python -m pytest tests/unit/test_recovery_diagnosis.py tests/unit/test_recovery_proposal_engine.py --tb=short --basetemp=.pytest_tmp
```

后一个命令中的两个测试文件是本任务计划新增的交付物。新增 action matrix、canonical diff、failed subjects、checkpoint、quota、no-side-effect 和 human-handoff tests。pytest 后执行限定清理。

## Stop Conditions

- 如果 Node Contract 无法证明 retry/idempotency/resume 语义，候选只能人工接管或新 plan，不能猜测安全重试。
- 如果无法区分旧 artifact 与当前 attempt 输出，停止实现该 action，先补 lineage/attempt output strategy。
- 若 backend switch 缺科学等价或 tolerance 证据，只能提出需人工审阅的新 plan，不能标记 safe。

## Completion Report Requirements

列出 diagnosis/proposal schema、决策表、contract 扩展、首批支持节点/actions、quota precedence、legacy 迁移、no-side-effect 证据、验证结果和被强制 handoff 的场景。
