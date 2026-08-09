# 10：Agent Trace、Replay 与评测体系方案

> 状态：Implemented，已完成源码、focused backend 回归与人工 Review 待办；不代表发布或科学验证。
> 依赖：03 Action result、05 计划版本、06 结果/恢复、07 Context、08 ModelCallRecord、09 Skill refs。

## 1. 目标

让 Reviewer 能从一个 lifecycle 重建 Agent 每一步使用的证据、模型路径、动作、handler 结果、状态变化和停止原因；并用固定数据集比较新旧 Planner/Prompt/Skill 是否退化。

非目标：Replay 不调用真实模型、不执行 handler、不重跑科学计算；评测分数不自动发布生产策略，也不证明科学算法 validated。

## 2. 当前实现分析

- attempt/context/step 和 lifecycle events 已持久化，但没有统一 trace 视图或链式完整性校验。
- `tests/fixtures/agent_harness_replay.json` 包含 30+ 中英文动作/state 安全案例；`test_agent_harness_replay.py` 主要校验 Action 是否被 capability catalog 允许。
- 当前测试无法从记录重建 context -> action -> result -> lifecycle transition，也无法证明 replay 零副作用。
- Agent Task events 已合并多个权威来源，可作为用户时间线，但不包含完整 Harness 审计字段。

## 3. 总体修改思路

不复制现有表。新增只读 `AgentTraceService`，按 ID/hash 引用 attempt、context、steps、lifecycle events、plan、ticket、run、Observation、Evaluation 和 Recovery，生成 `AgentTraceBundle`。Replay 只运行纯校验/reducer，比较记录中的预期状态和 hash。

## 4. Trace 结构

```text
AgentTraceBundle
├─ trace_id / project_id / lifecycle_id
├─ policy/prompt/skill/provider versions
├─ attempt summary
├─ entries[]
│  ├─ context_ref/hash
│  ├─ model_call metadata
│  ├─ action envelope/hash
│  ├─ validation result
│  ├─ action result/hash
│  ├─ lifecycle event refs
│  └─ evidence/plan/run/observation/recovery refs
├─ final state/outcome/stop reason
└─ integrity_hash
```

Bundle 默认只含安全摘要和引用；原始 Prompt、原始模型响应、绝对路径、秘密和影像数据不进入导出。

## 5. 详细实施方案

### 5.1 Trace 组装

`AgentTraceService.get(project_id, lifecycle_id)`：

1. 校验 project scope；
2. 读取 lifecycle、attempt 和按 step_no 排序的 steps；
3. 解析每个 context/action/result/model call 引用；
4. 合并关联 lifecycle events；
5. 校验 plan/ticket/run/Observation/Evaluation/Recovery binding；
6. 标记 missing、stale、conflict，不伪造缺失记录；
7. 生成 canonical integrity hash。

### 5.2 无副作用 Replay

新增 `AgentReplayService.replay(bundle)`，只执行：

- schema 和版本校验；
- capability/state 校验；
- typed refs 和 hash 校验；
- step idempotency/顺序校验；
- lifecycle event reducer；
- final state、budget 和 stop reason 比较。

依赖注入 spy 必须证明 provider、Evidence Service、Planner、Approval、Gateway、runner、filesystem 均未调用。

### 5.3 固定评测集

在 `tests/fixtures/agent_eval/` 建立版本化案例：

| 类别 | 至少场景 |
|---|---|
| 正常 | 直接规划、批量决定、plan-only、成功执行 |
| 恢复 | partial、failed、不可重载、恢复审批、handoff |
| provider | 缺 key、timeout、invalid JSON、repair、fallback |
| 安全 | 伪造审批、外部路径、未知 action、跨项目 ref、prompt injection |
| 稳定性 | 重复命令、双 claim、崩溃恢复、stale result、取消 |
| 双语 | 中英文目标和决定，核心路由结果一致 |

每个案例包含输入 fixture、预期 stop point、允许 Action、禁止调用、关键 hash/状态断言，不保存真实研究数据。

### 5.4 评测指标

- goal routing 正确率；
- 必要问题召回率和误提问率；
- 自动到达审批/结果的比例；
- 不安全 Action 拒绝率；
- 旧审批/跨项目/重复执行拦截率；
- schema repair/fallback 比例；
- step/model/token/latency；
- 恢复建议与 reference policy 一致率；
- 单次任务人工交互次数；
- single-agent 基线回归。

指标只用于版本比较；没有定义参考答案的案例不输出“质量通过”。

### 5.5 Trace API 和保留

只向高级详情提供脱敏 summary/entries 分页。完整内部 bundle 默认仅测试和受控导出使用。保留期沿用项目状态/审计策略；忘记 Memory 不删除执行审计，但 Trace 中被忘记的 Memory 正文只保留 tombstone/ref。

## 6. 数据结构变化

| 结构 | 变化 |
|---|---|
| `AgentTraceBundle/Entry` | 新增只读投影，不建第二权威表 |
| `AgentReplayResult` | integrity、state、budget、violations |
| Harness step/context | 补足关联 refs/hash |
| eval manifest | fixture 版本、输入 hash、期望、标签 |

## 7. 文件修改清单

| 文件 | 修改内容 |
|---|---|
| `services/agent_trace_service.py`（新增） | Trace 组装和脱敏 |
| `services/agent_replay_service.py`（新增） | 纯 replay/reducer |
| `schemas/agent_trace.py`（新增） | Bundle/Entry/ReplayResult |
| `services/agent_task_read_model.py`、API routes | 可选脱敏 trace 详情 |
| `tests/fixtures/agent_eval/`（新增） | 固定回归集 |
| `tests/unit/test_agent_harness_replay.py` | 从 allowlist 测试升级为真实 replay |
| `tests/unit/test_agent_trace_service.py`（新增） | binding、缺失、脱敏、分页 |

## 8. 风险与处理

| ID | 风险 | 处理 | 测试 |
|---|---|---|---|
| H10-01 | Trace 成为第二事实源 | 只读投影，全部字段引用权威记录 | 修改源记录后冲突可见 |
| H10-02 | Replay 产生副作用 | 纯 reducer，无业务 handler 依赖 | 全依赖 spy=0 |
| H10-03 | 导出泄露 Prompt/PHI | 安全 schema + redaction | secret/path/raw response fixture |
| H10-04 | 指标鼓励绕过安全 | 安全门槛先于效率指标 | unsafe fast case 必须失败 |
| H10-05 | fixture 过拟合 | 中英、故障、对抗和边界分层 | 未见案例 holdout |
| H10-06 | hash 链缺记录 | 明确 `incomplete`，不能自动补齐 | 删除中间 step 测试 |

## 9. 测试与验收

```powershell
python -m pytest tests/unit/test_agent_harness_replay.py tests/unit/test_agent_trace_service.py tests/unit/test_agent_harness_execution_boundary.py tests/integration/test_agent_harness_lifecycle.py --tb=short --basetemp=.pytest_tmp
```

验收：任一评测任务能在无网络、无模型、无文件写入和无 Gateway 调用下 replay；篡改 context/action/result/plan hash 会被定位到具体 entry。

## 10. 实施顺序

1. 冻结 Trace/Replay schema；
2. 补齐 step/context 关联字段；
3. 实现只读 TraceService；
4. 实现纯 ReplayService；
5. 迁移现有 30+ 安全 fixture；
6. 增加端到端评测集和指标脚本；
7. 接入高级只读 API；
8. 建立策略变更前的固定回归 Gate。

## 11. 实施结果（2026-08-09）

- 新增 `AgentTraceBundle`/`AgentTraceEntry`/`AgentReplayResult` 只读 schema；Trace
  从已有 lifecycle、Harness、plan、ticket、run、Observation、Evaluation 与 Recovery
  记录引用组装，未创建第二权威表。缺失记录显式为 `incomplete`，跨项目绑定显式为
  `conflict`，不会自动补齐。
- `AgentReplayService` 没有 store 或运行时依赖，只复算 integrity hash、引用、step
  顺序/幂等、capability/state、lifecycle reducer 与 budget；测试证明其不会触发模型、
  handler、Gateway、runner 或文件写入。
- 既有 30+ 中英文 safety corpus 现同时经纯 Replay runner 断言；
  `tests/fixtures/agent_eval/v1/manifest.json` 提供版本化、无真实研究数据的正常、恢复、
  provider、安全和稳定性固定 oracle，`AgentEvaluationService` 汇总比较指标但不自动
  更改策略。
- 新增高级、分页、只读 Trace API：
  `GET /api/projects/{project_id}/agent/tasks/{task_id}/trace`。它只返回脱敏 entry，
  不调用 scheduler、provider、Planner、审批、Gateway 或 runner。
- 本次 focused 验证：
  `python -m pytest tests/unit/test_agent_trace_service.py tests/unit/test_agent_harness_replay.py tests/unit/test_agent_evaluation_service.py tests/unit/test_agent_harness_execution_boundary.py --tb=short --basetemp=.pytest_tmp`，67 passed。
