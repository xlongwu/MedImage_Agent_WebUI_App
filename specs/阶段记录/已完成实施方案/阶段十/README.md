# 阶段十：Agent-first 前端与交互收敛

> 归档状态（2026-10-01 审计）：10A–10F 的源码与自动化验收范围已完成，按 `AGENTS.md` §7.6
> 与 `specs/阶段记录/已完成实施方案/README.md` 归档为历史实施记录。本目录不再是当前待办，
> 也不得作为当前行为来源。
>
> 归档边界：下表“仍未完成的证据”中，packaged、可见 Electron 三流程与重启投影已由阶段十五
> 的 clean exact-SHA 验收（2026-08-25）覆盖；installer、版本面 `v0.6.0-rc2`、tag 与发布
> 仍属独立的 Release 任务，记录在 `PROJECT_STATE.md` “Next Work”。
>
> 状态：Source implemented — packaged/release acceptance deferred
>
> 版本边界：当前版本面仍为 `v0.6.0-rc1`；阶段十尚无 exact-SHA、打包或发布版本声明。
>
> 主任务模式：Feature Bundle Mode + Architecture and Refactor Mode。
>
> 涉及受控执行、DICOM 转换或真实科学链路时，叠加 Scientific Validation Mode 与受保护模块规则。

> 2026-07-16 实施记录：维护者明确授权开展源码实现。10A–10E 与 10F 的 source/docs
> 子范围已经实现并通过当时的源码验证；exact-SHA 打包、可见 Electron 工作流、安装器和
> 发布对齐按维护者指示延期。当前工作树位于 `main` 且包含大量未提交改动，不能继续引用
> 原 feature branch 名称或把当时测试结果当作当前 clean-SHA 证据。

本阶段把项目内默认操作模式从“用户逐页驱动流水线”收敛为“用户描述目标，Agent 生成受控计划并在一次集中审批后推进”。Agent Task 只是既有 Agent Lifecycle、Reviewed Plan、Execution Ticket、Observation、Goal Evaluation、Recovery 和 Artifact Evidence 的前端投影，不保存第二份流程状态。

## 当前完成边界

| 子任务 | 当前状态 | 仍未完成的证据 |
|---|---|---|
| 10A Agent Task 投影 | 源码与自动化测试已实现 | packaged UI 复验 |
| 10B 目标、决策与集中审批 | 源码与自动化测试已实现 | clean-SHA 发布候选复验 |
| 10C Agent Workspace | 源码与前端自动化已实现 | 可见 Electron 人工流程 |
| 10D 自动观察与恢复建议 | 源码与自动化测试已实现 | 打包退出、强杀、重启 smoke |
| 10E DICOM 网关收敛 | 受控源码合同已实现 | 打包可见 UI 与新 rawdata manifest |
| 10F E2E | source/docs 验收完成 | exact-SHA、packaged UI、installer、发布对齐 |

详细事实见[阶段十验收记录](evidence/阶段十_E2E验收记录.md)。各任务文档保留的是实施前
要求和验收设计；其顶部当前状态覆盖正文中的历史 `Current Gap`、阻塞条件和启动关卡。

## 历史启动前置条件

以下是实施启动时的关卡，现作为历史决策记录保留：

1. 阶段九 `G9-6` 已关闭，RC2 已发布或维护者明确终止 RC2 主线；
2. 为阶段十创建独立 `codex/` 分支或 worktree；
3. 当前未提交的前端改动已经归属清楚，不与阶段十实现并行编辑；
4. 维护者确认 DICOM 路径是纳入 10E，还是先只交付 BIDS Agent-first 流程；
5. 维护者确认生命周期最小扩展（等待补充信息、等待科学决策、执行前取消）可以进入 `v0.7.0-rc1`。

## 文档导航

1. [阶段十总体计划](阶段十_Agent-first前端与交互收敛总体计划.md)
2. [任务 10A：交互基线与 Agent Task 投影层](任务10A_交互基线与AgentTask投影层.md)
3. [任务 10B：目标编排、科学决策与集中审批](任务10B_目标编排科学决策与集中审批.md)
4. [任务 10C：Agent Workspace 与导航收敛](任务10C_AgentWorkspace与导航收敛.md)
5. [任务 10D：自动观察、结果、恢复与高级证据](任务10D_自动观察结果恢复与高级证据.md)
6. [任务 10E：DICOM 执行入口收敛](任务10E_DICOM执行入口收敛.md)
7. [任务 10F：真实 E2E 与交互验收](任务10F_真实E2E与交互验收.md)

## 已执行的实施顺序

```text
阶段九 G9-6
  -> 10A 投影层与基线
  -> 10B BIDS 目标编排与审批
  -> 10C Agent-first Shell
  -> 10D 自动观察、结果与恢复
  -> 10E DICOM Gateway 收敛（独立保护性关卡）
  -> 10F BIDS / DICOM / Recovery 真实 E2E
```

10C 可以在 10B API 契约冻结后开始，但同一任务仍遵守单一代码所有者规则；不允许多个实现 Agent 同时编辑同一分支。10E 失败不得阻止 BIDS Agent-first 能力按真实范围验收，但会阻止宣称“DICOM 一句话完成”已交付。

## 仍有效的冻结边界

- 新公共 Agent Task API、生命周期 schema、执行入口、前端默认导航和持久化变化均属于
  能力扩展；当前源码存在不等于已进入 RC2 发布线。
- 本阶段不得借交互收敛修改科学公式、能力等级或 rawdata 只读边界。
