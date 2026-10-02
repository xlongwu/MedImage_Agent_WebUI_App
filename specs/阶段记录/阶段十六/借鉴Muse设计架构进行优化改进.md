# MedImage Agent Harness 优化方案
## ——借鉴 Meta Muse 架构的受控 Research Agent Runtime 升级设计

> 项目：[`xlongwu/MedImage_Agent_WebUI_App`](https://github.com/xlongwu/MedImage_Agent_WebUI_App)  
> 方案整理日期：2026-10-01  
> 目标：在**不破坏现有确定性执行、安全审批、医学影像科研可追溯性**的前提下，将当前 Harness 从“受控规划器”升级为“具备观察、规划、受控提案、结果反思和有限多 Agent 审查能力的 Research Agent Harness”。

---

# 1. 方案摘要

当前 MedImage Agent 的 Harness 已经具备非常好的安全基础：

- LLM 仅负责建议与规划，不直接执行 Pipeline；
- Reviewed Plan、Approval Summary、Execution Ticket、Execution Gateway、Pipeline Runtime 构成完整的确定性执行链；
- Memory 是 project-scoped、advisory-only，并通过快照绑定到计划；
- Observation、Goal Evaluation、Result Summary 由 deterministic service 生成；
- Recovery 由独立确定性恢复引擎生成候选，而不是让模型自由重试；
- Harness 当前只暴露 `request_decision` 和 `draft_plan` 两种动作；
- Harness 有 Context v3、EvidenceSnapshot、hash binding、预算限制、幂等步骤、lease、trace、replay、invariant checker；
- Multi-Agent 目前尚未直接进入生产，而是先通过独立 evaluation gate 评估。

这说明当前系统的核心问题**不是“缺少安全控制”**，而是：

> Harness 的感知能力、动作空间、Skill 分工和闭环能力仍然偏窄，当前更接近“受控 LLM Planner”，而不是完整的 Agent Runtime。

因此，本方案不建议复制 Muse 的通用自治能力，也不建议让 LLM 直接获得 Browser / Shell / Pipeline / File Write 权限。更适合本项目的升级路线是：

```text
Current Harness
Context → LLM → request_decision / draft_plan

↓

Target Research Agent Harness
Goal
→ Observe
→ Reason
→ Read Evidence
→ Reason
→ Propose
→ Policy
→ Human Approval / Deterministic Execution
→ Observe
→ Evaluate
→ Reflect
→ Finish / Recovery / Next-step Proposal
```

其中最核心的边界保持不变：

```text
Agent Model
= Observe + Reason + Propose

System Authority
= Authorize + Execute + Verify
```

---

# 2. 当前架构评估

## 2.1 当前架构的优势

当前项目的主架构可以概括为：

```text
Frontend
  ↓
FastAPI API
  ↓
Agent Runtime
  ↓
Reviewed Plan
  ↓
Approval Gate
  ↓
Execution Ticket
  ↓
Execution Gateway
  ↓
Pipeline Runtime
  ↓
Registered Node Runners
```

当前 README 与架构文档已经明确约束：

> LLM 负责 planning 和 advising，真实执行必须留在 Pipeline Runtime 与 registered node runners 中。

这是一条必须继续保留的原则。

当前 Harness 的几个关键优点如下。

### 2.1.1 LLM 与执行权彻底分离

当前 Harness 只能产生：

- `request_decision`
- `draft_plan`

不能：

- 创建执行票据；
- 调用 Gateway；
- 直接调用 runner；
- 写文件；
- 执行 shell；
- 访问数据库写接口；
- 自动批准 Recovery。

这和 Muse 的核心原则：

```text
Agent proposes; system permits.
```

高度一致。

---

### 2.1.2 计划与执行之间有明确的不可变绑定

当前 PlanningRequest、Reviewed Plan、Approval Summary、Execution Ticket 已经绑定：

- goal；
- evidence snapshot；
- science answers；
- memory context；
- provider / prompt version；
- execution environment；
- allowlist；
- plan hash；
- scope；
- recovery candidate（如果存在）。

任何输入变化都会产生新的 plan / summary，旧审批不会自动继承。

这一点非常适合医学影像科研场景，因为：

- 参数变化必须可追溯；
- scientific decision 必须能复现；
- 同一结果不能在不同上下文下复用旧批准。

---

### 2.1.3 Deterministic Result Truth 已经存在

当前 `AgentTaskResultSummaryService` 从持久化的：

- Observation；
- Goal Evaluation；
- artifact registry；
- reload status；
- subject status；
- scientific limitations；

构建结果。

LLM 生成的结果解释只能作为辅助文本，并且与真实 outcome 冲突时会被丢弃。

这意味着：

```text
Scientific Truth
≠ Model Narrative
```

这是正确方向，后续必须保留。

---

### 2.1.4 Recovery 已经不是开放式 Agent Retry

当前 Recovery 由确定性引擎生成：

- `RETRY_FAILED_SUBJECTS`
- `SAFE_RETRY`
- `RESUME`
- `PARAMETER_CHANGE`
- `BACKEND_SWITCH`
- `REPLAN`
- `HUMAN_HANDOFF`

并且受到：

- RecoveryQuota；
- CanonicalDiff；
- Node Contract；
- Approval；
- Execution Snapshot；

的约束。

因此后续 LLM 最适合做：

```text
Recovery Candidate Reviewer
```

而不是：

```text
Recovery Policy Owner
```

---

## 2.2 当前 Harness 的主要瓶颈

### 2.2.1 Action Space 太窄

当前：

```python
AgentHarnessActionKind =
Literal[
    "request_decision",
    "draft_plan"
]
```

因此模型实际上只有两个选择：

1. 问用户；
2. 生成计划。

它无法主动执行：

- 查看数据准备情况；
- 检查 BIDS；
- 检查已有预处理结果；
- 查看环境能力；
- 查看 QC；
- 查看已有 artifact；
- 查看 Run 状态；
- 根据结果继续分析。

这导致当前 Harness 很依赖 Context Builder 预先把所有信息准备好。

---

### 2.2.2 “观察”主要发生在 Context Builder，而不是 Agent Loop

现在 Context v3 已经包含：

- goal；
- policy；
- project_evidence；
- decision_state；
- plan_state；
- execution_state；
- latest_observation；
- last_action_result；
- memory_context；
- budget。

但模型无法主动说：

> “我还需要检查一下当前项目是否已经存在可用的 atlas。”

只能依赖 Context 中是否提前塞入。

这限制了 Agent 的动态推理能力。

---

### 2.2.3 Skill 数量太少

当前内置 Skill 只有：

```text
planning_evidence_review.v1
```

因此：

- 数据检查；
- 预处理规划；
- FC 规划；
- ALFF/ReHo；
- QC review；
- Recovery；
- Result interpretation；

本质上都依赖同一个通用 Skill。

随着任务复杂度上升，单一 Prompt Procedure 会出现：

- 指令越来越长；
- 不同任务规则混在一起；
- 无关 context 增多；
- action 约束难管理；
- evaluation 难拆分。

---

### 2.2.4 Harness 目前仍然主要工作在 Planning 阶段

虽然系统后端已经有：

- Observation；
- Goal Evaluation；
- Result Summary；
- Recovery Proposal；
- `run_reconciled` wake；

但模型参与的重点仍然是 planning。

完整 Agent 应该形成：

```text
Observe
→ Plan
→ Execute
→ Observe
→ Evaluate
→ Reflect
```

而不是：

```text
Plan
→ Execute
→ Deterministic Summary
```

---

### 2.2.5 Policy 权限判断未来容易分散

目前权限判断分布在：

- Capability Catalog；
- Lifecycle State；
- Harness validation；
- Approval Gate；
- Ticket；
- Gateway；
- Invariant Checker。

当前动作少时问题不大。

一旦未来 Action 增加到：

- inspect；
- explain；
- propose recovery；
- propose export；
- propose execution；
- result reflection；

权限逻辑容易散落到多个 handler。

因此需要一个统一的 Sentinel-like Policy Layer。

---

# 3. 目标架构

建议目标架构如下：

```text
                         User Goal
                             │
                             ▼
                    Goal / Task Lifecycle
                             │
                             ▼
                    Base Context Builder
                             │
                             ▼
                 Deterministic Skill Router
                             │
                             ▼
                    Planner / Agent Model
                             │
          ┌──────────────────┼──────────────────┐
          │                  │                  │
          ▼                  ▼                  ▼
    Read Evidence      Request Decision    Draft / Revise Plan
          │
          ▼
  Read-only Tool Layer
          │
          ▼
      Observation
          │
          └──────────────────→ Agent
                             │
                             ▼
                      Action Proposal
                             │
                             ▼
                     AgentPolicyService
                       /      |       \
                      /       |        \
                   ALLOW     ASK       DENY
                     │        │
                     │      Human UI
                     │        │
                     └────┬───┘
                          ▼
                     Reviewed Plan
                          │
                   Approval Summary
                          │
                     Human Approval
                          │
                    Execution Ticket
                          │
                    Execution Gateway
                          │
                     Pipeline Runtime
                          │
                        Run
                          │
                      Observation
                          │
                    Goal Evaluation
                          │
              ┌───────────┴────────────┐
              ▼                        ▼
            Finish               Recovery Engine
                                       │
                                Legal Candidates
                                       │
                                  Agent Review
                                       │
                                  Human Approval
```

核心设计原则：

```text
1. Model never owns execution.
2. Model can observe only through typed, read-only projections.
3. Every model action must pass capability and policy checks.
4. Every scientific side effect still requires reviewed deterministic path.
5. Deterministic truth always overrides model narrative.
6. Memory remains advisory.
7. Multi-Agent starts as reviewer team, not autonomous swarm.
```

---

# 4. 优化一：扩展 Capability-based Action Space

## 4.1 当前问题

当前模型只有：

```text
request_decision
draft_plan
```

导致模型无法建立完整 Agent Loop。

---

## 4.2 推荐动作分级

建议将动作空间划分为 A0～A3 四级。

### A0：只读观察动作

用于读取系统已存在的确定性证据。

```text
inspect_project
inspect_dataset
inspect_bids_validation
inspect_preprocessing_readiness
inspect_available_metrics
inspect_run
inspect_qc
inspect_artifacts
inspect_environment
inspect_memory_refs
```

特点：

- 无写操作；
- 无执行；
- 无外部网络；
- 无 raw medical data；
- 仅返回 typed projection。

---

### A1：规划动作

```text
request_decision
draft_plan
revise_plan
explain_plan
explain_result
propose_next_step
```

特点：

- 可以修改 Agent managed state；
- 不能创建 execution authority；
- 不能直接执行 scientific node。

---

### A2：高风险动作提案

```text
propose_execution
propose_recovery
propose_export
propose_backend_change
```

特点：

- 只生成 proposal；
- 必须进入 deterministic approval path；
- proposal 本身没有执行能力。

---

### A3：真实执行动作

```text
execute_pipeline
run_shell
write_file
modify_rawdata
network_request
issue_ticket
call_runner
```

这些动作：

```text
禁止暴露给 LLM。
```

即：

```text
A3 ∉ Agent Action Space
```

---

## 4.3 Capability Schema 扩展

当前 `AgentCapability` 建议增加：

```python
@dataclass(frozen=True)
class AgentCapability:
    kind: str
    automation_level: str

    allowed_states: frozenset[str]
    allowed_context_sections: frozenset[str]
    allowed_output_types: frozenset[str]

    risk_class: str
    side_effect_class: str

    allowed_tool_refs: frozenset[str]

    requires_policy_check: bool
    requires_current_approval: bool
    requires_human_approval: bool

    max_calls_per_task: int | None

    data_access_class: str
    result_schema_ref: str
```

---

## 4.4 建议修改文件

```text
src/backend/app/schemas/agent_harness.py
src/backend/app/runtime/agent_capability_catalog.py
src/backend/app/services/agent_harness_service.py
```

建议新增：

```text
src/backend/app/schemas/agent_action.py
```

用于将 ActionEnvelope 从单一 planning schema 拆成独立动作协议。

---

# 5. 优化二：新增 Read-only Observation Tool Layer

这是整个优化中优先级最高的一项。

## 5.1 目标

让 Agent 从：

```text
Context → Reason
```

升级为：

```text
Context
→ Reason
→ Read Tool
→ Observe
→ Reason
```

但严格保证：

```text
Read Tool ≠ Execution Tool
```

---

## 5.2 第一阶段建议工具

### 5.2.1 `inspect_dataset_summary`

返回：

- subject count；
- session count；
- modality；
- BIDS / raw / converted；
- TR；
- volume count；
- available structural；
- available functional；
- known data conflicts。

---

### 5.2.2 `inspect_bids_validation`

返回：

- validation status；
- missing required files；
- inconsistent metadata；
- subject-level issues；
- blocking / warning facts。

---

### 5.2.3 `inspect_preprocessing_readiness`

返回：

- 已完成 preprocessing stages；
- 可复用 output；
- 缺失前置步骤；
- 当前是否满足 FC / ALFF / ReHo 输入条件。

---

### 5.2.4 `inspect_available_metrics`

返回当前项目可计算：

```text
ALFF
fALFF
ReHo
FC
```

以及：

- required inputs；
- current readiness；
- supported backend；
- limitation。

---

### 5.2.5 `inspect_run_status`

返回：

- run state；
- completed subjects；
- failed subjects；
- current node；
- current evidence state；
- terminal / active。

---

### 5.2.6 `inspect_qc_summary`

返回：

- motion；
- registration；
- normalization；
- artifact validation；
- pass / warning / failed；
- blocking fact。

---

### 5.2.7 `inspect_artifact_registry`

返回：

- artifact id；
- artifact type；
- producing run；
- reload status；
- checksum；
- scientific capability。

不返回绝对路径。

---

### 5.2.8 `inspect_environment_capabilities`

返回：

- CPU / GPU；
- CuPy；
- supported backend；
- external tool availability；
- sandbox readiness；
- version hash。

---

## 5.3 Tool Schema

建议新建：

```python
class AgentReadToolRequest(BaseModel):
    tool_id: str
    project_id: str
    lifecycle_id: str
    resource_refs: tuple[str, ...] = ()

class AgentReadToolObservation(BaseModel):
    tool_id: str
    observation_id: str
    source_refs: tuple[str, ...]
    source_hash: str
    data_class: str
    payload: dict[str, object]
    generated_at: datetime
```

---

## 5.4 安全约束

Read Tool 必须满足：

```text
禁止 raw NIfTI bytes
禁止任意 filesystem path
禁止 SQL
禁止 shell
禁止 subprocess
禁止 external network
禁止 absolute path
禁止 credential
禁止修改 lifecycle
禁止修改 project
禁止创建 execution ticket
```

Tool 输入只能允许：

```text
project_id
lifecycle_id
typed resource ref
```

---

## 5.5 推荐文件结构

```text
src/backend/app/runtime/agent_read_tool_registry.py
src/backend/app/services/agent_read_tool_service.py
src/backend/app/schemas/agent_tool.py
```

---

# 6. 优化三：新增 Sentinel-like AgentPolicyService

## 6.1 为什么需要

未来动作增多后，不能把安全判断分散在每个 Action Handler 内。

因此新增统一的：

```text
AgentPolicyService
```

职责：

```text
输入 Action Proposal
输出 ALLOW / ASK / DENY
```

它不执行任何动作。

---

## 6.2 Policy Decision Schema

```python
class AgentPolicyDecision(BaseModel):
    proposal_id: str
    capability_id: str

    decision: Literal["ALLOW", "ASK", "DENY"]

    risk_class: str
    reason_codes: tuple[str, ...]

    policy_version: str
    policy_hash: str

    evaluated_context_hash: str

    required_approval_type: str | None = None
```

---

## 6.3 推荐规则

例如：

```text
inspect_dataset
→ ALLOW

inspect_qc
→ ALLOW

draft_plan
→ ALLOW

revise_plan
→ ALLOW

propose_execution
→ ASK

propose_recovery
→ ASK

propose_export
→ ASK

run_shell
→ DENY

write_file
→ DENY

modify_rawdata
→ DENY

issue_execution_ticket
→ DENY
```

---

## 6.4 目标流程

```text
LLM
 ↓
ActionEnvelope
 ↓
Schema Validation
 ↓
Capability Catalog
 ↓
AgentPolicyService
 ↓
ALLOW / ASK / DENY
 ↓
Typed Action Handler
```

---

## 6.5 与现有 Approval 的关系

AgentPolicyService 不替代：

- Approval Gate；
- Execution Ticket；
- Execution Gateway。

它只是更前置的 Agent Proposal Policy。

最终仍然：

```text
Agent Proposal
→ Policy
→ Reviewed Plan
→ Approval Summary
→ Human Approval
→ Ticket
→ Gateway
```

---

# 7. 优化四：扩展 Scientific Skill 系统

## 7.1 当前问题

现在只有：

```text
planning_evidence_review.v1
```

无法对不同医学影像任务形成稳定、可评估的专业工作程序。

---

## 7.2 第一阶段建议 Skills

```text
dataset_intake_review.v1
preprocessing_planning.v1
fc_analysis_planning.v1
alff_reho_planning.v1
qc_evidence_review.v1
failed_run_recovery_review.v1
result_interpretation.v1
artifact_export_review.v1
```

---

## 7.3 示例：FC Skill

```text
Skill:
fc_analysis_planning.v1
```

允许动作：

```text
inspect_dataset_summary
inspect_preprocessing_readiness
inspect_available_metrics
inspect_artifact_registry
request_decision
draft_plan
```

需要 Context：

```text
goal
policy
project_evidence
plan_state
memory_context
budget
```

禁止：

```text
rawdata
credential
shell
filesystem write
external network
```

---

## 7.4 Skill Manifest 建议扩展

当前 manifest 可以进一步增加：

```json
{
  "skill_id": "fc_analysis_planning.v1",
  "version": "v1",
  "allowed_actions": [],
  "allowed_states": [],
  "required_context_sections": [],
  "allowed_tool_refs": [],
  "data_access_classes": [],
  "output_schema_ref": "ActionEnvelope",
  "max_model_calls": 4,
  "max_tool_calls": 6,
  "content_hash": "..."
}
```

---

# 8. 优化五：增加 Deterministic Skill Router

不要让模型自由决定加载任意 Skill。

推荐：

```text
Goal Contract
+
Lifecycle State
+
Dataset State
+
Requested Metric
      ↓
Deterministic Skill Router
      ↓
Selected Skill Set
```

---

## 8.1 路由示例

### FC 任务

```text
goal.metric = FC
state = CONTEXT_READY

→ planning_evidence_review.v1
→ fc_analysis_planning.v1
```

### ReHo 任务

```text
goal.metric = ReHo

→ preprocessing_planning.v1
→ alff_reho_planning.v1
```

### 执行失败

```text
state = EVALUATING
failed_subjects > 0

→ failed_run_recovery_review.v1
```

---

## 8.2 设计原则

Skill：

```text
定义“怎么工作”
```

Capability：

```text
定义“允许做什么”
```

Policy：

```text
定义“当前能不能做”
```

三者不要混在一起。

---

# 9. 优化六：Context 从“全量快照”升级为“两层上下文”

## 9.1 Base Context

始终提供：

```text
goal
policy
current lifecycle state
pending decision
current reviewed plan summary
memory summary
budget
```

---

## 9.2 On-demand Evidence

以下信息不再默认全部塞入模型：

```text
dataset details
QC
artifact registry
environment details
run details
metric readiness
```

需要时通过 Read Tool 获取。

---

## 9.3 新的 Harness 逻辑

```text
Base Context
    ↓
Reason
    ↓
Need Evidence?
    ↓
Read Tool
    ↓
EvidenceSlice
    ↓
Reason
```

---

## 9.4 Evidence Slice

```python
class AgentEvidenceSlice(BaseModel):
    slice_id: str
    parent_snapshot_hash: str
    source_refs: tuple[str, ...]
    source_hash: str
    payload_hash: str
    data_class: str
    payload: dict[str, object]
```

这样既能按需取信息，又保持：

- provenance；
- replay；
- audit；
- hash binding。

---

# 10. 优化七：将 Harness 扩展为完整 Lifecycle Loop

目标：

```text
Goal
 ↓
Observe
 ↓
Plan
 ↓
Approve
 ↓
Execute
 ↓
Observe
 ↓
Evaluate
 ↓
Reflect
 ↓
Finish / Recovery / Next Step
```

---

## 10.1 新增 Result Reflection

建议新增：

```text
explain_result
propose_next_step
```

但它们只能读取：

- Observation；
- Goal Evaluation；
- Result Summary；
- artifact refs；
- limitation flags。

不能修改：

- outcome；
- capability；
- validation；
- artifact existence；
- scientific status。

---

## 10.2 Result Explanation 约束

模型只能生成：

```text
human-readable explanation
next-step suggestion
```

系统继续负责：

```text
truth fields
```

---

# 11. 优化八：Recovery 改成“候选审查”，不是“模型恢复”

## 11.1 当前确定性引擎继续保留

Recovery Engine 继续生成合法：

```text
SAFE_RETRY
RETRY_FAILED_SUBJECTS
RESUME
PARAMETER_CHANGE
BACKEND_SWITCH
REPLAN
HUMAN_HANDOFF
```

---

## 11.2 Harness 新职责

```text
Recovery Candidates
      ↓
Harness Reviewer
      ↓
解释：
- 为什么适合
- 影响什么
- 是否改变科学参数
- 是否需要重新审批
      ↓
request_decision / propose_recovery
```

---

## 11.3 禁止

```text
LLM 自己生成任意 parameter patch
LLM 自己改变 backend
LLM 自己决定 retry quota
LLM 自己执行 recovery
```

---

# 12. 优化九：Multi-Agent 从 Reviewer Team 开始

> **权威归属（2026-10-02）**：本节与 §22.6、Phase 8 的多 Agent 角色口径**已被阶段十四取代**。
> 多 Agent 审查范围与 G0 评估门槛的唯一权威是
> `specs/阶段记录/阶段十四/生产多Agent架构/`（三个固定只读 reviewer：`science` / `safety` /
> `completeness`，见其 `02_角色合同与能力边界方案.md` §2；G0 门槛见其
> `01_真实脱敏TraceReplay准入方案.md` §7）。本节的 Scientific + Safety **两** reviewer 图示
> 是三角色集合的子集，不构成第二套设计权威，不得据本节另立台账或评估门槛。
> §12.6/§22.6 所称“继续使用现有 Gate”即指阶段十四 `01` 的 Gate；该阶段已于 2026-10-02
> 在 G0 之前正式延期，因此本节的 Reviewer Team 也随之延期（见阶段十四 `00` §11）。
> 本节仍然有效的独立范围是动作空间与感知层扩展，见 §23 Phase 1 与本文末尾的归属说明。

## 12.1 不建议直接使用 Swarm

不建议：

```text
Planner Agent
Browser Agent
Code Agent
Research Agent
自由协商
```

医学影像科研更适合：

```text
Planner
   │
   ├─────────────┐
   ▼             ▼
Scientific    Safety
Reviewer      Reviewer
   │             │
   └──────┬──────┘
          ▼
Deterministic Aggregator
```

---

## 12.2 Planner

负责：

- 目标理解；
- workflow proposal；
- evidence request；
- plan draft。

---

## 12.3 Scientific Reviewer

检查：

- TR；
- atlas；
- GSR；
- smoothing；
- preprocessing prerequisite；
- subject scope；
- session scope；
- metric validity；
- simplified method；
- unsupported scientific claims。

只产生：

```text
Finding
Severity
EvidenceRef
Recommendation
```

---

## 12.4 Safety Reviewer

检查：

- rawdata write；
- scope expansion；
- backend change；
- unapproved recovery；
- missing approval；
- environment drift；
- unsupported tool；
- forbidden capability。

---

## 12.5 Deterministic Aggregator

最终：

```text
merge findings
deduplicate
sort severity
resolve exact rule conflicts
decide blocking / warning
```

不要让 Reviewer 自己执行。

---

## 12.6 保留现有 Multi-Agent Evaluation Gate

当前已经有：

- blocking omission recall；
- false-positive rate；
- consistency；
- model calls；
- input token；
- latency；
- human operations。

继续使用。

生产化要求：

```text
Multi-Agent candidate
必须显著优于 single-agent baseline
才能开放。
```

---

# 13. 优化十：统一 Event-driven Harness Runtime

## 13.1 当前基础

已经存在：

- durable wake；
- lease；
- retry；
- startup recovery；
- AgentTaskScheduler；
- AgentExecutionCoordinator。

因此不要新增开放式 `while True` Agent Loop。

---

## 13.2 推荐事件模型

```text
goal_created
decision_answered
plan_invalidated
approval_completed
execution_started
run_progressed
run_completed
run_failed
observation_ready
goal_evaluated
recovery_proposed
memory_changed
environment_changed
```

---

## 13.3 Event Schema

```python
class AgentWakeEvent(BaseModel):
    event_id: str
    project_id: str
    lifecycle_id: str
    event_type: str
    fingerprint: str
    priority: int
    available_at: datetime
    expires_at: datetime | None
    source_ref: str | None
```

---

## 13.4 Wake Policy

每次 wake：

```text
最多执行 N 个 Harness step
达到 blocked / waiting / terminal 就退出
```

继续保留 bounded wake。

---

# 14. 优化十一：统一 Human Attention Policy

现在 decision batch 已经做得很好。

建议进一步把所有用户干预统一成：

```text
AttentionRequest
```

---

## 14.1 类型

```text
SCIENCE_DECISION
PLAN_APPROVAL
RECOVERY_APPROVAL
EXPORT_APPROVAL
SECURITY_WARNING
ENVIRONMENT_CHANGED
```

---

## 14.2 Attention Schema

```python
class AgentAttentionRequest(BaseModel):
    attention_id: str
    kind: str
    blocking: bool
    lifecycle_id: str
    title: str
    summary: str
    evidence_refs: tuple[str, ...]
    expires_at: datetime | None
```

---

## 14.3 目标

减少：

```text
Human Operations
```

而不是扩大：

```text
Agent Autonomy
```

核心原则：

```text
更多自动观察
更少重复提问
关键科学决定仍由用户确认
```

---

# 15. 优化十二：Memory Taxonomy

当前 Memory 基础已经成熟，不建议推翻。

建议增加类型：

```text
USER_PREFERENCE
PROJECT_CONVENTION
SCIENTIFIC_PREFERENCE
SCIENTIFIC_ASSUMPTION
PAST_EXECUTION_EXPERIENCE
SYSTEM_FACT
```

---

## 15.1 每条 Memory 建议字段

```text
memory_type
scope
source_ref
source_hash
confidence
valid_from
valid_until
requires_confirmation
last_confirmed_at
```

---

## 15.2 科学 Memory 使用规则

例如：

```text
“用户通常使用 Schaefer-200”
```

属于：

```text
SCIENTIFIC_PREFERENCE
```

可以：

```text
推荐
```

但不能：

```text
自动成为当前项目事实
```

当前项目 atlas 必须来自：

```text
current task confirmation
或
project config
```

---

# 16. 优化十三：引入 Medical Data Taint Policy

这是未来必须提前准备的一层。

## 16.1 Data Sensitivity Classification

建议：

```text
PUBLIC
PROJECT_METADATA
DERIVED_NONIDENTIFYING
POTENTIALLY_IDENTIFYING
RAW_MEDICAL_DATA
CREDENTIAL
```

---

## 16.2 Context Policy

```text
RAW_MEDICAL_DATA
→ 禁止进入 remote LLM

POTENTIALLY_IDENTIFYING
→ 必须经过 redaction

DERIVED_NONIDENTIFYING
→ 可以通过 typed projection

PROJECT_METADATA
→ 可以进入 bounded context

CREDENTIAL
→ 永远禁止进入模型
```

---

## 16.3 Tool Observation 也带数据分类

```python
class AgentToolObservation(BaseModel):
    ...
    data_class: str
```

这样可以实现：

```text
Context Egress Policy
```

---

# 17. 优化十四：补齐 Windows Sandbox 的网络隔离

当前 Windows restricted-process provider 已经具备：

- restricted token；
- Job Object；
- environment restriction；
- ACL；
- process-tree kill。

但当前网络隔离仍然是：

```text
not_enforced
```

如果未来支持：

- external CLI；
- third-party tools；
- connector；
- generated helper；
- subprocess；

这一点必须补。

目标：

```text
Agent Process
 ├── restricted token
 ├── Job Object
 ├── restricted filesystem
 ├── controlled environment
 ├── no inherited credentials
 └── deny-by-default network
```

---

# 18. 优化十五：Trace 升级为用户可读的 Agent Explainability

当前底层已经有：

- AgentTraceService；
- AgentReplayService；
- ModelCallRecord；
- ActionRecord；
- context hash；
- evidence hash；
- invariant report。

这些足够做工程审计。

下一步重点是用户界面。

---

## 18.1 用户看到

```text
Goal
分析当前 rs-fMRI 的 FC

↓
系统检查
BIDS validation passed
Preprocessing available
Atlas not selected

↓
Agent 判断
FC 需要 atlas

↓
请求用户
请选择 atlas

↓
用户确认
Schaefer-200

↓
Plan revision 2

↓
等待审批
```

---

## 18.2 Advanced Trace

再展示：

```text
context_hash
evidence_snapshot_hash
action_hash
request_hash
policy_hash
plan_hash
ticket_hash
```

普通用户不需要默认看到 hash。

---

# 19. 推荐代码结构

建议逐步增加：

```text
src/backend/app/
  schemas/
    agent_action.py
    agent_tool.py
    agent_policy.py
    agent_attention.py
    agent_event.py

  runtime/
    agent_capability_catalog.py
    agent_read_tool_registry.py
    agent_skill_router.py

  services/
    agent_policy_service.py
    agent_read_tool_service.py
    agent_skill_router_service.py
    agent_attention_service.py
    agent_event_service.py
    agent_result_reflection_service.py
    agent_recovery_review_service.py

  agent_skills/
    planning_evidence_review.v1/
    dataset_intake_review.v1/
    preprocessing_planning.v1/
    fc_analysis_planning.v1/
    alff_reho_planning.v1/
    qc_evidence_review.v1/
    failed_run_recovery_review.v1/
    result_interpretation.v1/
```

---

# 20. 推荐 ActionEnvelope v3

建议未来：

```python
ActionEnvelope = Annotated[
    InspectAction
    | RequestDecisionAction
    | DraftPlanAction
    | RevisePlanAction
    | ExplainResultAction
    | ProposeRecoveryAction
    | ProposeNextStepAction,
    Field(discriminator="kind"),
]
```

但是：

```text
ExecuteAction
ShellAction
FileWriteAction
NetworkAction
IssueTicketAction
```

永远不要进入此 Union。

---

# 21. 推荐运行状态机

可以保留现有 lifecycle，但从 Harness 视角明确：

```text
CREATED
  ↓
CONTEXT_READY
  ↓
INSPECTING
  ↓
WAITING_FOR_INPUT
  ↓
PLAN_DRAFTED
  ↓
PLAN_VALIDATED
  ↓
WAITING_FOR_APPROVAL
  ↓
APPROVED
  ↓
EXECUTION_READY
  ↓
RUNNING
  ↓
OBSERVING
  ↓
EVALUATING
  ↓
REFLECTING
  ├── GOAL_SATISFIED
  ├── RECOVERY_PROPOSED
  └── HUMAN_HANDOFF
```

是否真正新增 `INSPECTING / REFLECTING` 为 durable lifecycle state，需要评估当前状态机迁移成本。

更保守做法是：

- lifecycle state 不变；
- Harness step kind 表示 inspection / reflection。

建议优先采用保守方案，避免破坏现有稳定状态机。

---

# 22. 测试与评估方案

## 22.1 Read Tool 测试

必须验证：

```text
不能读 rawdata
不能泄露绝对路径
不能跨 project
不能访问未绑定 run
不能写文件
不能触发 pipeline
不能调用 subprocess
```

---

## 22.2 Policy Engine 测试

覆盖：

```text
ALLOW
ASK
DENY
```

以及：

```text
state mismatch
capability mismatch
context mismatch
risk escalation
stale evidence
```

---

## 22.3 Skill Router 测试

输入：

```text
goal + lifecycle + dataset
```

必须 deterministic。

同样输入必须得到同样 Skill。

---

## 22.4 Result Reflection 测试

验证模型不能覆盖：

```text
outcome
capability
artifact exists
QC status
validation result
```

---

## 22.5 Recovery Review 测试

模型只能从 deterministic candidates 中选择 / 解释。

不能制造未知 recovery action。

---

## 22.6 Multi-Agent Gate

继续使用现有 Gate。

建议增加：

```text
scientific omission recall
unsafe suggestion rate
unsupported-claim rate
human interruption count
mean read-tool calls
```

---

# 23. 分阶段实施计划

## Phase 1：Observation Layer

目标：

```text
让 Agent 有“眼睛”
```

实现：

- `agent_read_tool_registry.py`
- `agent_read_tool_service.py`
- 6～8 个 A0 Tool
- ActionEnvelope 增加 inspect
- Tool Observation persistence
- tool call budget

验收：

```text
Agent 可以在不问用户的情况下
自行检查 dataset / readiness / artifact / QC
```

---

## Phase 2：Policy Layer

目标：

```text
所有 Agent Action 统一经过 Policy
```

实现：

- `AgentPolicyService`
- PolicyDecision
- ALLOW / ASK / DENY
- policy hash
- trace projection

验收：

```text
所有 Harness action
必须可追踪到 policy decision
```

---

## Phase 3：Skill Expansion

实现：

```text
dataset_intake_review.v1
preprocessing_planning.v1
fc_analysis_planning.v1
alff_reho_planning.v1
qc_evidence_review.v1
```

以及 deterministic Skill Router。

验收：

```text
不同科研任务加载不同 skill
不再依赖单一通用 prompt
```

---

## Phase 4：Context Retrieval

将：

```text
Full Context
```

改为：

```text
Base Context + On-demand Evidence
```

验收：

- 平均 input token 降低；
- 不减少 blocking scientific fact recall；
- tool call 数量可控。

---

## Phase 5：Result Reflection

实现：

```text
explain_result
propose_next_step
```

验收：

- deterministic truth 不受影响；
- 用户能获得更可读结果解释；
- 不能出现 unsupported success claim。

---

## Phase 6：Recovery Reviewer

实现：

```text
Recovery Candidate → Harness Review
```

验收：

- 不新增执行权限；
- 不修改 recovery quota；
- 不允许 agent 直接 retry。

---

## Phase 7：Event Runtime

统一：

```text
goal_created
decision_answered
run_completed
observation_ready
...
```

验收：

- restart 后可恢复；
- 无 duplicate wake；
- 无 open-ended loop；
- 每个 wake bounded。

---

## Phase 8：Multi-Agent Reviewer Team

仅在 Evaluation Gate 通过后开放。

第一版只支持：

```text
Planner
Scientific Reviewer
Safety Reviewer
```

不开放执行型 Subagent。

---

# 24. 明确暂缓或禁止的能力

## 24.1 暂缓：Agent 自己创建任意 Tool / Connector

Muse 可以动态创建 Connector。

但当前 MedImage Agent 不适合立即跟进。

原因：

- medical data risk；
- arbitrary code；
- external network；
- scientific reproducibility；
- package integrity；
- Windows sandbox 网络隔离未完成。

可以未来考虑：

```text
Reviewed Developer Tool Package
```

而不是运行时动态生成。

---

## 24.2 禁止：LLM 直接访问 Pipeline Runtime

不要暴露：

```text
pipeline.execute()
runner.run()
ticket.issue()
gateway.dispatch()
```

给模型。

---

## 24.3 禁止：模型直接修改 Rawdata

保持：

```text
rawdata = read-only
```

---

## 24.4 禁止：模型直接写任意文件

所有 artifact 必须由：

```text
registered pipeline / approved exporter
```

产生。

---

## 24.5 禁止：模型自由 Shell

如果未来必须加入 CLI：

```text
必须通过 sandboxed registered command contract
```

而不是：

```text
shell(cmd)
```

---

# 25. 建议优先级

| 优先级 | 改进项 | 价值 | 风险 |
|---|---|---:|---:|
| P0 | Read-only Observation Tools | 极高 | 低 |
| P0 | AgentPolicyService | 极高 | 低 |
| P0 | Action Space 扩展 | 高 | 中 |
| P0 | 多 Scientific Skills | 高 | 低 |
| P0 | Deterministic Skill Router | 高 | 低 |
| P1 | Base Context + Evidence Retrieval | 高 | 中 |
| P1 | Result Reflection | 高 | 低 |
| P1 | Recovery Reviewer | 高 | 低 |
| P1 | Event-driven Wake Registry | 高 | 中 |
| P1 | Human Attention Policy | 中 | 低 |
| P1 | Memory Taxonomy | 中 | 低 |
| P1 | User-facing Agent Trace | 中 | 低 |
| P2 | Reviewer Multi-Agent | 中高 | 中高 |
| P2 | Medical Data Taint | 高 | 中 |
| P2 | Windows Network Isolation | 高 | 高 |
| 暂缓 | Runtime Dynamic Tool Builder | 中 | 高 |
| 禁止 | LLM Direct Execution | 低 | 极高 |

---

# 26. 最终建议

如果下一阶段只做四件事，建议严格按以下顺序：

## 第一：Read-only Observation Tools

解决：

```text
Agent 看不到系统内部状态
```

让 Agent 真正具备：

```text
Observe → Reason
```

---

## 第二：AgentPolicyService

解决：

```text
未来 Action 增多后权限判断分散
```

建立统一：

```text
Proposal → Policy → Allow / Ask / Deny
```

---

## 第三：Scientific Skills + Skill Router

解决：

```text
一个 Skill 承担所有医学影像任务
```

让每种任务有：

```text
明确工作程序
明确上下文
明确工具
明确动作
明确预算
```

---

## 第四：Result / Recovery Reflection Loop

补齐：

```text
Execute 之后 Agent 不再思考
```

让完整流程变成：

```text
Goal
→ Observe
→ Plan
→ Approve
→ Execute
→ Observe
→ Evaluate
→ Reflect
→ Finish / Recover / Next Step
```

---

# 27. 最终目标形态

完成上述改造后，MedImage Agent Harness 不应变成一个“更自治的通用 Agent”，而应变成：

> **一个高可控、可审计、医学科研约束明确、具有主动观察能力和闭环推理能力的 Research Agent Runtime。**

最终架构目标：

```text
User Goal
   ↓
Research Agent Harness
   ├── Observe
   ├── Reason
   ├── Retrieve Evidence
   ├── Apply Scientific Skill
   ├── Propose
   └── Explain
        ↓
Agent Policy Plane
        ↓
Reviewed Scientific Plan
        ↓
Human Approval
        ↓
Execution Ticket
        ↓
Execution Gateway
        ↓
Deterministic Pipeline Runtime
        ↓
Observation
        ↓
Goal Evaluation
        ↓
Agent Reflection
```

最关键的原则保持不变：

```text
Model does not execute science.
Model proposes scientific work.

System authorizes execution.
Pipeline produces evidence.
Deterministic services determine truth.
Human retains consequential scientific decisions.
```

这才是最适合 MedImage Agent 的 Muse 架构迁移方式。

---

# 28. 参考实现位置

当前项目中与本方案关系最密切的实现：

- `src/backend/app/services/agent_harness_service.py`
- `src/backend/app/services/agent_harness_context_service.py`
- `src/backend/app/services/agent_planning_action_service.py`
- `src/backend/app/runtime/agent_capability_catalog.py`
- `src/backend/app/schemas/agent_harness.py`
- `src/backend/app/services/agent_orchestrator.py`
- `src/backend/app/services/agent_invariant_checker.py`
- `src/backend/app/services/agent_task_result_summary.py`
- `src/backend/app/services/recovery_proposal_engine.py`
- `src/backend/app/services/multi_agent_evaluation_service.py`
- `src/backend/app/agent_skills/registry.py`
- `src/backend/app/agent_skills/planning_evidence_review.v1/`

相关文档：

- `README.md`
- `docs/架构与决策/系统架构.md`
- `docs/架构与决策/记忆系统设计方案.md`
- `docs/安全与审批/真实项目运行生命周期.md`
- `docs/安全与审批/安全边界.md`
- `docs/规划与运行时/处理流程图.md`

---

# 29. 参考思想来源

Muse 架构中值得迁移的思想主要包括：

- Model 与 Execution Authority 分离；
- Agent Proposal 与 Security Control Plane 分离；
- Persistent Runtime；
- Event-driven Wake；
- Skill 化工作程序；
- Long-horizon Agent Loop；
- Human-in-the-loop；
- Defense in Depth；
- Read / Act 权限分离；
- Observability；
- Multi-Agent Coordination；
- Agent 不直接持有高风险执行权限。

本项目不应直接照搬 Muse 的 Browser / Credential / General-purpose Computer Use，而应保留医学影像研究系统更严格的：

- deterministic pipeline；
- scientific plan review；
- rawdata read-only；
- evidence binding；
- approval；
- reproducibility；
- artifact truth；
- recovery constraints。

