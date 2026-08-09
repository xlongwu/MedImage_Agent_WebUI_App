# 阶段十二：Agent 改造总体方案

> 状态：Draft，待人工 Review。
> 任务模式：Documentation + Architecture / Refactor 方案设计。
> 本文职责：确定改造目标、边界、子方案、实施顺序和总体验收标准；不授权直接修改生产代码，也不替代后续子方案。

## 1. 目标

当前项目已经具备 Agent Task、受控单 Agent Harness、Reviewed Plan、Approval Gate、Execution Ticket、Execution Gateway、Observation、Goal Evaluation、Recovery Proposal 和 Memory Domain，但用户创建任务或补充答案后，Harness 通常只推进一个步骤，执行后的观察、判断、解释和恢复也没有统一接回 Harness。因此，系统仍需要用户多次手动推动，自动完成任务的能力不足。

本阶段完成后，目标流程应为：

1. 用户提交一个研究目标；
2. 系统自动读取项目内已登记的结构化证据；
3. 仅在缺少必要输入或存在科学选择时，集中请求一次人工决定；
4. 系统自动生成并校验 Reviewed Plan；
5. 用户只对稳定的 Approval Summary 进行执行审批；
6. 审批后系统自动执行、监控、收集 Observation、判断目标是否满足并生成结果说明；
7. 执行失败或目标未满足时，系统自动诊断并生成恢复方案；涉及重新执行、扩大写入范围或改变科学参数时再次审批。

本阶段不包括：

- 绕过人工科学决定、Approval Gate、Execution Ticket 或 Execution Gateway；
- 从模型文本执行 shell、任意 Python、文件写入或外部程序；
- 修改、移动或删除 `rawdata/`、源 BIDS、源 DICOM 或已登记源 NIfTI；
- 把 Agent 结论作为临床诊断、治疗建议或科学验证结论；
- 在单 Agent 稳定前上线多 Agent 自动协作；
- 为尚未明确的外部调用需求提前建设完整 SDK 或插件市场。

## 2. 核心选择

### 2.1 复用现有生命周期和执行链

**选择：** 继续以 `AgentLifecycleRecord` 作为用户任务状态的唯一权威来源，Harness attempt、step、context 只保存运行和审计信息。

**原因：** 当前审批、执行、Observation、Goal Evaluation 和 Recovery 已围绕该生命周期工作。新建第二套任务状态会产生状态同步和重复恢复问题。

**不采用：** 不新增独立的“全能 Agent 状态机”，不让 Harness 直接修改 run、artifact 或执行状态。

### 2.2 把 Harness 改为可恢复的有限循环

**选择：** 保留 `AgentHarnessService.run_one()` 的“单次只处理一步”语义，在其上增加 `run_until_blocked()`，连续推进安全步骤，直到需要用户输入、需要审批、达到预算、任务终态或发生结构化错误。

**原因：** `run_one()` 已具备 lease、幂等键和单步审计。复用它可以减少重写，并保持每一步可恢复、可回放。

**不采用：** 不使用无上限的 `while` 循环，也不让 HTTP 请求一直等待科学计算结束。

### 2.3 人工操作按风险保留，不追求“完全无人化”

| 等级 | 可自动完成的动作 | 处理规则 |
|---|---|---|
| A0 | 读取项目、计划、run、artifact、Observation 和允许的 Memory 摘要 | 自动执行，只读并记录来源 |
| A1 | 创建 Harness 记录、上下文、解释、评估和恢复建议 | 自动执行，只写受管状态和审计记录 |
| A2 | 在已批准 Execution Ticket 的精确范围内执行和监控 | 一次审批后自动推进，不重复请求确认 |
| A3 | 改变科学参数、backend、overwrite、写入范围或重新执行 | 必须生成新摘要并重新审批 |
| A4 | 修改源数据、任意命令、绕过 Gateway、临床结论 | 永久禁止 |

### 2.4 先完成单 Agent，再评估多 Agent

**选择：** P0 和 P1 只改造单 Agent；多个只读 advisor 和 SDK 放在 P2 评估。

**原因：** 当前主要问题是控制循环和动作处理不完整。此时增加 Agent 数量只会放大状态、预算和审计复杂度。

## 3. 当前实现分析

### 3.1 当前主调用链

```text
Agent Task create/answer
-> AgentTaskCommandService._harness_or_plan()
-> AgentHarnessService.ensure_attempt()
-> AgentHarnessService.run_one()
-> HarnessContextBuilder.build()
-> AgentModelAdapter.propose_action()
-> ActionEnvelope 校验
-> AgentHarnessService._apply()
-> AgentLifecycleRecord 状态变化

Agent Task approve
-> 已有 Approval Summary 校验
-> post-approval dry-run
-> Execution Ticket
-> Execution Gateway
-> Pipeline Runtime / registered runner

执行完成
-> AgentTaskReconciler / AgentOrchestrator
-> ObservationRecord
-> Goal Evaluation
-> Recovery Proposal 或终态结果
```

当前执行链是受控且确定的，主要缺口位于“如何持续触发下一步”和“模型动作如何调用现有只读/规划/恢复服务”，不需要另建执行框架。

### 3.2 可以直接复用的实现

| 现有实现 | 位置 | 本阶段用途 |
|---|---|---|
| Agent 生命周期 | `src/backend/app/schemas/agent_lifecycle.py`、`src/backend/app/services/agent_orchestrator.py` | 保持任务状态、决定、Observation、评估和恢复的权威性 |
| 单步 Harness | `src/backend/app/services/agent_harness_service.py` | 继续负责 claim、单步幂等、动作校验和单步审计 |
| Harness 持久化 | `src/backend/app/services/mock_store.py` | 保存 attempt、context 和 step；按新增字段扩展，不另建数据库 |
| 项目存储接口 | `src/backend/app/api/dependencies.py:ProjectStore` | 隔离项目、计划、run、artifact 和 Harness 数据读取 |
| 规划链 | `src/backend/app/services/goal_planning_service.py`、`AgentTaskCommandService._plan()` | 继续生成、校验并持久化 Reviewed Plan |
| 审批和执行链 | `approval_summary_service.py`、`reviewed_execution_service.py`、`runtime/execution_gateway.py` | 保持唯一执行入口，不交给 Harness 重写 |
| 执行后判断 | `observation_collector.py`、`goal_evaluator.py`、`recovery_policy_service.py`、`agent_orchestrator.py` | 接入自动观察、目标判断和恢复建议 |
| Memory Domain | `docs/架构与决策/记忆系统设计方案.md` 对应实现 | 只提供经门控的 `MemoryContext`，不提供权限或当前事实 |
| 前端 Agent Workspace | `src/frontend/src/features/agent/` | 展示后端权威状态、决定、审批、执行和恢复信息 |

### 3.3 当前限制

| 编号 | 当前实现 | 为什么不满足目标 | 总体修改方向 |
|---|---|---|---|
| G-01 | `AgentTaskCommandService._harness_or_plan()` 在 create/answer 时只调用一次 `run_one()` | 一个无需人工参与的多步规划也会停在中间 | 增加有限的 `run_until_blocked()`，由命令入口和后台调度共同复用 |
| G-02 | `AgentHarnessScheduler.recover_once_on_startup()` 只在启动时为每个 lifecycle 处理最多一步 | 服务持续运行期间的新事件不会自动唤醒 Harness | 增加事件触发和有界后台推进；启动扫描只负责恢复遗留任务 |
| G-03 | `_apply()` 中 `read_evidence` 只返回 `READY`，`explain_result` 直接结束，`propose_recovery` 在生产入口通常没有注入处理器 | 动作名称存在，但没有完成对应业务工作 | 为六种固定动作提供明确处理器和结构化结果 |
| G-04 | `HarnessContextBuilder` 主要包含目标、生命周期、少量项目元数据、plan/ticket ID 和 Memory 摘要 | 模型看不到最新 Observation、Goal Evaluation、上一步结果、剩余预算和可用能力 | 改为有版本的分区上下文，并继续限制大小、来源和敏感字段 |
| G-05 | `AgentHarnessStep` 只记录动作种类、输入/输出 hash 和短摘要 | 无法重建模型调用、动作结果、失败分类和状态变化依据 | 增加模型调用记录、动作结果和关键引用，形成可回放 trace |
| G-06 | `PendingDecision` 一次只表示一个问题 | atlas、TR、模板等多个必要选择可能造成多轮人工往返 | 改为一个未解决的决定批次，批次内包含 1～N 个决定项 |
| G-07 | 规划、执行后 Observation、Goal Evaluation 和 Recovery 分别存在，但 Harness 未持续消费它们 | 执行后仍需人工进入不同页面或命令推动 | 在 lifecycle 事件后自动恢复 Harness，并根据结构化结果继续解释或提出恢复 |
| G-08 | provider/adapter 的实际来源未形成完整审计记录或前端字段；当前 provider 故障会结构化停止而非回退 `_plan()` | 用户无法判断模型、版本和调用来源 | 持久化并展示实际 provider、模型与停止原因；不得把故障回退伪装为既有行为 |
| G-09 | `test_agent_harness_replay.py` 主要验证动作和状态组合 | 无法证明同一 trace 可在不调用模型、不产生副作用时重放 | 建立真实 replay runner 和固定回归用例 |
| G-10 | 阶段十一存在工程完成表述，但未定位 Harness 专属 packaged smoke 或 release 证据 | 方案、源码、focused 测试和发布状态可能被混为一谈 | 第一阶段先完成基线审计，区分 source implemented、verified、release included |

## 4. 借鉴范围与舍弃范围

参考项目 `Question_Evolution/.../docs/Agent改造方案` 的价值在于 Harness 的工程结构，不在于题目进化业务本身。

### 4.1 借鉴并改造成 MedImage 版本

| 参考做法 | 本项目中的落地方式 |
|---|---|
| 有界 Agent 循环 | 使用持久化 attempt、单步 lease、预算和明确停止条件；不使用开放式自主循环 |
| Session 和计划版本 | 以 lifecycle 为 session，增加计划修订号、父 plan hash 和修订原因 |
| Tool / Action 合同 | 保留六种固定 `ActionEnvelope.kind`，为 payload 和 result 建立严格 schema |
| Observation 和恢复判断 | 复用现有 `ObservationRecord`、Goal Evaluation 和 Recovery Proposal，不建立第二套判断逻辑 |
| Trace 和 replay | 记录模型调用、上下文 hash、动作、结果、状态迁移和证据引用；支持无副作用重放 |
| 分层上下文和缓存 | 分开稳定规则、项目证据、当前任务、最新 Observation 和最近步骤；只缓存精确 hash 命中的只读结果 |
| 动态预算 | 预算由任务风险和阶段决定，但始终受配置硬上限限制 |
| Agent Skills | Skill 只定义工作步骤、输入和输出格式，不获得额外权限，不替代 schema 或审批 |

### 4.2 不借鉴或延后

| 内容 | 处理结论 | 原因 |
|---|---|---|
| 题目候选、算子、弱模型评分和组内相对评分 | 舍弃 | 与 rs-fMRI 任务、数值产物和审批链无直接关系 |
| 把主要业务拆成大量模型可自由组合的原子工具 | 舍弃 | 科学流程必须由 Reviewed Plan、Pipeline Runtime 和注册 runner 执行；过细工具会增加绕过风险 |
| Agent 自动修改 Prompt、策略、Memory 或发布规则 | 舍弃 | 会改变生产行为，必须经过离线评估和人工批准 |
| 任意 shell、Python 或文件工具 | 永久舍弃 | 与当前执行安全边界冲突 |
| 全量 transcript、日志、影像或 Memory 注入 | 舍弃 | 上下文过大，并可能泄露源数据或秘密 |
| 多 Agent 自动分工与投票 | 延后到 P2 | 单 Agent 循环、trace 和评测未稳定前无法判断收益 |
| 完整公共 SDK | 延后到出现明确外部调用方后 | 当前前后端内部接口已能覆盖产品调用链 |
| 旧字段、旧状态和新状态并存的兼容层 | 舍弃 | 本仓库规则要求同步更新当前消费者并删除废弃路径 |

## 5. 目标流程

### 5.1 正常流程

1. `create` 创建 lifecycle 和 Harness attempt，并写入初始预算。
2. Harness 读取项目索引、artifact 注册、现有 plan/run 和允许的 Memory 摘要，生成 `EvidenceSnapshot`。
3. 若证据充分，直接进入 `draft_plan`；若缺少必要输入，生成一个 `PendingDecisionBatch` 后停止。
4. 用户一次提交该批次的所有答案。后端校验批次 ID、每个 item 和 lifecycle 当前状态。
5. Harness 自动恢复，生成 Candidate Plan，并继续使用现有 validator、Reviewed Plan 和 Approval Summary。
6. lifecycle 进入 `WAITING_FOR_APPROVAL`，Harness 停止；用户只审批稳定 summary/hash。
7. 审批后的 dry-run、ticket、dispatch 和 monitor 沿用当前实现，Harness 不参与执行调度。
8. run 到达终态后，后端生成 Observation 和 Goal Evaluation，并唤醒 Harness。
9. 目标满足时，Harness 生成结构化结果解释并结束；目标未满足时进入失败流程。

### 5.2 失败或目标未满足流程

1. Observation Collector 记录真实 run、node、artifact 和 reload 结果。
2. Goal Evaluator 给出 `satisfied`、`not_satisfied` 或 `insufficient_evidence`，不得由模型自由改写。
3. Harness 根据评估结果调用现有 Recovery Proposal 服务，记录失败类别、可恢复范围和建议动作。
4. 仅解释、补读证据或转人工时自动完成。
5. 需要重试、局部重规划、改变参数或写入范围时，生成新的 Reviewed Plan 和 Approval Summary，等待新审批。
6. 达到 attempt、模型调用、动作、墙钟时间或恢复次数上限时，进入 `HUMAN_HANDOFF`，保留完整 trace。

### 5.3 必须停止自动推进的状态

- `WAITING_FOR_INPUT`；
- `WAITING_FOR_SCIENCE_DECISION`；
- `WAITING_FOR_APPROVAL`；
- `HUMAN_HANDOFF`；
- `CANCELED`、`SUCCEEDED`、`GOAL_SATISFIED` 等终态；
- 预算耗尽、lease 冲突超过上限、schema 校验失败或安全策略拒绝；
- 需要改变审批 scope、执行 backend、overwrite 或科学参数。

## 6. 总体修改方案

### 6.1 基线审计与文档状态纠正

**当前情况**

源码包含 Harness schema、service、scheduler、前端卡片和测试；阶段十一的工程完成表述必须与源码、focused 测试和发布证据分开解读。当前无法仅凭文件存在判断其是否完成全量验证、打包或发布。

**修改方式**

1. 由 `01_当前Agent基线与差距分析.md` 逐项记录“源码存在、测试覆盖、产品入口启用、发布包含”四种状态。
2. 为 `AgentLifecycleRecord`、Harness、Observation、Recovery、Memory、审批和前端绘制当前调用清单。
3. 先补 characterization tests，固定当前禁用路径、单步语义、审批顺序和执行边界。
4. 基线确认后再更新 `PROJECT_STATE.md`、能力矩阵和阶段十一状态，不在总纲中提前改写事实。

### 6.2 持久化循环和调度器

**当前情况**

`run_one()` 能安全完成一个动作，但 create/answer 和 startup scheduler 都不会持续推进。

**修改方式**

- 在 `AgentHarnessService` 增加 `run_until_blocked()`，内部只循环调用 `run_one()`；每步后重新读取 lifecycle、attempt 和预算。
- 单次调用设定额外的 `max_steps_per_wakeup`，避免一个任务占用后台 worker。
- `AgentHarnessScheduler` 处理 create、answer、run terminal、reconcile、recovery approved 和 startup recovery 事件。
- 同一 lifecycle 仍只允许一个有效 lease owner；重复事件依靠 idempotency key 合并。
- API 命令只等待快速的规划步骤；遇到长任务时返回后端状态，由后台 owner 继续推进。

**边界情况**

- 进程退出后，启动扫描只恢复 `READY` 或过期 `RUNNING`；不恢复等待用户和终态任务。
- 同一输入生成相同 idempotency key 时，不再次调用模型或动作处理器。
- 达到本次 wakeup 上限时保持 `READY` 并重新排队，不标记失败。

### 6.3 Action 合同和处理器

**当前情况**

`ActionEnvelope.payload` 是通用 `dict`，只有 `request_decision` 有专门校验；部分动作没有真实服务调用。

**修改方式**

| Action | 处理器行为 | 自动推进结果 |
|---|---|---|
| `read_evidence` | 调用只读 Evidence Service，返回证据引用和缺失项 | 有新证据则继续；无可读证据时结构化停止 |
| `request_decision` | 创建一个 `PendingDecisionBatch` | 进入等待用户状态 |
| `draft_plan` | 调用现有 `_plan()`/Goal Planning Service | 等待输入、等待审批或继续修订 |
| `explain_result` | 根据 Observation、Goal Evaluation 和产物引用持久化结构化结果说明 | 目标满足后结束 |
| `propose_recovery` | 调用现有 `AgentOrchestrator.propose_recovery()` | 只读建议可继续；执行性恢复等待审批 |
| `finish` | 仅在目标已满足、plan-only 已完成或明确转人工时结束 | 不改变执行或科学状态 |

每种 payload 使用独立 schema；每个处理器返回统一的 `ActionExecutionResult`，包含 `status`、`summary`、`output_refs`、`error_code` 和建议的下一状态。六种动作固定实现，不增加动态工具注册中心。

### 6.4 项目证据和人工决定收敛

**修改方式**

- 新增只读 Evidence Service，数据来源限于 `ProjectStore`、项目配置、数据索引、artifact registry、Reviewed Plan、run projection、Observation 和允许的 Memory 引用。
- Evidence Service 返回结构化摘要和引用，不读取影像正文、完整日志或项目外路径。
- 将 `PendingDecision` 改为一个批次对象：生命周期仍最多只有一个未解决对象，但对象内可包含 1～N 个 `DecisionItem`。
- Planner 先应用已有证据和安全默认值，只把会改变科学含义或无法从项目确定的内容放入批次。
- 用户回答后一次校验全部 item；缺项时保留原批次并返回字段级错误，不创建第二个决定。

### 6.5 Planner、计划版本和重规划

**修改方式**

- Candidate Plan 增加 `revision_no`、`parent_plan_hash`、`revision_reason` 和证据引用。
- 第一次规划、补充答案后的重规划、执行失败后的恢复规划使用同一 Goal Planning Service，不复制计划构建逻辑。
- 每次变更计划内容都重新运行 validator，并生成新的 plan hash 和 Approval Summary。
- 已批准计划发生任何内容变化时，旧审批立即失效；不得复用旧 hash 或 ticket。
- plan-only 继续保持零 dry-run、零 ticket、零 dispatch、零数值产物。

### 6.6 Observation、结果解释和恢复

**修改方式**

- run 终态协调完成后，先由现有 Observation Collector 和 Goal Evaluator 写入权威记录，再触发 Harness。
- `explain_result` 只能引用已登记 artifact、Observation 和 Goal Evaluation，不根据模型措辞推断“计算完成”或“validated”。
- `propose_recovery` 复用现有 recovery policy；模型只能补充解释和在允许方案中选择，不能新造执行动作。
- 恢复次数纳入预算；连续不可恢复、证据不足或同类失败超过上限时转 `HUMAN_HANDOFF`。

### 6.7 上下文、缓存、模型记录和预算

**修改方式**

- 将 `AgentHarnessContext` 升级为版本化分区结构：`goal`、`policy`、`project_evidence`、`decision_state`、`plan_state`、`execution_state`、`latest_observation`、`last_action_result`、`memory_context`、`budget`。
- 每个分区保存来源引用和 hash；总大小继续受硬上限限制，并记录被省略字段。
- 缓存只复用精确输入 hash 匹配的 EvidenceSnapshot、context 分区和幂等步骤结果。状态、plan hash 或 Observation hash 变化后不得命中旧结果。
- 新增 `ModelCallRecord`，记录 provider、model、prompt 版本、context hash、输入/输出 hash、延迟、token/成本（provider 能返回时）、repair 次数和错误码；不保存秘密或完整影像内容。
- 预算至少覆盖 step、模型调用、动作提议、repair、恢复次数、墙钟时间和可选成本。动态分配只能在全局硬上限内减少或重新分配额度。

### 6.8 Skills

**修改方式**

- Skill 只保存特定阶段的工作规程、所需输入、禁止动作和输出 schema，例如“规划前证据检查”“执行结果解释”“失败恢复评审”。
- Skill 通过静态 allowlist 选择；版本和内容 hash 写入 step。
- Skill 不直接执行动作，不获得额外数据访问或执行权限，不代替 validator、Goal Evaluator、Approval Gate 或 Memory 门控。
- 第一阶段只为反复出现且流程稳定的场景建立少量 Skill，不为每个 node 单独建 Skill。

### 6.9 Trace、Replay、评测和前端

**修改方式**

- Trace 串联 attempt、context、ModelCallRecord、ActionEnvelope、ActionExecutionResult、lifecycle transition、plan/approval/ticket/run、Observation 和 recovery 引用。
- Replay 模式只读取固定 trace，在不调用模型、不执行 handler 副作用的情况下重建状态变化并比较 hash。
- 建立固定 Agent 回归集，覆盖正常规划、缺证据、批量决定、plan-only、provider 失败、执行失败、目标未满足、恢复和重启。
- 前端将现有 `HarnessStatusCard` 扩展为后端权威的“当前步骤、等待原因、预算、证据来源、模型/确定性路径、计划版本、执行进度和恢复状态”。
- 前端只提交 answer、approve、cancel 等命令；GET/list 保持无副作用，不通过本地状态自动批准或推断成功。

### 6.10 P2 候选能力

以下能力必须在单 Agent 回归集稳定、trace 可回放、预算可统计后单独 Review：

- 多 Agent：仅允许只读 advisor 并行分析，主 Agent 仍是唯一计划和状态写入 owner；
- SDK：仅在出现明确外部调用方时，封装现有 Agent Task API，不暴露 Gateway 内部能力；
- 自适应策略：只能生成待审批变更，不自动发布 Prompt、Skill、Memory 或预算策略。

## 7. 数据结构变化方向

具体字段和存储变更由对应子方案最终确定。总体方案要求如下：

| 数据结构 | 变化 | 产生位置 | 使用位置 |
|---|---|---|---|
| `AgentHarnessAttempt` | 增加当前阶段、恢复次数、分项预算、唤醒原因和最后进展时间 | Harness service / scheduler | 调度、停止判断、前端摘要 |
| `AgentHarnessStep` | 增加 model call、action result、证据、Observation、plan revision 和状态迁移引用 | `run_one()` | 审计、replay、前端详情 |
| `AgentHarnessContext` | 改为版本化分区结构，保留分区 hash 和省略记录 | `HarnessContextBuilder` | Model adapter、trace、缓存 |
| `ActionEnvelope` | `payload` 改为按 kind 区分的严格 schema | Model adapter | validator、action handler |
| `ActionExecutionResult` | 新增；记录动作真实结果和输出引用 | action handler | 下一步上下文、trace、replay |
| `ModelCallRecord` | 新增；记录模型、版本、token、延迟、hash 和错误 | model adapter/provider | 预算、审计、评测 |
| `PendingDecisionBatch` | 替换当前单问题决定对象，内部包含多个 `DecisionItem` | planner / Harness | API、前端回答、重规划 |
| `PlanRevision` 或等价字段 | 记录修订号、父 hash、原因和证据 | Goal Planning Service | Approval Summary、trace、审计 |
| `EvidenceSnapshot` | 新增只读证据摘要、来源引用和 hash | Evidence Service | context、Planner、解释和 replay |

持久格式切换时使用单一新格式，同步更新当前后端、前端和测试消费者，不保留旧字段 fallback 或双写逻辑。受管 JSON 保持 `_schema_version`；SQLite payload 仍通过现有 store 接口读写。

## 8. 预计文件修改范围

### 8.1 后端核心

| 文件 | 预计修改 |
|---|---|
| `src/backend/app/schemas/agent_harness.py` | context、step、action payload/result、model call 和预算字段 |
| `src/backend/app/schemas/agent_lifecycle.py` | 批量决定和计划修订引用 |
| `src/backend/app/schemas/agent_task.py` | Agent Task API 的决定、Harness 和 trace 摘要类型 |
| `src/backend/app/services/agent_harness_service.py` | 有限循环、动作分派、结构化结果、预算和停止条件 |
| `src/backend/app/services/agent_harness_context_service.py` | 分区上下文、来源 hash、裁剪和缓存键 |
| `src/backend/app/runtime/agent_harness_scheduler.py` | 事件唤醒、后台 owner、启动恢复和公平调度 |
| `src/backend/app/services/agent_task_command_service.py` | create/answer/approve/recovery 后的 Harness 触发和显式 fallback |
| `src/backend/app/services/goal_planning_service.py` | 证据输入、决定批次、计划修订和重规划 |
| `src/backend/app/services/agent_orchestrator.py` | 执行后 Observation/评估/恢复事件接入 |
| `src/backend/app/services/agent_task_reconciler.py` | run 终态后触发下一步，不改变 GET 只读性质 |
| `src/backend/app/services/agent_task_read_model.py` | 新增结构化 Harness、trace、计划版本和恢复投影 |
| `src/backend/app/planner/agent_model_adapter.py` | 严格 Action payload、Skill 和模型调用记录 |
| `src/backend/app/planner/llm_provider.py` | provider/model/token/延迟/request ID 等可用元数据 |
| `src/backend/app/api/dependencies.py` | 新增持久记录的 `ProjectStore` Protocol 方法 |
| `src/backend/app/services/mock_store.py` | SQLite 表/payload、查询、幂等和重启恢复 |
| `src/backend/app/core/config_schema.py`、`src/backend/app/config/settings.py`、`.env.example` | 新增每次唤醒步数、恢复上限、可选成本等配置 |

如 Evidence Service 和 action result 没有合适现有文件承载，可分别新增：

- `src/backend/app/services/agent_evidence_service.py`；
- `src/backend/app/schemas/agent_trace.py`。

不预设新增 action registry、第二个 orchestrator 或第二个执行 service。

### 8.2 前端

| 文件 | 预计修改 |
|---|---|
| `src/frontend/src/lib/types/agentTask.ts` | 批量决定、Harness 详情、计划版本和 trace 类型 |
| `src/frontend/src/lib/api/agentTasks.ts` | 新 answer 合同及只读详情接口 |
| `src/frontend/src/features/agent/AgentWorkspace.tsx` | 收敛目标、决定、审批、执行、结果和恢复操作 |
| `src/frontend/src/features/agent/components/HarnessStatusCard.tsx` | 展示当前步骤、路径、预算和停止原因 |
| `src/frontend/src/i18n/messages/en.ts`、`zh-CN.ts` | 所有新增用户文案和结构化错误映射 |
| 对应 `__tests__/` | API contract、状态呈现、终态停止轮询和双语测试 |

### 8.3 测试和文档

| 范围 | 预计修改 |
|---|---|
| `tests/unit/test_agent_harness_*.py` | 单步、有限循环、动作、context、预算、lease 和 replay |
| `tests/integration/test_agent_harness_lifecycle.py` | create 到结果/恢复的完整流程 |
| `tests/unit/test_agent_task_commands.py` | 决定批次、审批顺序、plan-only、幂等和 fallback |
| Observation/Goal Evaluation/Recovery 测试 | 执行终态后的自动衔接 |
| `docs/规划与运行时/受控单AgentHarness.md` | 更新最终运行合同 |
| `docs/架构与决策/系统架构.md` | 更新真实调用链和模块职责 |
| `docs/安全与审批/安全边界.md` | 更新自动化等级和重新审批条件 |
| `docs/项目概览/能力矩阵.md`、`PROJECT_STATE.md` | 只在实现和验证完成后更新能力/发布状态 |

## 9. 配置原则

| 配置 | 当前值/状态 | 计划 |
|---|---|---|
| `MEDIMAGE_AGENT_HARNESS_ENABLED` | `false` | P0/P1 开发期间继续默认关闭；完整验收后另行决定是否默认开启 |
| `MEDIMAGE_AGENT_HARNESS_MAX_MODEL_CALLS` | `6` | 保留硬上限，可按阶段分配但不得扩大到无界 |
| `MEDIMAGE_AGENT_HARNESS_MAX_TOOL_PROPOSALS` | `8` | 更名与否由数据合同方案决定；含义改为 action proposal 总数 |
| `MEDIMAGE_AGENT_HARNESS_MAX_WALL_SECONDS` | `300` | 保留 attempt 总墙钟上限，后台唤醒另设更小上限 |
| `MEDIMAGE_AGENT_HARNESS_LEASE_SECONDS` | `30` | 保留并验证并发、过期接管和时钟边界 |
| `MAX_STEPS_PER_WAKEUP` | 新增 | 限制一次事件最多推进的步骤，避免独占 worker |
| `MAX_RECOVERY_ATTEMPTS` | 新增 | 限制恢复循环，达到上限后转人工 |
| 模型成本上限 | 可选新增 | provider 无成本信息时只按 token/调用数控制，不伪造成本 |

所有配置通过 `ConfigService` 读取，环境变量使用 `MEDIMAGE_` 前缀，非法值 fail closed。不得从项目 metadata 接受超过全局硬上限的值。

## 10. 风险和处理

| 风险 | 触发场景 | 处理要求 |
|---|---|---|
| 自动循环失控 | 模型持续返回 `read_evidence` 或重复动作 | step、call、proposal、wall time、recovery 和重复输入上限共同停止 |
| 重复执行 | 重复事件、进程重启、lease 过期接管 | step idempotency、command ID、plan hash 和 ticket ID 全链校验 |
| 审批后计划漂移 | 恢复或补充答案改变计划 | 生成新 plan hash 和 Approval Summary，旧审批失效 |
| 模型越权 | 输出未知 action、路径、命令或未引用输入 | strict schema、capability allowlist、typed refs 和 handler 白名单拒绝 |
| 科学状态虚假成功 | 模型解释覆盖真实 run/artifact 状态 | Observation、Goal Evaluation 和 artifact reload 结果始终权威 |
| 上下文泄露 | metadata 含秘密、源影像路径或完整日志 | 单一 context builder、字段 allowlist、脱敏、大小限制和审计 |
| 缓存使用旧证据 | plan、Observation 或项目状态已变化 | 缓存键绑定全部分区 hash；任一变化即失效 |
| provider 来源不可见 | LLM 调用失败或停止时无法辨别实际 provider/model | 持久化 provider、模型和结构化停止原因；现有 Harness 故障不得隐式回退 `_plan()` |
| 决定批次过大 | 一次向用户询问过多问题 | 仅包含阻塞计划的必要项，并给出推荐值和影响说明 |
| 多 Agent 过早引入 | 单 Agent 仍不能稳定回放 | P2 Gate 前禁止生产多 Agent 写入或自动投票 |
| 文档和发布状态再次漂移 | 源码完成但未验证/打包 | 分开记录 source、test、packaging、release 四种状态 |

## 11. 测试与验收

### 11.1 后端单元测试

1. `run_until_blocked()` 能连续完成多个安全步骤，并在等待用户、等待审批、终态和预算耗尽时停止。
2. 同一 step idempotency key 不会重复调用模型、Evidence Service、Planner 或 Recovery Service。
3. 六种 Action 的合法 payload 能调用正确处理器；未知字段、未知 kind、越权引用和 stale state 被拒绝。
4. context 分区 hash 稳定，敏感字段被移除，超过大小上限时按确定顺序裁剪。
5. `PendingDecisionBatch` 支持一次提交多项答案；缺项、重复项、未知 item 和过期批次被拒绝。
6. plan revision 改变后旧 Approval Summary、审批和 ticket 均不能继续使用。
7. provider 失败、一次 repair 失败和预算不足均产生结构化停止；任何未来的替代路径都必须作为新合同、审计事实和独立审批范围处理，不能被视为既有 failure fallback。
8. run 终态后按顺序生成 Observation、Goal Evaluation，再触发解释或 Recovery Proposal。
9. replay 不调用模型和有副作用 handler，且能重建相同状态、hash 和停止原因。
10. 所有失败路径都不调用 dry-run、Execution Gateway、runner 或文件写入。

### 11.2 集成和前端测试

至少覆盖：

1. 目标证据充分：create 后自动到 `WAITING_FOR_APPROVAL`；
2. 目标缺少多个科学选择：只出现一个决定批次，answer 后自动继续规划；
3. plan-only：生成 Reviewed Plan 和结果说明，但无审批、ticket、run 和数值产物；
4. 正常执行：一次审批后自动 monitor、Observation、Goal Evaluation 和结果说明；
5. 执行失败：自动生成 Recovery Proposal，但未重新审批前不 dispatch；
6. 进程重启：恢复 `READY`/过期 `RUNNING`，不恢复等待用户或终态 attempt；
7. 前端 GET/list 不触发模型、状态迁移或 reconcile；
8. `en` 和 `zh-CN` 正确显示决定、审批、预算、fallback、失败和恢复状态。

### 11.3 安全和科学验收

- 证明 Harness 无法直接调用 dry-run、Execution Ticket、Execution Gateway、runner、shell 或文件写入；
- 证明所有写路径仍经过现有 project boundary、write roots、rawdata 和 allowlist 检查；
- 证明模型输出不能把 `metadata_only`、stub 或失败产物标为 `computed`/`validated`；
- 证明 Memory 建议和 Skill 不会变成审批、权限、当前环境或科学事实；
- 证明执行恢复需要新的稳定 Approval Summary 和人工审批。

### 11.4 推荐验证命令

各子阶段先运行 focused tests，再按受影响范围扩大：

```powershell
python -m pytest tests/unit/test_agent_harness_service.py tests/unit/test_agent_harness_context.py tests/unit/test_agent_harness_replay.py tests/unit/test_agent_harness_lease.py tests/unit/test_agent_harness_execution_boundary.py tests/integration/test_agent_harness_lifecycle.py --tb=short --basetemp=.pytest_tmp
python -m pytest tests/unit/test_agent_task_commands.py tests/unit/test_agent_task_api.py tests/unit/test_agent_task_read_model.py tests/unit/test_agent_task_reconciler.py tests/unit/test_observation_collector.py --tb=short --basetemp=.pytest_tmp
npm --prefix src/frontend run format:check
npm --prefix src/frontend run typecheck
npm --prefix src/frontend run test
npm --prefix src/frontend run build
```

共享 lifecycle、审批、store 或 runtime 发生变化时，还必须运行后端完整测试。pytest 完成后按 `AGENTS.md` 安全清理仓库根目录直接子项 `.pytest_cache/` 和 `.pytest_tmp*`。

### 11.5 最终人工验收标准

- 常规任务从一个目标开始，人工操作通常收敛为“最多一次科学决定批次 + 一次执行审批”；
- 无需人工决定的任务能自动到达审批或 plan-only 结果；
- 一次审批后，执行、监控、结果收集和说明不需要人工逐步点击；
- 失败时自动给出引用真实证据的原因和恢复方案，但不会自动扩大权限或重新执行；
- 页面刷新、服务重启和重复命令不会产生第二次模型调用、第二份审批或重复执行；
- trace 可以解释每一步读取了什么、为什么行动、调用了什么服务、结果是什么、为何停止。

## 12. 实施顺序和阶段 Gate

| 阶段 | 内容 | 进入下一阶段的条件 |
|---|---|---|
| P0-1 | 基线审计、characterization tests、文档状态纠正 | 当前调用链、来源状态和差距已确认，无能力误报 |
| P0-2 | 有限循环、事件调度、六种 Action 处理器、Evidence Service | create/answer 可自动推进到阻塞点，执行边界测试通过 |
| P0-3 | 决定批次、Planner 版本、重规划 | 多问题只需一次回答，计划变化会使旧审批失效 |
| P0-4 | Observation、Goal Evaluation、解释和恢复接回 Harness | 一次审批后可自动到结果或恢复审批 |
| P1-1 | context v2、缓存、ModelCallRecord、预算 | 每一步可审计，缓存和预算边界可验证 |
| P1-2 | Skills、Trace、Replay、固定评测集 | 固定场景可无副作用重放，并能发现策略回归 |
| P1-3 | 前端/API 收敛、默认开关评估、正式文档同步 | 双语端到端测试通过，安全和发布 Review 批准 |
| P2 | 多 Agent、SDK 可行性评估 | 必须单独立项，不自动继承 P0/P1 的实现授权 |

一个阶段未通过 Gate 时，不并行修改下一阶段共享核心文件，特别是 `agent_task_command_service.py`、`agent_harness_service.py`、`mock_store.py`、`agent_lifecycle.py` 和 `PROJECT_STATE.md`。

## 13. 后续方案文档清单

后续按顺序逐一制定，本文不展开其详细实现：

| 文档 | 主要内容 |
|---|---|
| [01_当前Agent基线与差距分析.md](01_当前Agent基线与差距分析.md) | 源码、测试、产品入口、发布状态和现有调用链的事实基线 |
| [02_持久化Agent循环与调度器改造方案.md](02_持久化Agent循环与调度器改造方案.md) | `run_until_blocked()`、事件唤醒、lease、幂等、重启恢复和停止条件 |
| [03_Agent动作合同与能力处理器改造方案.md](03_Agent动作合同与能力处理器改造方案.md) | 六种 Action 的 payload、result、handler、权限和错误合同 |
| [04_项目证据收集与科学决策自动化方案.md](04_项目证据收集与科学决策自动化方案.md) | EvidenceSnapshot、证据来源、决定批次、推荐值和人工往返收敛 |
| [05_Planner与版本化重规划改造方案.md](05_Planner与版本化重规划改造方案.md) | 计划修订、证据绑定、validator、hash、旧审批失效和 plan-only |
| [06_Observation_Reflector与恢复闭环方案.md](06_Observation_Reflector与恢复闭环方案.md) | 执行后观察、目标判断、结果解释、恢复建议和重新审批 |
| [07_Agent上下文分层与缓存优化方案.md](07_Agent上下文分层与缓存优化方案.md) | context v2、裁剪、脱敏、分区 hash、缓存和失效规则 |
| [08_Agent预算账本与模型调用治理方案.md](08_Agent预算账本与模型调用治理方案.md) | step/call/token/时间/恢复预算、模型记录、repair 和 fallback |
| [09_Agent_Skills注册与工作规程方案.md](09_Agent_Skills注册与工作规程方案.md) | 少量静态 Skill、输入输出、版本、allowlist 和安全限制 |
| [10_Agent_Trace_Replay与评测体系方案.md](10_Agent_Trace_Replay与评测体系方案.md) | trace 结构、无副作用 replay、固定回归集和质量指标 |
| [11_Agent前端交互与API收敛方案.md](11_Agent前端交互与API收敛方案.md) | 目标、决定、审批、执行、结果、恢复的一体化交互和 API 合同 |
| [12_Agent安全审批与端到端自动化边界方案.md](12_Agent安全审批与端到端自动化边界方案.md) | A0～A4 边界、重新审批条件、路径、审计和威胁场景 |
| [13_多Agent协作评估方案.md](13_多Agent协作评估方案.md) | 只读 advisor、单一写 owner、成本和是否值得引入的 Gate |
| `14_Agent_SDK与扩展接口评估方案.md` | 明确外部调用方后再定义公共 API、版本和权限边界 |
| `16_分阶段实施依赖与任务拆分.md` | 将已批准方案拆成可独立 Review 的开发任务和依赖关系 |
| `17_总体验收与文档收敛方案.md` | 端到端验收、能力等级、默认开关、正式文档和发布状态更新 |

## 14. 待确认事项

以下问题不阻碍继续编写子方案，但必须在对应方案 Review 时确定：

1. **Harness 默认开关：** P1 完成后是默认开启，还是继续按项目显式开启。推荐先继续默认关闭，完成隔离项目和桌面端回归后再决定。
2. **后台调度承载方式：** 继续使用应用 lifespan 内单 owner，还是接入项目已有其他后台 task 机制。推荐先复用现有 lifespan 和 monitor 模式，不引入外部队列。
3. **决定批次的最大 item 数：** 推荐硬上限 6，超出时优先补读证据或转人工说明，避免一次表单过大。
4. **模型成本字段：** provider 是否都能返回可靠 token/成本。无法返回时只记录调用数、token（如有）和延迟，不估算虚假成本。
5. **阶段十一状态：** 现有 Harness 是否已经经过完整发布验证。需依据 Git 基线、CI、打包和 release 证据确认，不能只根据源码推断。

## 15. 本方案完成标准

- Reviewer 能从第 3、6、7、8 节定位当前缺口、数据变化和预计文件；
- 每个新增概念都有当前必要性，没有引入第二套生命周期、执行链或存储系统；
- 自动化目标与人工审批边界没有冲突；
- 借鉴内容已转换为 MedImage 的项目证据、科学产物和审批语义；
- P0、P1、P2 顺序明确，多 Agent 和 SDK 不会提前进入实现；
- 后续每份子方案都有清晰职责，不需要重新讨论总边界。
