# LLM Planner

## 目的

Planner 将用户目标转换为同一个强类型 `PlannerPlan`。规则规划器和
OpenAI-compatible provider 共享该 Pydantic schema、Node Contract 校验和
provenance contract；Planner 本身不执行任何 node。

```text
User Goal + current Project Context
  -> explicitly selected provider
  -> canonical PlannerPlan
  -> Node Contract / Plan Validator
  -> redacted PlannerInvocation + PlannerEvidence
  -> Reviewed Plan
```

## 当前 Provider

| Provider | 行为 |
|---|---|
| `rule_based` | 本地确定性规则规划；无需网络或 API key。 |
| `openai_compatible` | 通过 `MEDIMAGE_LLM_*` 显式配置的 provider；默认不可用。 |

旧 `mock` provider 已移除。调用方必须明确选择 provider；已选 provider 的缺少
配置、超时、网络失败或 schema/validator 失败都会返回结构化错误，不会静默改用
另一个 provider 或生成内容不同的规则计划。

远程 provider 最多进行一次格式修复。修复请求保持相同 goal、constraints 和
Tool Catalog，只要求把响应改为符合 schema 的 JSON；第二次仍失败则返回
`PLANNER_OUTPUT_INVALID`。

## 合同与证据

- `PlannerPlan` 禁止未知顶层字段和未知 node 字段。
- 每个 node 必须存在于 canonical Node Contract registry，并通过参数、backend、
  依赖、路径和能力校验。
- `PlannerInvocation` 记录 provider/model、prompt template 版本与 hash、输入 schema
  版本与 hash、开始时间和 timeout。
- `PlannerEvidence` 记录输出 hash、validation codes、失败码和脱敏摘要。
- `fallback_used` 固定为 `false`；prompt、API key 和 provider 原始响应不持久化。
- 决定性 provenance 会进入 Reviewed Plan 身份；动态时间仅作审计，不改变 plan
  identity。

## 安全边界

- Planner 不运行 dry-run、runner、外部工具、Ticket 或 Gateway。
- Tool Catalog 只向模型提供可读 node 投影；Node Contract 才是安全字段和参数
  schema 的权威来源。
- scientific memory 不进入自由文本约束，只能先形成当前任务待确认 decision。
- 所有 plan 必须通过确定性 Plan Validator，未知 node 和已移除 GUI node 立即拒绝。

## 手动测试

真实 provider 的隔离冒烟流程见
[`LLM提供方冒烟测试.md`](LLM提供方冒烟测试.md)。不得在测试证据、日志或文档中
记录 API key 或原始 provider 响应。

## 代码位置

- `src/backend/app/schemas/planner_plan.py`
- `src/backend/app/schemas/planner_provenance.py`
- `src/backend/app/planner/llm_planner.py`
- `src/backend/app/planner/llm_provider.py`
- `src/backend/app/runtime/node_contract_registry.py`
- `src/backend/app/planner/plan_validator.py`
- `tests/unit/test_llm_planner.py`
- `tests/unit/test_llm_provider.py`
- `tests/unit/test_llm_planner_api.py`
- `tests/unit/test_planner_provenance.py`
