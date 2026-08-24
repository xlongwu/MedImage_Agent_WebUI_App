# 阶段十五：Agent-first 最终形态分阶段完善计划

> 状态：源码实施、自动化回归、发布 Gate 工具化、dirty-tree 诊断和 clean exact-SHA 可见 packaged Electron 三流程正式验收已完成（2026-08-25）；installer/version/tag 与最终发布仍待独立 Release 任务
>
> 任务模式：Feature Bundle + Architecture / Refactor；其中资源自动选择属于受保护执行合同变更，真实桌面验收属于 Release / Packaging 验证
>
> 目标：在不重建 Pipeline、Lifecycle、Approval、Ticket、Gateway 或 Recovery 体系的前提下，把当前已经具备的 Agent Task 主链收敛为项目默认且完整的用户体验，并补齐真实桌面端验收证据。

## 1. 文档定位

本文件是对以下既有方案和当前源码状态的增量完善计划：

- `specs/阶段记录/阶段十/阶段十_Agent-first前端与交互收敛总体计划.md`；
- `specs/阶段记录/阶段十五/Agent自主化交互优化/00_自动续跑与确认交互优化方案.md`；
- `specs/阶段记录/阶段十五/Agent自主化交互优化/01_历史手动执行表面清单.md`；
- 当前 `AgentTaskResponse`、Agent Workspace、受控审批执行链和恢复链实现；
- `PROJECT_STATE.md`、能力矩阵、安全边界和当前测试证据。

本计划不重复已经完成的 Agent Task 投影、全局确认、自动续跑和历史 mutation 收口工作，而是处理当前源码与最终产品形态之间仍存在的差距。每个阶段都必须遵守“先刻画当前行为、再修改、再对照方案和验证”的执行顺序。

## 2. 最终目标与不可变边界

### 2.1 最终默认流程

```text
选择项目
  -> 描述研究目标
  -> Agent 自动收集项目证据并生成计划
  -> [仅必要时] 一次科学决定批次
  -> 一次集中执行审批
  -> Agent 自动 dry-run、执行、观察、评价
  -> 查看结果或审批恢复
```

普通用户不得为了推动标准流程而手动切换 Data、Plan、Preprocessing、QC 或 Results，也不得手动触发 dry-run、validation、report、refresh 或单节点执行。

### 2.2 最终一级导航

```text
Projects
Agent
Runs
Settings
```

- `Projects`：项目选择、创建、最近任务和需要处理的状态；
- `Agent`：项目默认入口和唯一标准操作主页面；
- `Runs`：历史运行、完整日志、节点、票据、恢复与证据；
- `Settings`：用户默认值、语言、主题、高级模式和环境状态。

旧工作区不保留为默认一级导航。仍有当前消费者的只读证据应迁入 Agent Details、Runs 或 Advanced；没有消费者的旧路由、字段、组件和测试必须删除，不新增兼容层或 fallback。

### 2.3 必须保持的不变量

- Agent Task 只是 canonical lifecycle、ticket、run、observation、evaluation 和 recovery 的前端投影，不持久化第二套状态机。
- 所有真实执行继续经过 Reviewed Plan、稳定 Approval Summary、Execution Ticket 和唯一 Execution Gateway。
- rawdata、源 DICOM、源 BIDS 和已登记源 NIfTI 永远只读。
- Agent 不自动决定 Atlas、GSR、TR 冲突、覆盖策略、实验性 backend 或扩大后的写入范围。
- GET/list/read-model 不得产生 reconcile、dispatch 或状态迁移副作用。
- 结果成功只能来自 Goal Evaluation 和可重载、已登记的真实产物；partial、failed 和 indeterminate 不得被隐藏。
- 本计划不修改 ALFF、fALFF、ReHo、FC 或预处理公式，不重写 Pipeline Runtime、Ticket、Gateway 或 Recovery。

## 3. 实施前基线和差距

| 能力 | 当前状态 | 本计划处理 |
|---|---|---|
| Agent Task 统一投影、五类用户状态、事件接口 | 已完成 | 保持并补 contract 回归 |
| 集中审批、细分 confirmation、自动续跑 | 已完成 | 保持，不新增第二个命令入口 |
| Observation、Goal Evaluation、Recovery Proposal | 已完成主链 | 补真实桌面局部恢复验收 |
| 默认项目入口为 Agent | 已实现 | 补全一级导航收敛 |
| 一级导航仅四项 | 未完成，旧工作区仍可见 | Phase 1 |
| Projects 最近任务和项目状态 | 未完成 | Phase 1 |
| 简短中英文 FC 目标自动规划 | 未完成 | Phase 2 |
| Sidecar、Atlas、已有 Run 等审批前证据 | 部分完成 | Phase 2 |
| 标准 CPU/Compute policy 为 `auto/auto` | 未完成，当前默认 `serial/cpu` | Phase 3 |
| 审批前自动检查与审批后 dry-run 边界 | 需要统一定义 | Phase 3 |
| 结果卡显示 QC、产物、限制、推荐和导出入口 | 部分完成 | Phase 4 |
| Settings 提供 Atlas、Template、资源偏好 | 未完成 | Phase 5 |
| Advanced 集中展示完整技术证据 | 部分完成 | Phase 5 |
| 可见 Electron 的 BIDS、DICOM、恢复 E2E | dirty-tree 诊断与 clean exact-SHA 正式 Gate 均已通过 | Phase 6 |
| 当前状态、能力矩阵和交互指标与源码一致 | 部分漂移 | Phase 0、Phase 7 |

## 4. 总体实施顺序

```text
Phase 0 事实冻结与验收基线
  -> Phase 1 Agent-first Shell 和 Projects 收敛
  -> Phase 2 目标语义与审批前项目证据
  -> Phase 3 自动资源选择和审批边界
  -> Phase 4 进度、结果和导出闭环
  -> Phase 5 Settings、Advanced 和证据分层
  -> Phase 6 真实桌面 E2E 与恢复验收
  -> Phase 7 文档、指标和最终状态收敛
```

Phase 0 至 Phase 2 可以在不改变数值算法的前提下推进。Phase 3 涉及默认执行策略和 provenance，必须先通过受保护执行合同 Review。Phase 6 必须建立在前序 Gate 全部通过的 clean exact-SHA 上。

## 5. Phase 0：事实冻结与验收基线

### 5.1 目标

把本计划使用的“当前事实”固定为可重复检查的源码、测试和交互证据，消除旧阶段文档与当前实现之间的漂移。

### 5.2 必做项

1. 记录当前 branch、exact SHA、`git status --short` 和未跟踪生成物；不得将用户已有 `artifacts/` 纳入或清理。
2. 建立当前导航、Projects、Agent Workspace、Settings、Result 和 Advanced 的组件清单及当前消费者清单。
3. 为以下三个目标建立 planner characterization tests：
   - `Generate FC`；
   - `Compute functional connectivity`；
   - `生成 FC`。
4. 固定当前 native full plan 的 `cpu_policy`、`compute_policy` 和实际 provenance 行为。
5. 重新测量三条标准流程的一级页面、显式操作、审批和人工排查步骤，区分 source contract、browser UI、Electron UI 和 packaged UI。
6. 标记 `PROJECT_STATE.md`、能力矩阵及阶段证据中的过期声明，但在行为尚未改变前不得提前更新为目标状态。

### 5.3 交付物

- 当前交互与导航基线；
- planner 意图覆盖矩阵；
- 资源策略和 provenance 基线；
- 文档漂移清单；
- 后续阶段可复用的验收 fixture。

### 5.4 Gate G15F-0

- [ ] 所有目标文件、调用方、类型和测试已列入 blast radius；
- [ ] 三条标准流程有区分验证层级的基线；
- [ ] 简短 FC 目标的当前失败已由回归测试稳定复现；
- [ ] 没有把旧文档声明当作当前源码事实。

## 6. Phase 1：Agent-first Shell 和 Projects 收敛

### 6.1 目标

让用户进入应用后只看到四个一级入口，项目打开后默认处于 Agent Workspace；旧工作区只作为只读证据的内部呈现存在。

### 6.2 实施内容

1. 将 `GlobalNavigationRail` 的默认项目导航收敛为 Agent、Runs、Settings；Projects 保持全局入口。
2. 移除 Overview、Data、Plan、Preprocessing、QC、Results 的默认导航项和当前普通消费者。
3. 将仍需保留的内容逐项迁移：
   - 数据准备摘要 -> Agent 数据卡或 Task Details；
   - Reviewed Plan -> Agent 计划详情；
   - 节点和日志 -> Runs；
   - QC 和主要产物 -> Result Summary；
   - 原始技术证据 -> Advanced / Runs。
4. 对无消费者的 workspace union、route branch、i18n、测试和组件执行一次性删除；不得保留 shim。
5. 扩展 Projects 读取模型和页面，使每个项目显示：
   - 最近 Agent Task；
   - 五类用户状态；
   - 最近结果摘要；
   - 是否需要用户处理；
   - 最近活动时间。
6. Projects 状态必须来自后端项目级 Agent Task/list 投影，不得根据“是否存在 Reviewed Pipeline”或本地 UI 状态推断任务完成。
7. 默认页面移除无异常时的 Operational Health 和节点级状态；仅在需要注意或 Advanced 模式下展示。

### 6.3 主要文件面

- `src/frontend/src/features/navigation/GlobalNavigationRail.tsx`；
- `src/frontend/src/features/navigation/workspaceModel.ts`；
- `src/frontend/src/features/navigation/useWorkspaceNavigation.ts`；
- `src/frontend/src/features/app/AppShellView.tsx`；
- `src/frontend/src/features/projects/ProjectsPage.tsx`；
- `src/frontend/src/features/agent/AgentWorkspace.tsx`；
- 相应 i18n、类型、client 和测试。

### 6.4 Gate G15F-1

- [ ] 默认一级入口只有 Projects、Agent、Runs、Settings；
- [ ] 项目进入后默认显示 Agent Workspace；
- [ ] 普通用户无法从一级导航进入历史逐阶段流程；
- [ ] Runs、日志、审计、产物和计划证据仍可访问；
- [ ] Projects 的状态和最近任务来自后端权威投影；
- [ ] 每个默认页面最多突出一个 primary action。

## 7. Phase 2：目标语义与审批前项目证据

### 7.1 目标

用户只描述目标，Agent 能识别目标产物、展开必要依赖，并在审批前确定数据和科学前提；缺少条件时提出结构化问题，而不是返回通用不支持或等到审批后失败。

### 7.2 实施内容

1. 为中英文 FC/功能连接建立明确的 canonical intent，不依赖完整句子或任意英文摘要解析。
2. 将 `生成 FC` 展开为当前注册能力支持的完整依赖链，例如：

   ```text
   输入证据
     -> 必要预处理
     -> 混杂回归
     -> 滤波
     -> Atlas/Labels 解析与重采样
     -> ROI Timeseries
     -> FC
     -> QC
     -> Report
   ```

   实际节点只能来自当前 node registry、Tool Catalog 和 canonical kernel，不得凭目标文本虚构能力。
3. 扩展项目上下文证据，结构化报告：
   - 空项目、DICOM、BIDS、NIfTI；
   - BOLD、T1、Sidecar；
   - 可复用预处理产物；
   - Atlas、Labels 和许可/注册状态；
   - 未完成或冲突 Run；
   - TR、Template 和已有科学参数冲突；
   - 当前环境能够实际使用的 backend。
4. 将证据分为：已确认、缺失、冲突和无法判断。只读检查无需审批，任何可能写入或执行的探测仍受 Approval Gate 约束。
5. 科学含义变化必须形成一个决定批次，并同时给出推荐、影响和证据；Agent 不得替用户作答。
6. 区分：
   - 支持目标但缺少输入 -> `provide_input`；
   - 科学参数歧义 -> `answer_science_decision`；
   - 真正超出注册能力 -> `revise_goal` 或结构化 handoff。
7. 对计划内 ReHo、FC、Normalization 等输入链执行审批前静态验证；审批后的 execution dry-run 仍是最终执行前门控。

### 7.3 测试矩阵

- 中英文短目标、长目标、组合目标和近似反例；
- BIDS 完整、缺 T1、缺 Sidecar、Atlas 缺失、Atlas 冲突；
- DICOM 需要转换和转换能力不可用；
- 已有可复用产物、未完成 Run、覆盖冲突；
- plan-only 零执行；
- 支持但缺前提不得映射为 `UNSUPPORTED_GOAL`；
- 项目上下文读取不得写状态或触发 runner。

### 7.4 Gate G15F-2

- [ ] 三条简短 FC 目标均能生成合法计划或准确的结构化缺失条件；
- [ ] 计划节点均来自当前 registry，依赖顺序可解释；
- [ ] Atlas、TR、GSR、Template、覆盖等科学歧义不会被自动决定；
- [ ] 审批前能够发现当前静态可知的关键缺失和冲突；
- [ ] read-only context 和 GET 投影保持无副作用；
- [ ] plan-only 不创建 approval、dry-run、ticket、run 或数值产物。

## 8. Phase 3：自动资源选择和审批边界

### 8.1 目标

普通模式不要求用户填写 worker、thread、chunk、VRAM 或 GPU token；系统以 `auto` 策略选择资源，并把实际选择写入计划、审批证据和 provenance。

### 8.2 审批前检查与 execution dry-run 的边界

本阶段明确区分两种检查：

| 检查 | 时机 | 允许行为 | 禁止行为 |
|---|---|---|---|
| Planning preflight | 审批前 | 读取已登记元数据、验证 schema、节点依赖、静态路径和能力声明，生成 Approval Summary | runner、外部工具、临时执行产物、ticket、dispatch |
| Execution dry-run | 审批后 | 绑定未变化的 summary/hash，执行受控运行前检查 | 扩大节点、backend 或写入范围；失败后继续真实执行 |

因此不得简单把现有 execution dry-run 移到审批前。若 planning preflight 需要新的持久字段，必须由 Reviewed Plan/Approval Summary 的 canonical 证据承载，不得新增平行状态机。

### 8.3 实施内容

1. 将普通模式的 native CPU policy 设为 `auto`，由 Resource Planner 确定 worker 和 thread。
2. 将普通模式的 compute backend 设为 `auto`，由 GPU Planner 基于当前注册能力、环境、显存、安全策略和收益选择 CPU 或 GPU。
3. 无安全 GPU 路径、能力不支持、收益不足或环境验证失败时确定性回落 CPU，并记录明确原因；不得静默切换。
4. 实际记录至少包含：
   - requested policy；
   - selected backend；
   - worker/thread/chunk；
   - GPU 设备和环境验证结果（如适用）；
   - fallback reason；
   - backend/version/dtype/tolerance；
   - deterministic seed（如适用）。
5. Approval Summary 展示自动资源策略和可能的 CPU fallback，不向普通用户展示低层预算参数。
6. Advanced 模式可修改受支持的策略；任何 scientific/backend/scope 变化必须重建 plan/hash/summary 并重新审批。
7. 删除旧 `serial/cpu` 作为普通 Agent 计划的隐式 fallback；所有当前消费者必须一次性切换。

### 8.4 受保护变更前置条件

- 明确当前 CPU/GPU node 支持矩阵；
- 对现有 CPU 行为建立 characterization tests；
- 确认 GPU Planner 不会把 scaffolded/unavailable 路径报告为可执行；
- 完成 Tool Catalog、Approval Summary、Ticket、Gateway、provenance 和前端类型的影响审查；
- 当前发布冻结若仍有效，必须在获准的后续版本分支实施。

### 8.5 Gate G15F-3

- [ ] 普通计划显式表达 `auto/auto`，不存在 schema 默认值造成的 `serial/cpu` 隐式回退；
- [ ] 实际 backend 和资源选择可在 provenance 重载；
- [ ] CPU fallback 原因准确且不暗示 GPU 已使用；
- [ ] 扩大 backend、节点、路径或科学参数会使旧审批失效；
- [ ] planning preflight 无执行副作用；
- [ ] execution dry-run 仍严格位于审批之后、ticket/dispatch 之前；
- [ ] dry-run 失败时不创建 ticket、不 dispatch。

## 9. Phase 4：进度、结果和导出闭环

### 9.1 目标

让普通用户只通过一个结果卡了解是否完成、完成了什么、QC 如何、有哪些限制以及下一步做什么。

### 9.2 实施内容

1. 保持五阶段宏观进度：准备数据、准备方案、执行处理、验证结果、完成。
2. 优先使用结构化后端 `current_action`/action code；前端只负责 i18n 映射，不再分别根据多个字段猜测另一套当前动作。
3. Result Summary 默认显示：
   - 总体 outcome；
   - 成功、失败、排除和总受试者数；
   - 主要 QC 结论；
   - 主要产物类型和可用数量；
   - 重要限制；
   - 推荐下一步。
4. 提供两个清晰入口：
   - `查看结果`：进入当前任务的结构化结果详情；
   - `导出报告`：仅在已登记且可导出的报告存在时启用。
5. `查看执行证据` 保持次级操作，进入 Runs 或 Technical Evidence，不与结果入口混淆。
6. `metadata_only`、缺少产物、reload 失败、partial 和 failed 必须使用不同结构化结果及双语文案。
7. 导出必须使用已登记 artifact，不从前端拼接本地路径，不重新执行 report 生成。

### 9.3 Gate G15F-4

- [ ] 结果卡渲染 QC、产物、限制和推荐操作；
- [ ] success 需要 Goal Evaluation 满足且产物可重载；
- [ ] 一个受试者失败时明确显示 partial/failed count；
- [ ] 查看与导出入口绑定当前 task/project/artifact；
- [ ] 无报告时导出按钮禁用并说明原因；
- [ ] 默认结果页不显示完整 Stage Table、hash 或原始日志。

## 10. Phase 5：Settings、Advanced 和证据分层

### 10.1 目标

建立清晰的三层信息架构，同时保留全部科学参数、审计和技术证据。

### 10.2 Settings

普通 Settings 增加：

- 默认 Atlas；
- 默认 Template；
- CPU/GPU 偏好；
- 语言、主题；
- Advanced 模式；
- 当前环境可用性摘要。

规则：

- 默认值只能作为新计划输入，不能静默修改已审阅或运行中的任务；
- 只有用户已经明确设置且资源注册验证通过的 Atlas/Template 才能作为默认计划输入；否则必须进入决定批次；
- Atlas/Template 必须来自已登记、许可明确且可验证的资源；
- 设置变更影响当前计划时必须重建计划并重新审批；
- 环境可用性来自后端权威探测，不由浏览器推断。

### 10.3 信息三层

| 层级 | 默认内容 |
|---|---|
| 普通视图 | 目标、状态、下一步、宏观进度、结果、风险、推荐操作 |
| 任务详情 | 计划摘要、阶段、受试者、主要 QC、关键参数、产物、恢复历史 |
| 技术证据 | Ticket、Node Contract、Plan/Goal Contract、backend、资源决策、provenance、checksum、日志、Validation JSON、Artifact Registry |

Advanced 默认关闭。开启时必须显示科学语义和可比性风险提示；关闭 Advanced 不得删除、降级或阻止 Runs/Audit 按权限读取证据。

### 10.4 Gate G15F-5

- [ ] Settings 的四类默认项具有后端 schema、存储、前端类型、调用方和测试；
- [ ] 默认值变化不会修改既有审批；
- [ ] Advanced 默认关闭并显示明确风险提示；
- [ ] 普通视图没有 worker、VRAM、hash、contract version 或节点表；
- [ ] 技术证据可追溯到 canonical ticket/run/artifact，不解析任意英文摘要；
- [ ] Logs、Artifacts、Audit、Validation、Provenance 均未丢失。

## 11. Phase 6：真实桌面 E2E 与恢复验收

### 11.1 目标

使用隔离 workspace、隔离 userData、临时 SQLite 和合成/许可测试数据，在可见 Electron 窗口中验证最终用户流程，而不是仅验证 source component 或 sidecar API。

### 11.2 必测流程

#### E2E-1：BIDS -> FC

```text
选择 BIDS 项目
  -> 输入“生成 FC”
  -> [必要时] 确认 Atlas/GSR
  -> 批准一次计划
  -> 自动执行和评价
  -> 查看 FC、QC 和报告
```

#### E2E-2：DICOM -> 预处理 -> FC

```text
选择 DICOM 项目
  -> 输入完整目标
  -> Agent 识别需要转换
  -> 集中展示转换与后续执行范围
  -> 受控审批
  -> Ticket/Gateway 执行转换和后续节点
  -> 验证 rawdata manifest 不变
  -> 查看结果
```

#### E2E-3：单受试者失败 -> 局部恢复

```text
三受试者任务运行
  -> 注入一个可恢复失败
  -> 保留两个已完成受试者
  -> 生成 Diagnosis/Recovery Proposal
  -> 用户批准一次恢复
  -> 仅执行批准范围
  -> 自动重新观察和评价
```

### 11.3 稳定性和生命周期场景

- 刷新、项目切换、任务切换；
- 应用正常退出和 sidecar 停止；
- 强制终止后的重启恢复；
- 审批摘要过期；
- 重复点击、命令 replay 和网络超时；
- 同一 task 不重复 dispatch；
- Electron 主进程异常退出后 sidecar 自动停止；
- 无 renderer error、无持久桌面数据库污染。

### 11.4 证据要求

每条 E2E 必须记录：

- exact SHA、构建命令和包表面；
- 隔离 workspace/userData/database；
- 页面跳转数、显式操作数、审批数；
- plan hash、approval hash、ticket、run、evaluation；
- selected backend 和 provenance；
- artifact registry、reload、checksum；
- rawdata 前后 manifest；
- 失败、partial、恢复范围和最终 outcome；
- 正常退出后的进程、端口和 sidecar 状态。

### 11.5 Gate G15F-6

- [x] BIDS 标准流程显式操作不超过 3 次；
- [x] DICOM 标准流程显式操作不超过 4 次；
- [x] 恢复只增加一次必要审批；
- [x] 默认一级页面不超过 4 个；
- [x] 三条流程均在可见 Electron 窗口完成；
- [x] packaged sidecar、renderer、退出和恢复 attempt 均有证据；
- [x] rawdata 未变化，写入只发生在批准范围；
- [x] 结果与实际 artifact/evaluation 一致。

### 11.6 2026-08-24 至 2026-08-25 执行状态

发布流程已补齐 clean/exact-SHA 前置拒绝、包内 provenance、artifact
checksum inventory 和一次性证据目录。当前工作树尚未形成候选提交，因此
`RELEASE_WORKTREE_DIRTY` 按预期阻断正式构建；未通过提交、tag 或发布来绕过。

在同一 dirty diagnostic `win-unpacked` 包上，三条可见 Electron 流程均通过
自动验收：BIDS 显式操作 3 次、DICOM 显式操作 4 次、恢复只增加 1 次必要
审批；三者 rawdata manifest 均不变、renderer console error 为 0、退出后 owned
sidecar 为 0。恢复只重试 `sub-003`，attempt 为 `EVALUATED` 且执行成功，
`sub-001`/`sub-002` 产物 hash 未变化，并绑定了新的 goal evaluation。

合成 atlas/preview 证据使三条流程保持 truthful `partial`，而不是虚报
`satisfied`。在候选 commit 创建后，又按桌面打包文档使用
`-ExpectedGitSha` 从 clean worktree 重建并重跑三条流程；包内 provenance
的 SHA 与候选一致且 `clean=true`，三条 write-once evidence 作为同一候选
证据集保存。因此 G15F-6 已完成；installer、版本、tag 和发布仍不由该 Gate
自动证明。

## 12. Phase 7：文档、指标和最终状态收敛

### 12.1 目标

让用户文档、开发文档、能力声明和当前状态只描述已经被相应验证层证明的行为。

### 12.2 文档同步矩阵

| 变化 | 必须检查并按需更新 |
|---|---|
| 导航和用户流程 | `README.md`、`README_CN.md`、前端使用文档 |
| Agent Task、项目摘要、结果模型 | API、生命周期和系统架构文档 |
| preflight/dry-run/审批顺序 | `docs/安全与审批/安全边界.md`、运行时文档 |
| 资源 auto 和 backend provenance | 能力矩阵、配置说明、科学验证文档 |
| Settings/Advanced/证据分层 | 前端与审计文档 |
| Electron E2E 和打包证据 | 桌面打包文档、`PROJECT_STATE.md` |
| 交互指标 | 阶段十五 evidence 和当前状态快照 |

`PROJECT_STATE.md` 只记录当前已验证状态，不追加实施流水账。source tests、browser UI、packaged sidecar、Electron renderer 和真实科学执行必须分层报告，不得互相替代。

### 12.3 Gate G15F-7

- [ ] 文档中的导航、默认值、状态和命令与源码一致；
- [ ] 能力矩阵没有把 scaffolded/metadata-only 写成 computed/validated；
- [ ] 交互指标来自实际最终 UI，而非只来自源码结构推断；
- [ ] `PROJECT_STATE.md` 日期、SHA、限制和 Next Work 已更新；
- [ ] 旧字段、旧路径、旧接口和旧描述已全仓搜索并处理；
- [ ] `AGENTS.md` 已做影响检查，只有出现新的可复发治理风险时才修改。

## 13. API、状态和数据合同原则

### 13.1 优先扩展现有投影

优先在现有 `AgentTaskResponse` 中补充结构化字段，不新增平行任务 API。可能需要的字段包括：

- Projects 所需的最近任务摘要；
- 结构化 current-action code；
- 主要 QC 和 artifact presentation；
- requested/selected resource policy；
- planning preflight evidence；
- 导出能力和 disabled reason。

字段变更必须同步：backend schema、route/service、contract tests、frontend type/client/caller、i18n 和文档。更新所有当前消费者后删除旧字段；禁止双格式解析和兼容 shim。

### 13.2 不新增第二套状态机

以下内容不得成为新的持久状态权威：

- 导航选项；
- 弹窗 dismissed key；
- 前端 current-action 文案；
- Projects 卡片展示状态；
- 宏观进度百分比；
- 导出按钮状态。

这些均必须从 canonical lifecycle、run、evaluation、artifact 和配置派生。

## 14. 实施前必须确认的决策

| RFI | 推荐决策 | 未确认时的处理 |
|---|---|---|
| RFI-15F-01：当前发布冻结是否允许修改默认资源策略 | 在明确获准的后续版本分支实施 Phase 3 | 保持 `serial/cpu` 当前事实，不得宣称 `auto/auto` 已完成 |
| RFI-15F-02：默认 Atlas/Template 的权威来源 | 仅接受项目设置或用户设置中已登记、许可明确、checksum 可验证的资源 | Agent 创建决定批次，不自动猜测 |
| RFI-15F-03：DICOM 转换和后续计算是否需要多个 canonical approval record | 允许一个集中 UI 展示多个范围明确、分别可验证的 approval record | 不用一个模糊 hash 覆盖不同执行边界 |
| RFI-15F-04：报告导出是读取既有 artifact 还是创建新 artifact | 首版只导出既有已登记报告；生成新报告属于新的受控执行 | 无已登记报告时禁用导出 |
| RFI-15F-05：可见 Electron E2E 的自动化方式 | 使用隔离 userData/workspace 的桌面测试驱动，并保留人工 walkthrough 作为补充 | 不能用 browser component test 替代 Gate G15F-6 |

RFI-15F-01 至 RFI-15F-03 是 Phase 3 或 DICOM E2E 的阻塞项；其他 RFI 有保守默认值，不阻止 Phase 0 至 Phase 2。

## 15. Blast Radius Map

| 表面 | 预计变化 | 风险 |
|---|---|---|
| 前端导航、workspace model、App shell | 移除默认旧工作区与消费者 | 中高 |
| Projects 页面及 project-scoped task list | 增加最近任务投影 | 中 |
| Goal planner、context、goal contract | FC 意图和输入依赖扩展 | 高 |
| Approval Summary 和 planning evidence | 审批前静态证据完整化 | 高 |
| CPU/GPU Resource Planner | 普通默认改为 auto | 极高 |
| Ticket/Gateway/Runtime | 不重写，仅验证新策略绑定 | 极高、受保护 |
| Result Summary、artifact client、export | 补结构化结果入口 | 中高 |
| Settings/config | 新默认值和计划绑定 | 高 |
| Runs/Technical Evidence | 证据迁移与分层 | 中 |
| Electron/packaging/E2E | 隔离真实 UI 验收 | 高 |
| 科学 kernels | 不修改 | 禁止扩张 |

## 16. Hazard Registry

| H-ID | 风险 | 缓解 | 验证 |
|---|---|---|---|
| H15F-01 | 隐藏旧导航时同时丢失审计和产物入口 | 先迁移当前消费者，再删除旧 route/component | deep-link、Runs、artifact、audit 测试 |
| H15F-02 | Projects 使用本地状态推断项目任务结果 | 只消费项目范围 Agent Task 投影 | 刷新、重启、跨项目、空任务测试 |
| H15F-03 | 简短 FC 意图误命中相近非科学目标 | 结构化 intent、双语正反例、依赖注册验证 | planner 回归矩阵 |
| H15F-04 | 支持目标缺输入被误报不支持 | 目标识别和证据检查分离 | 缺 BOLD/T1/Atlas/Sidecar 测试 |
| H15F-05 | 审批前 preflight 触发执行副作用 | 只读 contract；spy 证明 runner/ticket/gateway 零调用 | approval-order tests |
| H15F-06 | `auto` 暗示 GPU 已使用或静默切换 backend | 记录 requested、selected 和 fallback reason | provenance/reload/backend tests |
| H15F-07 | 资源默认变化使旧审批仍然有效 | backend/worker 策略进入 plan/summary hash | stale approval 和 scope drift 测试 |
| H15F-08 | Settings 静默修改运行中科学参数 | 设置仅作为新 plan 输入；变化强制 replan/reapprove | current-task immutability tests |
| H15F-09 | 结果 UI 根据 run terminal 伪报成功 | Goal Evaluation + artifact reload 为唯一成功依据 | partial/missing/reload failure tests |
| H15F-10 | 导出按钮重新运行报告或越界访问路径 | 只导出已登记 artifact，后端路径安全校验 | path traversal/rawdata/export tests |
| H15F-11 | Advanced 关闭后证据不可审计 | Runs/Audit 保持授权可达，Advanced 只改变展示密度 | permission/deep-link tests |
| H15F-12 | current action 再次由前端拼成第二状态 | 使用结构化 action code 和 i18n 映射 | non-English/current-action tests |
| H15F-13 | packaged smoke 被误报为可见 GUI E2E | 分层记录证据，G15F-6 要求 Electron window | evidence review checklist |
| H15F-14 | E2E 污染用户数据库或研究数据 | 隔离 userData、SQLite、workspace 和合成数据 | 前后 manifest、路径和 DB 检查 |
| H15F-15 | 旧 route/字段作为兼容层长期残留 | 一次性切换所有消费者并全仓搜索删除 | source contract 和 dead-code scan |

## 17. 验证矩阵

### 17.1 后端 focused

根据实际修改选择并扩展以下测试，不得用减少断言或放宽科学容差通过：

```powershell
python -m pytest tests/unit/test_agent_task_api.py tests/unit/test_agent_task_read_model.py tests/unit/test_agent_task_commands.py --tb=short --basetemp=.pytest_tmp
python -m pytest tests/unit/test_agent_task_reconciler.py tests/unit/test_agent_task_result_summary.py --tb=short --basetemp=.pytest_tmp
python -m pytest tests/unit/test_agent_planning_service.py tests/unit/test_llm_planner.py tests/unit/test_llm_planner_native_context.py --tb=short --basetemp=.pytest_tmp
python -m pytest tests/unit/test_approval_summary.py tests/unit/test_execution_ticket.py tests/unit/test_reviewed_execution_service.py --tb=short --basetemp=.pytest_tmp
python -m pytest tests/unit/test_path_safety.py tests/unit/test_native_preproc_api.py --tb=short --basetemp=.pytest_tmp
```

测试路径必须以当前仓库真实文件名为准；实施前先用 `rg --files tests` 确认，不得把不存在的建议路径写成已通过命令。每次 pytest 后按 `AGENTS.md` 清理并复核根目录直接子项 `.pytest_cache/`、`.pytest_tmp*`。

### 17.2 前端

```powershell
npm --prefix src/frontend run format:check
npm --prefix src/frontend run typecheck
npm --prefix src/frontend run test
npm --prefix src/frontend run test:project-runs
npm --prefix src/frontend run build
```

覆盖导航、Projects 投影、Agent Workspace、集中审批、结果、Settings、Advanced、双语、键盘、窄屏、项目切换、stale response 和 export disabled 状态。

### 17.3 桌面和打包

```powershell
npm --prefix desktop/electron run check
```

在 Phase 6 按 `docs/桌面与前端/桌面应用打包.md` 运行 sidecar、unpacked、packaged smoke 和可见 Electron workflow。构建成功、sidecar health、renderer smoke、人工 GUI 和真实科学执行分别报告。

### 17.4 最终回归

- 后端完整套件；
- 前端 format/typecheck/test/build；
- desktop check；
- 受保护审批、ticket、gateway、path/rawdata 回归；
- artifact reload/provenance/result truthfulness；
- 三条 packaged Electron E2E；
- 文档路径、命令、能力和状态一致性检查。

## 18. 关键验收指标

### 18.1 交互

- 一级页面不超过 4 个；
- 项目内默认主页面只有 Agent Workspace；
- 标准 BIDS 任务显式操作不超过 3 次；
- 标准 DICOM 任务显式操作不超过 4 次；
- 一个决定批次和一次集中执行审批分别只有一个 primary action；
- 默认页面不展示完整 Stage Table 或 CPU/GPU 低层参数；
- 用户不手动触发 dry-run、validation、report 或 refresh。

### 18.2 Agent 和安全

- Agent 自动识别项目状态并生成下一步；
- 信息不足或存在科学歧义时结构化停止；
- 安全只读检查无需审批；
- 写入和真实执行必须经过当前稳定摘要审批；
- 失败后生成明确恢复建议，但不自动扩大范围或审批恢复；
- Agent 不自动修改科学参数；
- 不建立第二套 Pipeline、Lifecycle 或执行入口。

### 18.3 科学与证据

- 所有完成声明绑定 Goal Evaluation 和真实产物；
- selected backend、参数、版本、dtype、checksum 和限制可追溯；
- partial、excluded、failed 和 reload failure 对普通用户可见；
- rawdata 前后 manifest 不变；
- Advanced 和 Runs 可访问完整审计证据。

## 19. Definition of Done

只有同时满足以下条件，才能声明本完善计划完成：

- [ ] G15F-0 至 G15F-7 全部通过；
- [ ] 四项默认导航、Agent 主页面和 Projects 摘要符合目标；
- [ ] 简短中英文 FC 目标通过真实 planner 回归；
- [ ] planning preflight 与 execution dry-run 边界有测试证明；
- [ ] 普通模式使用 auto 资源策略，实际选择进入 provenance；
- [ ] 结果、Settings 和三层证据完整接入当前调用链；
- [ ] BIDS、DICOM 和局部恢复在可见 packaged Electron 中通过；
- [ ] Approval Gate、Ticket、Gateway、rawdata、路径和科学真实性不变量未削弱；
- [ ] 旧消费者、旧字段、旧路由和旧描述已处理，无兼容 shim；
- [ ] 全部验证命令、退出码、警告、环境限制和未验证区域如实记录；
- [ ] 文档与当前 exact-SHA 一致，工作区没有混入用户数据、秘密或无关生成物。

## 20. 实施交接建议

每个 Phase 应作为独立任务执行，并在开始时声明任务模式、当前 Gate、明确非目标和受保护模块。不得同时并行修改同一 Agent Task 调用链。推荐交付顺序为：

1. Phase 0 + Phase 1：先完成可见产品壳收敛；
2. Phase 2：再补目标语义和审批前证据；
3. Phase 3：单独执行受保护资源策略变更；
4. Phase 4 + Phase 5：完成结果、设置和证据体验；
5. Phase 6：在 clean exact-SHA 上做真实桌面验收；
6. Phase 7：最后更新状态快照、能力声明和交互指标。

任何阶段发现上游 Gate 不成立时必须停止后续扩张，修正当前阶段后重新验证。无法完成的外部环境项只能保留为明确限制，不得用 source test 或模拟响应替代最终验收。
