# 处理流程有向无环图可视化实施方案

> 归档状态：该文档对应的当前阶段范围已完成；仅作为历史实施与审计记录保留。

> 状态：已实施并通过自动化、构建与桌面冒烟验收
> 任务模式：完整功能
> 建议排期：阶段一“单一规划主链”完成后独立实施
> 本方案只增加只读展示和运行状态投影，不增加执行入口，不改变审批权限。

本文后续统一称“流程图”，避免重复使用专业缩写。

## 1. 目标

把审阅计划中的 `depends_on` 真实绘制为有向无环图，并把当前运行状态覆盖到对应节点上，使用户能够明确看到：

- 完整流程有哪些节点、节点之间如何依赖。
- 当前正在执行哪些节点。
- 哪些节点已成功、失败、跳过、阻断或尚未开始。
- 按受试者执行的节点完成了多少个受试者。
- 当前状态最后更新时间，以及数据是否完整。

“规划与审批到了哪一步”和“流水线执行到了哪个节点”是两种不同进度：

```text
任务大阶段：目标 -> 补充决定 -> 计划 -> 审批 -> 执行 -> 验证 -> 结果

流水线节点图：审阅计划中的节点和依赖
                         -> 运行后叠加每个节点的真实状态
```

现有 `MacroProgress` 继续显示任务大阶段；新图只显示审阅计划内的流水线节点。不得把大阶段伪装成流水线节点，也不得在计划尚未生成时编造节点。

### 1.1 范围

- 必须同时覆盖计划预览、已审阅计划和真实运行三种状态。
- 必须以后端图投影作为运行状态唯一来源。
- 必须显示分叉、汇合和并行当前节点。
- 必须提供图形和等价表格两种读取方式。
- 必须保留现有审批门、执行票据、执行网关和流水线运行时边界。

### 1.2 非目标

- 不允许在图上拖动后保存顺序、增加节点、删除节点或修改依赖。
- 不在图上直接审批、执行、跳过、重试或恢复。
- 不显示 MATLAB/SPM 等工具内部没有正式状态合同的细分步骤。
- 不新增 WebSocket 或服务端推送；首期使用有界 HTTP 自动读取。
- 不制作历史播放动画，不估算剩余时间。
- 不修改科学算法、节点参数和产物真实性等级。

## 2. 当前实现依据

| 已有事实 | 源码位置 | 对本方案的影响 |
|---|---|---|
| 审阅计划节点已有 `depends_on` | `src/backend/app/planner/plan_validator.py`、`src/backend/app/planner/plan_adapter.py` | 边必须直接由已审阅计划生成 |
| `PlanWorkspace` 当前把节点画成纵向列表 | `src/frontend/src/features/workspaces/PlanWorkspace.tsx:258` | 替换为真正的图，不保留第二套节点解析 |
| 运行详情已有标准节点状态 | `src/backend/app/services/run_state_timeline.py` | 复用状态规范化函数，不复制状态含义 |
| 当前时间线不包含依赖边，且同节点的受试者状态会被折叠 | `run_state_timeline.py:256` | 新建专用图投影，不能直接把时间线当图数据 |
| `RunLinkRecord` 绑定 `reviewed_plan_id` 和 `run_id` | `src/backend/app/schemas/desktop.py:73` | 运行图必须按这两个 ID 绑定，不能按名称猜测 |
| `write_node_state()` 使用原子 JSON 写入 | `src/backend/app/runtime/state_store.py` | 节点开始和结束状态继续写同一权威文件 |
| 当前执行器只在节点结束后写状态 | `src/backend/app/runtime/pipeline_executor.py` | 必须先补“开始执行”状态，才能真实显示当前节点 |
| Runs 页面已有每三秒更新运行列表 | `src/frontend/src/features/runs/useProjectRunTasks.ts:54` | 图使用同样的三秒间隔，但单独请求图数据 |

## 3. 用户可见行为

### 3.1 计划尚未生成

- 只显示 `MacroProgress` 的任务大阶段。
- 图区域显示“计划生成后显示处理流程”。
- 不显示空白画布，不创建 `pipeline` 等虚构节点。

### 3.2 计划已生成但尚未执行

- 显示静态有向无环图。
- 所有节点状态为“待执行”。
- 高风险、需要审批和未知节点继续使用当前计划校验结果提示。
- 仅规划任务始终停留在该状态，并显示“本任务不会执行”。

### 3.3 等待审批

- 图结构保持不变。
- 图顶部显示“等待审批”，不把任何节点标成运行中。
- 用户选择节点后，可以查看后端、依赖、输入输出数量、风险和参数键摘要。
- 图中不放审批按钮，审批仍由现有 Agent 任务主操作处理。

### 3.4 正在执行

- 所有实际处于 `running` 的节点同时高亮；并行执行时允许有多个当前节点。
- 已成功节点显示完成标记，失败或阻断节点显示结构化错误摘要。
- 下游节点只有实际状态为 `ready` 时才显示“可执行”；不能仅根据上游完成在前端推断。
- 每三秒读取一次图投影；上一次请求未完成时不发起下一次请求。
- 状态更新时不重新缩放或跳回画布起点，保留用户视口和当前选择。

### 3.5 执行结束

- 停止自动读取，保留最终图。
- 成功、部分完成、失败、取消和超时必须使用不同状态。
- 点击失败节点打开节点详情，并提供跳转到现有日志、产物和恢复信息的入口。
- 图只解释发生了什么，不直接提供重试、恢复或执行按钮。

## 4. 后端权威图合同

### 4.1 新增数据结构

新增 `src/backend/app/schemas/execution_graph.py`：

```python
ExecutionGraphNodeState = Literal[
    "pending",
    "preflight",
    "ready",
    "running",
    "succeeded",
    "partial",
    "failed",
    "blocked",
    "skipped",
    "cancelled",
    "timeout",
    "reused",
    "invalidated",
    "unknown",
]


class ExecutionGraphSubjectSummary(BaseModel):
    total: int | None
    observed: int
    pending: int
    running: int
    succeeded: int
    failed: int
    skipped: int
    blocked: int
    cancelled: int
    timeout: int
    reused: int
    invalidated: int
    unknown: int


class ExecutionGraphNode(BaseModel):
    node_id: str
    label: str
    backend_id: str
    parallel_level: str
    depends_on: tuple[str, ...]
    risk: Literal["normal", "approval", "high", "unknown"]
    planned_input_count: int
    planned_output_count: int
    parameter_keys: tuple[str, ...]
    state: ExecutionGraphNodeState
    state_source: Literal["plan", "runtime", "summary", "mixed", "unknown"]
    started_at: datetime | None
    ended_at: datetime | None
    duration_seconds: float | None
    subject_summary: ExecutionGraphSubjectSummary | None
    warning_count: int
    error_count: int
    actual_output_count: int
    current: bool


class ExecutionGraphEdge(BaseModel):
    edge_id: str
    source_node_id: str
    target_node_id: str
    state: Literal["pending", "active", "completed", "blocked", "unknown"]


class ExecutionGraphResponse(BaseModel):
    schema_version: Literal[1]
    project_id: str
    reviewed_plan_id: str
    plan_hash: str
    run_id: str | None
    run_state: str | None
    run_terminal: bool
    graph_status: Literal["available", "partial", "unavailable"]
    structure_hash: str
    state_hash: str
    generated_at: datetime
    nodes: tuple[ExecutionGraphNode, ...]
    edges: tuple[ExecutionGraphEdge, ...]
    current_node_ids: tuple[str, ...]
    ready_node_ids: tuple[str, ...]
    terminal_nodes: int
    total_nodes: int
    node_completion_percent: int | None
    warnings: tuple[str, ...]
    errors: tuple[str, ...]
```

`node_completion_percent` 只表示“已进入终态的节点数 / 计划节点总数”，不是剩余时间，也不是科学计算完成比例。仅规划或尚未执行时返回 `null`。

`structure_hash` 只覆盖计划节点和边；`state_hash` 覆盖节点状态、计数和运行状态。前端只在 `structure_hash` 改变时重新布局，在 `state_hash` 改变时更新节点样式。

### 4.2 新增只读服务

新增 `src/backend/app/services/execution_graph_service.py`，公开三个方法：

```python
class ExecutionGraphService:
    def build_preview_graph(
        self,
        *,
        project_id: str,
        plan: dict[str, object],
    ) -> ExecutionGraphResponse: ...

    def build_plan_graph(
        self,
        *,
        project_id: str,
        reviewed_plan_id: str,
    ) -> ExecutionGraphResponse: ...

    def build_run_graph(
        self,
        *,
        project_id: str,
        run_id: str,
    ) -> ExecutionGraphResponse: ...
```

服务按以下顺序读取：

1. 通过 `ProjectStore` 获取项目、运行链接和审阅计划。
2. 校验 `RunLinkRecord.reviewed_plan_id`、项目 ID 和计划哈希绑定。
3. 从 `payload.plan.nodes` 读取节点和 `depends_on`。
4. 复用计划校验器确认节点 ID 唯一、依赖存在且没有环。
5. 从已存的项目配置解析批准的 `work_dir`，再通过安全路径服务定位其直接子目录 `states/<run_id>/`。
6. 读取该运行的节点状态文件，并按节点 ID、受试者 ID 聚合。
7. 读取最终摘要时只用于补充运行终态和最终计数，不覆盖更新的节点状态。
8. 生成节点、边、当前节点、完成节点计数和结构化警告。

服务必须是纯读取：不协调任务、不修改运行状态、不执行外部工具、不注册产物。

读取状态文件时不得沿用当前状态时间线的 50 条限制，也不得静默捕获所有异常。单个损坏文件转结构化警告；项目边界错误、运行 ID 不安全或计划绑定不一致直接阻断整次读取。

### 4.3 状态来源优先级

节点展示状态使用以下固定顺序：

```text
当前节点状态文件
-> 最终摘要中同节点状态
-> 审阅计划的待执行状态
-> unknown
```

不得根据日志文本、英文消息、列表顺序或前端计时器推断节点状态。

### 4.4 受试者状态聚合

同一节点可能有多个受试者状态文件，不能像当前时间线一样只保留第一个。聚合规则固定为：

1. 任一受试者正在运行：节点显示 `running`。
2. 无运行项且成功与失败、阻断或超时并存：显示 `partial`。
3. 全部成功：显示 `succeeded`。
4. 全部跳过：显示 `skipped`。
5. 全部失败：显示 `failed`。
6. 只有阻断：显示 `blocked`。
7. 文件损坏、节点 ID 不匹配或无法判定：显示 `unknown`，同时返回警告。

受试者总数只能来自已登记的数据索引或运行合同。无法确认时 `total=null`，不能用已观察文件数冒充总数。

### 4.5 边状态

- 目标节点 `running`：来自已成功或复用上游的边显示 `active`。
- 源节点 `succeeded` 或 `reused` 且目标已进入非等待状态：显示 `completed`。
- 目标节点因依赖失败而 `blocked`：显示 `blocked`。
- 其他情况显示 `pending`。
- 任一端节点不存在时整张图返回 `partial`，并记录结构化错误；不得画悬空边。

## 5. 节点开始状态的运行时改造

没有节点开始状态，就无法真实显示“当前执行到哪一步”。本阶段需要受控修改 `Pipeline Runtime`，但不改变节点执行顺序和算法。

### 5.1 修改 `state_store.py`

扩展 `write_node_state()`：

- `ended_at` 改为可空。
- 增加 `updated_at`。
- `status="RUNNING"` 时允许空结果、空输出和 `ended_at=None`。
- 终态写入覆盖同一文件，保留原 `started_at`，补齐 `ended_at` 和结果。
- 继续使用 `atomic_write_json()` 和 `_schema_version`。
- 不新增旧格式读取或双写文件。

同时在 `src/backend/app/schemas/execution_state.py` 增加不可变 `PersistedNodeState`，由写入端和图读取端共同校验。提升 `STATE_SCHEMA_VERSION`，同步更新全部当前消费者；旧版本状态直接返回结构化不支持错误，不增加兼容读取。

### 5.2 修改 `pipeline_executor.py`

在每次真实调用节点执行器前按以下顺序执行：

```text
安全检查和依赖检查通过
-> 原子写入 RUNNING 节点状态
-> 调用节点执行器
-> 原子覆盖为 SUCCESS / FAILED / BLOCKED 等终态
-> 把终态文件路径加入流水线摘要
```

项目级节点写 `subject="project"`；受试者级节点在每个受试者节点执行器调用前分别写 `RUNNING`，完成后覆盖对应受试者文件。

若写入 `RUNNING` 状态失败，禁止调用节点执行器。若节点执行器抛出异常，必须先尽力写入 `FAILED` 终态，再按现有错误规则结束。不得因为可视化失败而吞掉执行异常。

### 5.3 中断和陈旧状态

- 进程异常退出后可能留下 `RUNNING` 文件，这是事实记录，不能自动改成成功或失败。
- 运行链接已进入终态但仍有 `RUNNING` 节点时，图返回 `partial` 和 `EXECUTION_GRAPH_STALE_RUNNING_NODE`。
- 长时间运行的合法节点不能仅因时间较长被判失败。
- 没有可靠心跳时，界面显示“最后状态更新时间”，不显示伪造的剩余时间。

## 6. API 设计

新增领域路由 `src/backend/app/api/execution_graph_routes.py`，并在 `create_app()` 注册：

```text
POST /api/projects/{project_id}/plan-graph-preview
GET /api/projects/{project_id}/plans/{reviewed_plan_id}/graph
GET /api/projects/{project_id}/runs/{run_id}/graph
```

- 预览接口只校验并投影当前未持久化的草案，不保存计划、不创建审批或运行记录。
- 三个接口都通过 `ProjectStore` 和 `FastAPI Depends()` 注入存储。
- Route 只接收参数、调用 `ExecutionGraphService`，并统一通过 `raise_api_error()` 映射结构化错误。
- GET 必须保持纯读，不触发状态协调、模型、节点执行器、恢复或状态写入。
- 项目不匹配返回 404，计划或运行绑定不一致返回结构化 409。
- API 不返回本地绝对路径、原始日志、完整参数、凭据或研究数据。
- 参数只返回键名，不返回可能包含路径或研究信息的值。

不扩张当前聚合型 `project_history_routes.py`，也不把图结构塞进现有状态时间线响应。

## 7. 前端实现

### 7.1 依赖选择

在 `src/frontend/package.json` 和锁文件中增加固定版本：

- `@xyflow/react`：绘制只读节点、边、缩放、平移和适配视口。
- `@dagrejs/dagre`：根据有向边生成稳定的从左到右布局。

禁止使用 `latest`。实施时核对与当前 React、TypeScript 和 Vite 的兼容版本，并同步更新 `package-lock.json`。

选择依据：React Flow 官方支持自定义节点、键盘访问和 `fitView`，官方布局说明将 Dagre 列为简单、快速的有向图布局方案。首期不使用更复杂的 ELK，也不购买 React Flow Pro 示例。

官方资料：

- [React Flow 自定义节点](https://reactflow.dev/learn/customization/custom-nodes)
- [React Flow 布局说明](https://reactflow.dev/learn/layouting/layouting)
- [React Flow Dagre 示例](https://reactflow.dev/examples/layout/dagre)
- [React Flow 无障碍说明](https://reactflow.dev/learn/advanced-use/accessibility)

### 7.2 新增功能目录

新增 `src/frontend/src/features/execution-graph/`：

| 文件 | 职责 |
|---|---|
| `ExecutionGraphView.tsx` | 图画布、图例、空状态和错误状态 |
| `ExecutionGraphNode.tsx` | 单个节点的状态、名称、受试者进度和错误数量 |
| `ExecutionGraphInspector.tsx` | 选中节点的依赖、时间、计数和跳转入口 |
| `ExecutionGraphTable.tsx` | 与图等价的表格视图和窄屏降级 |
| `ExecutionGraphSummary.tsx` | 当前节点、节点完成数和最后更新时间 |
| `layoutExecutionGraph.ts` | Dagre 布局和稳定位置计算 |
| `useExecutionGraph.ts` | 请求、三秒自动读取、取消和终态停止 |
| `ExecutionGraphView.module.css` | 状态样式、暗色主题、缩放容器和减少动画 |

共享数据结构新增到 `src/frontend/src/lib/types/executionGraph.ts`；接口封装新增到 `src/frontend/src/lib/api/executionGraphs.ts`。不要继续向根级 `types.ts` 或单体接口文件堆积字段。

### 7.3 图交互

- `nodesDraggable=false`、`nodesConnectable=false`，禁止新增、删除和改线。
- 允许选择节点、缩放、平移、适配全部节点和回到当前节点。
- 初次打开时执行一次 `fitView`；后续状态读取不自动改变视口。
- 图结构或计划版本改变时重新布局；只有节点状态改变时保留原位置。
- 默认从左到右；窄屏使用从上到下布局或切换到表格。
- 当前节点边框和状态文字同时变化，不能只依赖颜色。
- 活动边动画遵守 `prefers-reduced-motion`；关闭动画不影响状态表达。

### 7.4 页面接入

#### `PlanWorkspace`

- 用 `ExecutionGraphView` 替换当前伪图节点列表。
- 有 `reviewedPlanId` 时调用计划图 GET 接口；未保存的当前草案调用预览接口。
- 显示静态结构和风险信息，预览响应不得被标为已审阅计划。
- 继续保留节点检查器，但改为消费强类型图节点。
- 删除 `normalizePlanNodes()` 及其重复节点解析。

#### `RunsWorkspace`

- 在详情标签增加“流程图”，并把它作为正在运行任务的默认标签。
- 只对流程图接口做三秒自动读取；日志、产物和诊断继续按现有方式加载，避免每三秒重复读取大内容。
- 终态停止自动读取，手动刷新按钮仍可重新获取。

#### `AgentWorkspace`

- `TaskCard` 的执行阶段增加 `ExecutionGraphSummary`，显示“当前：节点名称”或“当前并行执行 N 个节点”。
- 点击摘要进入 Runs 的流程图标签。
- `TaskDetails` 可显示缩略图或节点状态表，但不复制第二套图状态。

### 7.5 多语言和无障碍

- 所有状态、图例、按钮、错误和空状态加入 `en.ts` 与 `zh-CN.ts`。
- 节点可通过 Tab 聚焦，Enter 或 Space 选择。
- 每个节点的无障碍名称包含节点名、状态和受试者计数。
- 动态状态变化通过礼貌级 `aria-live` 摘要播报，不对每次读取重复播报未变化内容。
- 始终提供表格视图，保证不使用画布也能读取完整节点和依赖。

## 8. 自动读取规则

`useExecutionGraph()` 使用串行 `setTimeout`，不使用可能重叠的 `setInterval`：

```text
读取成功
-> 校验 project_id、run_id、reviewed_plan_id
-> 更新发生变化的节点状态
-> run_terminal=false 时三秒后再次读取
-> run_terminal=true 时停止
```

- 项目或运行切换时取消旧请求并清空旧图。
- 页面卸载时取消请求。
- 网络失败保留最后一次成功图，并显示“状态暂时无法更新”；不把节点改成失败。
- 连续失败使用最长十五秒的有界退避；用户手动刷新立即重试。
- 响应中的 `generated_at` 只表示投影生成时间，不冒充节点心跳。

## 9. 影响范围

### 9.1 直接修改

| 范围 | 原因 | 风险 |
|---|---|---|
| `src/backend/app/runtime/state_store.py` | 增加节点开始状态和新版持久格式 | 高 |
| `src/backend/app/runtime/pipeline_executor.py` | 在节点执行器调用前写 `RUNNING` | 高 |
| 新增图 schema、service 和 route | 生成后端权威图投影 | 中 |
| `PlanWorkspace`、`RunsWorkspace`、`AgentWorkspace` | 接入静态图、动态图和当前节点摘要 | 中 |
| 前端依赖和锁文件 | 增加图绘制与布局库 | 中 |
| i18n、测试和使用文档 | 覆盖用户可见状态与验证方法 | 低 |

### 9.2 明确不修改

| 范围 | 保持不变的原因 |
|---|---|
| Approval Gate、Approval Summary | 图只读，不改变审批对象和顺序 |
| Execution Ticket、Execution Gateway | 图不提供执行权限或分派入口 |
| node runner 和科学 kernel | 只在调用前后记录状态，不改计算逻辑 |
| rawdata 和登记源数据 | 图只读取受管状态和计划，不写源数据 |
| Observation、Goal Evaluation | 结果真实性继续由现有服务决定 |

## 10. 风险和处理

| 编号 | 风险 | 处理 | 证明方式 |
|---|---|---|---|
| H-VIS-01 | 把计划结构当成实际执行状态 | 图结构来自计划，状态来自运行记录，字段分开 | 尚未运行时全部待执行测试 |
| H-VIS-02 | 节点 ID 对不上导致状态贴错节点 | 用运行链接绑定计划，按稳定节点 ID 合并 | 项目、计划和节点不匹配测试 |
| H-VIS-03 | 节点开始时没有状态，界面只能看到上一步 | 节点执行器前原子写 `RUNNING` | 有序调用测试 |
| H-VIS-04 | 受试者状态被覆盖或只保留一个 | 按节点 ID 和受试者聚合 | 多受试者混合状态测试 |
| H-VIS-05 | 百分比让用户误以为是剩余时间 | 只显示节点终态比例并明确名称 | 文案和结构化字段测试 |
| H-VIS-06 | 自动读取重叠或切项目后串数据 | 串行计时、取消请求、校验响应 ID | 慢请求和项目切换测试 |
| H-VIS-07 | 每次状态更新重新布局，画面跳动 | 结构哈希不变时只更新节点数据 | 视口和位置稳定测试 |
| H-VIS-08 | 只用颜色表达状态 | 图标、文字、边框、图例和表格并用 | 无障碍测试和人工检查 |
| H-VIS-09 | 大图导致卡顿 | 布局只在结构变化时计算；首期人工验证 100 节点图 | 性能验收记录 |
| H-VIS-10 | 修改运行时破坏现有执行 | 先补特征测试，只增加开始状态写入，不改调度顺序 | 现有执行器完整回归 |
| H-VIS-11 | 进程崩溃留下 RUNNING 状态 | 显示陈旧警告，不自动伪造终态 | 中断恢复测试 |
| H-VIS-12 | 重规划后图与实际 run 不一致 | 运行图固定使用 RunLink 绑定的 reviewed_plan_id | 父子计划和历史 run 测试 |
| H-VIS-13 | 图详情泄露路径、参数或数据 | API 只返回白名单摘要和计数 | 安全序列化测试 |
| H-VIS-14 | 图组件意外允许编辑计划 | 关闭拖动、连接、删除和键盘删除 | 组件交互测试 |
| H-VIS-15 | 无效依赖或环导致布局错误 | 后端先运行计划校验；无效图安全拒绝 | 环、悬空边和重复 ID 测试 |
| H-VIS-16 | 旧版本节点状态被误读为当前状态 | 数据结构一次性切换；旧状态只显示计划图和不支持警告 | 历史运行状态测试 |

## 11. 分阶段实施

### 阶段 A：后端静态图合同

修改内容：

- 新增 `execution_graph.py` 数据结构。
- 新增 `ExecutionGraphService.build_plan_graph()`。
- 新增草案预览和计划图 GET 接口。
- 覆盖分叉、汇合、孤立节点、重复 ID、悬空依赖和环。

退出条件：同一审阅计划重复读取生成相同节点、边和 `plan_hash`，无效图返回结构化错误。

### 阶段 B：运行时开始状态和动态图投影

修改内容：

- 先用特征测试固定现有 `pipeline_executor.py` 调用顺序和终态文件。
- 扩展 `write_node_state()` 的 RUNNING 写入。
- 在项目级和受试者级节点执行器前写开始状态。
- 新增 `build_run_graph()` 和运行图 GET 接口。
- 聚合受试者状态，处理陈旧 RUNNING 和部分状态。

退出条件：真实节点执行器开始前可读到 `RUNNING`，结束后同一文件变为终态；执行顺序、结果和产物注册不变。

### 阶段 C：可复用图组件

修改内容：

- 固定安装 `@xyflow/react` 和 `@dagrejs/dagre`，更新锁文件。
- 新增图、节点、检查器、表格、布局和摘要组件。
- 覆盖加载、空、静态、运行、部分、失败、终态和 API 失败。
- 完成中英文、键盘和减少动画支持。

退出条件：分叉与汇合正确绘制；图和表格表达一致；只改变状态时节点位置不变。

### 阶段 D：接入计划、运行和 Agent 页面

修改内容：

- `PlanWorkspace` 替换伪图列表。
- `RunsWorkspace` 增加流程图标签和独立自动读取。
- `AgentWorkspace` 增加当前节点摘要和跳转。
- 删除旧节点规范化和重复状态推断。

退出条件：从 Agent 当前步骤可以一步进入实时图；运行终态后停止自动读取；旧图列表没有当前消费者。

### 阶段 E：完整回归与人工验收

修改内容：

- 后端完整测试、前端全套、桌面检查。
- 使用隔离临时项目执行最小两分支有向无环图。
- 人工检查窄屏、暗色主题、键盘、100 节点图和长时间运行节点。
- 同步架构、运行时、前端和用户文档。

退出条件：本方案第 12 节和 `AGENTS.md` Definition of Done 全部通过。

## 12. 测试计划

### 12.1 后端新增测试

| 测试文件 | 验证内容 |
|---|---|
| `tests/unit/test_execution_graph_service.py` | 静态图、状态合并、边状态、受试者聚合和错误处理 |
| `tests/unit/test_execution_graph_api.py` | 草案预览、项目隔离、接口合同、纯读和安全序列化 |
| `tests/unit/test_pipeline_executor_progress_state.py` | 节点执行器前 `RUNNING`、终态覆盖、异常和原子写入 |

推荐命令：

```powershell
python -m pytest tests/unit/test_execution_graph_service.py tests/unit/test_execution_graph_api.py tests/unit/test_pipeline_executor_progress_state.py tests/unit/test_run_state_timeline.py tests/unit/test_execution_state_schema.py --tb=short --basetemp=.pytest_tmp
python -m pytest tests/unit/test_pipeline_executor_project_settings.py tests/unit/test_execute_reviewed_consistency_integration.py tests/unit/test_contract_smoke_node_state_artifact.py --tb=short --basetemp=.pytest_tmp
```

### 12.2 前端新增测试

| 测试文件 | 验证内容 |
|---|---|
| `features/execution-graph/__tests__/layoutExecutionGraph.test.ts` | 分叉、汇合、方向和稳定位置 |
| `features/execution-graph/__tests__/ExecutionGraphView.test.tsx` | 全部显示状态、选择、图例、表格和无障碍 |
| `features/execution-graph/__tests__/useExecutionGraph.test.tsx` | 三秒读取、终态停止、取消、退避和 ID 校验 |
| `features/workspaces/__tests__/PlanWorkspace.test.tsx` | 静态图替换旧列表 |
| `features/workspaces/__tests__/RunsWorkspace.test.tsx` | 流程图标签、运行更新和详情跳转 |
| `features/agent/__tests__/AgentWorkspace.test.tsx` | 当前节点摘要和跳转 |

推荐命令：

```powershell
npm --prefix src/frontend run format:check
npm --prefix src/frontend run typecheck
npm --prefix src/frontend run test
npm --prefix src/frontend run test:project-runs
npm --prefix src/frontend run build
```

共享运行时修改后必须运行：

```powershell
python -m pytest --collect-only -q --basetemp=.pytest_tmp
python -m pytest --tb=short --basetemp=.pytest_tmp
npm --prefix desktop/electron run check
```

pytest 后按 `AGENTS.md` 只清理仓库根直接子项 `.pytest_cache/` 和 `.pytest_tmp*`，并复查 `git status --short`。

## 13. 人工验收清单

- [x] 计划中的分叉和汇合按 `depends_on` 正确绘制，不再只是顺序列表。
- [x] 计划尚未执行时没有节点被标为运行或成功。
- [x] 节点执行器开始后三秒内，图中对应节点显示运行中。
- [x] 并行受试者执行时，节点显示真实计数和混合结果。
- [x] 长时间运行节点不显示伪百分比或伪剩余时间。
- [x] 失败节点能看到错误数量并跳转现有日志。
- [x] 终态后停止自动读取，手动刷新仍有效。
- [x] 项目切换后旧项目图立即清空。
- [x] 图更新时画布不持续跳动。
- [x] 键盘和表格视图可以读取完整流程。
- [x] 暗色主题、窄屏和减少动画模式可用。
- [x] 图中没有本机绝对路径、凭据或研究数据。
- [x] rawdata 内容、大小和修改时间保持不变。

## 14. 文档影响

实施完成后按实际行为更新：

- `docs/架构与决策/系统架构.md`
- `docs/规划与运行时/流水线执行器产品化契约.md`
- `docs/规划与运行时/受控单AgentHarness.md`
- `docs/桌面与前端/前端视觉验收基线.md`
- `docs/桌面与前端/前端冒烟检查.md`
- `README.md`、`README_CN.md`
- `PROJECT_STATE.md`，仅在真实验证状态发生变化时

`AGENTS.md` 已覆盖后端权威状态、GET 纯读、运行时保护、原子状态和前端不能伪造成功。只有实施中发现新的可复发问题时才更新，不重复追加规则。

## 15. 证明要求、明确决策与假设

### 15.1 证明要求

| 需要证明的结论 | 实施时的验证方法 |
|---|---|
| 图结构完全来自已审阅计划 | 对比计划 `nodes`、`depends_on` 与接口节点、边 |
| 运行图没有串到其他计划 | 校验 RunLink、项目 ID、计划 ID 和计划哈希 |
| 当前节点是真实运行状态 | 用有序调用测试证明 `RUNNING` 写入先于节点执行器 |
| GET 接口没有副作用 | 检查存储写入、状态协调、模型和执行调用次数均为零 |
| 受试者聚合没有丢记录 | 同节点多受试者混合状态参数化测试 |
| 状态更新不会重排图 | 相同 `structure_hash` 下比较全部节点坐标 |
| 图不会泄露路径和数据 | 检查接口序列化白名单和危险测试样例 |
| 旧伪图逻辑已删除 | 全仓搜索 `normalizePlanNodes()` 和旧节点列表消费者 |
| 自动读取会在终态停止 | 假时钟测试请求次数和取消行为 |

### 15.2 决策与假设

| 编号 | 决策或假设 | 分类 | 依据 | 错误时的影响 |
|---|---|---|---|---|
| A-VIS-01 | 图是只读展示，不是流程编辑器 | 用户要求和安全约束 | 当前执行必须来自已审阅计划 | 若允许编辑，需要重新走计划、审批和哈希设计 |
| A-VIS-02 | 首期使用三秒 HTTP 自动读取，不新增 WebSocket | 工程决策 | 项目已有三秒运行列表更新 | 若必须亚秒更新，需要独立实时传输方案 |
| A-VIS-03 | 审阅计划有向无环图是图结构唯一来源 | 源码已确认 | 计划已有稳定 ID、哈希和 `depends_on` | 使用日志推图会导致不可审计 |
| A-VIS-04 | 节点状态文件是运行节点状态主要来源 | 源码已确认 | 当前运行时已原子持久化节点状态 | 若外部节点执行器不写状态，只能显示可用证据等级 |
| A-VIS-05 | Dagre 足以处理当前普通流水线 | 工程决策 | 当前没有子图和自由编辑需求 | 出现复杂子图后再单独评估 ELK |

### 15.3 待确认问题

当前没有阻塞实施的问题。以下需求若出现，必须暂停当前范围并另建方案：图上编辑、亚秒推送、内部工具子步骤、历史播放和图上直接恢复。

## 16. 完成标准

- 后端返回与审阅计划和 `RunLinkRecord` 严格绑定的强类型图投影。
- 节点开始、结束、异常和受试者状态均有可重载的原子记录。
- 计划页显示真实有向无环图，运行页实时覆盖权威节点状态。
- Agent 页面能直接看到当前节点并进入完整流程图。
- 图和表格都能表达加载、空状态、待执行、运行中、部分完成、失败、阻断、成功和不可用。
- 自动读取不会重叠、串项目、持续刷新终态或重置用户视口。
- 图不提供编辑、审批、执行和恢复旁路。
- 后端、前端、桌面和人工验收按本方案通过。
- 文档同步，`git diff` 和 `git status --short` 无无关修改、秘密、研究数据和生成物。
