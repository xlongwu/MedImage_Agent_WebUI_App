# MedImage Agent Harness 优化方案
## ——借鉴 Meta Muse 架构的受控 Research Agent Runtime 升级设计

> 项目：[`xlongwu/MedImage_Agent_WebUI_App`](https://github.com/xlongwu/MedImage_Agent_WebUI_App)  
> 方案整理日期：2026-10-01  
> 裁定日期：2026-10-02（源码逐节复核 + 维护者批准，见 §0）  
> 目标（已按 §0 收窄）：在**不破坏现有确定性执行、安全审批、医学影像科研可追溯性**的前提下，评估把当前 Harness 从“受控规划器”升级为具备观察与闭环推理能力的 Research Agent Harness。  
> 原文中“有限多 Agent 审查能力”一项**不在本阶段范围内**：其角色口径已被阶段十四取代，且阶段十四已于 2026-10-02 在 G0 之前正式延期（见 §0.3）。

---

# 0. 实施裁定（2026-10-02，权威）

本节是本文件唯一的实施范围权威。下方 §1～§29 保留为**设计推理与目标形态记录**，其中的现状描述若与本节冲突，以本节及其 `file:line` 证据为准。本节由 2026-10-02 的逐节源码复核和维护者对 §0.4 四项决策的批准产生；未获批准的条目不构成待办、实现授权或当前行为描述。

2026-10-02 实施更新：用户已在本轮明确批准采用 **无 capability 的 AppContainer + write-restricted token + Job Object**。§17 已接入 provider、策略/环境指纹、审批摘要/Ticket、持久尝试、API、前端中英文展示与固定 smoke 合同。基线、机制、消费者链和最终全量自动验收均通过（见 §0.6）。D-03 的机制选型已批准；SDK 资料已按微软头文件核实，人工核对及实测判读尚不冒充已确认。本文件在 D-03 闭环前继续留在当前路径。§18 的 GUI 层仍归清单 A-13，不因本次基础设施硬化获得验证。

## 0.1 裁定的证据层级约定

下表证据全部为**① 源码实现与注册**层级（必要时附自动化回归②）。本文件不声称、也不因本裁定获得 ③ packaged/release 或 ④ 科学验证层级的任何结论（`AGENTS.md` §5.7、§5.8）。科学能力等级一律以 `docs/项目概览/能力矩阵.md` 为准。

判定取值：`已实现` / `部分实现` / `未实现-获批立项` / `未实现-建议跳过` / `未实现-待用户拍板` / `已被取代`。

## 0.2 十五个优化逐节判定

| 节 | 主题 | 判定 | 源码锁定与缺失项 |
|---|---|---|---|
| §4 优化一 | Capability-based Action Space（A0～A3、§4.3 Schema 扩展、§20 v3） | **未实现-建议跳过**（经批准，见 §0.4-D2） | `schemas/agent_harness.py:25` 仍为 `Literal["request_decision", "draft_plan"]`；`runtime/agent_capability_catalog.py:27-35` 仅登记这两个 A1；`tests/unit/test_agent_harness_service.py` 的 `test_two_typed_actions_parse_and_removed_actions_or_extra_fields_are_rejected` 把 `read_evidence`/`explain_result`/`propose_recovery`/`finish` 被拒**固化为回归断言**。§4.3 提议的 12 个字段中 7 个已存在（`agent_capability_catalog.py:11-19`），新增 `risk_class`/`allowed_tool_refs`/`requires_policy_check`/`requires_human_approval`/`max_calls_per_task`/`data_access_class`/`result_schema_ref` 在全仓 0 命中，且**无任何当前消费者**。 |
| §5 优化二 | Read-only Observation Tool Layer（模型自主拉取） | **未实现-建议跳过**（经批准，见 §0.4-D2） | 提案 §19 的 `agent_read_tool_registry.py`、`services/agent_read_tool_service.py`、`schemas/agent_tool.py` 全仓 **0 命中**。同等**信息面**已由服务端在调用模型前组装：`services/agent_evidence_service.py:42/168/221/228`（`build_snapshot`/`select_for_context`/`select_for_purpose`/`read_for_context`）。差异只是「由谁发起拉取」，属 `AGENTS.md` §1.3 边界扩展。 |
| §6 优化三 | Sentinel-like AgentPolicyService | **未实现-建议跳过**（重复抽象，见 §0.4-D3） | fail-closed 策略入口**已集中**在 `runtime/agent_capability_catalog.py:39-53` `assert_capability_allowed()`（含 `automation_level not in {"A0","A1"}`、`requires_current_approval`、`side_effect_class` 白名单）与 `:54-69` `assert_capability_context_and_output_allowed()`，并向下接 Approval Gate → Execution Ticket → 唯一 Gateway。ALLOW/ASK/DENY 三态在动作面只有 2 个 A1 kind 时**没有可判别的输入**。`services/agent_policy_service.py`、`schemas/agent_policy.py` 0 命中。 |
| §7 优化四 | 扩展 Scientific Skill | **部分实现**（底座已实现，新 Skill 未立项） | 严格注册表与 manifest 合同**已实现**：`agent_skills/registry.py` 的 `load()`（`:53`）、`validate_all()`（`:80`）与 `_validate()`（`:96`）、`agent_skills/schemas.py:16-30` `SkillManifest` 已含 `allowed_actions`/`allowed_states`/`required_context_sections`/`output_schema_ref`/`content_hash`，`:33-36` 只持久化 `SkillContextRef`。§2.2.3「当前内置 Skill 只有 `planning_evidence_review.v1`」**与源码一致**（`registry.py:17-19` `BUILTIN_SKILL_IDS` 恰好 1 项）。§7.2 的 8 个新 Skill 未实现；§7.4 的 `allowed_tool_refs`/`data_access_classes`/`max_tool_calls` 三个 Skill 级字段未实现，且前两项依赖被跳过的 §5/§16——2026-10-02 校正：`max_model_calls` **并非全仓不存在**，它已作为 Harness attempt 预算存在于 `core/config_schema.py:126,157`，缺的只是 SkillManifest 上的同名字段。归属已随阶段十二-`09` 闭环：清单 D-04 于 2026-10-02 由维护者决定**不立项、正式收缩为 1 个 Skill**，因此这些字段与 8 个新 Skill 均不再是待办。 |
| §8 优化五 | Deterministic Skill Router | **未实现-建议跳过** | `runtime/agent_skill_router.py`、`services/agent_skill_router_service.py` 0 命中。**按用途的确定性选择已存在**且位于 Context 层：`services/agent_harness_context_service.py:39-49` `required_by_purpose`/`optional_order_by_purpose`、`:127` `purpose_for()`。在 Skill 只有 1 个、动作只有 2 个的前提下，Router **没有可路由的分叉**，属 `AGENTS.md` §3.0 禁止的预建间接层。 |
| §9 优化六 | 两层 Context（Base + On-demand Evidence） | **部分实现**；按需**拉取**部分**未实现-建议跳过** | Base/On-demand 的**服务端裁剪已实现**：`schemas/agent_harness.py:298-305` 有 `required_sections`/`included_sections`/`omitted_sections`/`complete`，`agent_harness_context_service.py:204-222` 按优先级裁剪并记 `{name}:byte_budget` 省略原因。§9.4 `AgentEvidenceSlice` 的溯源字段**已等价存在**于 `agent_harness.py:264-267`（`source_refs`/`source_hash`/`data`）+ 逐节 `section_hashes`，仅缺 `data_class`。因此本节真实剩余量只有「模型自主发起拉取」，随 §0.4-D2 判定为跳过。 |
| §10 优化七 | 完整 Lifecycle Loop + §10.1 Result Reflection | **部分实现**（**核心不变量已实现**，模型动作未实现） | §10.2「模型只写文本、真值归系统」已落实为结构化字段：`schemas/agent_task.py:252-267` `AgentResultExplanation` 携带 `outcome`/计数/`artifact_refs`/`criteria`/`limitation_codes`/`recommended_action_code` 真值，并把模型文本限于 `generated_text` + `generated_text_status: not_requested｜accepted｜conflict_rejected`；`services/agent_task_result_summary.py:109-174` `_guard_generated_text()` 在冲突时丢弃文本并记 `conflict_rejected`。前后端消费者齐备（`AgentWorkspace.tsx`、`ResultSummaryCard.tsx`）。缺的只是 `explain_result`/`propose_next_step` 两个**动作 kind**，它们正是 §0.4-D2 拒绝扩展、并被回归测试固化拒绝的项。 |
| §11 优化八 | Recovery 改候选审查 | **部分实现**（审查链已实现，模型审查动作未实现） | 确定性候选 + 独立人工批准链**已实现**：`schemas/agent_lifecycle.py:31-35` `RETRY_PROPOSED`/`RECOVERY_PROPOSED`/`WAITING_FOR_RECOVERY_APPROVAL`、`services/agent_recovery_command_service.py:39` 状态守卫、`services/recovery_proposal_engine.py`、前端 `RecoveryApprovalReview.tsx`。§11.3 的四条禁止与 `AGENTS.md` §5.2 一致，属**现状描述**而非待办。剩余量为「让模型解释候选」，需 `propose_recovery` 动作，随 §0.4-D2 跳过。 |
| §12 优化九 | Multi-Agent Reviewer Team | **已被取代**（阶段十四），并随其**在 G0 前延期** | 见 §0.3 归属裁定。§12.6/§22.6 所称“现有 Gate”即阶段十四 `01` 的 G0。离线脚手架确实存在（`services/agent_review_role_registry.py`、`agent_review_context_projector.py`、`agent_review_finding_aggregator.py`、`structured_agent_model_adapter.py`、`services/multi_agent_gate_runner.py`），因此本节把它们称作“新增”的表述**已不准**；`tests/fixtures/agent_eval/multi_agent/` 目录**为空**，脚手架不产生任何能力等级。 |
| §13 优化十 | 统一 Event-driven Harness Runtime | **未实现-建议跳过**（重复抽象） | §13.3 `AgentWakeEvent` 是既有 `schemas/agent_task_wake.py:AgentTaskWakeRecord` 的**子集**（现存记录额外含 `step_key`/`attempts`/`lease_owner`/`lease_expires_at`/`last_error_code`，且文档字符串明示“只含控制面引用，不含 prompt/用户数据/ticket”）。消费侧 `services/agent_task_scheduler.py:52/73/93` 已实现 `enqueue`/lease `claim_next`/`run_once`、指数退避与 `MAX_WAKE_ATTEMPTS` 耗尽 → `HUMAN_HANDOFF`（`:114-129`）。再建一套事件抽象会形成**第二个唤醒权威**。§13.1 “已经存在 durable wake/lease/retry/startup recovery”一句**与源码一致**。 |
| §14 优化十一 | 统一 Human Attention Policy | **未实现-建议跳过**（与阶段十五已批准决策冲突） | 阶段十五 `00` §D1/Phase B **明确批准**把 attention 协调器保持为“仅浏览器内的 action-key/dismissal hook”，理由是不落库、不改审批哈希；当前实现即 `src/frontend/src/features/agent/attentionAction.ts:3-6`（`decision｜approval｜recovery`，注释第 18-19 行）。§14.1 的 `EXPORT_APPROVAL`/`SECURITY_WARNING` **没有生产者**（导出动作不存在；`EXECUTION_ENVIRONMENT_CHANGED` 只是错误码，见 `services/execution_environment_service.py:102,159`）。`schemas/agent_attention.py`、`services/agent_attention_service.py` 0 命中。 |
| §15 优化十二 | Memory Taxonomy | **部分实现**，且 §15.1 **实质已实现** | §15.1 九个字段全部有对应实现，只是命名不同：`memory_type`→`schemas/memory.py:77` `kind`、`scope`→`:76` `scope_type: Literal["project"]`、`source_ref`→`:82` `source: MemorySource`、`confidence`→`:88`、`sensitivity`→`:89`、`requires_confirmation`→`:91` `requires_review` + `MemoryRevision:113` `confirmation_status`/`:118` `confirmation_event_id`，`MemoryItem` 另有 `:137-138` `valid_from`/`valid_until`。§15 提议的 6 个类型名与现存 `:10-16` 六类（`user_preference`/`project_decision`/`environment_fact`/`workflow_lesson`/`error_lesson`/`presentation_preference`）**语义重叠但不得并列为第二套分类**（`AGENTS.md` §3.6：独立 memory SQLite 是唯一长期记忆权威）。 |
| §16 优化十三 | Medical Data Taint Policy | **部分实现**；§16.3 部分**未实现-随 §0.4-D2 跳过** | 记忆侧外发门**已实现**：`schemas/memory.py:19` `MemorySensitivity = public｜project_internal｜restricted｜rejected`，`services/memory_repository.py:575,606,641` 在检索/投影处以 SQL + 读时二次校验阻止 `restricted`/`rejected` 离开仓库。模型侧的同等控制是**字段白名单**而非 taint 标签：`agent_harness_context_service.py:105-112` 逐节允许字段、`agent_evidence.py` 全文无 `data_class`。§16.3 的 `data_class` 只在**新增读工具**上才有挂载点，因此其剩余量完全依赖被跳过的 §5。 |
| §17 优化十四 | Windows Sandbox 网络隔离 | **已实现（①源码、② provider 自动化实测；最终回归见 §0.6）** | `WindowsProcessSandbox.run()` 以 suspended 创建无 capability AppContainer 子进程，经 `_verify_network_token()` 和 Job 绑定后恢复；`SandboxPolicy.network_isolation` 表达要求，`SandboxAttemptRecord.network_isolation` 为 `unverified/enforced`。`sandbox_runtime_fingerprint()` 哈希实际实现内容，环境复验阻止旧审批跨实现漂移；所有当前消费者已同步，外部科学契约保持 `executable=false`。用户已批准机制，D-03 人工判读尚待确认。 |
| §18 优化十五 | Trace → 用户可读 Explainability | **部分实现** | 后端已实现且已对外：`services/agent_trace_service.py:20` `calculate_trace_integrity_hash()`、`:37/74` `get()`/`page()`，端点 `api/agent_task_routes.py:176-184` `GET /{task_id}/trace`（docstring 明示“paginated, redacted … advanced review only”）。§18.2 的高级字段**前端已在展示**：`features/agent/components/TechnicalEvidence.tsx:12-38` 逐行输出 `plan_hash`/`evidence_snapshot_hash`/`goal_hash`/`planning_inputs_hash`/`ticket_id`/`run_id`/`memory_context_hash` 等。剩余量是 §18.1 的**叙事式分层展示**与默认折叠策略，属前端信息层级工作，与阶段十二 简洁方案 §5.2「单一 `TaskCard` 信息层级」为**同一条改造线**；该线已于 2026-10-02 按清单 D-06 的拍板落到源码（`components/TaskCard.tsx` 为唯一视觉卡片、Harness 投影移入 Level-3 `TaskDetails`，见简洁方案标头），本节的剩余差异只在 ③ 层可见 GUI 走查（清单 A-13），不得另立第二条改造线，也不得把它写成 ① ② 层未闭合。 |

## 0.3 结构性章节与 Phase 归属

| 节 | 判定 | 归属 |
|---|---|---|
| §19 推荐代码结构 | 未实现 | 所列 **14 个新文件全仓 0 命中**（`schemas/agent_action.py`、`agent_tool.py`、`agent_policy.py`、`agent_attention.py`、`agent_event.py`、`runtime/agent_read_tool_registry.py`、`agent_skill_router.py`、`services/agent_policy_service.py`、`agent_read_tool_service.py`、`agent_skill_router_service.py`、`agent_attention_service.py`、`agent_event_service.py`、`agent_result_reflection_service.py`、`agent_recovery_review_service.py`）。其中多数是 §5/§6/§8/§13/§14 的别名，随其一并跳过；不得作为“目录骨架”预先落地。 |
| §20 ActionEnvelope v3 | 未实现-建议跳过 | 与 §0.4-D2 同一决策；`tests/unit/test_agent_harness_service.py` 的 `test_two_typed_actions_parse_and_removed_actions_or_extra_fields_are_rejected` 是直接反例。其“Execute/Shell/FileWrite/Network/IssueTicket 永不进入 Union”一条**已经成立**，因为 Union 只有 2 个成员。 |
| §21 运行状态机 | 未实现；且**其自我保留意见已采纳** | `schemas/agent_lifecycle.py:14-40` 的 25 个状态中**没有** `INSPECTING`/`REFLECTING`。§21 末段“优先采用保守方案（lifecycle state 不变，用 Harness step kind 表示）”与本裁定一致，但 `schemas/agent_harness.py:240` 的 `AgentHarnessStep.kind` 目前复用动作枚举、没有 inspection/reflection kind，故保守方案同样未实现。另需更正：§21 的线性链省略了现存的 `WAITING_FOR_SCIENCE_DECISION`、`DIAGNOSING`、`RETRYING`、`RECOVERING`、`SUCCEEDED`/`FAILED`/`CANCELED`，**不得当作当前状态机描述**。 |
| §22.1～§22.3、§22.5 | 未实现（其验证对象不存在） | 依赖 §5/§6/§8 与模型动作；§22.5 的部分不变量已由 `agent_recovery_command_service.py:39` 与 lifecycle 迁移表覆盖。 |
| §22.4 | **已实现** | `AgentResultExplanation` 真值字段 + `generated_text_status` + `_guard_generated_text()`（§10 行证据）。 |
| §22.6 | **已被取代** | 归阶段十四 `01` 的 G0；其新增指标（含 “mean read-tool calls”）依赖被跳过的 §5。 |
| §23 Phase 1（Observation） | 未立项-经批准跳过 | §0.4-D2 |
| §23 Phase 2（Policy Layer） | 未立项-经批准跳过 | §0.4-D3 |
| §23 Phase 3（Skill Expansion） | 部分立项-归**阶段十二-09** | 剩余 2 个 Product Skill 已随清单 D-04 于 2026-10-02 决定不立项，阶段十二-`09` 范围据此收缩并闭环归档；本阶段不重复承接，也不得建第二个 Skill 台账。 |
| §23 Phase 4（Context Retrieval） | 未立项-经批准跳过 | §0.4-D2；服务端裁剪部分已在 §9 行判定为已实现。 |
| §23 Phase 5（Result Reflection） | 不变量已实现；动作未立项 | 真值守护见 §10；`explain_result` 动作随 §0.4-D2 跳过。 |
| §23 Phase 6（Recovery Reviewer） | 未立项-经批准跳过 | 确定性候选 + 独立审批链已在 §11；模型审查动作随 §0.4-D2 跳过。 |
| §23 Phase 7（Event Runtime） | 未立项-判定为**不需要** | §13 行：等价实现已是超集，再建即双权威。 |
| §23 Phase 8（Multi-Agent Reviewer Team） | **已被取代且阻塞** | 阶段十四 `00` §11.4 取代其角色口径；阶段十四 2026-10-02 在 G0 前延期（`00` §11），故 Phase 8 无可启动前提。 |
| §24.1 | 保留为暂缓，但理由需更正 | “Windows sandbox 网络隔离未完成”**不是**拒绝动态 Tool 的主要理由——真正的约束是 `AGENTS.md` §1.3 与 §3.2（新可执行 node 必须同步 Tool Catalog/Approval Gate/审计/allowlist/前端/测试）。§17 完成后该项也不得被解读为获得放行。 |
| §24.2～§24.5 | **永久禁止**（非待办） | 已按 `AGENTS.md` §4.4 就地改写为永久禁止条目，见 §24。 |
| §25 优先级表 | 已过期，就地更正 | 见 §25 标头：P0 的三项（Observation Tools / AgentPolicyService / Action Space 扩展）已被 §0.4 判定为跳过；表内 “Windows Network Isolation 风险高 / P2” 低估了它——它是本阶段唯一获批、且不与 §1.3 冲突的项。 |
| §26 最终建议 | 已过期，就地更正 | “下一阶段只做四件事”的第一、二、三件均不在批准范围内；第四件的 deterministic truth 部分**已实现**。 |
| §27 最终目标形态 | 保留为愿景 | 不构成范围；其 “Retrieve Evidence” 一环已被 §0.4-D2 明确排除在当前架构之外。 |
| §28 参考实现位置 | 路径有效 | 11 个模块全部存在（含 `services/agent_planning_action_service.py`、`agent_orchestrator.py`、`recovery_proposal_engine.py`、`multi_agent_evaluation_service.py`、`agent_skills/registry.py`）。注意 `multi_agent_gate_runner.py` 实际位于 `services/` 而非 `runtime/`。 |
| §29 参考思想来源 | 保留 | 纯来源清单，无现状主张。 |

## 0.4 本轮经维护者批准的四项决策

| ID | 决策 | 影响 | 重启条件 |
|---|---|---|---|
| **D1** | 本阶段唯一获批实施包是 **§17 Windows Sandbox 网络隔离硬化**。§4/§5/§6/§8/§9.2/§13/§14/§19/§20/§21 全部延期。 | §17 属基础设施硬化，不新增科学/执行能力，不改变任何能力等级。 | 见 §0.5。 |
| **D2** | **不接受**“给模型只读拉取能力”（§5 读工具 + §9.2 On-demand Evidence + §20 `InspectAction`）。证据继续**只能由服务端在调用模型前组装**。 | 维持 `AGENTS.md` §1.3 的“LLM 只能规划/解释/校验/提出动作”边界；保持 `tests/unit/test_agent_harness_service.py` 的 `test_two_typed_actions_parse_and_removed_actions_or_extra_fields_are_rejected` 的拒绝断言有效；与阶段十二 G-08（`Agent简洁优化实施方案.md` §5.4，`阶段十二/Agent改造/03` 标头）的反向收敛决定一致。 | 同时满足三条才可重开：① 有可度量证据表明服务端预组装在真实任务上**遗漏了阻断性科学事实**（须来自可导出的人工标注 Trace 语料，不得来自合成 fixture）；② 单独立项并同步 Tool Catalog、Approval Gate、审计、安全 allowlist、API/前端能力展示与测试；③ 维护者显式承担 `AGENTS.md` §3.2 受保护模块改动风险。 |
| **D3** | **跳过** §6 `AgentPolicyService`，不新增策略服务或 `AgentPolicyDecision`/`policy_hash` schema。 | 策略权威保持为 `assert_capability_allowed()` + Approval Gate + Execution Ticket + 唯一 Execution Gateway 这一条链，避免第二个策略判决点。 | 动作面因 D2 重启而真实扩大、且 `assert_capability_allowed()` 出现**跨 handler 的重复判断**时；届时应先扩展现有函数而不是新建服务。 |
| **D4** | 文档处置：**保持单文件**，加本节裁定表并就地更正矛盾描述；不拆子方案目录、不归档。 | 避免为一份只有一个获批实施项的提案引入 3 个索引与全部交叉引用的移动风险。 | 若 §0.4-D1 实施完成且 §17 闭环，本文件按 `specs/索引.md` 生命周期规则整体移入 `已完成实施方案/`。 |

## 0.5 §17 获批范围与实施约束

- **不扩展能力或执行入口。** `node_contract_registry.py` 仍仅将不可执行的 MATLAB/SPM/DPABI 契约标为 `sandbox_process`；生产 `SandboxPolicySet` 仍为空。实际验收仅到 provider 级，不能称为科学执行、packaged smoke 或 release 验证。
- **唯一获批机制。** 用户于本轮明确批准无 capability AppContainer + 现有 write-restricted token + Job Object。Windows 为每次尝试创建独立 profile，结束后销毁；子进程不获 profile 写权限。`LOCALAPPDATA/TEMP/TMP` 显式绑定隔离临时根，不继承凭据或用户环境。初始化、token 检查和 Job 绑定失败必须阻止恢复，不得退化启动。
- **实施顺序。** 先以 characterization 锁定现有行为；再实现隔离机制并实测网络与旧边界；最后同步全部消费者并回归完整审批与状态链，每个阶段通过后才开始下一阶段。验收记录见 §0.6。
- **当前消费者。** `schemas/sandbox.py`、`SandboxPolicyService`、`ExecutionEnvironmentService`、`SandboxWorkspaceService`、审批摘要与 Ticket schema/service、`api/sandbox_routes.py`、sidecar 固定自检、前端 sandbox 类型/API/面板/i18n、packaged smoke 合同和 CI 均须同步。旧 `not_enforced`、旧 policy version 与旧 attempt schema 必须被拒绝，不增加兼容读取。
- **真实证据。** 准备记录 `unverified`；恢复子进程前实际查询 `TokenIsAppContainer` 与 `TokenCapabilities`，只有 AppContainer 且 capability 数为零才记录 `enforced`。固定 curl 网络案例另以 IPv4/IPv6 回环及本机非回环的沙箱外前后成功连接作对照，要求沙箱连接没有到达服务器。WFP 可拒绝或丢弃连接，连接失败/连接超时必须与进程超时区分，不能用不可达对照冒充隔离成功。
- **指纹与审批。** `sandbox_runtime_fingerprint()` 对当前源码或冻结 loader 的实际 bytecode 求哈希；不可获得实现内容时阻断。实际内容变化使环境 hash 变化，必须在 Gateway 记录 dispatch/消费 Ticket 之前拒绝，重新规划审批。
- **SDK 证据。** 常量和 ctypes 布局核对 [微软 WinBase.h](https://github.com/microsoft/win32metadata/blob/main/generation/WinSDK/RecompiledIdlHeaders/um/WinBase.h)（`ProcThreadAttributeSecurityCapabilities=9`、输入位 `0x20000`、`STARTUPINFOEX`）及 [微软 winnt.h](https://github.com/microsoft/win32metadata/blob/main/generation/WinSDK/RecompiledIdlHeaders/um/winnt.h)（`SECURITY_CAPABILITIES`、Token information class、`WRITE_RESTRICTED`）；启动流程参考 [AppContainer 官方文档](https://learn.microsoft.com/en-us/windows/win32/secauthz/implementing-an-appcontainer)。指针宽度/字段偏移已有正式回归；资料核对不替代清单 D-03 的人工确认。
- **历史机制探测。** 先前对 Job 网络速率候选 class 13/14/15 与尺寸 37/38/40 的九次失败，只是本机探测结果，既不证明 SDK 正确布局，也不外推到其他 Windows。该路径未被采用，不留 fallback。

## 0.6 审核后剩余责任（2026-10-03）

已通过的§17实现、消费者切换和自动化验证不再保留为活跃实施任务或重复验收流水。当前技术复核、精确命令、普通/受限桌面token对照与证据见[审核执行报告](../审核计划/审核执行报告.md)阶段1和D-03；原实现内容保留在源码、测试及专项文档。

唯一本阶段人工剩余为[待人工清单](../../待人工审核校验清单.md)D-03：SDK布局/资料和“沙箱内无到达、沙箱外前后可达”对照判读，继续“保留待人工复核”。suspended → Job绑定 → 实际无capability AppContainer token核验 → resume，任一前置失败阻断；不为纠正文档而重排生产路径。

packaged/GUI/独立科学证据仍分别由P/S/A条目承接，不能从source通过推导。没有生产科学消费者，外部契约不可执行，动作扩展/读工具/独立policy/event权威仍按§0.4跳过，multi仍归阶段十四Deferred。D-03未人工闭环前不整体归档本文件。

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

> **裁定更正（2026-10-02）**：第一句成立。第二句是**本提案的前提主张，不是待解决问题**——§0.4-D2/D3
> 已判定“受控 LLM Planner”是当前**有意选择**的形态。另需更正本节 §1 第 21 行：Multi-Agent 不只是
> “尚未直接进入生产、先经 evaluation gate”，而是已于 2026-10-02 **在 G0 之前正式延期**
> （阶段十四 `00` §11），恢复它不属于本阶段队列（见 `PROJECT_STATE.md` Next Work 末尾）。

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

> **裁定（2026-10-02）**：§2.2.1～§2.2.4 的**事实陈述与当前源码一致**（动作面 2 个 kind、观察发生在
> Context Builder、内置 Skill 只有 1 个、模型主要参与 planning），复核逐个确认。但它们是否构成“瓶颈”
> 取决于是否接受 `AGENTS.md` §1.3 的边界扩展；维护者于 §0.4-D2 **不接受**，因此这四项按现状记为
> **有意的设计取舍**，不是缺陷清单。§2.2.5 是对未来的预测，另见其就地更正。

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

> **裁定更正（2026-10-02）**：本节是**对未来的预测**，不是已观察到的瓶颈，不得当作现状问题清单的一部分。
> 实际权限判决目前是**单点集中**的：`runtime/agent_capability_catalog.py:39-53` `assert_capability_allowed()`
> 与 `:54-69` `assert_capability_context_and_output_allowed()`，其下即 Approval Gate / Execution Ticket /
> 唯一 Gateway。§0.4-D3 据此判定 §6 为重复抽象并跳过。


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

> **裁定（2026-10-02）**：下图是**未被批准的目标形态**，其中三处与当前权威架构不符，阅读时必须替换：
> ① `Read Evidence → Read-only Tool Layer` 这条模型自主拉取回路不存在且不拟新增（§0.4-D2），实际是
> 服务端在调用模型前一次性组装 `EvidenceSnapshot`；② `AgentPolicyService(ALLOW/ASK/DENY)` 不是策略层，
> 策略判决由 `runtime/agent_capability_catalog.py` 的 fail-closed 断言 + Approval Gate 链承担（§0.4-D3）；
> ③ `Deterministic Skill Router` 不存在，按用途的确定性投影在
> `services/agent_harness_context_service.py:purpose_for()`（§0.2 §8 行）。
> 图下第 1、3、4、5、6 条原则**与源码一致且已生效**；第 2 条以“服务端组装”而非“模型经投影拉取”的方式
> 成立；第 7 条随阶段十四 延期。

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

> **裁定（2026-10-02）：未实现-建议跳过。** 见 §0.2 §4 行与 §0.4-D2。A2/A3 分级在动作面被
> `tests/unit/test_agent_harness_service.py 的
`test_two_typed_actions_parse_and_removed_actions_or_extra_fields_are_rejected`` 固化拒绝的前提下没有可落地的中间态。
> 本节保留为分级设计参考，不是待办。


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

> **裁定更正（2026-10-02）：未实现-建议跳过；本行“优先级最高”的评价已失效。** 维护者在 §0.4-D2
> 明确**不接受**给模型只读拉取能力。本节列出的信息面并非缺失，而是**已由服务端在调用模型前组装**：
> `services/agent_evidence_service.py:42/168/221/228`。剩余差异只有“由谁发起拉取”，属
> `AGENTS.md` §1.3 边界扩展。§5.4 列出的禁止项（无 raw 字节、无路径、无 SQL/shell/subprocess/网络、
> 不改 lifecycle、不创建 ticket）与当前服务端组装路径**同样适用且已生效**，不得被读为“新增工具后才有”。

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

> **裁定（2026-10-02）：未实现-建议跳过（重复抽象）。** 见 §0.4-D3。§6.5 自己写明“不替代 Approval Gate /
> Execution Ticket / Execution Gateway”，而 §6.4 流程中 Schema Validation → Capability Catalog 一步
> （`runtime/agent_capability_catalog.py` 两个 fail-closed 断言）**已经是**策略判决点；再加一层会把
> 一个判决点变成两个。重启条件见 §0.4-D3。

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

> **裁定（2026-10-02）：部分实现。** §7.1 的现状描述**准确**——`agent_skills/registry.py:17-19` 的
> `BUILTIN_SKILL_IDS` 恰好只有 `planning_evidence_review.v1`。但 Skill **底座已实现**：
> `registry.py:47-95` 严格注册表（manifest/hash/capability/Context-section 校验、`validate_all`）与
> `agent_skills/schemas.py:16-30` `SkillManifest`（已有 `allowed_actions`/`allowed_states`/
> `required_context_sections`/`output_schema_ref`/`content_hash`）。剩余工作是**新增 Skill 内容**，
> 剩余工作归属阶段十二-`09`，该文档已于 2026-10-02 按清单 D-04 的「不立项、正式收缩为 1 个 Skill」
> 决定闭环归档；本阶段不重复承接、不另建 Skill 台账。§7.2 中含 `inspect_*` 允许动作的
> 示例随 §0.4-D2 失效；§7.4 的 `allowed_tool_refs`/`data_access_classes` 同上。

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

> **裁定（2026-10-02）：未实现-建议跳过。** 按用途的**确定性选择已经存在**，位置在 Context 层而不是
> 新建 Router：`services/agent_harness_context_service.py:39-49` `required_by_purpose` /
> `optional_order_by_purpose` 与 `:127` `purpose_for()`。在 Skill 只有 1 个、动作只有 2 个的前提下
> Router 没有可路由的分叉，属 `AGENTS.md` §3.0 禁止的预建间接层。§8.2 的 Skill/Capability/Policy
> 三分概念本身仍然成立，只是不需要三个服务。

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

> **裁定（2026-10-02）：部分实现，且“全量快照”这一现状描述不准确。** Context v3 **已经不是全量**：
> `schemas/agent_harness.py:298-305` 有 `required_sections`/`included_sections`/`omitted_sections`/
> `complete`/`incomplete_reason`，`services/agent_harness_context_service.py:204-222` 按 purpose 优先级
> 裁剪并在超字节预算时记 `{name}:byte_budget` 省略原因。§9.4 `AgentEvidenceSlice` 的溯源要求也**已等价
> 存在**：`agent_harness.py:264-267` 每个 section 携带 `source_refs`/`source_hash`，加上逐节
> `section_hashes` 与 `evidence_snapshot_hash`；只缺 `data_class` 一个字段（其挂载依赖被跳过的 §5）。
> 因此 §9.2 的“不再默认塞入”已由服务端完成，本节的真实剩余量只有 §9.3 的**模型拉取回路**，随 §0.4-D2 跳过。

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

> **裁定（2026-10-02）：部分实现——本节的核心不变量已经是当前行为。** §10.2“模型只写可读文本、系统保留
> truth fields”已落实为结构化契约：`schemas/agent_task.py:252-267` `AgentResultExplanation` 的真值字段
> （`outcome`/subject 计数/`artifact_refs`/`criteria`/`limitation_codes`/`recommended_action_code`）
> 与 `generated_text` + `generated_text_status: not_requested｜accepted｜conflict_rejected` 分离，
> `services/agent_task_result_summary.py:109-174` `_guard_generated_text()` 在文本与真值冲突时丢弃文本并
> 记 `conflict_rejected`。因此 §2.1.3 的判定成立，§10 的剩余量只有 `explain_result`/`propose_next_step`
> 两个**动作 kind**，它们正是 §0.4-D2 拒绝、且被 `tests/unit/test_agent_harness_service.py 的
`test_two_typed_actions_parse_and_removed_actions_or_extra_fields_are_rejected``
> 固化为拒绝断言的项。§10 顶部的 Observe→…→Reflect 闭环图不得读作当前调用链。

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

> **裁定（2026-10-02）：部分实现。** §11.1 与 §11.3 **已经是当前行为**，不是待办：确定性候选由
> `services/recovery_proposal_engine.py` 生成，候选到执行之间必须经
> `schemas/agent_lifecycle.py:31-35` 的 `RECOVERY_PROPOSED → WAITING_FOR_RECOVERY_APPROVAL → RECOVERY_READY`
> 迁移与 `services/agent_recovery_command_service.py:39` 的状态守卫，前端为
> `features/agent/components/RecoveryApprovalReview.tsx`；这与 `AGENTS.md` §5.2 的顺序一致。
> 本节剩余量只有 §11.2 的“由模型解释候选”，需要一个 `propose_recovery` 动作，随 §0.4-D2 跳过。

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
>
> **2026-10-02 补充裁定**：本注原先指向的“本文末尾的归属说明”**并不存在**（正文以 §28/§29 参考清单
> 结束），该锚点已改为指向 §0.3 归属表与本节 §0.2 §12 行。同时更正：本节**没有**“仍然有效的独立范围”——
> 动作空间与感知层扩展（§23 Phase 1）已经维护者于 §0.4-D2 判定为**经批准跳过**，不得据本节另启实施。
> 另需更正：§12 把 Reviewer Team 描述为“新增”，但离线 G0 脚手架已存在于
> `services/agent_review_role_registry.py`、`agent_review_context_projector.py`、
> `agent_review_finding_aggregator.py`、`structured_agent_model_adapter.py`、
> `multi_agent_gate_runner.py`；其 fixture 目录 `tests/fixtures/agent_eval/multi_agent/` 为空，
> 因此脚手架不产生任何能力等级证据。

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

> **裁定（2026-10-02）：未实现-建议跳过（重复抽象）。** §13.3 `AgentWakeEvent` 是**既有 schema 的子集**：
> `schemas/agent_task_wake.py` 的 `AgentTaskWakeRecord` 除提案列出的字段外还含 `step_key`、`attempts`、
> `lease_owner`、`lease_expires_at`、`last_error_code`，并显式声明“只含控制面引用，不含 prompt、用户数据、
> 执行票据或可执行工作”。消费侧 `services/agent_task_scheduler.py:52/73/93` 已实现 `enqueue` / lease
> `claim_next` / `run_once`、指数退避与 `MAX_WAKE_ATTEMPTS` 耗尽 → `HUMAN_HANDOFF`（`:114-129`）。
> 再建一套事件模型会形成**第二个唤醒权威**，违反 `AGENTS.md` §3.0。§13.1 的现状描述与 §13.4 的
> bounded wake 要求**均与源码一致**，属已实现而非待办。

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

> **裁定（2026-10-02）：未实现-建议跳过，且与阶段十五已批准的决策相反。** 阶段十五
> `00_自动续跑与确认交互优化方案.md` §D1/Phase B **明确批准**把确认协调器保持为“仅浏览器内的
> action-key/dismissal hook”，理由正是不落库、不改审批哈希；当前实现即
> `src/frontend/src/features/agent/attentionAction.ts:3-6`（`decision｜approval｜recovery`），其
> 第 18-19 行注释记录了该有意约束。把它改成后端持久 schema 需要**推翻阶段十五 的批准决定**，而不是
> 补完它。此外 §14.1 的 `EXPORT_APPROVAL` 与 `SECURITY_WARNING` **没有生产者**（导出动作不存在；
> `EXECUTION_ENVIRONMENT_CHANGED` 只是错误码，见
> `services/execution_environment_service.py:102,159`）。§14.3“减少 Human Operations 而不是扩大
> Agent Autonomy”的目标**已由阶段十五 的自动续跑实现**，不需要 §14.2 的新 schema。

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

> **裁定（2026-10-02）：§15.1 实质已实现；§15 的类型清单不得作为第二套分类引入。** §15.1 的九个字段
> 全部有对应实现，仅命名不同：`memory_type`→`schemas/memory.py:77` `kind`、`scope`→`:76`
> `scope_type: Literal["project"]`、`source_ref`→`:82` `source: MemorySource`、`confidence`→`:88`、
> `sensitivity`→`:89`、`requires_confirmation`→`:91` `requires_review` 与 `MemoryRevision:113`
> `confirmation_status`/`:118` `confirmation_event_id`，`MemoryItem` 另有 `:137-138` `valid_from`/`valid_until`。
> 现存类型轴是**正交三维**而非单枚举：`memory.py:10-16` `kind`（6 类）、`:18` `impact_class`
> （`presentation｜workflow｜scientific｜safety`）、`:20-24` `trust_class`。§15 的
> `SCIENTIFIC_PREFERENCE`/`SCIENTIFIC_ASSUMPTION`/`PAST_EXECUTION_EXPERIENCE`/`SYSTEM_FACT` 与
> `kind` + `impact_class` 组合重叠，按 `AGENTS.md` §3.6（独立 memory SQLite 是唯一长期记忆权威）
> **不得并列维护**。§15.2 的规则已经是当前行为：`memory.py:183-184`
> `advisory_only: Literal[True]` + `confirmation_policy: Literal["confirm_each_agent_task"]`。

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

> **裁定更正（2026-10-02）：部分实现，且“尚未准备”这一措辞不准确。** §16.2 的语境政策**已经有等价实现**，
> 只是不叫 taint 标签：
> - 记忆侧按敏感度阻断外发：`schemas/memory.py:19` `MemorySensitivity = public｜project_internal｜restricted｜rejected`，
>   `services/memory_repository.py:575,606,641` 在检索/投影 SQL 中排除 `restricted`/`rejected`，并在
>   `:555` 附近做读时二次校验（注释明记“离开仓库前必须先查 sensitivity”）。
> - 模型侧按**字段白名单**而非标签控制：`services/agent_harness_context_service.py:105-112` 逐节枚举允许
>   字段，`schemas/agent_harness.py:259-267` 的 Context section 自述为“immutable, provenance-bound,
>   **redacted**”，`schemas/agent_evidence.py` 全文自述“Immutable, **redacted** project evidence”。
>
> 真正未实现的只有 §16.3：给 Tool Observation 打 `data_class`。它的唯一挂载点是尚不存在的读工具层，
> 因此**随 §0.4-D2 一并跳过**，不得为其单独引入一个无人读取的分类字段。

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

# 17. 优化十四：Windows Sandbox 网络隔离（技术范围已通过）

已通过的实施任务退出活跃正文；当前机制与安全不变量保留于§0.5及专项安全文档，技术复核/证据见§0.6。仅D-03人工判读仍未闭环；新packaged/GUI/科学验收按原清单独立取证，不启用生产外部科学节点。

---

# 18. 优化十五：Trace 升级为用户可读的 Agent Explainability

> **裁定（2026-10-02）：部分实现。** “这些足够做工程审计”一句成立且**已经对外**：
> `services/agent_trace_service.py:20` `calculate_trace_integrity_hash()`、`:37/74` `get()`/`page()`，
> 端点 `api/agent_task_routes.py:176-184` `GET /{task_id}/trace`（docstring 明示 “paginated, redacted …
> advanced review only”）。§18.2 的高级字段**前端已在展示**，无需新增：
> `features/agent/components/TechnicalEvidence.tsx:12-38` 已逐行输出 `plan_hash`、
> `evidence_snapshot_hash`、`goal_hash`、`planning_inputs_hash`、`parent_plan_hash`、`ticket_id`、
> `run_id`、`observation_id`、`evaluation_id`、`memory_context_hash` 及记忆门控状态。
> 剩余量只有 §18.1 的**叙事式分层展示与默认折叠**，这与阶段十二 `Agent简洁优化实施方案.md` §5.2
> “单一 `TaskCard` 信息层级”是**同一条改造线**；该线已于 2026-10-02 落到源码并随阶段十二归档，
> 必须并入该条推进，不得另立第二条前端信息层级改造线。
> 本节未获本轮立项（§0.4-D1 只批 §17）。

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

> **裁定（2026-10-02）：未实现，且不得作为“目录骨架”预先落地。** 本节列出的新模块**全仓 0 命中**：
> `schemas/agent_action.py`、`agent_tool.py`、`agent_policy.py`、`agent_attention.py`、`agent_event.py`；
> `runtime/agent_read_tool_registry.py`、`agent_skill_router.py`；
> `services/agent_policy_service.py`、`agent_read_tool_service.py`、`agent_skill_router_service.py`、
> `agent_attention_service.py`、`agent_event_service.py`、`agent_result_reflection_service.py`、
> `agent_recovery_review_service.py`。其中绝大多数是 §5/§6/§8/§13/§14 的别名，随其判定一并跳过
> （§0.4-D2/D3）。`agent_skills/` 下 8 个新 Skill 目录同属阶段十二-`09` 范围，不在本阶段。

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

> **裁定（2026-10-02）：未实现-建议跳过（§0.4-D2）。** 当前 Union 只有 `RequestDecisionAction` 与
> `DraftPlanAction` 两个成员（`schemas/agent_harness.py:25`），且
> `tests/unit/test_agent_harness_service.py 的
`test_two_typed_actions_parse_and_removed_actions_or_extra_fields_are_rejected`` 显式断言 `read_evidence`/`explain_result`/
> `propose_recovery`/`finish` 解析即抛错。本节末段“Execute/Shell/FileWrite/Network/IssueTicket 永不进入
> Union”**已经是事实**，不需要新增约束来保证。

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

> **裁定（2026-10-02）：未实现；上表的线性链不得当作当前状态机阅读。** `schemas/agent_lifecycle.py:14-40`
> 实际是 25 个状态，**不含** `INSPECTING`/`REFLECTING`，且本节链图省略了仍在使用中的
> `WAITING_FOR_SCIENCE_DECISION`、`DIAGNOSING`、`RETRY_PROPOSED`、`WAITING_FOR_RETRY_APPROVAL`、`RETRYING`、
> `RECOVERY_READY`、`RECOVERING`、`SUCCEEDED`、`FAILED`、`CANCELED`。迁移表由
> `services/agent_orchestrator.py` 持有，前端投影由 `services/agent_task_read_model.py:_current_action_code`
> 承担。本节末段的保守方案（lifecycle 不变、用 Harness step kind 表示 inspection/reflection）**方向正确
> 但同样未实现**：`schemas/agent_harness.py:240` 的 `AgentHarnessStep.kind` 目前复用动作枚举，没有独立
> kind 空间。因 §0.4-D2 已拒绝拉取与反思动作，本节整体延期。

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

> **裁定（2026-10-02）**：§22.1～§22.3、§22.5 的验证对象（读工具、策略服务、Skill Router、模型恢复动作）
> **当前不存在**，随 §0.4-D2/D3 跳过；不得先落测试脚手架再补实现。§22.4 的不变量**已由源码与回归覆盖**
> （`AgentResultExplanation.generated_text_status` + `_guard_generated_text()`）。§22.6 归阶段十四 `01`
> 的 G0（该阶段已延期），其新增指标中的 “mean read-tool calls” 依赖被跳过的 §5。
> §22.1 列出的“不能读 rawdata / 不泄露绝对路径 / 不跨 project / 不触发 pipeline / 不调用 subprocess”
> **已是现有约束**，由 `AGENTS.md` §3.5、`runtime/path_safety.py` 与
> `runtime/sandbox_process_runner.py:reject_unreviewed_process_start()` 承担，不是读工具引入后的新要求。

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

> **裁定（2026-10-02）：Phase 1～8 无一在本阶段获批立项。** 逐阶段归属见 §0.3 表：Phase 1/2/4/6/7 为
> 经批准跳过或判定不需要（§0.4-D2/D3）；Phase 3、Phase 5 的主体分别归属阶段十二-`09` 与已实现的
> `AgentResultExplanation`；Phase 8 被阶段十四 取代并随其在 G0 前延期。本阶段实际的实施计划只有
> §0.4-D1 一项（§17），其范围前提与验收见 §0.5。下面的 Phase 小节保留为原始排序推理。

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

> **裁定（2026-10-02）**：§24.2～§24.5 由“禁止”升级为**永久禁止**，并按 `AGENTS.md` §4.4 明确它们
> **不是未来计划、不是待办、不随任何 Phase 或 §17 完成而自动放行**。§24.1 保持为暂缓项。

## 24.1 暂缓（非永久禁止）：Agent 自己创建任意 Tool / Connector

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

> **裁定更正（2026-10-02）**：上列理由中的“Windows sandbox 网络隔离未完成”**不是**拒绝动态 Tool 的主因，
> 且 §17 完成后也**不得**被解读为该项获得放行。真正的约束是 `AGENTS.md` §1.3（本项目不是通用外部命令
> 执行器）与 §3.2（新可执行 node 必须同步 Tool Catalog、Approval Gate、审计、安全 allowlist、
> API/前端能力展示与测试）。医疗数据风险、任意代码与外部网络三条仍然成立。

---

## 24.2 【永久禁止】LLM 直接访问 Pipeline Runtime

不要暴露：

```text
pipeline.execute()
runner.run()
ticket.issue()
gateway.dispatch()
```

给模型。

---

## 24.3 【永久禁止】模型直接修改 Rawdata

保持：

```text
rawdata = read-only
```

---

## 24.4 【永久禁止】模型直接写任意文件

所有 artifact 必须由：

```text
registered pipeline / approved exporter
```

产生。

---

## 24.5 【永久禁止】模型自由 Shell

如果未来必须加入 CLI：

```text
必须通过 sandboxed registered command contract
```

而不是：

```text
shell(cmd)
```

> **裁定（2026-10-02）**：§24.2～§24.5 为**永久禁止**，与 `AGENTS.md` §1.3、§3.5、§24 一致。它们不得被
> 任何后续 Phase、§17 的完成、或动作空间扩展的重启（§0.4-D2）解读为放行前提；“永久”不依赖
> “未来必须加入 CLI”这一假设是否成立。

---

# 25. 建议优先级

> **裁定更正（2026-10-02）：下表已过期，不得据以排序。** 三项被标为 P0 的条目已经 §0.4 判定跳过——
> Read-only Observation Tools（D2）、AgentPolicyService（D3）、Action Space 扩展（D2）；“多 Scientific
> Skills + Skill Router” 分别归阶段十二-`09` 与判定不需要（§0.2 §8 行）。反向地，**Windows Network
> Isolation 被低估为 P2**：它是本阶段唯一获批（§0.4-D1）、且唯一不与 `AGENTS.md` §1.3 冲突的项。
> P1 的 Result Reflection / Recovery Reviewer 的**不变量部分已实现**（§0.2 §10/§11 行）。
> 本阶段当前唯一有效的优先级是 §0.5。

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

> **裁定更正（2026-10-02）：本节的“下一阶段只做四件事”已失效，顺序不成立。** 第一件（Read-only
> Observation Tools）经 §0.4-D2 拒绝；第二件（AgentPolicyService）经 §0.4-D3 跳过；第三件（Scientific
> Skills + Skill Router）的 Skill 部分归阶段十二-`09`、Router 部分判定不需要（§0.2 §8 行）；第四件的
> deterministic truth 部分**已经是当前行为**（§0.2 §10 行），只有模型 Reflect 动作未获批。
> 本阶段真实的“下一件事”只有一项：**§17 Windows Sandbox 网络隔离硬化**，范围与验收见 §0.5。

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

> **裁定（2026-10-02）：保留为愿景，不构成范围。** 下图中的 `Retrieve Evidence` 一环已被 §0.4-D2
> 明确排除在当前架构之外（证据只能由服务端在调用模型前组装）；`Agent Policy Plane` 一环由
> `runtime/agent_capability_catalog.py` 的 fail-closed 断言 + Approval Gate 链承担，不需要独立平面
> （§0.4-D3）。本节末段四条原则（Model does not execute science / System authorizes execution /
> Deterministic services determine truth / Human retains consequential decisions）**与源码一致并已生效**，
> 是本提案中应当长期保留的部分。

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

