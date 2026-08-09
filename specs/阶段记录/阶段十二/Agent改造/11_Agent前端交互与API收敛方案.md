# 11：Agent 前端交互与 API 收敛方案

> 状态：Draft，待人工 Review。
> 依赖：04 批量决定、05 计划版本、06 结果/恢复、08 预算、10 Trace。

## 1. 目标

把用户操作收敛为“提交目标 -> 必要时回答一个决定批次 -> 审批计划 -> 查看自动执行结果/审批恢复”。前端只显示后端权威状态，不要求用户手动触发 Harness 下一步。

非目标：不在前端运行 Planner、推断审批成功、直接访问文件系统或自动点击审批；不把完整内部 Trace 默认展示给普通用户。

## 2. 当前实现分析

- `AgentWorkspace.tsx` 已按 Goal、Current Action、Harness、Progress、Next Action、Result、Recovery 和 Details 组织页面。
- `useAgentTaskController.ts` 通过 project-scoped API 调用 create/answer/approve/cancel/recovery，并只在 `preparing/running` 时每 3 秒轮询。
- `NextActionCard.tsx` 只处理一个 `AgentTaskDecision` 和单个 answer。
- `HarnessStatusCard.tsx` 只显示 calls/proposals、最新摘要和 terminal reason。
- `AgentTaskReadModel` 已合并权威后端记录，GET/list 无副作用；应继续作为 UI 唯一状态来源。

## 3. 总体修改思路

保持现有 Agent Workspace，不重建页面。扩展 response schema 和组件：普通视图只显示当前需要用户做什么；高级详情显示 Agent 步骤、证据、计划版本、provider/fallback 和预算。

## 4. API 合同

### 4.1 修改 answer

`POST /api/projects/{project_id}/agent/tasks/{task_id}/answer` 改为 `batch_id + answers[] + command_id + actor`。后端返回完整最新 `AgentTaskResponse`。

删除旧 `decision_id + answer` 请求类型和所有当前消费者，不保留双格式解析。

### 4.2 扩展任务响应

| 字段 | 内容 |
|---|---|
| `decision_batch` | 1..6 items、推荐、影响、过期时间 |
| `plan_revision` | 当前版本、父 plan、修订原因 |
| `harness_summary` | phase、status、provider/fallback、预算、等待/停止原因 |
| `automation` | 当前 A0-A4 等级和为何需要用户 |
| `result_summary` | deterministic outcome、证据、限制、生成说明 |
| `recovery` | diagnosis、candidate、scope 变化和审批要求 |

`decisions[]` 旧字段随批次合同同步删除。

### 4.3 只读详情

新增：

```text
GET /api/projects/{project_id}/agent/tasks/{task_id}/harness?after=&limit=
```

返回脱敏 step entries、ModelCall metadata、Action/result summary 和 refs。不得返回完整 Prompt、raw response、绝对路径或凭据。Route 保持薄适配，读取通过 `ProjectStore`/Trace service。

### 4.4 错误合同

前端至少映射：batch stale/expired/incomplete、approval stale/expired、provider fallback、budget exhausted、context limit、capability denied、recovery approval stale 和 handoff。错误码/ID/hash 不翻译，用户说明走 i18n。

## 5. 前端交互

### 5.1 `DecisionBatchCard`（新增）

- 一次显示 batch 内所有 item；
- 支持单选、布尔、受限数值和短文本；
- 每项显示影响、来源和推荐标识；
- 提交前本地只做必填/类型检查，最终以服务端为准；
- 服务端字段错误保留其他输入，不自动重试 mutation。

### 5.2 Current Action

当前步骤只显示以下一种：自动准备、等待决定、等待审批、执行中、验证结果、需要恢复审批、完成、需人工处理。Harness 内部 step 不与用户主流程并列成多个按钮。

### 5.3 Approval

- Approval Summary 继续显示 goal、数据范围、nodes/backend、write roots、rawdata 只读、限制和 Memory/Skill/计划版本影响；
- 页面只提交后端 summary hash；
- summary 变化时旧对话框自动失效并要求重新 Review；
- Harness/Team 的建议不能呈现为已批准。

### 5.4 进度与结果

- `preparing/running` 保持有界轮询；等待用户和终态停止轮询；
- Observation/Goal Evaluation 阶段显示“正在验证结果”，不显示“已成功”；
- `metadata_only/partial/failed/indeterminate` 使用不同文案；
- fallback 明确显示“已转为确定性规划”，不显示连接失败后仍是模型结果；
- Recovery 卡片显示哪些 scope 不变、哪些变化、为何需要新审批。

### 5.5 高级详情

复用 `TaskDetails` 增加折叠的 Agent Activity：step、Action、provider、budget、evidence refs、plan revision、stop reason。普通模式不展示 token、hash 等内部字段。

## 6. 文件修改清单

| 文件 | 修改内容 |
|---|---|
| `schemas/agent_task.py` | 新 response、batch answer、Harness detail schema |
| `api/agent_task_routes.py` | 修改 answer，新增只读 harness detail |
| `services/agent_task_read_model.py` | 后端权威投影 |
| `lib/types/agentTask.ts` | 同步类型并删除旧 decision 类型 |
| `lib/api/agentTasks.ts` | 新 answer/detail wrapper |
| `useAgentTaskController.ts` | 批次提交、详情读取、轮询状态 |
| `NextActionCard.tsx` | 拆出 `DecisionBatchCard`，保留审批/查看动作 |
| `HarnessStatusCard.tsx`、`TaskDetails` | 状态、预算、fallback、trace 摘要 |
| `i18n/messages/en.ts`、`zh-CN.ts` | 全部新增用户文案 |
| 对应 API/client/component/controller tests | 合同和交互回归 |

## 7. 风险与处理

| ID | 风险 | 处理 | 测试 |
|---|---|---|---|
| H11-01 | UI 本地状态覆盖后端 | mutation 后使用完整 response，刷新以 GET 为准 | stale local state 测试 |
| H11-02 | 旧 summary 被批准 | 每次点击提交当前 hash，后端重建校验 | summary drift |
| H11-03 | 多个内部 step 增加操作量 | 主视图只显示一个 next action | 交互快照测试 |
| H11-04 | 轮询不停止 | 仅 preparing/running | waiting/terminal timer 测试 |
| H11-05 | 高级详情泄露 | 脱敏 API，不下发 raw prompt/path | client fixture 检查 |
| H11-06 | 英文摘要解析状态 | 新增结构化字段，不增加正则 | en/zh 非英文后端摘要测试 |

## 8. 测试与验收

```powershell
python -m pytest tests/unit/test_agent_task_api.py tests/unit/test_agent_task_read_model.py --tb=short --basetemp=.pytest_tmp
npm --prefix src/frontend run format:check
npm --prefix src/frontend run typecheck
npm --prefix src/frontend run test
npm --prefix src/frontend run build
```

人工验收：常规任务页面最多出现一次决定批次和一次执行审批；审批后无需点击“继续”；刷新、切项目和重启后状态一致；中英文均能区分自动处理中、等待用户、失败、恢复和完成。

## 9. 实施顺序

1. 冻结后端 schema/API；
2. 修改 read model 和 contract tests；
3. 同步 TypeScript types/client；
4. 实现 DecisionBatchCard；
5. 更新 Harness/Result/Recovery/Details；
6. 修改 controller polling 和错误映射；
7. 补双语、a11y、project switch 和 stale tests；
8. build 后进行人工工作流 Review。
