# MedImage Agent

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue)](https://www.python.org/)
[![Version](https://img.shields.io/badge/version-v0.6.0--rc1-1976d2)](docs/发布记录/v0.6.0-rc1.md)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.136%2B-green)](https://fastapi.tiangolo.com/)
[![React](https://img.shields.io/badge/React-18%2B-61dafb)](https://react.dev/)
[![TypeScript](https://img.shields.io/badge/TypeScript-5%2B-3178c6)](https://www.typescriptlang.org/)

[English](README.md) | **中文**

MedImage Agent 是面向静息态 fMRI（rs-fMRI）研究的确定性
Plan-then-Execute 桌面平台。LLM 只负责规划和建议；执行必须留在
Pipeline Runtime 和注册节点 runner 内。

本项目是研究工程平台，不用于临床诊断或医疗决策。

当前发布线：**v0.6.0-rc1**。详见
[发布说明](docs/发布记录/v0.6.0-rc1.md)。

## 快速开始

### 环境要求

- Python 3.11+
- Node.js `^20.19.0` 或 `>=22.12.0`（Vite 8 engine 要求）
- `nibabel` 与 `pydicom` 已包含在核心依赖中，用于项目内置 NIfTI 与
  DICOM 路径
- CuPy 可选，仅用于 GPU 路径

### 安装

```bash
pip install -r requirements.txt
cd src/frontend && npm install
```

### 启动开发服务

```bash
uvicorn src.backend.app.main:app --host 127.0.0.1 --port 8000
cd src/frontend && npm run dev

# 或一键启动：
start.bat
./start.sh
```

### 运行测试

使用当前项目 Python 环境。在 Windows 上可先激活 `.venv`，或显式传入项目解释器。

```bash
python -m pytest --collect-only -q --basetemp=.pytest_tmp
python -m pytest --tb=short --basetemp=.pytest_tmp
```

前端验证：

```bash
npm --prefix src/frontend run format:check
npm --prefix src/frontend run typecheck
npm --prefix src/frontend run test
npm --prefix src/frontend run build
```

## 桌面应用

Windows 桌面应用使用 Electron 壳和 PyInstaller 后端 sidecar。前端仍然只通过
HTTP API 与后端通信，不直接访问本地文件系统。

主要 Windows 打包入口会构建前端、PyInstaller 后端 sidecar、launcher 和 Electron
桌面包：

```powershell
powershell -ExecutionPolicy Bypass -File desktop\packaging\build_all_windows.ps1 -DirOnly -PythonExe .\.venv\Scripts\python.exe
```

未打包安装器形态的 Windows 可执行文件会生成在
`desktop/electron/dist/win-unpacked/MedImage Agent.exe`。构建成功只证明制品已组装，
不能替代交互式 GUI 工作流 smoke 验证。

详见[桌面应用打包](docs/桌面与前端/桌面应用打包.md)。

## 架构

```text
Frontend (React + TypeScript + Vite)
    -> HTTP API
API Layer (FastAPI + Pydantic)
    -> Services and Schemas
Agent Runtime (Plan-then-Execute + Approval Gate)
    -> Pipeline Runtime (DAG Executor + Scheduler)
    -> Plugin Node Registry + 权威 Node Contract
    -> Tool Catalog（只读展示投影）
```

状态保存在本地并按项目隔离：SQLite 存储项目元数据，JSON 存储运行状态和
artifact。运行时状态写入使用原子文件写入。Pipeline Runtime 是唯一 pipeline
执行路径。

项目记忆是默认关闭的可选功能。只有安装级门控和项目授权同时启用后，经过
审核的偏好与项目经验才会写入独立的本地 Memory SQLite，并在规划前以有界、
强类型上下文注入。科学记忆永远不是执行约束，必须在当前任务中重新确认，且
实际使用的快照会绑定 Reviewed Plan 和 Approval Summary。显式关闭记忆时返回
强类型 disabled context；记忆已启用但数据库或 outbox preflight 失败时，以结构化
错误阻断规划，不会静默使用空 context 继续；运营积压则投影为 partial。详见
[记忆系统设计方案](docs/架构与决策/记忆系统设计方案.md)。

当前 router、service、schema、node registry、前端 API、存储和桌面边界见
[架构文档](docs/架构与决策/系统架构.md)。

## 当前源码工作流

```text
选择 BIDS/rawdata 或 converted BIDS
-> 创建项目
-> 生成 project_config.yaml 和 dataset_index.json
-> 在项目 Agent 工作区描述目标
-> 回答必要的数据或科学决策
-> 审查一份带哈希的 Approval Summary
-> 审批未发生变化的计划和执行范围
-> 查看有界进度和结果
-> 通过 Runs 或技术详情查看 validation、logs、artifacts 和 provenance
```

Agent Task API 和源码界面只是既有 lifecycle、Reviewed Plan、Approval Gate、
Execution Ticket、唯一 Execution Gateway、Pipeline Runtime 和 artifact 证据之上的
投影与命令入口，不建立第二条执行路径。该源码能力尚不代表已经打包或发布
`v0.7.0`；当前各版本面仍为 `v0.6.0-rc1`。
每次执行都会持久化不可变 dispatch 和有序 Gateway 事件；相同 command 重放只返回
已持久化结果，不会再次运行 executor。已经进入 started 但缺少终态的崩溃窗口会
报告 outcome-unknown 并要求检查证据，不会自动重复执行。

DICOM/FunRaw/T1Raw 数据支持只读检测和转换 dry-run 预览。只有存在有效的 release
readiness 证据时，原生转换才能进入受审网关路径；旧公共转换端点继续 fail-closed，
系统不会仅凭发现 rawdata 就自动转换。

Reviewed preprocessing 工作流运行在 converted/sandboxed 输入上，仍然需要显式确认和
环境变量门控。当前 stage catalog 会区分 metadata-only、planned、blocked、computed、
partial 和 preview 状态，避免 UI 将占位或预览结果呈现为已完成的数值输出。

## 项目结构

```text
src/backend/app/
  api/                         领域 router 和 API middleware
  core/                        配置、异常、日志
  schemas/                     请求/响应与契约 schema
  services/                    业务逻辑和 read model
  runtime/                     pipeline executor、state store、node registry
  runtime/node_registry_plugins/
                               node runner 插件注册表
  tools/                       处理模块、QC、wrapper、CLI helper

src/frontend/src/
  lib/api/                     统一 client 和领域 API module
  components/                  可复用 UI 面板
  features/                    feature 级 UI 组合
  hooks/                       共享 React hooks
  state/                       workflow state model
  types/                       共享前端类型

desktop/
  electron/                    Electron shell 和 smoke checks
  packaging/                   PyInstaller 与 Windows 构建脚本

docs/
  文档索引.md                   当前文档索引
  架构与决策/                   当前架构与 ADR
  项目概览/                     能力矩阵和兼容入口
  安全与审批/                   安全边界与运行生命周期
  预处理与科学计算/             科学计算与外部工具契约
  桌面与前端/                   桌面打包和前端指南
  发布记录/                     与版本绑定的历史发布说明

specs/
  规范/                         持久的工程与科学计算规范
  阶段记录/                     保留的阶段级历史记录

tests/
  unit/                        单元测试和源码契约测试
  integration/                 opt-in smoke / integration tests
```

## 安全架构

| 规则 | 机制 |
| --- | --- |
| Rawdata 只读 | 路径策略、checksum、审批文案 |
| 必须审批 | Tool Catalog + Approval Gate + 显式确认 |
| 防目录穿越 | `path_safety.py` 和 project/run artifact ID |
| 前端隔离 | HTTP API modules 和受控 Electron bridge |
| 执行限定在项目内 | 注册 Python runner、approval/readiness、audit records |
| 记忆仅作项目级建议 | 安装/项目授权、来源追溯、科学二次确认、计划哈希绑定、墓碑遗忘 |
| 仅研究用途 | UI 和文档警示 |

## 已知限制

- 不用于临床诊断或医疗决策。
- 预处理使用项目内置 Python 实现，不要求也不会调用 MATLAB、SPM 或 DPABI 可执行程序。
- DICOM 转换执行默认阻断，需要 release approval evidence 和多重确认。
- 内置 DICOM 转换当前支持经典单帧 MR 序列和 Siemens 单帧 mosaic MR
  时间序列；混合序列或不支持的格式会安全拒绝。
- ALFF/fALFF、ReHo 和 functional connectivity 在满足输入条件时已有 Python 后端路径；
  metadata-only 和 preview 输出仍会明确标注为对应状态。
- 当前发布线不包含 group statistics、classification、diagnosis model、report
  editor 或 auto-update 工作流。
- 桌面打包和 GUI smoke 需要兼容的本地 Windows 桌面环境。

## 文档

- [当前项目状态](PROJECT_STATE.md)
- [架构文档](docs/架构与决策/系统架构.md)
- [记忆系统设计方案](docs/架构与决策/记忆系统设计方案.md)
- [发布说明 v0.6.0-rc1](docs/发布记录/v0.6.0-rc1.md)
- [发布说明 v0.4.0-rc1](docs/发布记录/v0.4.0-rc1.md)
- [发布说明 v0.3.0-rc1](docs/发布记录/v0.3.0-rc1.md)
- [桌面应用打包](docs/桌面与前端/桌面应用打包.md)
- [真实项目运行生命周期](docs/安全与审批/真实项目运行生命周期.md)
- [安全边界](docs/安全与审批/安全边界.md)

## 许可证

本项目用于学术研究目的。
