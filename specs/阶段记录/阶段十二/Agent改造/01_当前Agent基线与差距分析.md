# 01：当前 Agent 基线与差距分析

> 状态：Draft，待人工 Review。
> 依赖：`00_Agent改造总体方案.md`。
> 本文只建立事实基线和后续改造入口，不修改代码、不提升能力等级。

## 1. 目标

在开发前回答四个问题：源码里已经有什么、哪些路径真实接入产品、哪些行为有测试、哪些能力进入过发布验证。基线完成后，后续方案只能修正已确认的缺口，不能把“文件存在”直接写成“功能完成”。

非目标：不在本任务实现 Harness 循环，不修改 API，不更新版本号，不把阶段十一 `Proposed` 直接改为 `Implemented`。

## 2. 当前实现分析

### 2.1 权威调用链

| 阶段 | 当前入口 | 当前行为 |
|---|---|---|
| 创建任务 | `agent_task_routes.py:create_agent_task()` -> `AgentTaskCommandService.create()` | 创建 lifecycle，然后调用 `_harness_or_plan()` |
| 回答问题 | `answer_agent_task()` -> `AgentTaskCommandService.answer()` | 校验唯一 `PendingDecision`，更新 `command_context` 后恢复规划 |
| Harness | `_harness_or_plan()` -> `ensure_attempt()` -> `run_one()` | 每次命令最多处理一个模型动作 |
| 规划 | `AgentTaskCommandService._plan()` -> `GoalPlanningService.plan()` | 构造、校验、持久化 Reviewed Plan 和 Approval Summary |
| 审批执行 | `approve()` | 校验 summary/hash，审批后 dry-run，再经现有 reviewed execution 链执行 |
| 运行协调 | `AgentTaskReconciler` | 有界检查 run 终态并接入 Observation/Goal Evaluation/Recovery |
| 只读投影 | `AgentTaskReadModel` | 合并 lifecycle、plan、ticket、run、Observation、Recovery 和 Harness 摘要 |

### 2.2 已存在的 Harness 表面

| 表面 | 代码位置 | 已确认事实 |
|---|---|---|
| Action 合同 | `schemas/agent_harness.py:ActionEnvelope` | 六种 kind，`extra="forbid"`，payload 仍是通用 `dict` |
| Attempt/Step/Context | `schemas/agent_harness.py` | 有版本、hash、预算计数、deadline 和 lease 字段 |
| 单步执行 | `services/agent_harness_service.py:run_one()` | claim 一步、调用模型、校验动作、执行一个 handler 后释放 lease |
| 上下文 | `services/agent_harness_context_service.py:HarnessContextBuilder` | 32 KiB、字段 allowlist、秘密/影像/日志键过滤 |
| 能力表 | `runtime/agent_capability_catalog.py` | 按 lifecycle state 限制六种只读动作 |
| 启动恢复 | `runtime/agent_harness_scheduler.py:recover_once_on_startup()` | 启动时每个 lifecycle 最多处理一步 |
| 持久化 | `services/mock_store.py` | SQLite 保存 attempts、contexts、steps |
| 前端 | `HarnessStatusCard.tsx` | 显示状态、调用/提议计数、最新摘要和停止原因 |

### 2.3 已确认差距

| ID | 当前情况 | 直接影响 | 后续方案 |
|---|---|---|---|
| B-01 | create/answer 只调用一次 `run_one()` | 无人工依赖的多步任务仍会停下 | 02 |
| B-02 | scheduler 只做启动扫描 | 服务运行期间没有统一事件唤醒 | 02 |
| B-03 | `read_evidence` 和 `explain_result` 没有真实业务处理 | Action 名称多于实际能力 | 03、04、06 |
| B-04 | `PendingDecision` 只能表达一个问题 | atlas、TR、template 等可能多次往返 | 04 |
| B-05 | context 缺少最新 Observation、上一动作结果和分项预算 | 模型无法根据新结果继续行动 | 07、08 |
| B-06 | Step 缺少模型调用和 handler 结果 | 不能完整解释或回放 | 08、10 |
| B-07 | provider/adapter 的实际来源未作为完整审计记录或前端字段持久化；当前仅保存 `provider_ref`、step 摘要和停止码 | 用户无法从投影判断模型、版本或调用来源 | 08、11 |
| B-08 | Trace fixture 只验证动作/state allowlist | 不能证明无副作用 replay | 10 |
| B-09 | 阶段十一 README 声称工程验收完成，但未定位到 Harness 专属 packaged smoke 或 release 证据 | 源码、focused 测试和发布状态可能混淆 | 本文 |
| B-10 | `AgentLifecycleRecord` 仍有 legacy `observation` 字段和兼容 validator | 与当前“不保留兼容路径”规则冲突，并增加双事实来源 | 06 在修改生命周期时同步删除 |

## 3. 总体修改思路

先生成一份机器可复核的基线清单，再补 characterization tests。只有通过测试和入口检查的项目才能标记为“已验证”；只有存在打包或发布证据的项目才能标记为“已发布”。

## 4. 详细实施方案

### 4.1 建立四级状态清单

对每项能力分别记录：

1. `source_present`：源码和 schema 存在；
2. `wired`：生产入口实际调用；
3. `verified`：focused/integration 测试通过；
4. `release_evidence`：当前发布线存在构建、打包或验收证据。

清单放在本文实施后的“基线结论”章节，不新增运行时表。无法验证的项写 `unknown`，不从源码推断发布状态。

### 4.2 固定现有行为

新增或补充 characterization tests：

- Harness 关闭时 create/answer 仍直接进入确定性 `_plan()`；
- Harness 开启时一次命令最多完成一个 step；
- Harness 已启用但 provider 不可用时停止 attempt，且不回退到确定性 `_plan()`；关闭 Harness 时 create/answer 才直接进入确定性 `_plan()`；
- planning 阶段不调用 dry-run、ticket、gateway 或 runner；
- `GET /agent/tasks` 和详情接口不触发 reconcile、claim 或模型调用；
- cancel 会停止已注入或配置启用的 Harness attempt；
- startup recovery 不处理 `WAITING_FOR_USER` 和终态。

### 4.3 校正文档状态

在 characterization 通过后再处理：

- `specs/阶段记录/阶段十一/README.md`：区分“方案状态”和“源码实现状态”；
- `docs/规划与运行时/受控单AgentHarness.md`：只描述已验证合同；
- `PROJECT_STATE.md`：只在确认 source、test、packaging、release 证据后更新；
- `docs/项目概览/能力矩阵.md`：Harness 不是科学计算能力，不提升数值能力等级。

## 5. 基线结论（证据日期：2026-08-09）

状态值只回答对应列的问题：`yes` 表示本次已定位并验证的证据，`partial` 表示入口可达但行为仍是后续改造缺口，`unknown` 表示本次未找到可独立证明的证据。`release_evidence` 绝不从源码或单元测试推断。

| 能力表面 | source_present | wired | verified | release_evidence | 本次证据 |
|---|---|---|---|---|---|
| `ActionEnvelope` 六种受限 action 与 fail-closed catalog | yes | yes | yes | unknown | `agent_harness.py`、`agent_harness_service.py:_validate_envelope()`；`test_agent_harness_capabilities.py`、`test_agent_harness_replay.py` 通过 |
| attempt/context/step SQLite 审计、lease、预算与单步 `run_one()` | yes | yes | yes | unknown | `agent_harness_service.py:run_one()` -> `mock_store.py`；Harness focused suites 通过 |
| create/answer 的 Harness 选择与关闭时确定性 `_plan()` | yes | yes | yes | unknown | `agent_task_routes.py` -> `AgentTaskCommandService._harness_or_plan()`；`test_agent_task_commands.py`、`test_agent_harness_lifecycle.py` 通过 |
| 已启用 Harness 的 provider 故障结构化停止（无确定性 fallback） | yes | yes | yes | unknown | `agent_harness_service.py:run_one()`；`test_enabled_harness_provider_failure_stops_without_deterministic_plan_fallback` 通过 |
| `read_evidence` / `explain_result` action | yes | partial | yes（当前 stub 语义） | unknown | `_apply()` 仅让前者返回 `READY`、后者结束 attempt；后续 03、04、06 负责真实处理器 |
| 启动恢复扫描 | yes | yes | yes | unknown | `main.py` lifespan -> `recover_once_on_startup()`；本次 waiting/canceled characterization 通过；运行期事件唤醒仍缺失 |
| 只读 `harness_summary` 与前端卡片 | yes | yes | unknown | unknown | `AgentTaskReadModel._harness_summary()` -> `AgentWorkspace` -> `HarnessStatusCard`；本次未运行前端 suite |

本次 Python 验证：

```powershell
python -m pytest tests/unit/test_agent_harness_service.py tests/unit/test_agent_harness_execution_boundary.py tests/unit/test_agent_task_commands.py tests/unit/test_agent_task_read_model.py tests/integration/test_agent_harness_lifecycle.py --tb=short --basetemp=.pytest_tmp
# 52 passed

python -m pytest tests/unit/test_agent_harness_context.py tests/unit/test_agent_harness_capabilities.py tests/unit/test_agent_harness_lease.py tests/unit/test_agent_harness_replay.py --tb=short --basetemp=.pytest_tmp
# 41 passed
```

本次未发现可独立归属于 Harness 的 Windows packaged smoke、installer/portable 验收或正式 release 记录。因此该列保持 `unknown`；这不改变科学能力等级，也不把 Harness 作为数值计算能力。

## 6. 文件修改清单

| 文件 | 修改内容 |
|---|---|
| 本文 | 补入最终四级基线矩阵和证据日期 |
| `tests/unit/test_agent_harness_service.py` | 已补单步和 startup recovery characterization |
| `tests/integration/test_agent_harness_lifecycle.py` | 已补 cancel 停止已注入 Harness attempt 的生产接入链 |
| `tests/unit/test_agent_harness_execution_boundary.py`、`test_agent_task_commands.py`、`test_agent_task_read_model.py` | 复用现有 planning 零执行、关闭时确定性路径和 GET 纯读覆盖；本次未改动 |
| 阶段十一 README、Harness 正式文档、`PROJECT_STATE.md` | 仅按验证结果纠正状态 |

## 7. 风险与处理

| 风险 | 处理 | 验证 |
|---|---|---|
| 把未跟踪源码当作发布能力 | 四级状态分开记录 | Git 基线、CI、打包证据分别检查 |
| characterization 把 bug 固化为目标行为 | 只固定安全边界和真实调用，不把缺口写成长期要求 | 每项测试标注“需保留”或“待后续改变” |
| 测试写入用户数据库 | 使用临时 `SQLiteDesktopStore` 和临时项目 | 测试后检查仓库与用户工作区 |
| 文档互相冲突 | 同一事实只由对应权威文档声明 | 全仓搜索旧状态描述 |

## 8. 测试与验收

执行：

```powershell
python -m pytest tests/unit/test_agent_harness_service.py tests/unit/test_agent_harness_execution_boundary.py tests/unit/test_agent_task_commands.py tests/unit/test_agent_task_read_model.py tests/integration/test_agent_harness_lifecycle.py --tb=short --basetemp=.pytest_tmp
```

验收标准：

- 每个基线结论都有文件、函数、测试或发布证据；
- 无法确认的内容明确标为 `unknown`；
- 阶段十一、正式 Harness 文档和 `PROJECT_STATE.md` 不再互相矛盾；
- 后续 02—14 可以直接引用本基线，不重新猜测当前行为。

## 9. 实施顺序

1. 记录当前 Git 状态和配置；
2. 复核调用链、schema、store 和前端消费者；
3. 补 characterization tests；
4. 运行 focused tests；
5. 填写四级状态矩阵；
6. 按证据更新文档；
7. 全仓搜索冲突描述并 Review。

## 10. 待确认

**待确认：** 当前 Harness 是否进入过 Windows packaged smoke 或正式 release。代码中未发现足以单独证明该结论的证据；在确认前 `release_evidence` 应为 `unknown`。
