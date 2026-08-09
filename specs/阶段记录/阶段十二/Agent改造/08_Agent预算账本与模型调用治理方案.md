# 08：Agent 预算账本与模型调用治理方案

> 状态：Implemented，已完成代码、focused backend 和 frontend 回归验证；仍按项目流程接受人工 Review。
> 依赖：02 有限循环、07 Context v2；10 消费本方案记录进行 replay/评测。

## 1. 目标

准确记录每个 Harness attempt 使用了多少 step、模型调用、repair、Action、恢复次数、时间和 provider 可返回的 token。达到任一硬上限时停止自动推进，并给出用户可理解的原因。

非目标：不根据估算费用自动扣款，不允许项目配置扩大系统硬上限，不在日志中保存 API key 或完整模型响应。

## 2. 当前实现分析

- `AgentHarnessConfig` 使用安装级环境变量，并将 step、真实 provider 调用、action、repair、
  recovery、wall time 和可选 token 限额夹紧至系统硬上限。
- `AgentHarnessAttempt` 保存快速预算总量和阶段分配；`AgentHarnessStep` 嵌套逐次
  `ModelCallRecord`，可从步骤明细重建总量。
- invalid output repair 在调用前后均写入独立账本行，repair 前重新检查同一总调用预算。
- `LLMProviderResult` 在 OpenAI-compatible 响应可用时返回 model、usage、latency、request ID
  和 cache metadata；缺失字段为 `None`，不估算成本或 token。
- provider unavailable 和 deterministic fallback 都通过结构化 attempt/step 投影暴露实际路径，
  不伪装为同一模型成功。

## 3. 总体修改思路

预算权威仍放在 `AgentHarnessAttempt`，每次模型调用记录作为当前 step 的嵌套 `ModelCallRecord`。不为首期新增独立计费系统；总量从 attempt 快速读取，明细从 steps 重建。

## 4. 预算模型

| 预算 | 默认建议 | 硬上限 | 消耗时机 |
|---|---:|---:|---|
| `max_steps` | 8 | 16 | 每个 accepted/rejected step |
| `max_model_calls` | 6 | 6 | 每次真实 provider 请求，包括 repair |
| `max_action_proposals` | 8 | 8 | 每个解析出的 envelope |
| `max_repairs` | 1 | 1/step | repair 请求 |
| `max_recovery_attempts` | 2 | 3 | 进入一次新 recovery proposal/attempt |
| `max_wall_seconds` | 300 | 300 | attempt 从创建到当前时间 |
| `max_input_tokens` | provider 可选 | 管理员配置 | provider 返回 usage 后累计 |
| `max_output_tokens` | provider 可选 | 管理员配置 | provider 返回 usage 后累计 |

精确默认值在实现前由配置 Review 确认；硬上限不能通过 project metadata、模型输出或用户请求提高。

## 5. 详细实施方案

### 5.1 `ModelCallRecord`

每次调用记录：

- `call_id`、`step_id`、`attempt_id`；
- `provider`、`model`、`endpoint_class`（不含 URL 凭据）；
- `prompt_template_version`、`context_hash`、`request_hash`；
- `response_hash`、`schema_valid`、`repair`；
- `started_at`、`completed_at`、`latency_ms`；
- `input_tokens`、`output_tokens`、`cached_input_tokens`（provider 有时才填）；
- `provider_request_id`（脱敏并限制长度）；
- `status`、`error_code`、`fallback_to`。

不保存 Authorization header、API key、完整 raw response、完整 prompt 或任意影像内容。

### 5.2 Provider 返回合同

扩展 `LLMProviderResult`，从 OpenAI-compatible response 的 `model`、`usage`、header/request ID 中提取可用字段。字段缺失保持 `None`，不得估算为 0 或伪造成本。

`DefaultAgentModelAdapter.propose_action()` 返回 envelope 和 call metadata；rule-based/mock 记录 `provider=rule_based/mock`、token 为 `None`、`network_called=false`。

### 5.3 原子预算结算

1. claim 后先检查剩余预算是否允许请求；
2. 创建 step 和 call started 记录；
3. provider 返回后完成 call record；
4. 在同一次 expected-status 更新中累计 attempt；
5. repair 前再次检查剩余额度；
6. handler result 完成后累计 action/step；
7. crash 后按已持久 call/step 明细对账，不重复消费或调用。

### 5.4 动态分配边界

允许按阶段在总额内分配，例如 planning 4 calls、result/recovery 2 calls；未使用额度可转给后续阶段。模型不能申请提高总额，scheduler 只能减少可用额度或停止。

### 5.5 fallback

- 缺 key、超时、HTTP、schema invalid、budget exhausted 使用不同 error code；
- 确定性 fallback 是新路径记录，不伪装为同一模型成功；
- budget exhausted 默认停止/hand off，不通过 fallback 规避总预算；
- provider raw 错误先清洗，再进入日志和前端。

## 6. 数据结构与配置

| 结构/配置 | 变化 |
|---|---|
| `AgentHarnessAttempt` | 增加 step、repair、recovery、token 计数和阶段分配 |
| `AgentHarnessStep.model_calls` | 新增 0..2 个 `ModelCallRecord` |
| `LLMProviderResult` | 增加 model/usage/latency/request ID/cache metadata |
| `AgentHarnessSummary` | 增加实际 provider、fallback、step/time/recovery 预算 |
| `MEDIMAGE_AGENT_HARNESS_MAX_STEPS` | 新增 |
| `MEDIMAGE_AGENT_HARNESS_MAX_RECOVERY_ATTEMPTS` | 新增 |
| token/cost configs | provider 能稳定支持后再启用，默认空 |

## 7. 文件修改清单

| 文件 | 修改内容 |
|---|---|
| `schemas/agent_harness.py` | Budget、ModelCallRecord、summary |
| `services/agent_harness_service.py` | 预检查、结算、对账、fallback |
| `planner/agent_model_adapter.py` | 返回模型调用元数据 |
| `planner/llm_provider.py` | 解析 usage/model/request ID/latency |
| `core/config_schema.py`、`.env.example` | 新预算配置和硬上限 |
| `services/mock_store.py` | step 嵌套记录重载和 expected update |
| `services/agent_task_read_model.py`、前端 types/card | 安全预算投影 |

## 8. 风险与处理

| ID | 风险 | 处理 | 测试 |
|---|---|---|---|
| H08-01 | 并发超支 | claim + expected update + repair 前复查 | 双 owner 最多一次 call |
| H08-02 | crash 后重复调用 | call/step started/completed 与 idempotency 对账 | fault injection |
| H08-03 | provider 字段缺失 | nullable，不估算 | 无 usage response |
| H08-04 | 错误泄露秘密 | error sanitizer + log capture | key/header 不出现 |
| H08-05 | fallback 绕过预算 | fallback 受同一总预算/明确策略 | exhausted 不继续调用 |
| H08-06 | 项目扩大硬上限 | ConfigService clamp | metadata 超限被忽略/拒绝 |

## 9. 测试与验收

```powershell
python -m pytest tests/unit/test_agent_harness_service.py tests/unit/test_agent_harness_lease.py tests/unit/test_planner_llm_provider.py tests/unit/test_llm_provider.py --tb=short --basetemp=.pytest_tmp
```

覆盖单次调用、repair、超时、无 usage、fallback、并发、重启对账和各预算上限。人工验收要求前端能说明“用了哪个路径、多少预算、为何停止”，但不显示秘密、完整 prompt 或原始响应。

## 10. 实施顺序

1. 确认预算默认值和硬上限；
2. 定义 ModelCallRecord/Budget；
3. 扩展 provider/adapter；
4. 实现原子结算和 crash 对账；
5. 接入 loop/recovery/fallback；
6. 更新 read model/前端；
7. 补并发、隐私和 provider fixture；
8. 更新配置与运行合同。

## 11. 待确认

**待确认：** OpenAI-compatible 目标服务是否都返回一致的 `usage` 和 request ID。推荐将 token/cache 字段设计为可空；在真实 provider 验证前不增加货币成本字段。

## 12. 实施结果（2026-08-09）

- 已采用方案推荐默认值：8 step、6 model call、8 action proposal、每 step 1 repair、
  2 recovery、300 秒；配置只接受安装级 `MEDIMAGE_` 环境变量并分别受方案硬上限夹紧。
  可选 input/output token 上限默认关闭，provider 未返回 usage 时维持 `null`。
- `AgentHarnessStep` 现在嵌套 redacted `ModelCallRecord`，provider/adapter 会返回实际
  model、usage、cache usage、latency、request ID 和 `network_called`。账本不保存 prompt、
  raw response、header、API key 或影像；错误只保留结构化 code。
- 调用前会持久化 started record；完成、repair、handler 和 attempt totals 通过 lease/
  expected-status fence 结算。已持久化但 outcome 未知的调用会对账并安全停止，不重试。
- 只读 `harness_summary` 与 Agent 前端卡片已展示实际路径、完整预算、可用 token 和
  结构化停止原因；中英文文案与 TypeScript contract 已同步。
- 本任务的 focused backend 76 项与 frontend 332 项测试均通过；未对真实远程 provider
  发起请求，目标服务 usage/request ID 的兼容性仍保持上述待确认状态。
