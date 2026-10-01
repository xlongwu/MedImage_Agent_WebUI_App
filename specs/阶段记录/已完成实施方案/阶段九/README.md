# 阶段九：RC2 功能冻结、证据收敛与 Windows 发布验证

> 状态：**已终止**（2026-10-01）。RC2 主线由维护者正式终止，发布线改走 `v0.7.0-rc1`。
> 本阶段归档结论是「已终止/未完成」，**不是发布完成**：`v0.6.0-rc2` 从未构建、从未打 tag、从未发布。
> 任务模式：Release and Packaging Mode；真实数据链路同时适用 Scientific Validation Mode。
> 原目标版本：`v0.6.0-rc2`（作废）。

本文件记录终止理由、逐关卡定级、分级证据和正式跳过项的闭环结论。范围、退出条件与证明义务的定义仍以
[总体计划](阶段九_RC2发布收敛总体计划.md)为准；`2026-07-16` 的候选取证见
[RC2 候选证据](RC2候选证据_2026-07-16.md)，该记录只对其记录的候选有效。

## 1. 终止原因

冻结基线 `6a392c15079f51c16a8e3c2a035915972aabd9ff` 已被其后的提交实质作废，且作废内容属于本阶段
冻结规则要求「退出 RC2 主线并重新立项」的类别，而不是发布阻塞修复：

| 检查项 | `6a392c15..743fb1bb` 实测 |
|---|---|
| 提交数 | 62 |
| 变更文件总数 | 1,412 |
| `src/` 下变更文件 | 692 |
| `src/backend/app/api` 与 `src/backend/app/schemas` 变更文件 | 111 |
| 路径含 `memory` 的变更文件 | 34 |

新增的项目级 Memory Domain（`src/backend/app/memory/`、`src/backend/app/api/memory_routes.py`、
`src/backend/app/api/memory_dependencies.py`）以及 Agent-first 生命周期与持久化契约变化，是
`AGENTS.md` §1.3 与本文档「冻结规则」下必须重新立项的能力扩展。「以 `v0.6.0-rc2` 稳定发布」的前提
因此不成立，启用 [阶段十 README](../阶段十/README.md) 中预先记录的
「维护者明确终止 RC2 主线」分支。

## 2. 关卡判定

判定取值：`通过` / `部分通过` / `证据已失效` / `正式跳过` / `待办`。范围与完成定义见总体计划 §3，
证明义务见 §7，退出条件与终局判定见 §9。

| Gate | 判定 | 判定依据 |
|---|---|---|
| G9-0 状态同步 | `通过`（仅文档一致性收敛，不构成任何发布证据） | 2026-10-01 按真实分级证据重写 `PROJECT_STATE.md`、能力矩阵说明段与本阶段文档；`PROJECT_STATE.md` 快照日期与其记录事件时间不再矛盾 |
| G9-1 功能冻结 | `证据已失效` | 见第 1 节：冻结基线被 62 个提交作废，其中含记忆域与新公共 API/持久化契约 |
| G9-2 候选构建 | `证据已失效` | `desktop/packaging/dist/` 不存在；`desktop/electron/dist/` 只有 `win-unpacked/` 与 `builder-debug.yml`，无 installer/portable；现存 `win-unpacked` 的 `resources/release/build-provenance.json` 记录 `clean=false`、SHA `4a587673bae0b90efd6ceb60293d7476c165bec3`、`2026-09-12T14:22:06Z` 构建，落后当前 `HEAD` 10 个提交 |
| G9-3 真实 E2E | `部分通过`；「真实数据由可见 Electron UI 驱动」子项 `正式跳过` | 真实 `data/DemoData` 三被试科学链路只有 packaged sidecar/**HTTP API** 证据；可见 Electron 三流程使用合成 atlas/preview fixture，结论为 truthful `partial` |
| G9-4 恢复 E2E | `部分通过`；打包多被试运行中强制终止 + 重启 reconciliation 为 `待办`（正式延期） | 正常退出、失败被试隔离与一次经批准的 `sub-003` 局部重试有记录；`desktop/packaging/test_electron_packaged_smoke.ps1:5` 只提供 `shell/bids/dicom/recovery` 四种 workflow，其中 `taskkill`（`:266`）仅在超时时清理进程，不是被验证的终止-恢复场景 |
| G9-5 远端 CI | `通过`（对当前 `HEAD`，但不授予发布许可） | GitHub Actions run `36845148426` 绑定 `743fb1bb48a5b13fb90b6d1703326cbcb13ed7da`，`backend`/`frontend`/`desktop`/`windows-sandbox` 四个 job 全部 `success`；旧候选 `6a392c15` 的 run `29469529639` 亦为历史全绿 |
| G9-6 RC2 发布 | `正式跳过`（RC2 不发布）；发布动作转为 `v0.7.0-rc1` 的 `待办` | 四个版本面 `src/backend/app/version.py`、`pyproject.toml`、`src/frontend/package.json`、`desktop/electron/package.json` 全部仍为 `0.6.0-rc1`；本地与远端 tag 止于 `v0.5.0-rc1`；`docs/发布记录/` 无 `v0.6.0-rc2.md`；`docs/发布记录/SHA256校验和.txt` 仍是 2026-06-08 的 v0.3.0-rc1 产物 |

任何 `证据已失效` 或 `待办` 都不因源码测试通过而自动关闭。

## 3. 分级证据

按 `AGENTS.md` §5.8，六层结论分别记录，不得互相替代。「可归因于当前 `HEAD`」为否意味着该层必须在新的
clean exact-SHA 候选上重跑才算数。

| 层 | 现有记录的最强结论 | 证据归属 | 可归因于当前 `HEAD` |
|---|---|---|---|
| build success | Electron unpacked 目录构建成功 | `6a392c15`（2026-07-16）；本机现存 dirty `4a587673` 包（2026-09-12） | 否 |
| sidecar health | 内置 backend `/api/health` ready，nonce/HMAC 证明校验通过 | `6a392c15` | 否 |
| packaged launch | unpacked、portable 启动及 NSIS 隔离安装后启动成功 | `6a392c15` | 否 |
| renderer smoke | React root 非空、main landmark 存在、renderer console error 为 0、退出后 owned sidecar 回收 | `6a392c15` | 否 |
| 人工/自动化 GUI workflow | 可见 Electron `bids`/`dicom`/`recovery` 三流程通过，但输入为合成 fixture，truthful `partial` | 2026-08-25 记录；write-once 证据目录未随仓库保留，SHA 不可复核 | 否 |
| 真实科学执行 | 真实三被试 21 个 float32 NIfTI、ALFF/fALFF 实际后端 `gpu-cupy`、rawdata 指纹不变 | `6a392c15`，且仅在 packaged sidecar/HTTP API 层 | 否 |

本机现存 dirty 包只有 `build-provenance.json`，没有 smoke 结果文件、截图或证据目录，因此它只能证明
build success 层，不能证明其后任何一层。

只读性基线已于 2026-10-01 重新复核：`data/DemoData` 仍为 1,104 个文件，`FunRaw` 与 `T1Raw` 下
`Sub_001`、`Sub_002`、`Sub_003` 成对存在；本任务未运行任何转换或执行流程，未写入 rawdata。

## 4. 正式跳过项的闭环结论

### 4.1 跳过：真实 DemoData 三被试由可见 Electron UI 驱动的科学 E2E（G9-3）

- 决定与日期：维护者批准降级为已知限制，2026-10-01。
- 理由：该层与已具备的两级证据在科学计算上等价——真实数据数值产物由 packaged sidecar 的受控 HTTP
  链路产生并注册，UI 驱动层已由合成 fixture 证明交互合同。补齐它需要真实 GPU/CuPy 环境与人工 GUI
  会话，不改变任何数值产物或能力等级，对发布正确性的边际收益低于其成本。
- 承担该风险的条件：只能主张「sidecar/API 真实数据 E2E + 合成 fixture 可见 UI E2E」两级结论；
  能力矩阵中 DICOM Conversion 与 FC 的 `visible Electron UI E2E pending` 表述必须保留；不得因跳过
  而把任何能力标为 `validated` 或 `Release Ready`。
- 重新启用触发条件：出现任一 UI 与 API 行为可能分叉的变更（Electron 审批 IPC bridge、Agent Task
  生命周期命令、结果投影或 artifact 发现根变化），或即将对外分发安装包/首次 GA 发布。

### 4.2 延期：打包多被试运行中强制终止 + 重启 reconciliation（G9-4）

- 决定与日期：维护者批准正式记为延期，2026-10-01；本轮不新增自动化代码。
- 理由：这是唯一必须写新打包侧自动化才能关闭的关卡。RC2 主线已终止，把它插入已作废的发布线不产生
  可归因证据；应在 `v0.7.0-rc1` 候选冻结后，在同一 clean exact-SHA 上与新构建证据一起取证。
- 承担该风险的条件：不得声称打包应用已验证崩溃/强杀恢复；`docs/桌面与前端/桌面应用打包.md` 关于
  隐藏 smoke、过期本地包或源码测试不得当作可见 Electron workflow 通过的约束继续生效；现有
  reconciliation 保证仍受源码级测试与启动时单 owner 有界扫描约束。
- 重新启用触发条件：`v0.7.0-rc1` 收敛窗口开始，或任何修改 Electron 生命周期、sidecar watchdog、
  单实例锁、startup reconciliation 的实现进入 `main`。

## 5. 主线

1. 按第 1 节记录终止理由并完成第 2 节定级。
2. 将当前待办移交 `PROJECT_STATE.md` 的 Next Work，不再由本阶段承载。
3. `v0.7.0-rc1` 的候选冻结、构建、恢复与发布关卡由新的 Release 任务立项，且必须先取得
   capability review。

## 6. 冻结规则

以下规则原样适用于 `v0.7.0-rc1` 收敛窗口；窗口由维护者显式开启时才生效：

- 新执行入口或 Runner；
- 科学公式、默认参数或能力等级升级；
- 新公共 API 或持久化契约；
- 新的必选依赖；
- 开启 MATLAB、SPM、DPABI、真实 GUI 自动化或外部命令执行；
- 任何 rawdata 写入或安全门控弱化。

冻结期间只接受发布阻塞修复、测试、证据和文档修正；上述类别的改动必须退出该 release 线并重新立项。
当前 `main` **没有**处于生效中的冻结窗口，因此本节目的不是声称当前处于冻结，而是保留可重新启用的规则。
