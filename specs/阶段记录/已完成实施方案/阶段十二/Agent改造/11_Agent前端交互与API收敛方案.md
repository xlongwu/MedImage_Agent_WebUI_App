# 11：Agent 前端交互与 API 收敛方案

> 归档状态：本文于 2026-10-02 随阶段十二 `Agent改造` 整目录归档，只记录当时的范围与验收依据；
> 当前行为一律以源码、测试、`PROJECT_STATE.md` 和专项文档为准，需要人工取证或人工拍板的条目集中在
> `specs/待人工审核校验清单.md`。

> 归档审计（2026-10-02）：本文声明的源码范围已闭环（公共投影、只读端点、错误码 i18n 与
> 批次表单的前后端消费者齐备，且 `features/agent/` 不再以正则解析后端英文摘要）。
> 归档**不外推为发布结论**：§8 的人工验收场景仍属 ③ 层未取证，见
> `specs/待人工审核校验清单.md` A-11、P-03。相邻的单一 `TaskCard` 信息层级（阶段十二
> 简洁方案 §5.2）已于同日按清单 D-06 的拍板落到源码，结论写在
> `Agent简洁优化实施方案.md` 标头，不在本文重复。当前行为以源码、测试和 `PROJECT_STATE.md` 为准。

> 状态：已实现（2026-10-01 源码复核；§5.3 前端英文解析的删除于当日完成）。证据层级为源码 +
> 前后端 focused 回归，不含 packaged/GUI 人工验收。
> 已落地：`answer` 已切换为 `batch_id + answers[] + command_id + actor` 且旧单项 `decision_id` 合同已删除；
> 公共投影含 `decision_batch`、`automation`、`harness_summary`、`result_explanation`、`recovery`、
> `evidence_links`、`technical_details`；只读详情端点 `GET .../agent/tasks/{id}/trace` 与 `/harness`；
> 错误合同按 `AGENT_DECISION_*`、`APPROVAL_SUMMARY_*`、`*_BUDGET_EXHAUSTED`、
> `AGENT_HARNESS_CAPABILITY_DENIED` 等稳定码走 i18n；`DecisionBatchCard`/`DecisionBatchForm` 与
> 有界轮询已接入。
> 与本文的偏离：状态码字段实名 `current_action_code`（10 值稳定 Literal），不是 §4.2 假设的
> `status_code`；本轮另补 `task_kind` 与 `execution_performed`。`next_action` 不再携带
> `title`/`description`/`disabled_reason`（后者后端从未产生），文案由前端按 `type` 映射 i18n。
> 审批范围不再下发 `dataset_summary`/`execution_summary` 英文句子，改为
> `registered_subject_count`/`selected_subject_ids`/`node_ids`；结果改为 `summary_code`、
> `limitation_codes`、`validation_checks_passed/failed`、`recommended_action_code`、
> `export_disabled_code`。前端已删除英文整句查表与计数正则（原 `agentTaskMessages.ts`），不再用
> `reviewed_plan` artifact 推断 plan-only。
> 未由本文覆盖但仍开放的是 `Agent简洁优化实施方案.md` §5.2 的单一 `TaskCard` 合并。
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

人工验收项已移入 `specs/待人工审核校验清单.md` A-11（该行为唯一权威）。

## 9. 实施顺序

1. 冻结后端 schema/API；
2. 修改 read model 和 contract tests；
3. 同步 TypeScript types/client；
4. 实现 DecisionBatchCard；
5. 更新 Harness/Result/Recovery/Details；
6. 修改 controller polling 和错误映射；
7. 补双语、a11y、project switch 和 stale tests；
8. build 后进行人工工作流 Review。
