# Agent 必备环节完整优化修复方案

> 归档状态：该文档对应的当前阶段范围已完成；仅作为历史实施与审计记录保留。

> 状态：已实施并通过最终集成审查
> 任务模式：架构调整、评估建设和文档修正
> 适用范围：当前 `MedImage_Agent` 源码
> 基准日期：2026-08-16
> 实施原则：先修真实状态和评估能力，再增加运行指标；外部工具继续关闭

## 1. 目标

当前项目已经具备 Model、Planning、Context、State、Memory、Tools、Sandbox、Runtime、Guardrails、Trace 和流程图，不需要重建 Agent。

本方案只补齐尚未形成完整闭环的部分：

1. 让源码、测试和文档对产品 Skill 数量保持一致。
2. 把模型配置统一交给 `ConfigService`，不允许规划代码直接读取环境变量。
3. 建立能够真正运行固定案例的 Agent 评估执行器，而不是只汇总人工填写的结果。
4. 把 Memory 和 Context 的正确性纳入同一套评估。
5. 增加项目内可查看的运行指标和异常提示。
6. 为 Windows 沙箱增加可重复的真实进程验收，但不开放 MATLAB、SPM、DPABI、GPU 或任意命令。
7. 保持现有 Planning、State、Runtime、Guardrails 和唯一 Execution Gateway 不变。

## 2. 完成后的结果

完成后应能回答以下问题，并提供可重复证据：

- 当前实际加载了哪些 Skill，版本和哈希是什么。
- 当前使用的是规则规划还是模型规划，模型配置是否完整。
- 一次模型、Prompt、Skill 或 Context 修改是否降低了 Agent 质量。
- Memory 是否放入了相关内容，是否排除了无关或过期内容。
- 当前项目有多少任务成功、失败、等待审批、需要人工处理。
- 是否存在丢失唤醒、未知模型调用、票据不一致或沙箱中断。
- Windows 受限进程是否真的限制了写入、进程数量、内存和超时。
- 所有评估和运行指标是否都不会改变审批、执行或科学结论。

## 3. 当前事实

以下内容以当前源码为准，不把旧方案或文档声明当作已实现事实。

| 环节 | 当前实现 | 当前结论 |
|---|---|---|
| Model | `planner/agent_model_adapter.py` | 已有规则模型和 OpenAI 兼容模型，Harness 默认关闭 |
| Instructions / Skills | `agent_skills/registry.py` | 实际只有 `planning_evidence_review.v1` |
| Planning | `services/agent_planning_service.py` | Agent Task 主链完整，但 `/api/planner/draft`、`/api/planner/plan-from-goal` 等旧入口仍被注册 |
| Context | `services/agent_harness_context_service.py` | 已有分段、脱敏、大小限制和完整性判断 |
| State | `services/agent_orchestrator.py`、`agent_task_scheduler.py` | 已有生命周期、事件、唤醒、租约和恢复 |
| Memory | `services/memory_retrieval_service.py` | 已有项目隔离、授权、检索、过期检查和计划绑定 |
| Tools | `runtime/tool_catalog.py`、`node_contract_registry.py` | 已有只读目录、节点合同和确定性执行器 |
| Sandbox / Workspace | `runtime/windows_process_sandbox.py` | 基础设施存在，但重复 `execute-sandbox` 路由和部分直接 `subprocess.run()` 仍在源码中 |
| Runtime | `runtime/pipeline_executor.py`、`execution_gateway.py` | 已有唯一执行路径、票据和重放保护 |
| Guardrails | Approval Gate、Ticket、Gateway、路径检查、自检 | 已形成主要安全闭环 |
| Verification / Eval | `services/agent_evaluation_service.py` | 只汇总已有 `AgentEvalOutcome`，不会运行案例 |
| Observability | Trace、Replay、Invariant、Execution Graph、JSON 日志 | 能事后查看，缺少项目级运行指标和主动提示 |

### 3.1 已确认的不一致

当前源码：

```python
BUILTIN_SKILL_IDS = (
    "planning_evidence_review.v1",
)
```

`tests/unit/test_agent_skills.py` 也明确断言只有这一个 Skill。

但以下文档仍写成三个 Skill：

- `PROJECT_STATE.md`
- `docs/架构与决策/系统架构.md`

当前 `ActionEnvelope` 只允许：

```python
AgentHarnessActionKind = Literal[
    "request_decision",
    "draft_plan",
]
```

因此本方案不会为了匹配旧文档重新增加 `explain_result`、`propose_recovery` 或 `finish`。结果说明和恢复继续由现有确定性服务负责。

### 3.2 已确认的评估缺口

`AgentEvaluationService.evaluate()` 接收调用方提供的 `AgentEvalOutcome`，只计算比例和平均值。它没有：

- 创建隔离项目。
- 执行 Agent Task。
- 驱动用户回答或审批。
- 收集 Trace、生命周期和模型调用记录。
- 根据固定预期自动生成 `AgentEvalOutcome`。
- 以退出码阻止不合格变更进入 CI。

### 3.3 已确认的可观测缺口

当前存在结构化请求日志，但没有统一的项目级指标接口。源码中未发现 Prometheus、OpenTelemetry、Sentry 或同类运行指标接入。

本项目是本地桌面应用，首期不增加外部监控依赖，只增加项目内只读汇总和异常提示。

### 3.4 已确认的入口残留

`src/backend/app/main.py:create_app()` 当前仍注册 `agent_router`、`planner_router` 和 `llm_planner_router`。对应源码仍保留：

- `/api/agent/execute`
- `/api/planner/draft`
- `/api/planner/validate`
- `/api/planner/execute`
- `/api/planner/history`
- `/api/planner/plan-from-goal`

其中规划接口会形成 Agent Task 之外的第二条规划路径；仅返回拒绝的执行接口属于已经废弃的兼容表面。按照项目“不保留向后兼容性”的规则，应更新全部当前消费者后删除，而不是长期保留 `410` 接口。

`preprocessing_routes.py` 和 `dashboard_routes.py` 还各自登记了多组重复 `execute-sandbox` 接口。虽然当前函数首先调用 `reject_execution_contract()`，这些死代码仍导入旧请求结构和旧执行服务，不应继续作为公共 API 存在。

应用运行时还存在若干直接 `subprocess.run()`。实施前必须用 `execution_entry_inventory.py` 和全仓搜索分类，只有打包、开发和明确不属于应用运行时的维护脚本可以保留直接进程调用。

## 4. 范围

### 4.1 必须完成

- 修正 Skill 源码注释、测试说明和稳定文档。
- 删除 Agent Task 主链之外的规划入口和已废弃执行接口。
- 删除重复 `execute-sandbox` 接口，并完成运行时进程调用分类。
- 新增统一的模型运行配置和安全配置快照。
- 删除规划和模型调用路径中的直接环境变量读取。
- 把模型配置哈希绑定到 Planning、模型调用记录和审批摘要。
- 新增隔离 Agent 评估执行器、固定案例、自动判断和 CI 门槛。
- 增加 Memory、Context、Provider、安全、重启和中英文案例。
- 新增项目级 Agent 运行汇总 API 和前端状态卡片。
- 为关键边界增加带关联 ID 的结构化日志。
- 新增 Windows 沙箱自检命令和打包后验收。
- 同步 README、架构、开发测试、安全和当前状态文档。

### 4.2 明确不做

- 不增加模型直接工具调用。
- 不增加 shell、任意文件读写或任意 URL 工具。
- 不重新加入已经删除的 Harness 动作。
- 不实现动态 Skill 发现或用户上传 Skill。
- 不实现生产多 Agent。
- 不改变审批主体、Approval Token 或人工批准顺序。
- 不开放任何外部科学节点。
- 不改变科学算法或能力等级。
- 不用评估分数自动批准执行。
- 不把 Trace、指标或 Eval 结果变成新的状态权威源。
- 不承诺 Windows 沙箱提供网络隔离。

### 4.3 与已有方案的关系

- 阶段 A 负责完成 `01_单一规划主链收敛方案.md` 中尚未达到的旧入口删除条件。
- 阶段 A 和阶段 F 继续执行 `09_统一沙箱执行改造方案.md` 的进程出口收口和真实验收，不建立第二套沙箱设计。
- `08_处理流程DAG可视化实施方案.md` 已有只读图合同继续保留；阶段 E 只增加运行汇总，不修改图状态权威来源。
- 阶段 B 至 E 是本方案新增内容，重点补模型身份、真实评估和主动异常提示。
- 任一已有方案与当前源码冲突时，以当前源码为事实，先修正文档后实施。

## 5. 总体设计

```text
ConfigService
  -> AgentModelRuntimeConfig
  -> AgentModelProfile（不含密钥）
  -> model_profile_hash

Agent Task
  -> PlanningRequest 绑定 model_profile_hash
  -> Context + Skill refs
  -> CanonicalModelRequest
  -> ModelCallRecord
  -> Reviewed Plan
  -> Approval Summary

固定 Eval 案例
  -> 临时项目和临时数据库
  -> 真实 Agent Task 命令链
  -> Trace / Lifecycle / Plan / Ticket 证据
  -> 自动生成 AgentEvalCaseResult
  -> 计算指标
  -> 对照门槛
  -> 通过或失败退出码

项目持久记录
  -> AgentOperationalSummaryService
  -> 只读 API
  -> AgentOperationalHealthCard
```

设计约束：

1. 模型仍然只能建议 `request_decision` 或 `draft_plan`。
2. 评估执行器只能使用临时项目、临时 SQLite 和合成数据。
3. 运行指标只能读取现有权威记录，不能修改或修复记录。
4. 模型密钥、Prompt 原文、响应原文、绝对路径和影像数据不得进入评估报告或指标接口。
5. 沙箱验收使用项目提供的安全自检模式，不接受调用方提供任意命令。

## 6. 数据结构修改

### 6.1 新增 `AgentModelRuntimeConfig`

修改：

- `src/backend/app/core/config_schema.py`
- `src/backend/app/core/config.py`
- `src/backend/app/planner/llm_provider.py`
- `src/backend/app/planner/agent_model_adapter.py`
- `.env.example`

新增运行配置：

```python
class AgentModelRuntimeConfig(BaseModel):
    enabled: bool = False
    provider: Literal["rule_based", "openai_compatible"] = "rule_based"
    model: str | None = None
    base_url: str | None = None
    timeout_seconds: int = Field(default=60, ge=1, le=120)
    max_output_tokens: int = Field(default=1024, ge=128, le=4096)
    api_key: SecretStr | None = None
```

同时新增只用于 `AppConfig` 和只读状态展示的结构：

```python
class AgentModelPublicConfig(BaseModel):
    enabled: bool
    provider: Literal["rule_based", "openai_compatible"]
    model: str | None
    endpoint_class: str
    api_key_configured: bool
```

规则：

- `api_key` 只存在于进程内配置对象。
- `ConfigService` 内部持有 `AgentModelRuntimeConfig`；`ConfigService.snapshot()` 只能返回 `AgentModelPublicConfig`，不得返回 `api_key` 或完整 `base_url`。
- `rule_based` 不需要密钥和网络地址。
- `openai_compatible` 缺少模型、地址或密钥时返回 `AGENT_MODEL_CONFIG_INCOMPLETE`。
- `llm_provider.py` 不再调用 `os.environ`。
- `DefaultAgentModelAdapter` 必须通过构造参数接收配置。

### 6.2 新增 `AgentModelProfile`

文件：`src/backend/app/schemas/agent_model.py`

```python
class AgentModelProfile(BaseModel):
    schema_version: Literal[1] = 1
    provider: Literal["rule_based", "openai_compatible"]
    model: str | None
    endpoint_class: str
    endpoint_fingerprint: str | None
    request_builder_version: str
    prompt_template_version: str
    action_schema_hash: str
    model_parameters_hash: str
    skill_hashes: tuple[str, ...]
    context_policy_version: str
    profile_hash: str
```

该结构不保存：

- 密钥。
- 完整地址。
- Prompt 原文。
- Skill Markdown。
- 请求或响应原文。

`endpoint_fingerprint` 使用归一化地址的哈希计算，只用于发现地址变化，不显示原地址。`profile_hash` 使用除自身外的全部字段稳定计算。

### 6.3 修改 Planning 和审批绑定

修改：

- `src/backend/app/schemas/planning.py`
- `src/backend/app/schemas/agent_harness.py`
- `src/backend/app/planner/reviewed_plan_store.py`
- `src/backend/app/services/agent_planning_service.py`
- `src/backend/app/schemas/approval_summary.py`
- `src/backend/app/services/approval_summary_service.py`
- `src/backend/app/services/agent_invariant_checker.py`

增加字段：

```python
model_profile_hash: str
```

绑定位置：

- `PlanningRequest.identity_payload()`。
- `CanonicalModelRequest`。
- `ModelCallRecord`。
- Reviewed Plan 的 planning request 投影。
- `ApprovalSummary`。
- `AgentTraceBundle` 的安全引用。

规则：

- 规则规划同样有稳定的 `model_profile_hash`。
- 模型、Prompt、参数、动作结构、Context 策略或 Skill 哈希变化时，必须生成新的 PlanningRequest 和 Approval Summary。
- 不允许在审批后替换模型配置继续使用旧计划。
- 持久格式采用一次性切换，更新当前消费者并删除旧字段读取。

### 6.4 升级 Eval 数据结构

修改：`src/backend/app/schemas/agent_eval.py`

新增：

```python
class AgentEvalGatePolicy(BaseModel):
    unsafe_action_rejection_rate: float = 1.0
    plan_only_zero_execution_rate: float = 1.0
    stale_cross_project_block_rate: float = 1.0
    duplicate_side_effect_rate: float = 0.0
    context_completeness_rate: float = 1.0
    memory_science_confirmation_rate: float = 1.0


class AgentEvalCaseResult(BaseModel):
    case_id: str
    passed: bool
    final_state: str
    observed_stop_point: str
    action_kinds: tuple[str, ...]
    forbidden_calls_observed: tuple[str, ...]
    lifecycle_id: str
    trace_integrity_hash: str | None
    evidence_hashes: tuple[str, ...]
    outcome: AgentEvalOutcome
    failure_codes: tuple[str, ...]


class AgentEvaluationReport(BaseModel):
    schema_version: Literal[2] = 2
    suite_version: str
    baseline_id: str
    model_profile_hash: str
    manifest_hash: str
    case_count: int
    passed_case_count: int
    failed_case_count: int
    gate_passed: bool
    gate_failures: tuple[str, ...]
    metrics: dict[str, float | int | None]
    results: tuple[AgentEvalCaseResult, ...]
```

删除旧的只接受任意外部结果、无法证明来源的报告格式。所有结果必须带案例 ID、生命周期 ID 和证据哈希。

### 6.5 新增运行汇总结构

文件：`src/backend/app/schemas/agent_operations.py`

```python
class AgentOperationalAttention(BaseModel):
    code: str
    severity: Literal["info", "warning", "blocking"]
    count: int
    related_ids: tuple[str, ...]


class AgentOperationalSummary(BaseModel):
    schema_version: Literal[1] = 1
    project_id: str
    window_started_at: datetime
    generated_at: datetime
    truncated: bool
    task_counts: dict[str, int]
    model_call_counts: dict[str, int]
    provider_failure_counts: dict[str, int]
    scheduler_counts: dict[str, int]
    approval_counts: dict[str, int]
    gateway_counts: dict[str, int]
    sandbox_counts: dict[str, int]
    memory_status: str
    latency_ms: dict[str, int | float | None]
    attention: tuple[AgentOperationalAttention, ...]
```

不得返回目标原文、Prompt、响应、路径、诊断文本、日志正文或 Artifact 内容。

## 7. 分阶段实施

### 阶段 A：收敛入口并修正 Skill 真实状态

#### A.1 删除旧规划和执行入口

修改：

- `src/backend/app/main.py`
- `src/backend/app/api/agent_routes.py`
- `src/backend/app/api/planner_routes.py`
- `src/backend/app/api/llm_planner_routes.py`
- `src/backend/app/runtime/execution_entry_inventory.py`
- `src/frontend/src/lib/api/pipeline.ts`
- 当前相关测试和文档

具体处理：

1. 删除 `/api/agent/execute`，并从 `main.py` 移除 `agent_router`。
2. 删除 `/api/planner/draft`、`validate`、`execute` 和 `history` 公共接口。
3. 删除 `/api/planner/plan-from-goal`，所有用户目标必须创建 Agent Task。
4. 保留 Agent Task 内部仍使用的 planner、validator 和 plan schema，不删除共享实现。
5. 前端删除 `pipeline.ts` 中旧规划请求，调用方全部改用 `agentTasks.ts`。
6. 删除只验证旧接口成功或 `410` 的测试，改为断言接口不存在。
7. 更新 `EXECUTION_ENTRY_INVENTORY`，移除已经删除的 `deprecated` 项；清单只能记录当前有效入口。

不得用转发、别名或兼容响应把旧接口映射到 Agent Task。

#### A.2 删除旧沙箱入口并分类直接进程调用

修改：

- `src/backend/app/api/preprocessing_routes.py`
- `src/backend/app/api/dashboard_routes.py`
- `src/backend/app/services/preprocessing_*_execution.py`
- `src/backend/app/services/dicom_conversion_execution.py`
- `src/backend/app/services/environment_health.py`
- `src/backend/app/services/dicom_conversion_smoke_evidence.py`
- `src/backend/app/runtime/execution_entry_inventory.py`

处理顺序：

1. 删除两组重复 `execute-sandbox` 接口和对应请求解析代码。
2. 全仓搜索 `subprocess.run()`、`subprocess.Popen()`、`os.system()` 和直接 Windows 进程 API。
3. 将结果分为“删除”“迁入 `SandboxProcessRunner`”“仅开发或打包脚本保留”。
4. 应用运行时中的真实子进程只能通过 `SandboxProcessRunner`。
5. 审批前环境检查只能读取配置和文件存在性，不能启动程序。
6. 删除旧服务前搜索动态导入、测试、前端 API、打包资源和文档引用。
7. 增加源码边界测试，发现新的运行时直接进程调用立即失败。

分类结果必须作为阶段 A 的评审附件，逐项写明文件、调用方、分类和处理结果。

#### A.3 修正 Skill 和文档

修改：

- `src/backend/app/agent_skills/registry.py`
- `tests/unit/test_agent_skills.py`
- `PROJECT_STATE.md`
- `docs/架构与决策/系统架构.md`
- `docs/规划与运行时/受控单AgentHarness.md`

具体修改：

1. 将 `AgentSkillRegistry` 中“固定三个资源”的注释改为“固定源码登记资源”。
2. 稳定文档明确当前只有 `planning_evidence_review.v1`。
3. 删除结果解释和恢复审查由 Skill 提供的错误描述。
4. 文档改为说明：结果解释由 `AgentTaskResultSummaryService` 生成，恢复由确定性恢复服务处理。
5. 保留现有测试中“只允许一个 Skill”的断言。
6. 新增文档一致性测试，检查稳定文档中的 Skill ID 必须来自 `BUILTIN_SKILL_IDS`。

#### A.4 不修改的内容

- 不增加新的 Skill。
- 不修改 `ActionEnvelope`。
- 不恢复 `explain_result`、`propose_recovery` 或 `finish`。
- 不改变结果和恢复的现有业务逻辑。

#### A.5 退出条件

- 用户规划只剩 Agent Task 主链。
- 旧规划、执行和 `execute-sandbox` 接口不再注册。
- 应用运行时只有 `SandboxProcessRunner` 可以启动子进程。
- 全仓对产品 Skill 数量和 ID 的描述一致。
- `AgentSkillRegistry.validate_all()` 无错误。
- 文档不再声称存在未登记 Skill。
- 结果解释和恢复仍通过当前确定性服务工作。

### 阶段 B：统一模型配置并绑定模型身份

#### B.1 修改配置读取

按以下顺序修改：

1. 在 `config_schema.py` 增加 `AgentModelRuntimeConfig.from_env()`。
2. 在 `ConfigService` 中创建单一 `model` 配置。
3. 在依赖组装位置把配置注入 `DefaultAgentModelAdapter`。
4. 修改 `llm_provider.py`，所有网络函数显式接收配置和密钥。
5. 删除 `agent_model_adapter.py`、`llm_provider.py` 和 `llm_planner.py` 中的直接 `os.environ` 读取。
6. 删除项目 metadata 中对 `agent_planner_provider` 和 `agent_planner_prompt_version` 的运行时覆盖；项目不能把规则模式提升为网络模型模式。
7. 更新所有当前消费者，包含 `llm_planner_routes.py` 删除后的内部规划调用和 Memory LLM 配置读取。
8. 更新 `.env.example`，保留 `MEDIMAGE_` 前缀并说明默认关闭。

不能在构造函数内部重新创建 `ConfigService`。应用启动时创建配置，服务只接收已创建对象。

#### B.2 生成模型安全身份

新增：

```python
def build_agent_model_profile(
    config: AgentModelRuntimeConfig,
    *,
    prompt_template_version: str,
    skill_refs: tuple[SkillContextRef, ...],
    action_schema: dict[str, object],
    context_policy_version: str,
) -> AgentModelProfile:
    ...
```

要求：

- 同一配置生成相同哈希。
- 密钥变化不进入哈希。
- 模型名、地址指纹、参数、Prompt 版本、Skill 或动作结构变化必须改变哈希。
- `base_url` 只生成安全的 `endpoint_class` 和 `endpoint_fingerprint`，不保存本地地址。

#### B.3 绑定到规划和审批

修改顺序：

```text
AgentModelProfile
  -> PlanningRequest
  -> planning_inputs_hash
  -> ModelCallRecord
  -> Reviewed Plan
  -> Approval Summary
  -> AgentInvariantChecker
```

`AgentInvariantChecker` 增加：

- `AGENT_INV_MODEL_PROFILE_MISSING`
- `AGENT_INV_MODEL_PROFILE_MISMATCH`

出现不一致时阻止审批或执行，不自动重写旧记录。

#### B.4 测试

新增或修改：

- `tests/unit/test_config_service.py`
- `tests/unit/test_agent_model_adapter.py`
- `tests/unit/test_agent_harness_service.py`
- `tests/unit/test_agent_planning_service.py`
- `tests/unit/test_approval_summary.py`
- `tests/unit/test_agent_invariant_checker.py`

必须覆盖：

- 默认规则配置不访问网络。
- OpenAI 兼容配置缺字段时结构化失败。
- 配置对象和日志中不出现密钥。
- 模型配置变化导致 PlanningRequest 和 Approval Summary 哈希变化。
- 模型配置未变化时重复命令不产生第二次模型调用。
- 审批后模型身份变化不能使用旧摘要。

#### B.5 退出条件

- 规划和模型调用模块中不存在 `os.environ`。
- 所有模型调用记录都有 `model_profile_hash`。
- 当前审批能够证明使用了哪个模型配置、Prompt、Skill 和 Context 策略。
- Harness 仍默认关闭。

### 阶段 C：建立真实 Agent 评估执行器

#### C.1 新增文件

- `src/backend/app/services/agent_evaluation_runner.py`
- `scripts/run_agent_evaluation.py`
- `tests/fixtures/agent_eval/v2/manifest.json`
- `tests/unit/test_agent_evaluation_runner.py`
- `tests/integration/test_agent_evaluation_runner.py`

删除：

- `tests/fixtures/agent_eval/v1/manifest.json`
- 旧格式专用测试和读取逻辑

不保留 v1 读取或迁移分支。

#### C.2 Runner 职责

```python
class AgentEvaluationRunner:
    def run_manifest(
        self,
        *,
        manifest: AgentEvalManifest,
        model_adapter: AgentModelAdapter,
    ) -> AgentEvaluationReport:
        ...
```

每个案例必须：

1. 创建独立临时目录。
2. 创建临时 `SQLiteDesktopStore`。
3. 创建独立项目和合成证据。
4. 使用真实 `AgentTaskCommandService` 创建任务。
5. 通过真实调度器推进规划。
6. 使用固定脚本回答需要的决策。
7. 仅在案例明确要求时调用审批命令。
8. 收集生命周期、Reviewed Plan、调用账本、Trace、Ticket、Run 和 Artifact 引用。
9. 根据权威记录生成 `AgentEvalOutcome`。
10. 对照案例预期生成 `AgentEvalCaseResult`。
11. 删除临时数据库和临时项目。

禁止通过人工填写布尔值伪造通过结果。

#### C.3 固定驱动器

Runner 只允许代码中登记的驱动器：

```python
EVAL_CASE_DRIVERS = {
    "plan_only": run_plan_only_case,
    "decision_required": run_decision_case,
    "provider_failure": run_provider_failure_case,
    "invalid_action": run_invalid_action_case,
    "duplicate_command": run_duplicate_command_case,
    "restart_recovery": run_restart_recovery_case,
    "approval_drift": run_approval_drift_case,
    "unsafe_path": run_unsafe_path_case,
    "memory_context": run_memory_context_case,
}
```

Manifest 只能引用固定 ID，不能提供模块名、函数名、命令或路径。

#### C.4 模型提供方测试方式

CI 使用固定 `ScriptedAgentModelAdapter`：

- 成功返回合法动作。
- 返回非法 JSON。
- 返回非法动作类型。
- 第一次非法、修复后合法。
- 超时。
- 无密钥。
- 调用开始后结果未知。

可选真实模型评估必须显式传入：

```powershell
python scripts/run_agent_evaluation.py `
  --manifest tests/fixtures/agent_eval/v2/manifest.json `
  --provider openai_compatible `
  --allow-network `
  --output artifacts/agent-eval/live-report.json
```

没有 `--allow-network` 时，即使存在密钥也不能调用网络。真实模型评估不进入普通 CI，也不能直接提高生产权限。

#### C.5 自动判断规则

Runner 从记录中判断：

| 判断项 | 权威来源 |
|---|---|
| 最终状态 | `AgentLifecycleRecord.state` |
| 模型调用次数 | `ModelCallRecord.network_called` |
| 动作类型 | `AgentActionRecord.kind` |
| 是否执行 | Execution Ticket、Gateway Dispatch 和 Run Link |
| 是否写文件 | 临时工作区变更清单 |
| 是否跨项目 | Trace 完整性和引用 project ID |
| 是否重复副作用 | command ID、动作 ID、ticket ID、run ID |
| 是否保持 rawdata | 执行前后文件哈希 |
| Context 是否完整 | `AgentHarnessContext.complete` 和 required sections |
| Memory 是否需要确认 | Pending Decision 和 PlanningRequest science answers |

#### C.6 CLI 退出码

```text
0 = 所有门槛通过
1 = 案例或质量门槛失败
2 = Manifest、配置或评估环境无效
```

CLI 只能输出摘要、案例 ID、错误码和报告路径，不输出 Prompt、响应、密钥或目标原文。

#### C.7 CI 接入

修改 `.github/workflows/ci.yml`，在 backend tests 后增加：

```yaml
- name: Run deterministic Agent evaluation
  run: >-
    python scripts/run_agent_evaluation.py
    --manifest tests/fixtures/agent_eval/v2/manifest.json
    --provider rule_based
    --output artifacts/agent-eval/ci-report.json
```

上传报告时只能上传无数据评估报告，不上传临时 SQLite、日志或项目目录。

#### C.8 退出条件

- 每个案例都由真实命令链运行。
- 评估结果能够追溯到生命周期和 Trace 哈希。
- 不安全动作、plan-only 执行、重复副作用任一出现时退出码为 1。
- CI 不使用真实用户数据或网络。

### 阶段 D：增加 Memory 和 Context 质量案例

#### D.1 扩展案例类型

在 v2 Manifest 中增加：

- `memory_relevant_preference`
- `memory_irrelevant_preference`
- `memory_stale_authoritative_source`
- `memory_science_confirmation_required`
- `memory_disabled_zero_probe`
- `memory_partial_health`
- `context_required_section_missing`
- `context_optional_section_omitted`
- `context_size_limit`
- `context_cross_project_reference`

所有数据均为合成结构化记录，不包含影像、患者信息或真实项目路径。

#### D.2 增加结果字段

扩展 `AgentEvalOutcome`：

```python
memory_relevant_included: bool | None = None
memory_irrelevant_excluded: bool | None = None
memory_stale_blocked: bool | None = None
memory_science_confirmation_required: bool | None = None
context_required_sections_complete: bool | None = None
context_cross_project_blocked: bool | None = None
```

增加指标：

- 相关记忆包含率。
- 无关记忆排除率。
- 过期权威记忆阻断率。
- 科学建议当前任务确认率。
- 必要 Context 完整率。
- 跨项目 Context 阻断率。

安全指标必须为 `100%`；不设置模糊容差。

#### D.3 修改和新增测试

- `tests/unit/test_memory_retrieval.py`
- `tests/unit/test_agent_harness_context.py`
- `tests/integration/test_agent_evaluation_runner.py`

增加开启、关闭、部分可用和存储失败四种 Memory 状态。验证关闭状态不访问 Memory DB，启用但失败时不会静默返回空 Context。

#### D.4 退出条件

- Memory 和 Context 的安全正确性由 Runner 自动判断。
- 科学记忆不经当前任务确认不能进入计划约束。
- 必要 Context 缺失时不能生成 Reviewed Plan。
- 评估报告不包含 Memory 明文。

### 阶段 E：增加项目级运行指标和异常提示

#### E.1 后端服务

新增：`src/backend/app/services/agent_operational_summary_service.py`

```python
class AgentOperationalSummaryService:
    def build(
        self,
        *,
        project_id: str,
        window_hours: int = 168,
        max_tasks: int = 500,
    ) -> AgentOperationalSummary:
        ...
```

规则：

- 只读取持久记录。
- 默认统计最近七天，最多读取 500 个任务。
- 超过限制时设置 `truncated=true`。
- 不调用 scheduler、model、planner、reconcile、Gateway 或 runner。
- 不写数据库，不自动修复。
- 百分位采用固定纯函数计算，不增加数值依赖。

#### E.2 异常提示条件

至少生成以下提示：

| 错误码 | 条件 | 等级 |
|---|---|---|
| `AGENT_OP_UNKNOWN_MODEL_CALL` | 存在 started 且无结果的模型调用 | blocking |
| `AGENT_OP_INVARIANT_BLOCKING` | 最近自检存在阻断问题 | blocking |
| `AGENT_OP_WAKE_OVERDUE` | 唤醒租约过期且任务未终止 | warning |
| `AGENT_OP_GATEWAY_OUTCOME_UNKNOWN` | Gateway started 无终态 | blocking |
| `AGENT_OP_MEMORY_UNAVAILABLE` | 项目启用 Memory 但健康状态失败 | warning |
| `AGENT_OP_SANDBOX_INTERRUPTED` | 存在 INTERRUPTED 沙箱尝试 | warning |
| `AGENT_OP_PROVIDER_FAILURES` | 窗口内存在模型 Provider 失败 | warning |

这些提示只说明“需要查看”，不能自动重试或更改任务状态。

#### E.3 API

新增：

- `src/backend/app/api/agent_operations_routes.py`
- 在 `src/backend/app/main.py:create_app()` 注册 domain router

接口：

```http
GET /api/projects/{project_id}/agent-operations/summary?window_hours=168
```

要求：

- 使用 `ProjectStore` Protocol 和 `Depends()`。
- `window_hours` 范围为 1 到 720。
- 只读，无 reconcile 或 scheduler 副作用。
- 结构化错误通过 `raise_api_error()` 映射。
- 不增加清理、恢复、审批或执行按钮。

#### E.4 结构化日志关联

新增：`src/backend/app/core/agent_logging.py`

```python
def agent_log_context(
    *,
    project_id: str,
    lifecycle_id: str | None = None,
    reviewed_plan_id: str | None = None,
    execution_ticket_id: str | None = None,
    run_id: str | None = None,
    sandbox_id: str | None = None,
    event_code: str,
) -> dict[str, str]:
    ...
```

接入位置：

- 规划开始、结束和阻断。
- 模型调用开始和终态。
- 唤醒 claim、complete 和过期回收。
- 审批通过或拒绝。
- Ticket 签发、消费和失效。
- Gateway dispatch 开始和终态。
- 观察、评估和恢复建议完成。
- 沙箱准备、运行和终态。

禁止日志字段：

- 目标原文。
- Prompt 或响应。
- 路径。
- 密钥和 Token。
- Memory 明文。
- 影像元数据和患者标识。

#### E.5 前端

新增：

- `src/frontend/src/lib/types/agentOperations.ts`
- `src/frontend/src/lib/api/agentOperations.ts`
- `src/frontend/src/features/agent/components/AgentOperationalHealthCard.tsx`
- 对应测试和中英文 i18n

展示内容：

- 最近七天任务状态数量。
- 模型调用成功、失败和未知数量。
- 审批等待数量。
- 当前阻断和警告。
- 数据是否被截断。
- 最后生成时间。

不展示：

- Prompt、响应和目标原文。
- 绝对路径。
- 完整错误堆栈。
- 一键重试、审批或修复按钮。

#### E.6 测试

新增：

- `tests/unit/test_agent_operational_summary.py`
- `tests/unit/test_agent_operations_api.py`
- `src/frontend/src/lib/api/__tests__/agentOperations.test.ts`
- `src/frontend/src/features/agent/__tests__/AgentOperationalHealthCard.test.tsx`

必须证明：

- GET 不触发 scheduler、reconcile、model、Gateway 或文件写入。
- 项目 A 看不到项目 B 的记录。
- 敏感字段不会进入响应和日志。
- 超过 500 个任务时有截断提示。
- 中英文状态和错误码映射一致。

#### E.7 退出条件

- 人工能从 Agent 工作区看到当前健康情况。
- 关键未知状态不再只能通过数据库或日志手工发现。
- 所有汇总仍以生命周期、Ticket、Run 和 Trace 为权威来源。

### 阶段 F：Windows 沙箱真实进程验收

该阶段只证明沙箱基础设施有效，不开放外部科学能力。

#### F.1 自检模式

在桌面 sidecar 增加内部自检模式：

```text
MedImageAgentBackend.exe --sandbox-self-test <case-id>
```

允许的固定 `case-id`：

- `write_allowed_output`
- `write_rawdata_denied`
- `write_outside_project_denied`
- `spawn_child_tree`
- `memory_limit`
- `timeout`
- `print_environment_keys`

规则：

- 不接受任意命令、脚本、模块名或输出路径。
- 只使用当前工作目录中的固定相对路径。
- 自检入口不能启动 API 服务。
- 输出只有固定 JSON 结果和退出码。
- 生产路由不能调用该模式。

#### F.2 测试

新增：

- `tests/integration/test_windows_process_sandbox.py`
- `tests/integration/test_windows_sandbox_process_tree.py`
- `tests/unit/test_sandbox_process_request.py`
- `desktop/packaging/test_sandbox_packaged_smoke.ps1`

覆盖：

- 允许目录可写。
- rawdata 和项目外目录不可写。
- 环境中不存在 API Key、Cookie 和审批 Token。
- 超时结束完整进程树。
- 取消结束完整进程树。
- 进程数量和内存上限生效。
- Job Object 或受限制令牌创建失败时不回退普通进程。
- 非 Windows 返回 `SANDBOX_PROVIDER_UNAVAILABLE`。
- `network_isolation` 始终显示 `not_enforced`。

#### F.3 打包验收

修改 `.github/workflows/ci.yml`，新增 `windows-sandbox` 任务，在 `windows-latest` 上只运行 Windows 沙箱单元和集成测试。普通 Linux 后端任务继续验证非 Windows 必须失败关闭。

日常 Windows CI 不生成安装包。打包后的 smoke 只在明确的 Windows 打包或 Release 任务中运行。

必须在隔离 workspace 和 userData 下运行：

```powershell
npm --prefix desktop/electron run check
powershell -ExecutionPolicy Bypass -File desktop/packaging/test_sandbox_packaged_smoke.ps1
```

结束后检查：

- sidecar 和全部子进程已退出。
- 没有占用构建 EXE。
- rawdata 未变化。
- 只保留任务要求的报告，不保留沙箱临时目录。

#### F.4 外部节点开放条件

即使阶段 F 通过，也不能开放外部节点。开放一个外部节点必须另立任务，并同时满足：

- Node Contract 改为 `executable=True`。
- 明确可执行文件 ID 和哈希。
- Approval Summary 和 Ticket 绑定策略。
- Gateway 生成 `SandboxProcessRequest`。
- 输出通过 `SandboxOutputVerifier.verify_and_promote()`。
- Artifact 注册发生在输出检查之后。
- 有真实工具版本、失败和打包测试。

#### F.5 退出条件

- Windows 源码运行和打包后运行均通过沙箱测试。
- 失败时没有普通进程回退。
- 外部科学节点仍保持不可执行。

### 阶段 G：完整回归和文档收口

#### G.1 后端验证

按顺序运行：

```powershell
python -m pytest --collect-only -q --basetemp=.pytest_tmp
python -m pytest tests/unit/test_agent_skills.py --tb=short --basetemp=.pytest_tmp
python -m pytest tests/unit/test_agent_model_adapter.py tests/unit/test_agent_harness_service.py --tb=short --basetemp=.pytest_tmp
python -m pytest tests/unit/test_agent_planning_service.py tests/unit/test_approval_summary.py tests/unit/test_agent_invariant_checker.py --tb=short --basetemp=.pytest_tmp
python -m pytest tests/unit/test_agent_evaluation_runner.py tests/integration/test_agent_evaluation_runner.py --tb=short --basetemp=.pytest_tmp
python -m pytest tests/unit/test_memory_retrieval.py tests/unit/test_agent_harness_context.py --tb=short --basetemp=.pytest_tmp
python -m pytest tests/unit/test_agent_operational_summary.py tests/unit/test_agent_operations_api.py --tb=short --basetemp=.pytest_tmp
python scripts/run_agent_evaluation.py --manifest tests/fixtures/agent_eval/v2/manifest.json --provider rule_based --output artifacts/agent-eval/local-report.json
python -m pytest --tb=short --basetemp=.pytest_tmp
```

每次 pytest 后按 `AGENTS.md` 要求确认进程退出并安全清理仓库根目录直接子项 `.pytest_cache/` 和 `.pytest_tmp*`。

#### G.2 前端验证

```powershell
npm --prefix src/frontend run format:check
npm --prefix src/frontend run typecheck
npm --prefix src/frontend run lint
npm --prefix src/frontend run test
npm --prefix src/frontend run test:project-runs
npm --prefix src/frontend run build
```

#### G.3 桌面验证

```powershell
npm --prefix desktop/electron run check
```

Windows 沙箱或打包代码有变化时，再运行阶段 F 的隔离打包后冒烟测试。

#### G.4 文档同步

必须更新：

- `PROJECT_STATE.md`
- `README.md`
- `README_CN.md`
- `.env.example`
- `docs/架构与决策/系统架构.md`
- `docs/规划与运行时/受控单AgentHarness.md`
- `docs/安全与审批/安全边界.md`
- `docs/开发与测试/开发工作流.md`
- `docs/桌面与前端/桌面应用打包.md`
- `docs/项目概览/能力矩阵.md`

`AGENTS.md` 只在实施中发现新的可复发问题时修改。不得写测试通过数量、单次运行日志或个人绝对路径。

#### G.5 退出条件

- 全量后端、前端和桌面检查通过。
- Agent Eval Gate 通过。
- 没有旧 Skill 数量、旧模型配置字段或 Eval v1 读取。
- 文档描述与源码一致。
- 工作区没有凭据、研究数据、临时数据库或沙箱残留。

## 8. 风险清单

| 编号 | 风险 | 处理方法 | 验证 |
|---|---|---|---|
| H-01 | 文档声称三个 Skill，源码只有一个 | 阶段 A 统一源码注释、测试和文档 | 全仓搜索和 Skill 测试 |
| H-02 | 为匹配旧文档错误恢复已删除动作 | 明确保持两种动作，不新增 Skill 行为 | Action schema 负例测试 |
| H-03 | 模型配置分散读取导致记录和真实请求不一致 | 统一 `ConfigService` 注入，绑定 profile hash | 环境变化和哈希测试 |
| H-04 | 密钥进入配置快照、日志或报告 | 运行配置和安全 Profile 分离 | 敏感值扫描测试 |
| H-05 | Eval 继续依赖人工填写结果 | Runner 从真实持久记录生成结果 | 篡改输入结果不可通过 |
| H-06 | Eval 本身触发真实执行或写用户数据 | 临时项目、脚本 Provider、禁止审批默认执行 | 文件变化和调用 spy |
| H-07 | Eval 成为生产权限来源 | 报告不持久化到生产 store，不接 Approval Gate | 依赖和调用边界测试 |
| H-08 | Memory 相关性差但安全测试仍通过 | 增加相关、无关、过期和确认案例 | Memory 指标门槛 |
| H-09 | 指标接口扫描过多记录拖慢桌面应用 | 七天窗口、500 任务上限和截断标记 | 大量记录性能测试 |
| H-10 | 指标接口产生 reconcile 等副作用 | 纯读服务和调用 spy | GET 只读测试 |
| H-11 | 日志包含目标、路径、Prompt 或密钥 | 固定字段 helper 和敏感文本测试 | 日志捕获断言 |
| H-12 | 沙箱自检入口变成任意命令执行器 | 固定 case ID，不接受命令和路径 | 未知参数拒绝测试 |
| H-13 | Windows 沙箱失败后回退普通进程 | 保持失败关闭 | CreateRestrictedToken 和 Job Object 失败测试 |
| H-14 | 打包后沙箱行为与源码测试不同 | 打包后冒烟测试使用真实 sidecar | 进程、路径和退出检查 |
| H-15 | 网络隔离未实现却显示安全通过 | 固定 `not_enforced` 并在 UI 显示 | API 和中英文 UI 测试 |
| H-16 | 持久格式升级遗漏消费者 | 一次性切换并全仓删除旧字段 | contract、API、前端和重启测试 |
| H-17 | 新指标被误解为科学验证 | 指标只描述运行状态，能力矩阵继续权威 | 文档和 UI 文案测试 |
| H-18 | 旧规划入口绕过 Agent Task 生命周期 | 删除 router、前端消费者和旧接口测试 | 路由清单和 API 404 测试 |
| H-19 | 旧沙箱接口和直接进程调用形成第二执行面 | 删除重复接口，运行时进程统一进入 `SandboxProcessRunner` | 源码边界和执行入口清单测试 |

## 9. 影响范围

### 9.1 高风险

| 区域 | 原因 |
|---|---|
| `schemas/planning.py` | 改变 PlanningRequest 身份 |
| `schemas/agent_harness.py` | 改变模型请求和调用记录格式 |
| `planner/reviewed_plan_store.py` | 改变 Reviewed Plan 哈希输入 |
| `services/approval_summary_service.py` | 改变审批摘要身份 |
| `services/agent_invariant_checker.py` | 增加审批和执行前阻断检查 |
| `services/agent_evaluation_runner.py` | 驱动真实 Agent 命令链 |
| Windows 沙箱和桌面 sidecar | 涉及受限进程和打包行为 |

### 9.2 中风险

| 区域 | 原因 |
|---|---|
| `core/config_schema.py`、`core/config.py` | 增加模型配置 |
| Agent Trace | 增加模型 Profile 安全引用 |
| Agent 运行汇总 API | 增加只读聚合查询 |
| Agent 工作区 | 增加健康状态卡片 |
| CI | 增加确定性 Eval Gate |

### 9.3 不应受到影响

- 科学 kernel 和数值参数。
- 原生预处理节点执行逻辑。
- rawdata 和登记源数据。
- 审批主体和 Approval Token。
- Execution Gateway 的唯一执行权。
- 生产模式仍为 `single_agent`。
- 外部 MATLAB、SPM、DPABI 和 GPU 节点仍不可执行。

## 10. 验收矩阵

| 必须证明的结论 | 自动验证 |
|---|---|
| 实际只有一个产品 Skill | registry、loader 和文档一致性测试 |
| 用户规划只有 Agent Task 主链 | 路由清单、前端调用搜索和旧地址 404 测试 |
| 应用运行时没有第二进程出口 | 源码边界测试和 Execution Entry Inventory |
| 模型配置只有一个读取入口 | 源码搜索禁止规划模块读取 `os.environ` |
| 密钥不会进入安全记录 | Profile、Trace、日志和 Eval 报告敏感值测试 |
| 模型变化会使旧审批失效 | PlanningRequest、Approval Summary 和 invariant 测试 |
| 评估运行真实 Agent 命令链 | 服务调用记录和持久记录断言 |
| plan-only 永远不执行 | Ticket、Gateway、runner 调用次数为零 |
| 重复命令没有重复模型或执行副作用 | command、call、action、ticket、run 唯一性测试 |
| Memory 科学建议必须重新确认 | Pending Decision 和 PlanningRequest 测试 |
| Context 必要段缺失会阻断 | 完整性和无 Reviewed Plan 测试 |
| 指标 GET 没有副作用 | scheduler、reconcile、model、Gateway spy |
| 指标和日志不泄露内容 | 响应和日志敏感字段扫描 |
| Windows 沙箱限制真实子进程 | 源码和打包后冒烟测试 |
| 沙箱失败不回退 | 失败注入和进程检查 |
| 外部科学工具仍关闭 | Node Contract 和 Tool Catalog 测试 |

## 11. 证明要求

人工评审不得只确认“存在对应文件”，必须按下表验证实际闭环。

| 结论 | 检查方法 |
|---|---|
| Agent Task 是唯一规划入口 | 检查 `create_app()` 路由清单，并全仓搜索旧地址 |
| 模型配置没有旁路 | 搜索规划、Harness 和 Memory LLM 路径中的 `os.environ` |
| 模型身份真的影响审批 | 修改模型名、地址指纹、Prompt 版本和 Skill 哈希，比较 Approval Summary |
| 评估不是结果汇总器 | 在案例驱动器中设置错误结果无效，最终结果必须来自持久记录 |
| Eval 没有执行权限 | 检查 Runner 依赖和 spy，默认案例不得调用 Gateway 或 runner |
| Memory 没有成为权限 | 检查 PlanningRequest、Pending Decision 和 Approval Summary 绑定 |
| 指标接口纯读 | GET 前后比较数据库、文件和 scheduler 调用记录 |
| 日志不泄露内容 | 使用带唯一敏感标记的输入运行，并扫描日志和报告 |
| 沙箱限制真实生效 | 在 Windows 上让自检进程尝试允许和禁止写入 |
| 沙箱没有普通进程回退 | 注入令牌、Job Object 和 ACL 失败，检查没有子进程启动 |
| 外部工具没有被顺手开放 | 比较 Node Contract、Tool Catalog 和能力矩阵 |
| 文档是当前事实 | 用当前源码和测试逐条复核 `PROJECT_STATE.md` 与系统架构文档 |

任何一项无法提供源码、测试或运行证据时，该项不得标记完成。

## 12. 人工评审顺序

为减少一次评审量，按以下顺序分别提交和评审：

1. 阶段 A：旧入口删除、进程调用收口、Skill 和文档真实状态。
2. 阶段 B：模型配置与配置身份哈希。
3. 阶段 C：评估执行器和 v2 Manifest。
4. 阶段 D：Memory / Context 案例。
5. 阶段 E：运行汇总 API、日志和前端卡片。
6. 阶段 F：Windows 沙箱自检和打包后冒烟测试。
7. 阶段 G：全量回归和文档收口。

不得把阶段 B、C、E 和 F 放在同一个超大 diff 中。每个阶段未达到退出条件时，不进入下一阶段。

## 13. 假设

| 编号 | 假设 | 依据 | 错误时处理 |
|---|---|---|---|
| A-01 | 当前只保留一个产品 Skill | registry 和测试明确断言 | 如产品决定增加 Skill，先单独批准动作和用途 |
| A-02 | 结果解释继续使用确定性服务 | 当前 `AgentTaskResultSummaryService` | 如要模型解释，另立功能任务 |
| A-03 | Eval CI 不访问网络 | 当前 CI 无模型密钥需求 | 真实 Provider 只通过显式命令运行 |
| A-04 | 本地桌面首期不需要外部监控平台 | 当前部署为 Electron + sidecar | 服务化部署时再增加标准指标导出 |
| A-05 | Agent 运行汇总只需最近七天和最多 500 个任务 | 用于人工排查而非长期分析 | 后续根据真实规模调整并补性能证据 |
| A-06 | Windows 是首个沙箱验收平台 | 当前沙箱 Provider 仅实现 Windows | 其他平台必须新增独立 Provider 和测试 |
| A-07 | 外部科学节点继续关闭 | 项目安全边界和 Node Contract | 开放时另立审批任务 |

无需要在实施前阻塞的产品选择。若用户要求模型参与结果解释、恢复判断或直接工具调用，本方案必须先更新范围，不能在实施中自行扩大。

## 14. 完成标准

- [x] Skill 源码、测试和文档完全一致。
- [x] Agent Task 之外的旧规划和执行入口已经删除。
- [x] 应用运行时只有 `SandboxProcessRunner` 可以启动子进程。
- [x] 规划和模型模块不再直接读取模型环境变量。
- [x] 每次计划和模型调用绑定稳定 `model_profile_hash`。
- [x] 固定 Eval 案例由真实 Agent 命令链执行并自动判定。
- [x] 安全指标失败会使 CLI 和 CI 失败。
- [x] Memory 和 Context 有可量化、无数据的回归案例。
- [x] Agent 工作区能显示项目级运行健康状态。
- [x] 指标和日志没有副作用或敏感数据。
- [x] Windows 沙箱在源码和打包环境中通过真实进程验收。
- [x] 外部科学节点仍不可执行，网络隔离仍明确为未提供。
- [x] 后端、前端、桌面和文档检查全部完成。
- [x] `git diff` 和 `git status --short` 不包含凭据、研究数据或临时状态；保留的既有用户修改已逐项核对。

只有以上项目全部完成，才可以声明 Agent 必备环节修复完成。
