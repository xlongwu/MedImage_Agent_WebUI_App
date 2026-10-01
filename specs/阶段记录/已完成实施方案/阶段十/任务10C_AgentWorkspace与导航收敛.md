# 任务 10C：Agent Workspace 与导航收敛

> 状态：Source implemented and frontend automation recorded；可见 Electron 验收延期。
>
> 本文正文保留实施前要求；其中 `Current Behavior`、清单和 Stop Conditions 是历史方案，
> 当前结果以 `README.md` 和 `evidence/阶段十_E2E验收记录.md` 为准。
>
> 任务模式：Architecture and Refactor Mode + Feature Bundle Mode
>
> 交付目标：把项目默认入口改为 Agent Workspace，并把旧流水线页面降为兼容/高级视图。

## 1. Current Behavior

- `PrimaryWorkspace` 当前是 overview/data/plan/preprocessing/qc/results：`src/frontend/src/features/navigation/workspaceModel.ts:3-24`。
- shell 默认渲染 LifecycleRail：`src/frontend/src/features/app/AppShellView.tsx:335-346`。
- Assistant 当前是 overlay：`src/frontend/src/features/app/AppShellView.tsx:411-424`。
- 各工作区已独立 feature 化，适合保留为兼容详情而不是删除：`src/frontend/src/features/workspaces/`。
- i18n、theme、primitive 和 controller 基础已存在；本任务应复用而非重建。

## 2. Required Behavior

顶层位置模型调整为：

```ts
type GlobalLocation = { kind: "projects" };
type ProjectWorkspace = "agent" | "runs" | "settings";
type ProjectLocation = { kind: "project"; projectId: string; workspace: ProjectWorkspace };
```

项目默认进入 `agent`。旧 workspace 使用兼容 location 或 Agent 卡片内的 detail route 到达，不进入顶层导航。

Agent Workspace 固定由五个区域组成：

1. Project Summary：项目名、数据状态、任务状态、最近结果、待处理问题；
2. Goal Composer：无任务或用户明确创建新任务时显示；
3. Current Action：一句用户语言状态；
4. Next Action Card：任何时刻只突出一个主动作；
5. Result Summary：Goal Evaluation 满足或明确 partial/failure 后显示。

普通页不显示内部路径、完整 stage table、worker/GPU budget、plan hash、ticket、node contract 或 raw logs。所有内容必须可以从 Task Details 或 Runs 访问。

## 3. Files

### Create

- `src/frontend/src/features/agent/AgentWorkspace.tsx`
- `src/frontend/src/features/agent/AgentWorkspace.module.css`
- `src/frontend/src/features/agent/useAgentTaskController.ts`
- `src/frontend/src/features/agent/components/ProjectSummaryCard.tsx`
- `src/frontend/src/features/agent/components/GoalComposer.tsx`
- `src/frontend/src/features/agent/components/CurrentAction.tsx`
- `src/frontend/src/features/agent/components/NextActionCard.tsx`
- `src/frontend/src/features/agent/components/MacroProgress.tsx`
- `src/frontend/src/features/agent/components/ResultSummaryCard.tsx`
- 相应 `__tests__` 文件。

### Modify

- `src/frontend/src/features/navigation/workspaceModel.ts`
- `src/frontend/src/features/navigation/useWorkspaceNavigation.ts`
- `src/frontend/src/features/app/AppShellView.tsx`
- `src/frontend/src/features/dashboard/TopBar.tsx`
- `src/frontend/src/features/tasks/RunActivityBar.tsx`
- `src/frontend/src/features/tools/AssistantSheet.tsx`
- `src/frontend/src/hooks/useAppState.ts`：增加默认关闭的 advanced mode 可见性偏好；
- `src/frontend/src/i18n/messages/en.ts`
- `src/frontend/src/i18n/messages/zh-CN.ts`
- `src/frontend/src/lib/projectWorkflow.ts`：旧启发式只保留兼容层，不作为 Agent 事实源；
- 相关导航、shell、accessibility tests。

### Preserve

- DataConversionWorkspace、PlanWorkspace、PreprocessingWorkspace、QCReportsWorkspace、ResultsWorkspace、RunsWorkspace、SettingsEnvironmentWorkspace；
- MedicalImageViewer、ArtifactBrowser、audit/export 能力；
- 现有 HTTP client 和 Electron bridge 边界。

## 4. Detailed Tasks

1. 先为现有 deep-link、project switch、Runs、Settings 建 characterization tests。
2. 新增 project default `agent`，删除 default Overview 依赖。
3. 移除默认 LifecycleRail；必要时在 Task Details 中显示五阶段宏观进度，不复用旧六 workspace rail。
4. 建立 `useAgentTaskController`：load latest/selected task、create、answer、approve、cancel、event cursor、AbortController、active-only polling。
5. controller 切项目时取消旧请求并清空旧 task；不得短暂显示上一个项目状态。
6. 每种 `next_action.type` 只对应一个 primary button；详情/放弃为 secondary。
7. Goal Composer 支持多行文本、提交中、错误、空目标、只读项目和重新开始确认。
8. Approval Card 显示目标、数据、执行、写入范围、安全边界、科学差异和限制；单击批准发送 summary hash。
9. 运行中显示 subject 宏观进度与阶段，不显示伪 ETA；后台运行只改变 UI 可见性，不中止 backend。
10. 完成/partial/failed 分别展示结果和限制，所有数字来自 AgentTaskResponse。
11. 把旧 pages 链接放入“查看任务详情/技术详情”；兼容期不删除。
12. Assistant Sheet 改为解释当前任务和回答文档问题，不能自己保存 task status。
13. Settings 增加默认关闭的 Advanced Mode；开启时显示科学参数风险警告。高级参数修改必须走 answer/replan/reapproval，不能只改前端本地值。

## 5. Accessibility and Information Layering

- Next Action Card 使用可聚焦 heading 和 live region；状态更新不强抢焦点。
- approval/recovery modal 打开时正确 trap/restore focus。
- progress 使用语义文本和 `aria-valuenow`；未知 total 不伪造百分比。
- loading、empty、disabled、waiting、running、partial、failure、completed、offline 全覆盖。
- 技术错误默认显示用户摘要，原始 error code 可展开查看并复制。
- English 与简体中文必须同时交付；科学 ID、路径、日志原文不机器翻译。

## 6. Acceptance Criteria

- [ ] 顶层只显示 Projects、Agent、Runs、Settings。
- [ ] 选择项目后直接进入 Agent Workspace。
- [ ] 普通模式任一时刻最多一个 primary action。
- [ ] 无手动 Dry Run、Validation、Report、Refresh、worker、GPU budget 控件。
- [ ] 旧 workspace 不出现在默认导航，但从详情仍可到达。
- [ ] 刷新页面后 task 状态完全由 backend 恢复。
- [ ] project switch、offline/reconnect 和 stale response 不串状态。
- [ ] 1024/1280/1440/1920 宽度以及 keyboard/reduced-motion 通过。
- [ ] 默认页不把 internal status 直接展示给普通用户。
- [ ] Advanced Mode 默认关闭；开关只影响可见性，任何科学/资源修改仍由 backend contract 和重新审批约束。

## 7. Hazard/Test Matrix

| Hazard | Required test |
|---|---|
| H10-09 证据不可达 | compatibility deep-link + Runs/Technical Details |
| H10-10 stale polling | AbortController、cursor duplicate、project switch |
| H10-14 frontend inference | fixture 中 task name 与真实 binding 冲突时以 API 为准 |
| fake success | completed UI 只对 completed projection；partial 独立断言 |
| multiple primary actions | DOM contract test 每种 fixture primary count <= 1 |

## 8. Validation Commands

```text
npm --prefix src/frontend run typecheck
npm --prefix src/frontend run test
npm --prefix src/frontend run build
```

实施者还需运行 backend Agent Task API focused tests，保证 UI fixture 与真实 schema 一致。GUI smoke 记录四种宽度、两种语言和 keyboard 路径。

## 9. Stop Conditions

- 10B schema 尚未冻结；
- 需要前端组合 lifecycle/ticket/run/observation 原始接口；
- 需要删除旧 workspace 才能继续；
- backend 尚未提供某字段而 UI 只能猜测；此时回到 10A/10B 修 contract；
- 出现与当前未提交前端改动的文件冲突且归属不明。

## 10. Completion Report Additions

附导航前后图、每个 task state 的主动作表、兼容 route 清单、交互计数对比、四宽度和双语 smoke、未迁移高级控件清单（必须为零或有阻塞说明）。
