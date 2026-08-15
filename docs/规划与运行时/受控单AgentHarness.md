# 受控单 Agent Harness

Harness 是可选的单 Agent 控制层，默认由
`MEDIMAGE_AGENT_HARNESS_ENABLED=false` 关闭。它只协助 Agent Task 的规划、
澄清、解释和恢复建议；不执行计算，也不拥有审批权限。

Harness 和确定性模式都只从项目级 Agent Task 命令进入。它们不会创建或读取
文件型 Agent plan/run、`plan.json`、review summary 或 `agent_runs/` 目录；规划和
运行证据分别由 Agent Task 投影与项目 Runs 提供。

当前可验证的源码和 focused-test 基线见
`specs/阶段记录/阶段十二/Agent改造/01_当前Agent基线与差距分析.md`。该基线不构成
Windows packaged smoke 或正式 release 证据；未定位到该类 Harness 专属证据时，其状态为
`unknown`。

## 运行合同

- 一个 `AgentLifecycleRecord` 最多对应一个项目绑定的 Harness attempt；attempt
  和 context/step 是从属审计记录，不能成为第二个用户状态机。
- 每次 lease claim 最多处理一个 step。`run_until_blocked()` 只组合已持久化的
  单步，并在每一步后重新读取 lifecycle 和 attempt；它不持有跨 step 事务。step
  idempotency key 为 `attempt_id:step_no:input_hash`，重复 key 绝不触发第二次模型调用。
- 安装级预算由 `ConfigService` 读取并以硬上限夹紧：默认/硬上限分别为 8/16 个
  step、6/6 次真实 provider 调用、8/8 个 action proposal、每 step 1/1 次 repair、
  2/3 次 recovery 和 300/300 秒 wall time。输入/输出 token 限额默认关闭，只有
  provider 实际返回 usage 且管理员设置 `MEDIMAGE_AGENT_HARNESS_MAX_*_TOKENS` 后才
  累计和限制；缺失 usage 保持 `null`，绝不估算为 0。项目 metadata、用户请求或模型
  输出不能提高这些上限。每次 wake 默认最多 3 步，硬上限为 6。达到 wake 上限时
  attempt 保持 `READY`、递增 `yield_count` 并在 FIFO 队列尾部重新排队，不能独占 worker。
- Scheduler 只处理 create、answer、recovery 执行、run terminal 和 startup recovery
  的 wake 请求。它在应用 lifespan 内保持单一后台 owner；`GET`/list/read projection
  不会登记 wake、claim lease 或调用模型。startup 只恢复 `READY` 或 lease 已过期的
  `RUNNING` attempt；shutdown 拒绝新 claim，并只等待 scheduler 自己当前的有限 step。
- 唯一模型协议是 schema version 1 的 `ActionEnvelope`。允许 kind 仅为
  `read_evidence`、`request_decision`、`draft_plan`、`explain_result`、
  `propose_recovery`、`finish`；所有其他 kind 默认拒绝。
- Context v2 由唯一 builder 从显式 `HarnessContextSources` 产生，最大 32 KiB；
  builder 不读取 store 或文件。它以固定顺序保存 `goal`、`policy`、
  `project_evidence`、`decision_state`、`plan_state`、`execution_state`、
  `latest_observation`、`last_action_result`、`memory_context` 和 `budget` 十个
  typed sections。每个 section 都带 schema version、稳定 source refs/source hash，
  总 context 绑定 section hashes、prompt/skill、policy 和 redaction version。
- Context row 仅在重建后的完整 context hash 相同才可复用；attempt 中的旧 hash
  不能跳过当前动态来源的重建。Observation、计划、答案、上一步结果、预算或策略变化
  都会使缓存失效。provider cache miss 不影响确定性行为。
- 裁剪只会整项移除非必要 section 数据，绝不截断 ID 或 hash。先裁剪 Memory 和
  可重新读取的 evidence，再裁剪高优先级状态；仍超限时仅保留 goal、policy、
  decision state、last action result 和 budget。它们仍无法装入时在模型调用前以
  `AGENT_CONTEXT_LIMIT_EXCEEDED` 安全停止。

## 模型调用账本

- 每个 `AgentHarnessStep` 嵌套 0–2 个 immutable `ModelCallRecord`。一条记录只保存
  call/step/attempt ID、provider/model/endpoint class、phase、template/context/request/
  response hash、schema/repair 状态、时间/延迟、可用 token usage、脱敏 request ID、
  error code、network 标识和 fallback 目标；不保存 prompt、完整 response、header、
  API key、影像或任意原始 provider 错误。
- OpenAI-compatible response 的 model、usage、cache usage 和 request ID 在可用时才
  写入；rule-based 路径显式记录为 `provider=rule_based`、`network_called=false`，其
  token 字段为 `null`。模型调用计数只统计真实 provider 请求，repair 也受同一总调用
  预算约束。
- service 在 provider 调用前先持久化 `started` call record，再写完成状态，并通过
  attempt 的 expected-status/lease fence 结算 totals。若进程在已持久化调用后失效，
  恢复会对已有 step 对账；未知 outcome 以 `AGENT_HARNESS_CALL_OUTCOME_UNKNOWN` 停止，
  不会重复调用 provider 或重复消费预算。

## 安全和恢复

`draft_plan` 唯一可调用的业务服务是现有 Goal Planning Service，仍由既有
validator、Reviewed Plan 和 Approval Summary 流程决定后续状态。Harness 从不
调用 dry-run、Execution Ticket、Execution Gateway、node runner、shell 或文件
系统写入。取消和 lifecycle 终态会停止 attempt。关闭 Harness 时，Agent Task 明确
选择确定性命令路径。已启用 Harness 的 provider 不可用时，当前 model attempt 会先以
`AGENT_HARNESS_PROVIDER_UNAVAILABLE` 停止，并写入 `fallback_from`、`fallback_to`
和结构化原因；随后才由确定性 Goal Planning Service 重新生成其自身的 plan/context
hash。该 fallback 不复用旧 Approval Summary；schema、安全或预算拒绝仍只安全停止。

前端只显示后端只读 `harness_summary`（实际规划路径、steps/calls/actions/repairs/
recovery/token 预算、状态、下一步、让出次数、actual fallback 路径、停止原因和最新
脱敏步骤摘要），不推断执行成功，也不会从 GET 触发模型调用。

## 终态 Observation 与结果说明

- terminal run 先由确定性 Reconciler 依次持久化 Observation、Goal Evaluation，必要时
  生成 Recovery Proposal；之后才发送 `run_reconciled` wake。wake 以 lifecycle、run、
  observation、evaluation 和 recovery proposal 的 hash 组成指纹，同一指纹只处理一次。
- Reflector 只能读取这些结构化记录并选择解释、补读允许证据、请求既有 recovery service
  或转人工；不能改写 evaluation、outcome、capability、artifact 或审批范围。
- `explain_result` 重新从绑定的 Observation 和 Goal Evaluation 计算 outcome、受试者计数、
  artifact/reload 状态、criterion 和限制。模型生成文本与这些字段分离；冲突文本以
  `AGENT_EXPLANATION_CONFLICT` 丢弃，确定性摘要仍可读取。
- 每个 Harness step 使用 schema v4，分别保存 canonical `action_hash`、
  `action_result_hash` 以及 Observation、Evaluation、Recovery Proposal 和 explanation 的
  引用/hash，不复制其正文；旧 `output_hash` 字段不再读取。wake 和 explanation 都写入
  lifecycle 审计事件。Recovery 仍只
  是 proposal，所有 retry、重规划或 scope 变化仍要经过新的显式审批。

## Trace、Replay 与离线评测

- `AgentTraceService` 从 lifecycle、Harness attempt/context/step、生命周期事件、Reviewed
  Plan、ticket、run、Observation、Goal Evaluation 与 Recovery 的权威记录组装一个只读
  `AgentTraceBundle`。它不写入数据库，不复制这些记录，也不会补造缺失数据；缺失和跨项目
  绑定冲突分别标为 `incomplete` 与 `conflict`，并进入 canonical `integrity_hash`。
- Trace 只保存安全摘要和 typed ID/hash 引用，以及已经脱敏的 `ModelCallRecord` metadata。
  Prompt、原始模型响应、绝对路径、secret、原始影像、完整日志与 Memory 正文都不进入 bundle
  或 `/trace` 响应。高级只读 API 为
  `GET /api/projects/{project_id}/agent/tasks/{task_id}/trace?after=0&limit=50`；前端高级详情
  使用等价的 `/harness?after=0&limit=50` 只读投影。entries 分页，单页最多 100 项，且不会
  wake、claim、调用模型、handler、Gateway 或 runner。
- `AgentReplayService` 只对 bundle 执行 schema/hash、reference、step 顺序和幂等键、
  capability/state、lifecycle reducer 与 budget ledger 校验。它没有 store、provider、
  Evidence Service、Planner、Approval、Gateway、runner 或 filesystem 依赖，因此 replay
  不能触发任何生产副作用。
- `tests/fixtures/agent_eval/v1/manifest.json` 是版本化、无研究数据的离线评测集，覆盖正常、
  recovery、provider、safety、stability 及中英文案例。`AgentEvaluationService` 只汇总显式
  oracle 的 routing、提问、停止点、安全拒绝、schema repair/fallback、step/call/latency 和
  交互指标；分数只用于版本比较，不自动发布策略，也不构成科学验证结论。
