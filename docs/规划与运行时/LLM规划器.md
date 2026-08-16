# Agent 模型与受控规划

## 目的

Agent Task 将用户目标转换为 Reviewed Plan。规则模型和 OpenAI-compatible
模型共享强类型动作协议、Node Contract 校验和 provenance contract；模型本身
不执行任何 node，也不能审批或派发执行。

```text
User Goal + current Project Context
  -> Agent Task lifecycle / Harness Context
  -> canonical model request + AgentModelProfile
  -> deterministic planning service / Node Contract validation
  -> redacted model-call ledger + Reviewed Plan
```

## 当前 Provider

| Provider | 行为 |
|---|---|
| `rule_based` | 本地确定性规则规划；无需网络或 API key。 |
| `openai_compatible` | 通过 `MEDIMAGE_AGENT_MODEL_*` 显式配置；默认不可用。 |

旧的独立 Planner HTTP 路由已移除。调用方通过 Agent Task 生命周期进入规划；
缺少配置、超时、网络失败或动作 schema 失败都会返回结构化错误，不会静默改用
另一个 provider 或绕过审核。

远程 provider 最多进行一次格式修复；第二次仍失败会停止 Harness 并保留脱敏账本。

## 合同与证据

- 仅 `request_decision` 与 `draft_plan` 两类受控动作可由模型提出。
- 每个 node 必须存在于 canonical Node Contract registry，并通过参数、backend、
  依赖、路径和能力校验。
- `AgentModelProfile` 绑定 provider/model、地址指纹、参数、Prompt、Skill、动作 schema
  和 Context policy；密钥、完整地址、Prompt 与原始响应不持久化。
- `ModelCallRecord`、PlanningRequest、Reviewed Plan 和 Approval Summary 共享模型身份 hash。

## 安全边界

- Planner 不运行 dry-run、runner、外部工具、Ticket 或 Gateway。
- Tool Catalog 只向模型提供可读 node 投影；Node Contract 才是安全字段和参数
  schema 的权威来源。
- scientific memory 不进入自由文本约束，只能先形成当前任务待确认 decision。
- 所有 plan 必须通过确定性 Plan Validator，未知 node 和已移除 GUI node 立即拒绝。

## 手动测试

使用 `scripts/run_agent_evaluation.py` 执行固定、离线的规则模型评估。真实模型
调用不属于常规测试或 CI，且不得在测试证据、日志或文档中记录 API key、Prompt 或
原始 provider 响应。

## 代码位置

- `src/backend/app/schemas/agent_model.py`
- `src/backend/app/schemas/agent_harness.py`
- `src/backend/app/planner/llm_planner.py`
- `src/backend/app/planner/llm_provider.py`
- `src/backend/app/runtime/node_contract_registry.py`
- `src/backend/app/planner/plan_validator.py`
- `tests/unit/test_llm_provider.py`
- `tests/unit/test_agent_model_profile.py`
