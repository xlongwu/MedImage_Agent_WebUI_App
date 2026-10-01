# MedImage Agent 前端 Agent 化与交互收敛总体计划

> 当前结果（2026-07-16）：10A–10E 与 10F 的 source/docs 范围已实现并完成当时的
> 自动化验证；exact-SHA 打包、可见 Electron 真实流程、安装器和发布版本对齐延期。
> 本文以下内容是实施前方案和风险登记，不再作为当前阻塞状态或待办来源。当前事实见
> [阶段十 README](README.md) 与[验收记录](evidence/阶段十_E2E验收记录.md)。

> 历史启动状态：Proposed — Blocked by Phase 9 `G9-6`
>
> 日期：2026-07-15
>
> 任务模式：Feature Bundle Mode + Architecture and Refactor Mode；10E 与 10F 的真实执行部分叠加 Scientific Validation Mode。
>
> 交付目标：以既有 Agent Lifecycle 和受控执行链为唯一事实来源，交付 Agent-first、Workflow-second、Details-on-demand 的项目主流程。

> 实施更新（2026-07-16）：维护者已明确授权独立分支源码实施。10A–10E 和 10F
> source/docs 子范围已完成验证；packaged GUI、exact-SHA、安装器与发布关卡延期，不能从
> 本次 source 结果推断通过 G10-6。

## 1. Scope Anchor

### 1.1 目标

把当前“Overview → Data → Plan → Preprocessing → QC → Results”的手动阶段导航，收敛为：

```text
选择项目
  -> 描述目标
  -> Agent 自动检查、规划、dry-run 和验证
  -> 用户集中审批
  -> Agent 通过既有受控执行链运行、观察和评价
  -> 用户查看结果或审批安全恢复
```

项目内默认页面只保留 Agent Workspace；Runs 与 Settings 保留为工具入口，原 Data、Plan、Preprocessing、QC、Results 页面先作为兼容路由和高级详情继续存在。

### 1.2 必须完成

- Agent Task 是现有 Agent Lifecycle、Reviewed Plan、Execution Ticket、Observation、Goal Evaluation、Recovery 和 Artifact Evidence 的只读投影；投影状态不得持久化。
- 普通用户只处理五类人类决策：研究目标、数据确认、科学方案差异、真实写入/执行审批、无法安全自动恢复时的处置。
- 用户目标必须先绑定项目上下文，再生成和验证计划。
- 自动 dry-run、Plan Validation、Goal Contract 和 Approval Summary 必须在审批前完成。
- 一次集中审批必须在服务端展开为现有细粒度确认字段，并绑定不可变的计划、路径、节点、后端和审批摘要 hash。
- 真实执行继续经过 Reviewed Plan、Approval Gate、Execution Ticket、Execution Gateway、Pipeline Runtime 和已注册 runner。
- 执行结束后由有限、可重入、确定性的协调器自动收集 Observation、执行 Goal Evaluation，并在必要时生成 Recovery Proposal。
- 普通模式只显示目标、用户状态、当前动作、下一步、宏观进度、结果、风险和推荐操作。
- 技术证据完整保留在 Runs、Technical Details 和 Evidence 中。
- 默认资源策略使用 `auto`；实际 CPU/GPU 选择写入 provenance。当前 GPU `auto` 未放行的 stage 必须继续显式落到 CPU，不得在 UI 中暗示已使用 GPU。
- BIDS → FC、DICOM → 预处理 → FC、单受试者失败 → 审批恢复三条流程必须分别以真实证据验收。

### 1.3 必须不做

- 不创建第二套 Pipeline、Lifecycle、Recovery 或 artifact registry。
- 不让前端组合多个后端状态并自行推断任务成功。
- 不让 LLM 直接执行、重试、修改科学参数或扩大审批范围。
- 不改变 ALFF、fALFF、ReHo、FC、滤波、回归、配准或标准化公式。
- 不把 `metadata_only`、preview、partial 或 simplified 结果包装成完成。
- 不修改 rawdata，不开放 MATLAB、SPM、DPABI 或任意外部命令执行。
- 不在 RC2 冻结主线中实现本计划。
- 不以“Agent 自动化”为由隐藏日志、provenance、audit、validation 或 artifact evidence。

### 1.4 成功标准

- [ ] 顶层导航为 Projects、Agent、Runs、Settings。
- [ ] 项目选择后默认进入 Agent Workspace。
- [ ] 标准 BIDS 任务从目标提交到结果查看不超过 3 次显式操作。
- [ ] 标准 DICOM 任务不超过 4 次显式操作；若 10E 未通过，产品不得声称达到该指标。
- [ ] 标准页面不出现手动 Dry Run、Validation、Report、Refresh、CPU worker、GPU budget 等操作。
- [ ] 一次审批卡片可以形成所有必需的细粒度审计确认，但任何范围变化都会使审批失效。
- [ ] 失败任务默认得到一条可行动建议；不安全、证据不足或超出配额时进入人工处理。
- [ ] 默认页不显示完整 stage table；Runs/技术详情仍可访问全部节点和证据。
- [ ] 端到端状态与 artifact truthfulness 测试证明 UI 不产生假成功。

## 2. Current Behavior and Evidence Summary

当前前端已经完成上一轮工作站式重构，因此本阶段不是从旧版页面起步，而是在现有 React 壳层上改变默认交互主线。

| 事实 | 当前证据锚点 | 计划影响 |
|---|---|---|
| 当前主导航仍有六个 lifecycle workspace | `src/frontend/src/features/navigation/workspaceModel.ts:3-24` | 10C 将默认 lifecycle rail 替换为 Agent 主入口，旧 workspace 保留兼容 |
| Projects 选择后当前默认进入 Overview | `src/frontend/src/features/navigation/workspaceModel.ts:28-30` | 默认入口要改为 `agent` |
| shell 仍渲染 LifecycleRail 和六个 workspace | `src/frontend/src/features/app/AppShellView.tsx:335-346,454-563` | 需要改 outlet 和导航模型，不能只改视觉文案 |
| Assistant 当前是侧边 Sheet | `src/frontend/src/features/app/AppShellView.tsx:411-424` | 10C 将目标输入提升为主页面，辅助聊天保留为次级能力 |
| Assistant API 只是 `/api/assistant/chat` | `src/frontend/src/lib/api/assistant.ts:4-8` | 不能把聊天响应误当 Agent Task；新增项目范围 API |
| 当前 task controller 组合通用 task、events、diagnostics 和 websocket | `src/frontend/src/features/tasks/useTaskController.ts:49-146` | Runs 可继续复用，但 Agent Workspace 改用统一投影 |
| 当前前端通过 task 名称猜测是否存在预处理 run | `src/frontend/src/features/tasks/useTaskController.ts:140-154` | 投影层必须由后端给出绑定关系，移除名称启发式作为主依据 |
| 当前 Preprocessing UI 暴露 5 项 reviewed confirmations 和 5 项 native confirmations | `src/frontend/src/features/workspaces/PreprocessingReviewedFlow.tsx:60-122,643-688` | 10B 将它们收敛为一次审批，服务端仍保存细粒度字段 |
| 当前 native UI 暴露 CPU mode、GPU backend、Dry Run、Execute、Validation、Report | `src/frontend/src/features/workspaces/PreprocessingReviewedFlow.tsx:859-946` | 普通模式移除，迁入高级详情 |
| 当前 native 运行状态每 3 秒轮询 | `src/frontend/src/features/workspaces/PreprocessingReviewedFlow.tsx:373-391` | 自动刷新可以保留，但由 Agent Task controller 统一管理 |
| 后端已有持久化 Agent Lifecycle 和完整状态枚举 | `src/backend/app/schemas/agent_lifecycle.py:15-38,77-112` | 只扩展 canonical lifecycle，不另建 task state machine |
| 生命周期转移由单一表约束且执行前要求 ticket | `src/backend/app/services/agent_orchestrator.py:59-81,166-214` | Agent 命令服务必须复用 orchestrator，不得直接改状态 |
| Observation、Goal Evaluation、Recovery API 已项目范围化 | `src/backend/app/api/agent_lifecycle_routes.py:184-307,377-488` | 10D 主要做自动协调和面向用户投影，不重建底层能力 |
| Goal Contract builder 已对真实 artifact、reload、registry 和 capability 建模 | `src/backend/app/planner/goal_contract_builder.py:147-246` | 结果摘要必须来自 Goal Evaluation，不来自 HTTP 200 或本地推断 |
| 自然语言规划接口已能加载项目上下文并验证 plan | `src/backend/app/api/llm_planner_routes.py:46-98` | 10B 抽取可复用 service，并补 persistence/dry-run/approval orchestration |
| reviewed execution 路由已负责 Approval Gate、ticket 和 lifecycle | `src/backend/app/api/execute_reviewed_routes.py:726-1184` | 先抽取 application service，Agent route 与旧 route 共用；禁止复制 1400 行路由逻辑 |
| GPU planner 明确规定 auto 未放行时回退 CPU | `src/backend/app/native_preproc/orchestrator/gpu_resource_planner.py:109-124,232-239` | UI 文案必须反映真实后端选择，不承诺自动 GPU |
| DICOM conversion 执行当前不是 Execution Ticket | `src/backend/app/runtime/execution_entry_inventory.py:33-44` | DICOM 一句话流程受 10E 独立保护性关卡约束 |
| 当前 RC2 主线禁止新 API 和状态契约 | `specs/阶段记录/阶段九/README.md:19-28` | 本计划只能进入 v0.7 能力线 |

## 3. Architecture Decision

### 3.1 唯一状态源

```text
User Goal / Answer / Approval Command
        |
        v
Agent Task Command Service
        |
        +--> AgentOrchestrator (canonical lifecycle)
        +--> Reviewed Plan / Goal Contract
        +--> Approval Gate / Execution Ticket / Gateway
        +--> Observation / Goal Evaluation / Recovery
        |
        v
AgentTaskReadModel (pure projection)
        |
        v
Agent Workspace / Runs / Evidence
```

`AgentTaskReadModel` 每次从 canonical records 计算：

- `state`；
- `current_action`；
- `next_action`；
- `progress`；
- `decisions`；
- `result_summary`；
- `evidence_links`。

上述字段不得作为第二份任务状态写入数据库。允许持久化的新增内容仅限用户输入事实、科学决策答案、审批记录和 canonical lifecycle 扩展字段；它们不包含独立的 `status/progress/next_action`。

### 3.2 公共状态映射

默认 UI 只使用五个状态带；具体要回答信息、确认科学方案还是批准执行，由 `next_action.type` 区分。

| 用户状态 | canonical lifecycle |
|---|---|
| `preparing` | CREATED、CONTEXT_READY、PLAN_DRAFTED、PLAN_VALIDATED |
| `waiting_for_user` | 新增 canonical `WAITING_FOR_INPUT`、`WAITING_FOR_SCIENCE_DECISION`，以及 WAITING_FOR_APPROVAL、WAITING_FOR_RETRY_APPROVAL、WAITING_FOR_RECOVERY_APPROVAL |
| `running` | APPROVED、EXECUTION_READY、RUNNING、OBSERVING、EVALUATING、RETRYING、RECOVERY_READY、RECOVERING |
| `needs_attention` | FAILED、DIAGNOSING、RETRY_PROPOSED、RECOVERY_PROPOSED、HUMAN_HANDOFF、新增 CANCELED（同时返回 `outcome=canceled`，不得显示为成功） |
| `completed` | GOAL_SATISFIED、SUCCEEDED |

新增状态是对现有 lifecycle 的最小扩展，不是新生命周期。历史 schema 读取必须通过默认值和迁移测试；运行中 cancel 在 runtime 有真实取消语义前保持不可用。

### 3.3 Agent Task API

目标接口：

```text
POST /api/projects/{project_id}/agent/tasks
GET  /api/projects/{project_id}/agent/tasks
GET  /api/projects/{project_id}/agent/tasks/{task_id}
POST /api/projects/{project_id}/agent/tasks/{task_id}/answer
POST /api/projects/{project_id}/agent/tasks/{task_id}/approve
POST /api/projects/{project_id}/agent/tasks/{task_id}/cancel
GET  /api/projects/{project_id}/agent/tasks/{task_id}/events?after=<cursor>
```

设计约束：

- `task_id` 第一版直接等于或确定性绑定 `lifecycle_id`，避免额外 identity 漂移。
- create/answer/approve/cancel 都要求 `command_id`，服务端幂等拒绝 replay。
- `approve` 只接受 `approval_summary_hash`、actor 和命令身份；细粒度 confirmation 由服务端从已审阅摘要展开，前端不能自由伪造。
- `events` 是 lifecycle/ticket/observation/evaluation/recovery 事件的只读合并视图；使用 cursor polling，不新增 websocket 依赖。只在任务活跃时自动轮询。
- GET 请求不得触发状态迁移、执行、观察或恢复。
- 运行后自动推进由后台 reconciler 完成，读取 API 不能“顺便修复”任务。

### 3.4 Agent Task 响应最小契约

```json
{
  "schema_version": 1,
  "task_id": "lifecycle_xxx",
  "project_id": "project_xxx",
  "state": "waiting_for_user",
  "goal_summary": "完成标准预处理并生成 ALFF、ReHo 和 FC",
  "current_action": "计划已验证，等待执行审批",
  "next_action": {
    "type": "approve_execution",
    "title": "审批处理方案",
    "requires_user": true
  },
  "progress": {
    "phase": "plan_ready",
    "completed_subjects": 0,
    "failed_subjects": 0,
    "total_subjects": 3
  },
  "decisions": [],
  "approval_summary": {
    "summary_hash": "sha256:...",
    "write_roots": ["project://derivatives", "project://runs"],
    "rawdata_read_only": true,
    "external_tools": []
  },
  "result_summary": null,
  "evidence_links": []
}
```

便携响应使用 project-relative URI 或稳定 ID，不默认泄漏本机绝对路径；绝对路径只在本地技术证据中按既有策略显示。

### 3.5 有限自动推进

Agent 自动推进不是开放式自治循环。每次命令只允许执行有限序列：

```text
create:
  context -> plan -> validate -> dry-run -> goal contract -> approval summary -> stop

approve:
  verify summary hash -> existing approval/ticket/gateway -> dispatch -> stop

terminal run event:
  observe -> evaluate -> [satisfied | diagnosis -> recovery proposal] -> stop

approved recovery:
  existing recovery service -> observe -> evaluate -> stop
```

任一阶段遇到缺失数据、科学歧义、范围变化、未知状态、配额耗尽或不完整证据时立即停止，并投影为唯一 next action。

### 3.6 Advanced Mode

Settings 提供默认关闭的 `advancedMode`。开启前显示固定警告：高级模式会暴露科学参数和执行资源配置，不恰当修改可能改变结果含义或降低可比性。

高级模式可以查看 Profile、Stage Overrides、TR、GSR、FWHM、ReHo neighborhood、Atlas/Template、CPU/GPU policy、preview limit、validation、node contract 和 artifacts。任何参数修改都必须生成新的科学决策/plan/hash/approval；该开关只控制可见性，不能改变 backend safety gate。

## 4. Implementation Ledger

### 10A：交互基线与只读投影

- 对 BIDS → FC、DICOM → 完整预处理 → FC、单受试者失败 → 恢复建立可复现交互基线。
- 新增 `AgentTaskResponse` schema 和纯 `AgentTaskReadModel`。
- 建立 lifecycle → 用户状态、next action、progress、evidence link 的确定性映射。
- 先交付 GET list/detail/events 和契约测试，不触碰执行路径。

完成定义：同一 canonical evidence 必须产生稳定投影；缺失/冲突证据只能保守降级，不能显示完成。

### 10B：目标、决策、dry-run 与集中审批

- 对 canonical lifecycle 做最小版本扩展，加入等待输入、等待科学决策、执行前取消。
- 把 goal planner 和 execute-reviewed 的应用逻辑从 route 抽取为可复用 service。
- create 自动完成 context、plan、validation、dry-run、Goal Contract、pending Reviewed Plan 和 Approval Summary。
- answer 只回答当前 pending decision；科学参数改变后重建 plan/hash/summary 并重新审批。
- approve 通过 summary hash 展开细粒度确认并调用既有门控执行链。

完成定义：标准 BIDS 任务只需提交目标和一次审批即可进入真实受控执行；范围漂移必定阻断。

### 10C：Agent-first Shell

- 把 `ProjectWorkspace` 调整为 `agent | runs | settings`，项目默认入口为 `agent`。
- 新增 AgentWorkspace、ProjectSummary、GoalComposer、CurrentAction、NextActionCard、MacroProgress、ResultSummary。
- 旧 workspace 保留兼容 route/advanced link，不出现在默认导航。
- Assistant Sheet 降级为解释和追问工具，不承担任务状态。

完成定义：普通路径只有一个主要操作，刷新或切项目后由后端投影恢复界面。

### 10D：自动观察、结果、恢复与高级证据

- 建立有界、幂等的 post-run reconciler，自动调用 Observation 和 Goal Evaluation。
- 失败时自动生成 Diagnosis 与 Recovery Proposal，但不自动审批或执行恢复。
- 把结果摘要绑定 Goal Evaluation 和 artifact registry；把节点、ticket、provenance、checksum、validation 放入 Technical Details。
- Runs 继续作为完整审计入口，移除前端名称猜测和手动 refresh 作为标准路径。

完成定义：成功、partial、失败、证据缺失和恢复提议均由 canonical facts 驱动。

### 10E：DICOM 执行入口收敛

- 先表征现有 conversion prepare/approval/execute 行为和 rawdata 不变性。
- 将 native DICOM conversion 纳入 Tool Catalog、Approval Gate、Execution Ticket、Gateway、audit 和安全路径约束。
- 旧 conversion endpoint 变为兼容适配器或明确退役；不得保留第二个主执行入口。
- 只有该关卡通过后，Agent 才能自动把 DICOM conversion 放入统一 reviewed plan。

完成定义：`execution_entry_inventory` 不再把 DICOM 主路径标记为“approval package is not an execution ticket”。

### 10F：真实 E2E 与交互验收

- 在打包 Windows 应用中验证 BIDS → FC。
- 10E 通过后验证 DICOM → preprocessing → FC。
- 注入单受试者可恢复失败，验证一次审批的局部恢复。
- 记录操作数、页面跳转、审批数、结果证据、rawdata manifest 和 exact-SHA。

完成定义：三条流程分别满足交互、科学真实性、安全、恢复和打包证据关卡。

## 5. Blast Radius Map

| 表面 | 变化 | 风险 |
|---|---|---|
| `schemas/agent_lifecycle.py`、SQLite payload | canonical 状态最小扩展与兼容读取 | 高 |
| `services/agent_orchestrator.py` | 新状态转移与命令幂等 | 高 |
| reviewed execution / ticket / gateway | 提取共享 application service，不改安全语义 | 极高 |
| DICOM conversion | 10E 纳入唯一受控执行入口 | 极高 |
| `schemas/agent_task.py`、read model、route | 新公共投影 API | 中高 |
| frontend navigation/shell | 默认入口和工作区收敛 | 中高 |
| Agent Workspace controller | 新 server-state controller 与自动 polling | 中 |
| Runs/technical evidence | 信息重新分层，证据不删除 | 中 |
| scientific kernels | 不修改 | 禁止变更 |
| rawdata / user datasets | 只读 | 禁止变更 |

安全解耦表面：ALFF/ReHo/FC kernels、node formula、artifact numerical content、Electron bridge、主题/i18n 基础设施不需要因本阶段重写。

## 6. Hazards and Mitigations

| H-ID | 风险 | 缓解 | 验证 |
|---|---|---|---|
| H10-01 | Agent Task 变成第二状态机并与 lifecycle 漂移 | 投影字段纯计算且不入库；task_id 绑定 lifecycle_id | persistence/source contract 测试 |
| H10-02 | 新 route 复制 execute-reviewed 逻辑导致绕过 gate | 先抽 application service，旧/新 route 共用 | monkeypatch 证明 gateway 唯一被调用一次 |
| H10-03 | 一键审批弱化细粒度确认 | summary hash 绑定 plan/path/node/backend；服务端展开 confirmations | tamper、stale hash、scope expansion 测试 |
| H10-04 | LLM 自行修改科学参数 | science decision 必须进入 canonical waiting state；无答案不继续 | GSR/atlas/TR 决策测试 |
| H10-05 | 自动循环无限重试或无审批恢复 | 每个 trigger 有有限状态序列；恢复始终停在 approval | quota、replay、no-auto-approve 测试 |
| H10-06 | HTTP 200 或 run terminal 被误报成功 | result 只由 Goal Evaluation + artifact evidence 生成 | missing artifact / partial / reload failure 测试 |
| H10-07 | GET 请求产生写入副作用 | read model 纯函数；reconciler 独立触发 | GET 前后 DB/event 数不变 |
| H10-08 | 自动资源选择暗示 GPU 已用 | 投影实际 selected backend 和 fallback reason | CPU fallback/provenance 测试 |
| H10-09 | 旧页面删除导致证据不可达 | 先隐藏导航，保留兼容 route 与 Runs links | deep-link/advanced navigation E2E |
| H10-10 | 轮询重复事件或项目切换串数据 | cursor、AbortController、project/task binding | stale response 与 duplicate cursor 测试 |
| H10-11 | lifecycle schema 扩展破坏旧 SQLite | schema version、默认值、migration/round-trip tests | 旧 fixture 加载与迁移测试 |
| H10-12 | DICOM 自动化绕开 ticket/gateway | 10E 是硬前置；未通过时 task 停在 needs_attention | execution inventory 与 gateway dispatch 测试 |
| H10-13 | 原始路径通过摘要泄漏或被扩大 | portable links + safe root normalization + hash binding | path containment/redaction 测试 |
| H10-14 | frontend task 名称启发式产生错误进度 | 后端投影提供明确 run/ticket binding | 同名 run、缺失 run、cross-project 测试 |
| H10-15 | RC2 冻结被计划实施打破 | 阶段十状态保持 Blocked，独立 v0.7 分支 | branch/status/release gate 审查 |

## 7. Test and Validation Plan

### 7.1 Backend

```text
python -m pytest tests/unit/test_agent_task_read_model.py --tb=short --basetemp=.pytest_tmp
python -m pytest tests/unit/test_agent_task_api.py tests/unit/test_agent_task_commands.py --tb=short --basetemp=.pytest_tmp
python -m pytest tests/unit/test_agent_lifecycle.py tests/unit/test_execution_ticket.py tests/unit/test_execute_reviewed_api.py --tb=short --basetemp=.pytest_tmp
python -m pytest tests/unit/test_observation_collector.py tests/unit/test_goal_evaluator.py tests/unit/test_recovery_proposal_engine.py tests/unit/test_recovery_execution.py --tb=short --basetemp=.pytest_tmp
python -m pytest --tb=short --basetemp=.pytest_tmp
```

每次 pytest 后严格按 `AGENTS.md` 清理仓库根目录直接子项 `.pytest_cache/` 与 `.pytest_tmp*`，保留命令输出和退出码后再清理。

### 7.2 Frontend

```text
npm --prefix src/frontend run typecheck
npm --prefix src/frontend run test
npm --prefix src/frontend run build
```

新增测试至少覆盖：投影契约、workspace controller、单一主要动作、审批卡、宏观进度、结果/partial/失败、自动 polling、project switch、advanced evidence deep-link、中英文文案与键盘可达性。

### 7.3 Protected and scientific paths

- execution gateway/ticket/approval focused regression；
- rawdata path containment 和前后 manifest；
- artifact persistence/reload/provenance/status truthfulness；
- CPU/GPU selected backend 与 tolerance 证据；
- DICOM conversion 仅在 10E 独立安全测试全绿后进入 E2E；
- packaged Windows launch、restart 和 recovery smoke。

## 8. Proof Obligations

| 声明 | 必须提供的证明 |
|---|---|
| 没有第二状态机 | 数据库/schema 搜索证明 AgentTaskResponse 未持久化，projection determinism 测试通过 |
| 一次审批未弱化安全 | summary hash、细粒度 confirmation、ticket、audit、gateway 证据链 |
| Agent 没有直接执行 | 所有真实 runner 调用栈都经过 ExecutionGateway |
| 自动推进有界 | trigger-to-transition 表和最大步数测试 |
| 结果真实 | Goal Evaluation criteria、artifact reload、registry、validation 对照 |
| partial 不被隐藏 | 一个受试者失败时 public state/result 明确显示 failed/excluded count |
| GPU 文案真实 | selected backend 与 provenance 一致，auto fallback 可见 |
| DICOM 已收敛 | execution inventory、node registration、ticket consumption、rawdata 不变性 |
| 旧证据仍可访问 | Runs/Technical Details 深链接和审计导出测试 |
| 交互指标达成 | 三条真实流程的录屏/步骤日志与操作计数表 |

## 9. Ambiguities and RFIs

| RFI | 推荐决策 | 未解决后果 |
|---|---|---|
| RFI-10-01：10E 是否纳入同一 v0.7 候选 | 纳入，但作为独立高风险 gate | 不纳入则只能宣称 BIDS Agent-first 完成 |
| RFI-10-02：运行中 cancel 是否支持 | 首版不支持；只允许 dispatch 前取消 | UI 不显示运行中取消按钮 |
| RFI-10-03：events transport | 第一版 cursor polling | 不阻塞；未来可在同一契约上加 SSE |
| RFI-10-04：是否引入 TanStack Query | 先沿用 hooks/controller，待 API 稳定后单独决定 | 避免把依赖扩展与核心契约绑定 |
| RFI-10-05：标准默认 Atlas/Template 来源 | 必须是已登记、许可明确、可验证资源 | 未解决时 Agent 必须询问或阻断 FC/normalization |
| RFI-10-06：Approval Summary 是否覆盖 DICOM 与 preprocessing | 允许一个用户卡片生成多个范围明确的 canonical approval records | 不能用单一模糊 approval 覆盖不同执行边界 |

当前阻塞：阶段九 `G9-6`、RFI-10-01、RFI-10-05。其他 RFI 有安全默认值，可在相应任务启动前确认。

## 10. Assumption Registry

| A-ID | 假设 | 分类 | 证据 | 错误风险 |
|---|---|---|---|---|
| A10-01 | 阶段十属于 v0.7 能力扩展 | VERIFIED | Phase 9 禁止新 API/state contract | 破坏 RC2 冻结 |
| A10-02 | Agent Task 可直接绑定 lifecycle_id | WORKER CONSENSUS | lifecycle 已项目范围持久化 | 未来复合 lifecycle 需增加聚合 ID |
| A10-03 | BIDS Agent-first 可先于 DICOM 收敛验收 | IMPLICIT | DICOM 当前非 ticket，BIDS reviewed execution 已有 ticket/gateway | 产品声明需分范围 |
| A10-04 | 用户状态可以纯映射 canonical lifecycle | CRITICAL | lifecycle 枚举和 evidence 已存在 | 若映射不完备会诱发第二状态 |
| A10-05 | 现有 hooks/controller 足以完成首版 | USER DECISION | ADR-003 把 React Query 放在 v0.7+，但不是强制 | 可能增加手写缓存代码 |
| A10-06 | 普通模式默认资源策略为 auto | USER DECISION | 用户方案要求；后端已有 resource planners | 需要明确 GPU auto 仍可能选 CPU |
| A10-07 | 旧 workspace 可以先隐藏后删除 | VERIFIED | 当前页面均已独立 feature 化 | 兼容期会保留额外维护成本 |

## 11. Phase Gates

| Gate | 退出条件 |
|---|---|
| G10-0 启动授权 | Phase 9 G9-6 关闭，v0.7 分支建立，RFI 关键项确认 |
| G10-1 投影可信 | 10A GET API、状态映射、baseline 和 no-side-effect 测试通过 |
| G10-2 BIDS 受控闭环 | 10B goal → approval → ticket/gateway 路径通过 |
| G10-3 Agent-first UI | 10C 默认导航、单一动作和 advanced deep-link 通过 |
| G10-4 观察恢复闭环 | 10D 自动 observe/evaluate/propose，恢复仍需审批 |
| G10-5 DICOM 收敛 | 10E 唯一 gateway 主入口和 rawdata 不变性通过 |
| G10-6 真实 E2E | 10F 三条 packaged 流程与交互指标通过 |

任何 gate 失败都必须保持真实能力描述。尤其是 G10-5 未通过时，DICOM 自动化只能显示为“需要人工受控处理”，不得用 UI 编排掩盖执行入口未收敛。

## 12. Hazard Coverage Check

| Hazard | 任务 | 自动化验证 |
|---|---|---|
| H10-01, H10-07, H10-14 | 10A | projection/persistence/GET side-effect tests |
| H10-02, H10-03, H10-04, H10-11, H10-13 | 10B | command, schema compatibility, approval tamper tests |
| H10-09, H10-10 | 10C | navigation, polling, deep-link tests |
| H10-05, H10-06, H10-08 | 10D | bounded reconciler, artifact truth, backend evidence tests |
| H10-12 | 10E | inventory/ticket/gateway/rawdata tests |
| H10-15 及全部集成风险 | 10F | branch gate、packaged E2E、interaction audit |

## Handoff

源码实施范围已交付；后续只在形成 clean exact-SHA 候选后继续 packaged/release 验收。

- Proof Obligations：10
- Hazards Mitigated：15/15
- Implementation Tasks：6
- Critical RFIs：3
- 当前状态：source implemented；packaged/release acceptance deferred
