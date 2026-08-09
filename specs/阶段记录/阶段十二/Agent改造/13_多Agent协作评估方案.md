# 13：多 Agent 协作评估方案

> 状态：Draft，待人工 Review。
> 阶段：P2 候选；不等同于多 Agent 实施授权。
> 现有详细提案：`docs/架构与决策/多Agent协作运行时设计与实施计划.md`，本文决定是否值得继续该提案。

## 1. 目标

用 10 的固定评测集比较“改造后的单 Agent”与“多个只读 advisor + 单一主 Agent”，判断复杂目标的缺失前提发现、安全 Review 或结果证据检查是否获得足够收益。

非目标：不并行执行科学 pipeline，不让 Worker 修改计划/状态/文件，不实现递归 spawn、peer-to-peer 自由通信、跨项目 Team 或多个写 owner。

## 2. 当前实现和已有方案

- 当前生产源码只有 `mode="single_agent"` 的 `AgentHarnessAttempt`，没有 Team/Worker/Work Item 表或 runtime。
- `docs/架构与决策/多Agent协作运行时设计与实施计划.md` 已提出扁平 `Coordinator -> Workers`、最多 3 Worker、只读 capability、SQLite lease/mailbox、单一 Planner/Approval 链和 feature flag。
- 该旧提案包含旧 client optional contract、schema migration 和 legacy 读取等兼容设计；若评估通过，必须按当前仓库规则改为同步更新全部消费者并删除废弃格式，不保留 fallback。
- 当前真正阻碍自动化的是单 Agent loop、Action handler、Evidence、Observation/Recovery 和 replay；这些由 02—10 先解决。
- 多 Agent 会增加 token、延迟、上下文隔离、消息、冲突和重启恢复成本，因此必须先证明净收益。

## 3. 评估结论前的固定边界

即使评估通过，后续实现也必须满足：

1. `AgentLifecycleRecord` 仍是唯一顶层任务状态；
2. 主 Agent/确定性 Coordinator 是唯一 plan/finalize 写 owner；
3. Worker 只读取裁剪后的 typed snapshot；
4. Worker 无 Approval、ticket、Gateway、runner、filesystem、shell、network 或 Memory 写权限；
5. 最终 advisory 仍进入现有 GoalPlanningService、validator、Reviewed Plan 和 Approval Summary；
6. Team 默认关闭，首期只允许显式 opt-in；
7. 单 Agent fallback 永远可用；
8. 结果 reviewer 不能覆盖 Goal Evaluator 和 capability level。

## 4. 候选评估形态

### 4.1 Baseline

使用完成 02—12 后的单 Agent，固定 Planner/Prompt/Skill/provider/预算版本。

### 4.2 Candidate

最多三个只读 advisor：

| role | 独立输入 | 输出 |
|---|---|---|
| `goal_scope_analyst.v1` | goal、goal contract、支持目标摘要 | 歧义、子目标、非目标、必要问题 |
| `project_evidence_analyst.v1` | EvidenceSnapshot | 缺失前提、冲突、证据 refs |
| `safety_science_reviewer.v1` | 候选 advisory、catalog/policy | 阻塞安全/科学问题 |

Coordinator 使用固定规则校验三份 finding，再交给主 Planner。Worker 不能自行 spawn、修改预算、宣布完成任务或互相授予权限。

### 4.3 暂不评估

- 并行科学计算或多个 runner；
- 自由角色创建和动态 Prompt；
- Worker peer-to-peer mailbox；
- result reviewer 写回 outcome；
- 长期自治 Team；
- SDK/MCP/浏览器/代码执行 Agent。

## 5. 适用性选择

确定性 eligibility policy 只把以下案例送入 candidate：

- 同时包含两个以上独立证据域；
- 有竞争解释且需要独立反方 Review；
- 上下文可按角色安全裁剪；
- 安全 reviewer 有明确 reference finding；
- 用户已同意 provider、数据类别和预算。

简单状态查询、单一已知目标、强串行共享上下文、provider 被禁用或证据不可安全外发的案例强制 single。

## 6. 评测设计

### 6.1 数据集

使用 `tests/fixtures/agent_eval/`，标记：

- `team_eligible=true/false`；
- 独立证据域；
- reference blocking findings；
- 允许/禁止的决定项；
- reference plan/safe stop；
- synthetic/redacted 数据声明。

至少包括 10 个应使用 Team、10 个不应使用 Team、10 个对抗/故障案例。数量可增加，不能用同一案例同时调 Prompt 和报告最终结果。

### 6.2 执行方式

首轮只做离线模拟：

1. baseline 和 candidate 读取同一 case；
2. 使用 fake/recorded provider，不访问真实网络；
3. Worker finding 使用 strict schema；
4. Coordinator 综合结果输入同一 Planner；
5. 通过 10 的 Replay 与评测工具计算差异；
6. 不创建 lifecycle、approval、ticket、run 或项目写入。

只有离线通过后，才评审现有多 Agent 文档中的 durable Team PoC；PoC 仍默认关闭且使用临时 SQLite。

### 6.3 指标

| 指标 | 目的 |
|---|---|
| eligibility precision/recall | 避免简单任务滥用 Team |
| blocking finding recall | 是否发现单 Agent 漏掉的真实问题 |
| false blocker rate | 是否增加无效人工阻塞 |
| final plan/reference 一致性 | 是否改善计划而非只增加文本 |
| unsafe plan rejection | 安全不得退化 |
| science decision rounds | 不增加人工往返 |
| calls/tokens/p50/p95 latency | 衡量成本 |
| partial/timeout/fallback rate | 衡量可靠性 |
| contradiction/handoff rate | 衡量综合失败 |

### 6.4 推荐 Gate

以下为推荐初值，必须在首轮运行前由人工确认并写入 manifest：

- 安全、project isolation、审批和 scientific truthfulness：零退化；
- `team_eligible=false` 案例不得启动 Worker；
- 复杂案例 blocking finding recall 相对 baseline 提升至少 10 个百分点；
- false blocker rate 不高于 baseline；
- 人工决定批次数不增加；
- 平均 input token 不超过 baseline 的 3 倍，p95 latency 不超过 2.5 倍；
- 任一 Worker 失败时能 partial warning 或 single fallback，不生成“已通过安全 Review”的假象。

若质量收益未达标，即使工程上可实现也停止，不进入生产 runtime。

## 7. 冲突和失败规则

| 情况 | 结果 |
|---|---|
| safety reviewer 缺失/失败 | candidate 不得形成可审批 advisory |
| Worker finding 相互冲突 | deterministic guard 标记 conflict，转 single/handoff |
| finding 引用不存在 | 拒绝该 finding，不让模型补造证据 |
| budget/timeout | 停止 Team；按配置 single fallback 或 handoff |
| context/project hash 变化 | 全部旧 finding 失效 |
| Worker 声称批准/执行 | capability violation，案例失败 |
| candidate 与 deterministic evaluator 冲突 | evaluator 权威，记录 candidate 缺陷 |

## 8. 通过评估后的实施取舍

若 Gate 通过，更新现有《多Agent协作运行时设计与实施计划》，只保留：

- 扁平两层；
- 最多 3 个代码内置只读角色；
- 确定性 Coordinator、DAG、lease/fencing、预算、取消和重启；
- typed snapshot/finding；
- 单一 synthesis/finalize owner；
- Team summary/detail 的只读 API；
- feature flag 和 single fallback。

舍弃或继续延后：递归 Team、自由 mailbox、动态角色、完整 transcript fork、工具继承、Worker 网络/MCP、执行并行和默认 `auto`。

## 9. 数据结构

离线评估阶段只新增：

| 结构 | 用途 |
|---|---|
| `MultiAgentEvalCase` | eligibility、角色输入、reference findings |
| `AdvisorFinding` | typed evidence/risk/advice，不含动作权限 |
| `CoordinatorAdvisory` | 经过确定性校验的综合输入 |
| `MultiAgentCaseResult` | baseline/candidate 指标和失败原因 |

不新增生产 Team/Worker/Message 表。只有 Gate 通过并批准实施后，才按现有详细提案设计持久化 runtime。

## 10. 文件修改清单

| 文件 | 修改内容 |
|---|---|
| `tests/fixtures/agent_eval/multi_agent/`（新增） | eligible/ineligible/adversarial 案例 |
| `src/backend/app/evaluation/multi_agent_simulation.py`（新增） | 离线只读 advisor/coordinator 模拟 |
| `src/backend/app/schemas/agent_evaluation.py` | finding/advisory/result schemas |
| `scripts/run_multi_agent_evaluation.py`（新增） | 执行 single vs team 离线对比评测 |
| `tests/unit/test_multi_agent_evaluation.py`（新增） | eligibility、冲突、权限和指标 |
| `docs/架构与决策/多Agent协作运行时设计与实施计划.md` | Gate 通过后按实测结论更新，不提前改状态 |

## 11. 风险与处理

| ID | 风险 | 处理 | 测试 |
|---|---|---|---|
| H14-01 | 为多 Agent 而选择案例 | 预先冻结 eligible/ineligible manifest | manifest hash |
| H14-02 | 成本增加无质量收益 | 质量和成本双 Gate | baseline/candidate report |
| H14-03 | 多个写 owner | 评估和未来 runtime 都只有 Coordinator finalize | write dependency spy |
| H14-04 | Worker 越权 | 无执行依赖 + strict finding schema | forged action/approval |
| H14-05 | conflict 被 LLM 抹平 | deterministic contradiction guard | contradictory findings |
| H14-06 | 评估直接演变为生产实现 | 离线目录、无 route/scheduler/store 接入 | dependency/import check |

## 12. 测试与验收

```powershell
python -m pytest tests/unit/test_multi_agent_evaluation.py tests/unit/test_agent_harness_replay.py tests/unit/test_agent_harness_execution_boundary.py --tb=short --basetemp=.pytest_tmp
```

评估验收：报告能回答哪些任务值得使用 Team、发现了哪些 baseline 漏项、增加多少成本、失败时如何回退。只有 Gate 全部通过并完成 capability review，才允许进入生产多 Agent 实施。

## 13. 实施顺序

1. 等待 02—13 完成并冻结 single baseline；
2. 确认 Gate 阈值和数据策略；
3. 建立 eligible/ineligible/adversarial fixtures；
4. 实现离线 advisor/coordinator simulation；
5. 运行 single vs team 离线对比评测；
6. 人工复核安全、质量、成本和偏差；
7. 未通过则记录结论并停止；
8. 通过后更新现有多 Agent 实施计划并单独申请 capability review。

## 14. 待确认

**待确认：** 推荐 Gate 中 10 个百分点、3 倍 token 和 2.5 倍 p95 latency 是否符合项目成本预期。这些是评估初值，不是已批准产品指标；必须在首次正式评测前确认。
