# 任务 10E：DICOM 执行入口收敛

> 状态：Controlled source gateway contract implemented；packaged visible-UI 与新 rawdata
> manifest 验收延期。
>
> 本文正文保留实施前要求；其中旧入口描述、清单和 Stop Conditions 是历史方案，当前结果
> 以 `README.md` 和 `evidence/阶段十_E2E验收记录.md` 为准。
>
> 任务模式：Feature Bundle Mode + protected execution change；真实转换验证适用 Scientific Validation Mode 的 artifact/provenance 规则
>
> 交付目标：把已有 native DICOM conversion 收敛到 Reviewed Plan、Execution Ticket、Execution Gateway 和 Pipeline Runtime 唯一主路径，使 Agent Task 可以安全编排 DICOM → preprocessing。

## 1. Why This Is a Separate Gate

当前仓库明确记录：

- `/api/projects/{project_id}/conversion/execute` 是 deprecated execution entry；
- conversion approval package 不是 Execution Ticket；
- conversion prepare 是 proposal/dry-run，不 dispatch。

证据：`src/backend/app/runtime/execution_entry_inventory.py:33-44`。

因此，在该差距关闭前把 DICOM 自动接入 Agent Task 会违反“不绕过 Ticket/Gateway”和“不建立第二执行路径”的工程指标。本任务必须独立评审、表征、实施和验收。

## 2. Safety Invariants

- rawdata 只读，转换前后 filename/size/mtime/checksum 完全一致；
- output 仅允许项目内 approved converted/derivatives/work/run roots；
- fail-if-exists 默认，覆盖/废弃已有 run 必须单独审批；
- native converter 的 release readiness、release approval、rollback、audit、checksum 规则继续有效；集中用户审批不能替代发布级放行证据；
- 不启用 dcm2niix、MATLAB、SPM、DPABI 或任意外部命令；
- runner 只接受 server-issued ticket 和 reviewed plan binding；
- partial output 不能注册为成功转换输入；失败写入必须隔离并可回滚；
- 不修改 Pipeline Runtime 调度语义，只新增受控 node/plugin 和必要 adapter。

## 3. Required Behavior

目标调用链：

```text
Agent goal
  -> conversion dry-run/readiness
  -> Reviewed Plan includes native DICOM conversion node
  -> central Approval Summary includes conversion scope
  -> existing granular conversion confirmations + release evidence
  -> Execution Ticket binds node/backend/input/output/checksums
  -> Execution Gateway
  -> Pipeline Runtime
  -> registered native conversion runner
  -> artifact registration + provenance + rawdata post-check
  -> preprocessing input handoff
```

旧 endpoint 只能：

1. 调用同一 shared application service/gateway，作为兼容适配器；或
2. 在迁移窗口后明确返回 retired/upgrade guidance。

不得继续保留直接调用 conversion service 的主路径。

## 4. Files

### Create

- `src/backend/app/runtime/node_registry_plugins/conversion_nodes.py`
- `src/backend/app/services/reviewed_conversion_service.py`
- `src/backend/app/services/conversion_artifact_registration.py`（仅在现有 registration service 无法复用时）
- `tests/unit/test_conversion_execution_ticket.py`
- `tests/unit/test_conversion_gateway_dispatch.py`
- `tests/unit/test_conversion_agent_task_integration.py`

### Modify

- `src/backend/app/runtime/node_registry_plugins/create.py`
- `src/backend/app/runtime/tool_catalog.py`
- `src/backend/app/runtime/execution_entry_inventory.py`
- `src/backend/app/api/conversion_routes.py`
- `src/backend/app/services/dicom_conversion_execution.py` 或 `native_dicom_conversion_execution.py`：仅抽取 runner-safe callable；
- `src/backend/app/services/execution_ticket_service.py`：仅在现有 ticket 无法表达 checksum/rollback binding 时做兼容扩展；
- `src/backend/app/schemas/execution_ticket.py`：同上，需 schema migration；
- Agent goal planner、Approval Summary 和 AgentTaskReadModel；
- 相关 conversion、ticket、gateway、artifact、rawdata tests。

### Protected / Read Before Edit

- `src/backend/app/runtime/execution_gateway.py`
- `src/backend/app/runtime/pipeline_executor.py`
- `src/backend/app/planner/approval_gate.py`
- conversion safety/readiness/release approval/rollback services；
- `docs/预处理与科学计算/DICOM转换/` 现有契约；
- `docs/安全与审批/安全边界.md`。

若需要修改 protected module，先写 characterization test 和 invariants diff；不得通过 route 复制绕开。

## 5. Detailed Tasks

### 5.1 Characterization

1. 固化 prepare、persist approval、release readiness、execute、result registration、rollback 的 current responses。
2. 记录所有文件写入、状态写入、audit 和 failure points。
3. 证明 rawdata 不变性和 output root containment。

### 5.2 Node and catalog

1. 建立稳定 `node_id`，明确 input/output artifact contracts、backend id、approval required、rawdata read-only。
2. plugin registration duplicate ID 必须失败。
3. Tool Catalog、safe allowlist、Execution Ticket 和 audit 都包含该 node。

### 5.3 Shared application service

1. 把 route 内执行逻辑抽到 shared service。
2. service 验证 reviewed plan、release evidence、approval summary、ticket、path、rawdata snapshot。
3. 仅 Gateway 可以 dispatch runner。
4. runner 完成后写 artifact registry/provenance，并比较 rawdata snapshot。
5. 任何 post-check 失败都保持 failed/partial，不能注册 preprocessing-ready handoff。

### 5.4 Agent integration

1. 目标需要 DICOM 时计划包含 conversion dependency。
2. Approval Summary 分区展示 conversion 和 preprocessing 的写入范围/确认项。
3. 一个用户审批可以生成多个 canonical approval records，但每条都绑定自己的 scope/hash；不得用模糊的全局 boolean。
4. conversion 成功证据出现后才进入 preprocessing；失败进入 needs_attention/recovery policy。

## 6. Acceptance Criteria

- [ ] DICOM 主执行路径只经过 Gateway/Pipeline Runtime/registered runner。
- [ ] legacy endpoint 不再直接 dispatch。
- [ ] ticket 绑定 project、plan、node、backend、input root、output root、allowlist、approval context、expiry、retry policy。
- [ ] release approval/readiness 和 user approval 均为必需，互不替代。
- [ ] rawdata 前后 manifest 完全一致。
- [ ] output reload、shape/dtype/sidecar/provenance/checksum 和 registration 通过。
- [ ] partial/failed conversion 不产生 preprocessing-ready handoff。
- [ ] retry/resume 不覆盖成功产物，不重复消费 ticket。
- [ ] `execution_entry_inventory` 只保留一个 active conversion execution entry。
- [ ] 没有外部命令、MATLAB/SPM/DPABI 或桌面控制。

## 7. Hazard/Test Matrix

| Hazard | Required test |
|---|---|
| H10-02 duplicated execution | legacy + agent route gateway call graph |
| H10-03 approval scope | separate canonical records、hash tamper、release evidence missing |
| H10-12 DICOM bypass | inventory、ticket consumption、runner invocation guard |
| H10-13 path expansion | symlink, traversal, rawdata-under-output, cross-project root |
| partial artifact lie | injected mid-conversion failure, no handoff registration |
| rawdata mutation | pre/post checksum/size/mtime exact comparison |

## 8. Validation Commands

```text
python -m pytest tests/unit/test_conversion_execution_ticket.py tests/unit/test_conversion_gateway_dispatch.py tests/unit/test_conversion_agent_task_integration.py --tb=short --basetemp=.pytest_tmp
python -m pytest tests/unit/test_native_dicom_to_nifti.py tests/unit/test_dicom_conversion_safety.py tests/unit/test_dicom_conversion_public_execution_schema.py --tb=short --basetemp=.pytest_tmp
python -m pytest tests/unit/test_execution_ticket.py tests/unit/test_node_registry_plugins.py tests/unit/test_path_safety.py --tb=short --basetemp=.pytest_tmp
python -m pytest --tb=short --basetemp=.pytest_tmp
```

每次 pytest 后执行规定清理。真实数据验证只读 `data/DemoData`，转换输出写入忽略的临时项目目录，禁止提交数据和产物。

## 9. Stop Conditions

- release readiness/approval 无法映射到 ticket 且只能被绕过；
- 需要修改 rawdata、覆盖现有产物或开放 external command；
- Pipeline Runtime 无法表达 conversion dependency 而需要重写调度器；
- ticket schema migration 破坏现有 reviewed preprocessing；
- 维护者未明确批准 protected execution change。

## 10. Completion Report Additions

附旧/新执行入口 inventory、gateway call graph、node contract、ticket payload 摘要、approval 双层证据、rawdata manifest diff、failure/rollback trace、artifact reload/provenance 和 legacy endpoint 兼容结论。
