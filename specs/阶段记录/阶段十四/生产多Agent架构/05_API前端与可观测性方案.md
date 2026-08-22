# 多 Agent API、前端与可观测性方案

> 状态：Proposed；G5 实施依据

## 1. 公共 Contract 原则

- Agent Task 仍是唯一用户入口；不新增独立 `/teams` 顶层产品。
- 后端状态权威，前端不得用本地并行 promise 推断 Worker 或 Team 已完成。
- 写命令使用 command ID 幂等；GET/list 只读且无 reconcile、claim 或 scheduler notify。
- 用户可见文案使用 i18n message key；ID、hash、role_id、error code 保持机器值。
- contract 直接切换到当前格式并同步全部消费者，不保留旧字段 fallback。

## 2. 请求 Contract

在 `CreateAgentTaskRequest` 增加：

```text
planning_mode: "single_agent" | "multi_agent" = "single_agent"
```

首期不接受 `auto`、自定义角色、team size、prompt、tool、provider URL 或任意 budget dict。预算来自批准的 ConfigService + project consent，响应返回实际生效的只读预算摘要。

更新 goal 或回答决定时不得切换 planning mode。若用户要从失败 Team 改为 single，创建新的 single Agent Task；原 lifecycle、Team、findings 和 events 保持 sealed/read-only，不新增当前状态机不支持的 replan 出边，也不复用 Team findings 或旧 Approval Summary。前端可以预填原 goal，但必须明确这是新 task ID。

## 3. Read Model

在 `AgentTaskResponse` 增加可选 `team` 投影：

```text
AgentTaskTeamSummary
├─ team_id
├─ planning_mode
├─ status
├─ required_roles[]
├─ completed_roles[]
├─ failed_roles[]
├─ blocking_finding_count
├─ warning_finding_count
├─ conflict_count
├─ budget_used / budget_limit
├─ started_at / updated_at / completed_at?
├─ advisory_hash?
└─ attention_code?
```

该投影从 canonical Team records 派生，不单独持久化。`public_state`、`next_action` 和 automation level 仍由 Agent Task read model 统一计算。

## 4. 路由

在现有 project-scoped Agent Task domain router 下增加只读详情：

| Method | Path | 作用 |
|---|---|---|
| GET | `/api/projects/{project_id}/agent/tasks/{task_id}/team` | Team 摘要、角色状态、预算和 attention |
| GET | `/api/projects/{project_id}/agent/tasks/{task_id}/team/findings` | 分页返回已验证 finding 安全投影 |
| GET | `/api/projects/{project_id}/agent/tasks/{task_id}/team/events` | 分页返回系统事件，不返回 prompt/response 明文 |

不增加 Worker 直接调用路由、手工 complete route、自由 message route 或绕过 Agent Task 的 Team create route。

Route 只处理 schema、Depends 和 `raise_api_error()` 映射；业务规则全部在 read/command service。

## 5. 前端交互

### 5.1 创建任务

- 默认选择“单 Agent”。
- 仅当后端 capability 显示全局启用且项目已 consent，才显示“多 Agent 独立审查”。
- 选择多 Agent 时展示：固定三个只读角色、预算上限、数据类别摘要、不会执行计算的说明。
- disabled 原因由后端结构化 code 映射，不显示通用“服务不可用”。
- 项目 consent 设置显示当前 consent epoch、允许的数据类别和预算；撤销会阻止后续 provider 调用，但不删除历史审计记录。

### 5.2 Team Activity 卡片

在 Agent Workspace 中显示一个从属卡片：

- Team 准备中、三个 reviewer 的 queued/running/completed/failed 状态。
- blocking/warning/conflict 数量。
- 实际调用量、token 和耗时摘要。
- `needs_attention` 时显示后端权威下一步。
- 默认折叠 finding 详情；详情只显示 message key 映射、severity、角色和安全 evidence ref。
- 不显示模型思维链、完整 prompt、provider response 或原始研究数据。

### 5.3 必备 UI 状态

| 状态 | 表现 |
|---|---|
| loading | skeleton，不推断本地 Team |
| unavailable/disabled | 解释全局 flag、consent、provider 或 G0 Gate 原因 |
| empty | single Agent 任务显示“未启用多 Agent 审查” |
| running | 角色级状态和有界轮询 |
| partial/needs_attention | 明确未完成安全审查，不显示通过徽章 |
| completed | 显示 advisory 已冻结，不等同于任务执行完成 |
| canceled/failed | 显示结构化原因和后端允许动作 |

## 6. 前端文件规划

| 动作 | 文件 |
|---|---|
| MODIFY | `src/frontend/src/lib/types/agentTask.ts` |
| MODIFY | `src/frontend/src/lib/api/agentTasks.ts` |
| MODIFY | `src/frontend/src/features/agent/useAgentTaskController.ts` |
| CREATE | `src/frontend/src/features/agent/components/AgentTeamActivityCard.tsx` |
| CREATE | `src/frontend/src/features/agent/components/AgentTeamFindingList.tsx` |
| MODIFY | `src/frontend/src/features/agent/AgentWorkspace.tsx` |
| MODIFY | `src/frontend/src/i18n/messages/en.ts` |
| MODIFY | `src/frontend/src/i18n/messages/zh-CN.ts` |

所有新增组件需要对应 Vitest/Testing Library 测试；不得在 `App.tsx` 堆积 Team 业务状态。

## 7. Trace、Replay 与审计

`AgentTraceBundle` 增加安全摘要引用：team、snapshot、work item、model call、finding、advisory 和 Team event。Trace 只能输出：

- ID、hash、role/status、固定 error code、时间、token/latency 和 source refs；
- context included/omitted sections；
- finding code/severity/message key/ref；
- advisory accepted/rejected refs 和 conflict code。

禁止输出完整 prompt、完整 response、suggested_change 明文（除非已通过单独 redaction）、rawdata 路径和 credential。

Replay 只验证 schema/hash、状态 reducer、幂等键、lease/fencing、role coverage、finding refs、聚合顺序和 advisory/plan binding；不得调用 provider、planner、store 写入或执行面。

## 8. 运营指标与告警

在现有 Agent Operations summary 增加：

| 指标 | 说明 |
|---|---|
| `team_requested_count` | 显式多 Agent 任务数 |
| `team_started/completed/failed/attention_count` | Team 终态分布 |
| `team_role_timeout_count` | 分角色 timeout |
| `team_conflict_count` | 聚合冲突数 |
| `team_blocking_finding_rate` | Team 发现阻断问题比例 |
| `team_calls/input_tokens/p50/p95_latency` | 成本与延迟 |
| `team_lease_takeover_count` | 恢复和调度健康 |
| `team_to_plan_binding_violation_count` | advisory/plan hash 不一致，必须为 0 |

首期告警：safety reviewer 失败、context drift、跨项目拒绝、重复 finalize、lease takeover 突增、p95 超 Gate、成本超预算、Team completed 但无 planning wake。

## 9. API/前端测试

- create request 默认 single，非法 `auto`/未知模式拒绝。
- explicit multi 仍须通过确定性 eligibility；ineligible 时返回 reason code 且 Worker 调用为 0。
- Team disabled/consent missing/provider unavailable 的结构化错误映射。
- project A 无法读取 project B 的 team/findings/events。
- GET/list store spy 证明无状态迁移和调度副作用。
- response type、client wrapper、controller、组件和 en/zh-CN 同步。
- running/empty/disabled/completed/attention/canceled 状态全部覆盖。
- task 终态后轮询停止；切换 project 时立即清空旧 Team 投影。
- Team 失败切回 single 会创建新 task，旧 task 不发生非法状态回迁。
- 不渲染 prompt、provider response、rawdata path 或未脱敏 suggested text。

## 10. 退出条件

- [ ] 后端 schema、route、service、前端 type/client/caller/i18n 同一任务完成。
- [ ] Read Model 只派生，不形成第二状态源。
- [ ] UI 不把 Team completed 表示成 task execution completed。
- [ ] 所有读路由 project-scoped、分页、只读且脱敏。
- [ ] 运营指标可区分 single 与 Team，所有 alert 有固定 code 和测试。
