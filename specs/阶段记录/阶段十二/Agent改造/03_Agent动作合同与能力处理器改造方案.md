# 03：Agent 动作合同与能力处理器改造方案

> 状态：Draft，待人工 Review。
> 依赖：01；与 02 通过 `run_one()` 和 `ActionExecutionResult` 对接。

## 1. 目标

让六种 `ActionEnvelope.kind` 都有严格输入、明确处理器、统一结果和可验证权限。模型只能选择允许的动作和参数，不能提供函数名、URL、路径、命令或执行凭据。

非目标：不增加任意工具注册中心，不把 Pipeline node 暴露为 Harness Action，不允许模型直接调用 Execution Gateway、文件系统或网络。

## 2. 当前实现分析

- `ActionEnvelope` 使用 `extra="forbid"`，但 `payload: dict[str, Any]` 只有长度限制。
- `_validate_envelope()` 只对 `request_decision` 做 payload 专项校验。
- `_apply()` 中 `draft_plan` 和 `request_decision` 有实际逻辑；`read_evidence` 仅返回 `READY`，`explain_result` 直接结束；`propose_recovery` 依赖可选 callback。
- `agent_capability_catalog.py` 已限制 Action 与 lifecycle state，应继续作为 fail-closed 权威表。

## 3. 总体修改思路

保留一个 `ActionEnvelope` 公共入口，根据 `kind` 用固定映射把 `payload` 校验为六种 Pydantic schema。Handler 仍由 `AgentHarnessService` 显式注入依赖，不做动态发现、插件加载或模型自报工具。

## 4. Action 合同

| kind | payload 关键字段 | 允许状态 | 处理结果 |
|---|---|---|---|
| `read_evidence` | `evidence_types[]`、`missing_only` | planning states | `EvidenceSnapshot` 引用 |
| `request_decision` | `items[]`、`reason` | planning states | `PendingDecisionBatch` |
| `draft_plan` | `goal_ref`、`evidence_snapshot_ref`、`revision_reason?` | planning states | Reviewed Plan/决定/错误引用 |
| `explain_result` | `observation_ref`、`evaluation_ref` | goal/result/handoff states | `AgentResultExplanation` |
| `propose_recovery` | `observation_ref`、`evaluation_ref`、`diagnosis_hint?` | `DIAGNOSING` | Recovery Proposal 引用 |
| `finish` | `finish_reason`、`evidence_refs[]` | catalog 允许状态 | attempt 终止，不改变科学事实 |

所有引用必须是 context 中已给出的 typed ID/hash；不接受绝对路径、URI、自然语言形式的“调用某函数”或未绑定项目的 ID。

## 5. 详细实施方案

### 5.1 严格 payload schema

在 `schemas/agent_harness.py` 新增六个 payload model，全部使用 `frozen=True, extra="forbid"` 和长度/数量上限。`ActionEnvelope` 增加 model validator：

1. 根据 `kind` 选择唯一 payload model；
2. 校验后保存 canonical JSON 结果；
3. kind 与 payload 不匹配时返回 `AGENT_ACTION_PAYLOAD_INVALID`；
4. 未知字段和空必填引用直接拒绝；
5. schema version 不匹配不 repair 为旧格式。

### 5.2 统一 handler 结果

新增 `ActionExecutionResult`：

| 字段 | 含义 |
|---|---|
| `status` | `completed`、`waiting`、`rejected`、`failed`、`no_change` |
| `summary` | 最多 1024 字符的用户安全摘要 |
| `output_refs` | 新证据、plan、decision、evaluation、recovery 等 typed refs |
| `state_after` | handler 返回后的权威 lifecycle state |
| `continue_allowed` | 是否允许 02 的循环继续 |
| `error_code` | 结构化领域错误 |
| `details_hash` | 完整结果 canonical hash，不保存秘密正文 |

`run_one()` 先持久化 envelope，再调用 handler，最后把 result 绑定到 step。Handler 抛出的领域错误保持原 code；未知异常统一为 `AGENT_ACTION_HANDLER_FAILED`，日志保留 request/lifecycle/step ID，不记录敏感 payload。

### 5.3 六个固定处理器

| Handler | 复用实现 | 额外要求 |
|---|---|---|
| evidence | 新 `AgentEvidenceService` | 只读 `ProjectStore`，见 04 |
| decision | `AgentOrchestrator.transition()` | 一次只保存一个决定批次 |
| plan | `AgentTaskCommandService._plan()` 或拆出的 planning service | validator、Reviewed Plan、Approval Summary 单一 owner |
| explain | `AgentTaskResultSummaryService` + Observation/Evaluation | 不允许模型改变 outcome/capability |
| recovery | `AgentOrchestrator.propose_recovery()` | 只产生 proposal，不执行 |
| finish | Harness attempt 更新 | 校验 finish reason 与 lifecycle，不伪造任务终态 |

如 `agent_harness_service.py` 因处理器继续膨胀，可新增 `services/agent_harness_action_handlers.py` 保存固定函数；不得引入运行时注册、entry point 或远程 Tool。

### 5.4 Action 校验顺序

1. Pydantic schema/version；
2. `expected_state` 等于最新 lifecycle state；
3. capability catalog 允许该状态；
4. project/lifecycle/context hash 绑定；
5. input refs 存在于当前 context；
6. payload 业务校验；
7. 调用 handler；
8. 持久化 result 和状态引用。

任一校验失败都不得调用后续 handler。

### 5.5 repair 边界

- repair 仅修正 JSON/schema，不改变 context；
- 最多一次 repair，计入模型预算；
- capability、state、project binding、安全策略失败不可 repair；
- repair 后 envelope 必须重新走全部校验；
- 两次无效输出停止 attempt，不改走一个更宽松 parser。

## 6. 数据结构变化

| 结构 | 变化 |
|---|---|
| `ActionEnvelope` | payload 由通用 dict 改为 kind 对应的 canonical typed payload |
| `ActionExecutionResult` | 新增统一 handler 结果 |
| `AgentHarnessStep` | 增加 `action_hash`、`action_result_hash`、`output_refs` |
| `AgentCapability` | 增加 `handler_id` 和允许输出类型；不增加执行权限 |

## 7. 文件修改清单

| 文件 | 修改内容 |
|---|---|
| `schemas/agent_harness.py` | payload schemas、result、step 引用 |
| `services/agent_harness_service.py` | 校验顺序和 handler 调用 |
| `services/agent_harness_action_handlers.py`（按需新增） | 六种固定处理器 |
| `runtime/agent_capability_catalog.py` | 状态、handler 和输出 allowlist |
| `planner/agent_model_adapter.py` | 输出新 schema 和 repair prompt |
| `services/mock_store.py`、`api/dependencies.py` | result 持久化/查询 |
| `tests/unit/test_agent_harness_capabilities.py` | Action/state/output 矩阵 |
| `tests/unit/test_agent_harness_service.py` | handler 正反例和 repair |

## 8. 风险与处理

| ID | 风险 | 处理 | 测试 |
|---|---|---|---|
| H03-01 | 通用 dict 越权 | typed payload + extra forbid | 未知字段/命令/path fixture |
| H03-02 | handler 在校验前执行 | 固定校验顺序 | spy 断言零调用 |
| H03-03 | finish 伪造成功 | finish 只结束 attempt，任务结果来自 lifecycle/evaluator | premature finish 测试 |
| H03-04 | explain 提升科学等级 | outcome/capability 只读引用 | metadata_only 冲突测试 |
| H03-05 | repair 绕过策略 | policy 错误不可 repair | capability denied 无二次调用 |
| H03-06 | 新模块成为插件系统 | 固定映射、无动态 import | 未知 handler fail closed |

## 9. 测试与验收

至少覆盖 6 个 Action 的成功、非法 payload、错误 state、外部 ref、跨项目 ref、handler 失败和重复 step。执行：

```powershell
python -m pytest tests/unit/test_agent_harness_service.py tests/unit/test_agent_harness_capabilities.py tests/unit/test_agent_harness_execution_boundary.py --tb=short --basetemp=.pytest_tmp
```

验收标准：查看任一 `AgentHarnessStep`，能明确知道模型提出了什么、校验结果、调用哪个固定处理器、真实输出引用和为何继续/停止；任何 Action 都不能到达执行链。

## 10. 实施顺序

1. 定义 payload/result schema；
2. 扩展 capability catalog；
3. 实现固定 handler 接口；
4. 修改 `run_one()` 校验和持久化；
5. 更新 prompt/schema；
6. 补安全负例和 replay fixture；
7. 更新 Harness 合同。
