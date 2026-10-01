# 计划 02：受控单 Agent Harness

> 状态：**工程实现与当前规则验收已完成。**任务模式：Feature Bundle + Architecture / Refactor。
> 更新日期：2026-08-09。本文保留原始设计合同作为审计依据；下方实施清单不是从零开始的开发指令。

## 当前实施记录与验收边界

静态源码审查确认，Harness 的主要实现已存在：`AgentHarnessAttempt`、`AgentHarnessStep`、`AgentHarnessContext` 与 `ActionEnvelope` schema；项目绑定的 SQLite 存储和租约；单步 `AgentHarnessService`；启动恢复 scheduler；固定 capability catalog 与 provider adapter；默认关闭的配置；Agent Task read projection 和 Agent Workspace 状态卡；以及单元、回放、租约、执行边界和生命周期测试。

Harness 仍只能包裹规划/解释层，不能获得 Approval、Execution Ticket、Execution Gateway、runner、shell、文件写入或任意 MCP 权限。其开关关闭时不创建 Harness attempt；开关开启但 provider、schema、预算或 lease 出错时必须记录结构化停止原因，且不得创建 plan、ticket 或执行副作用。

原文中的“回退到当前路径”“保留兼容入口”和“旧数据库安全迁移”不再适用于当前仓库规则。重新验收或后续改动必须采用单一权威路径和单一持久格式：同步更新所有当前消费者并删除被替换实现，不新增 fallback、shim 或旧格式读取。

## 原始方案（仅作审计上下文）

Harness 是一个确定性的控制层，不是“让模型自由反复调用工具”。模型只能返回下一步的结构化建议；控制层负责读取上下文、检查预算、持久化每一步、请求用户决定、调用既有规划服务，并在审批后才允许现有执行链继续。

首版只做单 Agent，不实现递归、多 Agent、自由对话工具、终端、浏览器或任意 MCP。已有的多 Agent 设计继续保持 Proposed，等单 Agent 的 trace、预算、恢复和评测稳定后再实施。

## 范围、非目标与验收

| 项目 | 约定 |
|---|---|
| 顶层任务 | `AgentLifecycleRecord` 仍是唯一用户任务和状态机；Harness 只保存其从属 attempt/step。 |
| 模型权限 | 模型没有文件、shell、node runner、Approval、Ticket、Gateway 或数据库写权限。 |
| 可用动作 | 读取项目证据、提出澄清问题、生成/修订候选计划、解释结果、提出恢复建议。 |
| 执行权限 | 仅用户的既有 approve 命令可进入 post-approval dry-run、Ticket 和 Gateway。 |
| 默认值 | `MEDIMAGE_AGENT_HARNESS_ENABLED=false`；关闭时当前 Agent Task 行为完全不变。 |
| 不做 | 多 Agent、自动批准、自动重试、无限循环、跨项目记忆、任意工具 passthrough。 |

完成条件：

- [x] 每个启用的 Agent Task 只有一个可恢复的 Harness attempt，所有 model step、工具提议、校验和状态迁移均可按序读取。
- [x] 超预算、重复 step、进程重启、模型错误、非法工具请求和 stale lease 都安全停止，不产生 Ticket 或执行副作用。
- [x] 用户能在现有 Agent Workspace 看到下一步、停止原因和脱敏 trace 摘要；GET 不触发新的模型调用或协调。
- [x] 关闭 flag 时显式选择当前确定性 Agent Task 路径；Harness 已启用但 provider 未配置或运行失败时保存结构化停止原因，不切换 planner。

## 当前依据

| 事实 | 位置 | 采用方式 |
|---|---|---|
| 现有链路是 `goal -> context -> planning -> validation -> plan -> approval -> ticket -> gateway -> observation -> evaluation` | `docs/架构与决策/系统架构.md` 第 6 节 | Harness 只能包裹规划/解释层，不另造执行链。 |
| Agent Task 是 read projection，不是第二状态机 | `docs/架构与决策/系统架构.md` 第 6 节 | attempt/step 作为 lifecycle 从属记录。 |
| 当前 planner 是规则/Mock，另有 OpenAI-compatible provider | `src/backend/app/planner/llm_planner.py`、`llm_provider.py` | 新 runtime 调用统一 adapter，不依赖 provider 私有格式。 |
| 现有 reconciler 已有“有界、单 owner”原则 | `src/backend/app/services/agent_task_reconciler.py` | Harness 使用同样原则，但有独立 lease。 |
| 多 Agent runtime 只有 Proposed 设计 | `docs/架构与决策/多Agent协作运行时设计与实施计划.md` | 不把它混入首版 Harness。 |

## 锁定的运行合同

### 1. 从属状态

新增三个受 `project_id + lifecycle_id` 强绑定的表和 Pydantic schema：

```text
AgentHarnessAttempt
  attempt_id, lifecycle_id, project_id, status, mode, provider_ref,
  context_hash, next_step_no, model_calls_used, tool_proposals_used,
  deadline_at, lease_owner, lease_expires_at, terminal_reason, schema_version

AgentHarnessStep
  step_id, attempt_id, step_no, kind, input_hash, output_hash,
  requested_capability, validation_result, state_before, state_after,
  started_at, completed_at, error_code

AgentHarnessContext
  context_hash, lifecycle_id, allowed_fields_json, memory_context_hash,
  project_snapshot_hash, prompt_template_version, created_at
```

`AgentHarnessContext` 不保存原始影像、完整日志、secret、API key 或未过滤聊天记录。正文若必须用于审计，只保存脱敏摘要和 hash；权威数据仍在现有 ProjectStore、Reviewed Plan、Audit 和 Memory SQLite。

### 2. 有界循环

每次 claim 后只运行一个 step；服务重启不会把内存中的循环当作真值。

```text
创建或恢复 attempt
-> 生成不可变 context snapshot
-> 请求模型给出一个 ActionEnvelope
-> schema + capability + budget 校验
-> 持久化 step
-> 执行只读服务调用或写入“等待用户”状态
-> 读取结果，进入下一 step 或终态
```

默认硬限制：每 attempt 最多 6 次模型调用、8 个 action proposal、300 秒 wall time、2 次 lease 接管。每个 step 的 idempotency key 是 `attempt_id:step_no:input_hash`；相同 key 只能返回原结果，不能再次调用模型。

### 3. 唯一 ActionEnvelope

```json
{
  "schema_version": 1,
  "kind": "read_evidence | request_decision | draft_plan | explain_result | propose_recovery | finish",
  "reason": "short user-visible reason",
  "input_refs": ["typed-safe-reference"],
  "payload": {},
  "expected_state": "current lifecycle state"
}
```

未知字段、未知 kind、跨项目 reference、与当前 lifecycle state 不匹配的动作全部拒绝。`draft_plan` 只能调用 `GoalPlanningService`；`propose_recovery` 只能调用既有恢复 proposal service；没有 `execute`、`approve`、`write_file` 或 `shell` kind。

### 4. 上下文与模型调用

上下文只由 `HarnessContextBuilder` 生成，顺序固定为：任务目标、当前 lifecycle state、已确认答案、项目证据摘要、Reviewed Plan/Approval 摘要、MemoryContext 的 hash 与允许字段、上一步结构化结果。总序列化上限为 32 KiB；超过上限时按“旧 trace -> 可再读项目细节 -> 非必要解释”顺序删除，并记录 `omitted_fields`。

provider 接口只接收 `ActionEnvelope` JSON Schema 和上述 typed snapshot。一次返回无法解析时可请求一次“同输入、只修正 JSON”的修复；第二次失败即以 `AGENT_MODEL_OUTPUT_INVALID` 停止，不能自由重试或改写用户目标。

## 实施清单

### A. 状态、租约和服务

| 步骤 | 文件 | 明确交付 |
|---|---|---|
| A1 | 修改 `schemas/agent_lifecycle.py`、新建 `schemas/agent_harness.py` | attempt、step、context、ActionEnvelope 和 public summary schema。 |
| A2 | 修改 `services/mock_store.py`、ProjectStore Protocol 与 schema 测试 | 三张表、外键、project binding、命令幂等、lease claim/release/expiry；只接受当前持久格式。 |
| A3 | 新建 `services/agent_harness_context_service.py` | 唯一 context builder、字段 allowlist、32 KiB 截断、hash 和 redaction。 |
| A4 | 新建 `services/agent_harness_service.py` | claim 一步、调用 adapter、验证 envelope、保存 step、决定下一 lifecycle command；无后台无限 while loop。 |
| A5 | 新建 `runtime/agent_harness_scheduler.py` | lifespan-owned 有界调度；一次只 claim 一个过期/待运行 step；按 lease 退出。 |
| A6 | 修改 `services/agent_task_command_service.py`、`agent_task_reconciler.py` | create/answer/recovery 后在 flag 开启时提交 Harness step；approve/cancel/终态时停止 attempt。 |

### B. Capability 与 provider adapter

| 步骤 | 文件 | 明确交付 |
|---|---|---|
| B1 | 新建 `runtime/agent_capability_catalog.py` | 固定六种 kind、允许 lifecycle state、输入 schema、是否只读；默认拒绝。 |
| B2 | 新建 `planner/agent_model_adapter.py`，修改 `planner/llm_provider.py` | provider 无关的 `propose_action(snapshot) -> ActionEnvelope`；统一到当前 planner/provider contract，不保留被替换入口。 |
| B3 | 修改 `planner/llm_planner.py`、`services/goal_planning_service.py` | `draft_plan` 只走现有 validator/Goal Contract；Harness 不复制规则或计划构造。 |
| B4 | 修改 `runtime/execution_gateway.py`、`services/approval_summary_service.py` | 添加阻断测试，证明 Harness 永不传入 approval/ticket/dispatch 参数。生产逻辑除必要断言外不改写 gateway。 |

### C. 配置、投影和 UI

| 步骤 | 文件 | 明确交付 |
|---|---|---|
| C1 | 修改 `core/config_schema.py`、`config/settings.py`、`.env.example` | `MEDIMAGE_AGENT_HARNESS_ENABLED`、最大调用/提议/秒数、lease 秒数；默认关闭，非法值 fail closed。 |
| C2 | 修改 `schemas/agent_task.py`、`services/agent_task_read_model.py` | 增加只读 `harness_summary`：状态、已用预算、下一步、停止原因、最新 step ID。 |
| C3 | 修改 `src/frontend/src/lib/api/agentTasks.ts`、`lib/types/agentTask.ts`、`features/agent/` 和 i18n | 在现有 Agent Workspace 显示简短进度、等待用户原因和脱敏 trace；不能新增本地执行状态。 |
| C4 | 修改 `docs/架构与决策/系统架构.md`、`docs/安全与审批/安全边界.md`、`docs/规划与运行时/` | 说明 ActionEnvelope、预算、trace、默认关闭、恢复与非目标。 |

## 测试与验收

| 测试 | 必须证明 |
|---|---|
| `tests/unit/test_agent_harness_context.py` | allowlist、脱敏、稳定 hash、32 KiB 截断、memory 只以允许字段进入。 |
| `tests/unit/test_agent_harness_service.py` | 六种合法动作、未知动作拒绝、一次 JSON 修复、预算、幂等和终态。 |
| `tests/unit/test_agent_harness_lease.py` | 并发 claim、过期接管、重启、重复 step 不重复 model call。 |
| `tests/unit/test_agent_harness_execution_boundary.py` | Harness 不创建 ticket、不调用 gateway、不批准、不改 rawdata。 |
| `tests/integration/test_agent_harness_lifecycle.py` | 创建、澄清、计划、批准前停止、执行后观测、恢复建议、取消和重启投影。 |
| 前端 controller/view 测试 | loading、disabled、waiting-user、finished、budget-exhausted、model-error 与中英文文案。 |
| 离线回放集 | 固定 30+ 条中文/英文目标，覆盖支持目标、缺输入、plan-only、恶意 tool 请求、模型坏 JSON、恢复与取消；记录预期 state/动作。 |

必跑命令：

```powershell
python -m pytest tests/unit/test_agent_harness_*.py tests/unit/test_agent_task_*.py tests/unit/test_llm_provider.py --tb=short --basetemp=.pytest_tmp
python -m pytest tests/integration/test_agent_harness_lifecycle.py --tb=short --basetemp=.pytest_tmp
python -m pytest --tb=short --basetemp=.pytest_tmp
npm --prefix src/frontend run format:check
npm --prefix src/frontend run typecheck
npm --prefix src/frontend run test
npm --prefix src/frontend run build
```

## 风险与防护

| 风险 | 防护 | 验证 |
|---|---|---|
| 模型把建议写成执行命令 | 只有六种 envelope；无 execute/approve 能力；gateway 加阻断回归。 | execution-boundary 测试。 |
| loop 卡住或成本失控 | 单步 claim、6/8/300 硬上限、lease、显式终态。 | 预算、crash、stale lease 测试。 |
| trace 泄露数据或提示注入 | typed allowlist、redaction、hash、无完整 transcript 持久化。 | PHI/secret/injection fixtures。 |
| 新状态机与 lifecycle 分叉 | attempt 仅为从属记录；所有用户状态仍由 read model 映射。 | 重启与 GET 零副作用测试。 |
| provider 故障改变科学计划 | 失败明确停止或走既有确定性路径；不静默改写目标或审批摘要。 | provider timeout/invalid JSON replay。 |

## 评审结论

该 Harness 会让项目具备完整的受控单 Agent 运行外壳：上下文、模型调用、能力选择、预算、trace、恢复和评测都可验证。它不会把当前研究平台改造成自治执行器；真正执行仍完全属于已有审批链。
