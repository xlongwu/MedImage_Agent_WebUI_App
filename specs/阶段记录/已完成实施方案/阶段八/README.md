# 阶段八：Observation、Goal Evaluation 与 Recovery 闭环

> 归档状态：该文档对应的当前阶段范围已完成；仅作为历史实施与审计记录保留。

> 状态：8A–8D 源码实施完成并通过回归验证；发布级打包真实 E2E 转入阶段九。
> 规划日期：2026-07-14
> 基线：当前工作树中的阶段七 Execution Gateway、Execution Ticket、Agent Lifecycle 与 Node Contract 实现；阶段七 focused 与 integration 基线已于 2026-07-14 验证，Windows symlink 用例因本机权限跳过并保留为环境风险。

## 文档导航

| 顺序 | 文档 | 交付目标 | 前置条件 |
|---|---|---|---|
| 0 | [总体计划](阶段八_Observation_GoalEvaluation_Recovery闭环总体计划.md) | 定义端到端闭环、共同模型、关卡、风险与验证矩阵 | 阶段七验收证据可用 |
| 8A | [统一 Observation Model](任务8A_统一ObservationModel.md) | 将 Pipeline、Node、Artifact、Validation、Logs、Capability 与 Scientific Status 汇总成可追溯事实快照 | 阶段七生命周期、运行历史和节点契约基线稳定 |
| 8B | [Goal Evaluator](任务8B_GoalEvaluator.md) | 用 Reviewed Goal Contract 判断用户目标是否真正满足 | 8A |
| 8C | [Recovery Proposal Engine](任务8C_RecoveryProposalEngine.md) | 依据观察、诊断、Node Contract 和配额生成结构化恢复候选 | 8A、8B |
| 8D | [受控 Retry 与局部 Replan](任务8D_受控Retry与局部Replan.md) | 用派生票据、重新审批和新 Reviewed Plan 执行受控恢复并再次评估 | 8A–8C |

## 实施顺序

8A–8D 已随 `17e3ebac` 落地，并在 `52f183bc` 基线上完成相关回归验证。Observation、Goal Evaluation、Recovery Proposal 与受控 Retry/Resume/Replan 的源码契约已冻结；阶段九负责在 Windows 打包应用和真实多受试者数据上验证退出、崩溃、恢复与局部重试。
