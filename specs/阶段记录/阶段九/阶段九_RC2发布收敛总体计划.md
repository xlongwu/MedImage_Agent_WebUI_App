# 阶段九总体计划：RC2 功能冻结、证据收敛与 Windows 发布验证

> **Status：Ready for Implementation**
> **Task Mode：Release and Packaging Mode + Scientific Validation Mode**
> **Target：v0.6.0-rc2**
> **Packaging candidate baseline：`6a392c15079f51c16a8e3c2a035915972aabd9ff`；后续运行时或打包配置修复将产生新的候选提交并使旧构建证据失效。**

## 1. Scope Anchor

**目标**：不扩展当前能力边界，在 Windows 打包应用、三名真实 DemoData 被试和远端 CI 上证明当前源码能够被构建、启动、执行、退出、恢复和局部重试，并形成可追溯的 RC2 发布证据。

**必须完成**：

- `PROJECT_STATE.md`、能力矩阵、阶段七/八状态和发布说明与当前代码一致。
- `main` 冻结新执行路径、科学算法、能力等级、公共契约和必选依赖。
- 打包产物从同一个候选提交重新构建；旧 `dist/` 产物不得充当证据。
- 使用 `data/DemoData/FunRaw` 与 `T1Raw` 下的 `Sub_001`、`Sub_002`、`Sub_003` 完成真实多受试者链路。
- 执行前后对 rawdata 文件清单、大小、mtime 和 checksum 做只读比较。
- 验证正常退出、运行中强制终止、重启恢复、失败被试隔离、局部 retry/resume 和重复命令幂等。
- 候选提交的远端 backend、frontend、desktop CI 全绿。
- 版本、发布说明、构建产物、checksum 和已知限制一致。

**必须不做**：

- 不引入 MATLAB、SPM、DPABI 或其他外部软件执行依赖。
- 不启用外部桌面控制、任意命令、无界循环或未经审批的恢复。
- 不修改用户 DICOM、NIfTI、BIDS 或 rawdata。
- 不把 synthetic/preview/partial 输出升级为正式科学验证结果。
- 不因发布压力降低测试、路径、审批、审计或产物真实性要求。

## 2. Evidence Summary

| 事实 | 当前证据 | 发布含义 |
|---|---|---|
| 当前版本面均为 `0.6.0-rc1` | `src/backend/app/version.py`、frontend/electron `package.json`、`pyproject.toml` | 通过发布关卡后统一升级 `rc2` |
| 阶段七/八执行与恢复源码已落地 | commit `17e3ebac`；Execution Gateway/Ticket、Observation、Goal Evaluation、Recovery services | 冻结其契约，只做发布阻塞修复 |
| 当前打包候选 | commit `6a392c15` | 已完成源码、unpacked/portable/NSIS 构建与 smoke、打包 sidecar/API 三被试 E2E 和远端 CI 取证 |
| 本地源码级全量后端验证 | `4108 passed, 16 skipped` | 证明源码回归，不证明打包真实数据工作流 |
| 阶段七/八 focused 验证 | `103 passed, 1 skipped` | symlink 用例因 Windows 权限跳过，保留风险 |
| 本地前端验证 | format、typecheck、`238` tests、build 通过 | exact-SHA 远端 frontend job 同样通过 |
| DemoData 有三名成对被试 | `Sub_001`、`Sub_002`、`Sub_003` 同时存在于 FunRaw/T1Raw | RC2 多受试者 E2E 固定使用三名被试 |
| exact-SHA Windows 产物已重建 | unpacked、portable、NSIS 来自 `6a392c15` | 三类产物 smoke/安装验证通过；打包 sidecar/API 工作流通过，不替代 UI 驱动证据 |
| 正式 atlas-grounded FC 仍需项目内已登记 atlas | `docs/项目概览/能力矩阵.md` | 没有合格 atlas 时不得把 synthetic FC 记为正式通过 |

候选构建、smoke、产物 hash 和 CI job URL 见
[RC2 候选证据](RC2候选证据_2026-07-16.md)。

## 3. Implementation Ledger

### 9A：状态同步与功能冻结

- 更新项目状态、能力矩阵和阶段七/八状态。
- 建立 RC2 变更准入：只允许发布阻塞修复、测试、证据和文档。
- 对执行入口、node registry、科学 kernel、依赖清单和 API schema 建立候选提交差异审查。

**完成定义**：不存在“计划文档仍写未实施”或“能力矩阵仍声称外部 dcm2niix 执行”等已知矛盾；冻结规则可由提交差异审计执行。

### 9B：候选构建与打包启动

- 记录 Git commit、Windows 版本、Python、Node、npm、PyInstaller、Electron 和依赖锁文件摘要。
- 使用 `desktop/packaging/build_all_windows.ps1` 从候选提交构建 backend sidecar、frontend 和 Electron unpacked 应用；条件满足时再构建 NSIS/portable。
- 运行 packaged desktop smoke，证明 backend health、React mount、renderer 存活、无 console error、安全 Electron 配置和退出后 sidecar 回收。

**完成定义**：构建成功、应用真实启动成功、smoke 成功和用户流程成功分别记录，不相互替代。

### 9C：三被试真实科学 E2E

- 对 `Sub_001`、`Sub_002`、`Sub_003` 建立转换前 rawdata manifest。
- 通过打包应用创建真实项目并完成 DICOM 转换、转换产物登记、reviewed preprocessing、artifact reload、validation 和 report handoff。
- 逐被试记录输入序列、输出 NIfTI/JSON、阶段状态、shape/dtype/checksum、provenance、warnings 和最终 Goal Evaluation。
- atlas-grounded FC 只有在使用项目内登记、许可和 provenance 完整的 atlas 时才算正式通过；否则只记录到可证明的 metric map 层，并保持 FC 为 preview/partial。
- 执行后重新计算 rawdata manifest，要求完全一致。

**完成定义**：三个被试均有独立状态和可重载产物；任何失败/部分结果被如实标记；rawdata 零变化。

### 9D：退出、崩溃、恢复与局部重试

- 场景 1：空闲时正常关闭，确认 Electron 与 sidecar 均退出。
- 场景 2：运行中正常关闭，确认状态原子持久化且未完成任务不被标为成功。
- 场景 3：运行中强制终止 Electron/backend，重启后 reconciliation 不重复 dispatch 已完成工作。
- 场景 4：制造一个 derivatives/work 范围内的受控单被试失败，确认其他被试继续且总体为 partial/failed truthfully。
- 场景 5：经 Recovery Proposal、审批和 child ticket 仅重试失败范围，确认隔离输出、配额、幂等和回评闭环。
- 场景 6：重复提交相同恢复命令，确认不重复消费票据、不重复写有效产物。

**完成定义**：每个场景都有状态时间线、审计、进程、产物和 rawdata 证据；没有未审批 runner 调用。

### 9E：CI 证据与 RC2 发布

- 为最终候选提交保存 GitHub Actions backend/frontend/desktop job URL 与结论。
- 运行版本一致性、release-note integrity、frontend、backend、desktop 和打包验证矩阵。
- 仅在候选提交及其构建产物全部通过后更新四个版本面为 `0.6.0-rc2`，创建 RC2 发布说明和 checksum 清单。
- tag、GitHub Release 和产物上传仍需维护者明确发布授权；准备完成不等于自动发布。

**完成定义**：候选提交、CI、源码测试、打包产物、真实 E2E、版本与发布说明形成一一对应的证据链。

## 4. Blast Radius Map

| 表面 | 阶段九策略 | 风险 |
|---|---|---|
| Execution Gateway/Ticket/Capability | 冻结；仅修复 E2E 暴露的阻塞缺陷 | 高 |
| Scientific kernels/stage catalog | 冻结公式和能力等级 | 高 |
| Electron/PyInstaller packaging | 允许发布阻塞修复 | 高 |
| ProjectStore/SQLite/recovery state | 允许恢复阻塞修复并要求迁移回归 | 高 |
| Frontend reviewed workflow | 允许流程阻塞和错误显示修复 | 中 |
| 文档、release notes、checksums | 按证据持续同步 | 中 |
| rawdata、DemoData source files | 只读，永不编辑或提交 | 严禁变更 |

## 5. Hazards & Mitigations

| H-ID | 风险 | 缓解 | 验证 |
|---|---|---|---|
| H9-01 | 旧打包产物被误当候选证据 | 每次候选从 commit 重建并记录产物 hash | commit/build manifest 对照 |
| H9-02 | 单被试成功掩盖多被试调度缺陷 | 固定三名 DemoData 被试，逐被试断言 | subject-level timeline/artifact 表 |
| H9-03 | 打包应用启动成功被误当 E2E 成功 | 分离 build、launch、smoke、workflow 四种结论 | 四级证据均存在 |
| H9-04 | 崩溃后重复执行或覆盖产物 | 原子状态、一次性 child ticket、隔离 attempt 输出 | kill/restart/replay 场景 |
| H9-05 | 一个失败被试导致全局假成功或全局丢失 | failed-subject isolation 与 truthful partial | 单被试受控失败场景 |
| H9-06 | rawdata 被转换/恢复流程修改 | 前后 manifest + checksum + mtime 对比 | 必须零差异 |
| H9-07 | synthetic atlas 被当正式 FC | 要求项目内登记 atlas；否则保持 preview/partial | capability/status/assertion |
| H9-08 | 本地绿色但远端 CI 红色 | 最终候选必须远端三 job 全绿 | 保存 job URL、SHA 和结论 |
| H9-09 | 发布修复偷偷扩大能力 | 每个候选做冻结差异审计 | registry/schema/kernel/dependency diff |
| H9-10 | 版本和产物不对应 | 最后升级版本，重建并重新跑必要 smoke | 版本一致性 + artifact hash |

## 6. Test and Validation Plan

| 层级 | 最低验证 |
|---|---|
| Backend | collect-only、full suite、release/recovery/native E2E focused tests |
| Frontend | format:check、typecheck、lint、test、project-runs smoke、build |
| Desktop | `npm run check`、sidecar build、unpacked build、packaged launch smoke |
| Scientific | 三被试 reload/shape/dtype/checksum/provenance/status truthfulness |
| Recovery | graceful exit、forced kill、restart、partial subject failure、approved retry、replay refusal |
| Safety | rawdata manifest unchanged、path containment、approval/audit/ticket gates |
| Remote | GitHub Actions backend/frontend/desktop all green for exact SHA |
| Release | version consistency、release-note integrity、artifact inventory/checksums |

每次 pytest 后按 `AGENTS.md` 清理仓库根目录直接子目录 `.pytest_cache/` 与 `.pytest_tmp*`。运行产物、用户数据、SQLite、日志和打包输出默认不进入 Git。

## 7. Proof Obligations

| 声明 | 必须提供的证明 |
|---|---|
| RC2 不扩大能力 | 最终候选相对冻结基线的 registry/kernel/schema/dependency diff |
| Windows 包真实可用 | exact-SHA 构建日志 + packaged smoke + 用户流程记录 |
| 多被试真实执行 | 三被试输入/状态/产物/验证表，不接受只跑一个被试 |
| 崩溃恢复不重复执行 | kill/restart 后 attempt、ticket、state 和 artifact lineage |
| 局部重试受控 | proposal、approval、child ticket、quota、isolated output、回评 |
| rawdata 只读 | 执行前后 manifest/checksum/size/mtime 完全一致 |
| 远端 CI 绿色 | exact-SHA 的三 job URL 和成功结论 |
| 发布产物可追溯 | version、commit、toolchain、artifact hash、release note 一致 |

## 8. Assumption Registry

| A-ID | 假设 | 分类 | 处理 |
|---|---|---|---|
| A9-01 | 当前工作是稳定化而非新增能力 | USER DECISION | 默认选择 `v0.6.0-rc2` |
| A9-02 | `data/DemoData` 三名 FunRaw/T1Raw 被试是本轮真实数据范围 | VERIFIED | E2E 固定三名被试 |
| A9-03 | Windows 是本轮唯一发布平台 | USER DECISION | Linux/macOS 延后，不阻塞 RC2 |
| A9-04 | 项目不得依赖 MATLAB/SPM/DPABI 等外部软件 | USER DECISION | 所有 E2E 只走项目内 Python/packaged runtime |
| A9-05 | 缺少合格项目内 atlas 时不得声称正式 atlas FC E2E | CRITICAL | 保持 preview/partial，列为已知限制或另行补充合规 atlas |

## 9. Release Gates

| Gate | 退出条件 | 2026-07-16 状态 |
|---|---|---|
| G9-0 状态同步 | PROJECT_STATE、能力矩阵、阶段状态一致 | 通过；状态和边界已同步到当前候选 |
| G9-1 功能冻结 | 冻结基线和变更准入生效 | 生效；仅接受发布阻塞修复与证据 |
| G9-2 候选构建 | exact-SHA Windows build/launch/smoke 通过 | 通过；`6a392c15` unpacked、portable 和 NSIS 构建/启动/安装验证成功 |
| G9-3 真实 E2E | 三被试科学链路与 rawdata 不变性通过 | 源码级及 exact-candidate 打包 sidecar/API 级通过；Electron UI 驱动仍待验证 |
| G9-4 恢复 E2E | 退出、崩溃、恢复、局部重试和 replay 测试通过 | 已证明正常退出与转换登记复用；运行中退出/强杀/恢复/隔离/局部重试待执行 |
| G9-5 CI | exact-SHA 远端 backend/frontend/desktop 全绿 | 通过；run `29469529639` 的三个 job 全部成功 |
| G9-6 RC2 | 版本、说明、产物、checksum、已知限制全部一致 | 部分通过；安装包/portable/checksum 已验证，版本升级和正式发布仍由 G9-4 阻塞 |

任何 gate 失败都阻止 RC2 发布。只有出现明确的新能力或破坏性契约变更时才改走 `v0.7.0-rc1`；不得用版本升级掩盖未完成的 RC2 验证。
