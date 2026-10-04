# MedImage Agent

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue)](https://www.python.org/)
[![Version](https://img.shields.io/badge/version-v0.6.0--rc1-1976d2)](docs/发布记录/v0.6.0-rc1.md)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.136%2B-green)](https://fastapi.tiangolo.com/)
[![React](https://img.shields.io/badge/React-18%2B-61dafb)](https://react.dev/)
[![TypeScript](https://img.shields.io/badge/TypeScript-5%2B-3178c6)](https://www.typescriptlang.org/)

[English](README.md) | **中文**

MedImage Agent 是面向静息态 fMRI（rs-fMRI）研究工程的本地确定性 Plan-then-Execute 平台。语言模型可以帮助规划、解释和校验请求；实际计算必须经过项目任务生命周期，并由 Pipeline Runtime 和已注册的 node runner 执行。

本项目用于研究工程，不用于临床诊断、治疗决策或其他临床用途。

## 当前项目状态

应用与打包版本仍为 **0.6.0-rc1**。0.6.0-rc2 收敛工作已终止；本项目没有声明 0.6.0-rc2 或 0.7.0 已发布。当前源码仍包含开发中的工作区改动，因此本地 Windows 应用属于诊断包，不是发布候选。打包来源、人工复核和发布关卡见 [PROJECT_STATE.md](PROJECT_STATE.md)。

## 项目能力

- 面向 BIDS 和已转换影像数据的项目级工作流；源数据只读。
- Agent Task 先生成 Reviewed Plan 并收集必要决策，再展示带哈希的 Approval Summary。获批执行复用既有 Approval Gate、Execution Ticket、唯一 Execution Gateway 和 Pipeline Runtime。
- 在具备所需输入时，使用项目内 Python 实现执行预处理和 rs-fMRI 指标计算。各阶段的真实能力及验证等级见[能力矩阵](docs/项目概览/能力矩阵.md)。
- 只读展示运行、产物、评估、审计和来源追溯信息。
- 项目记忆和受控 Harness 为可选能力，默认关闭授权和功能门控；它们不会授予执行权限，也不能证明科学有效性。

生成数值结果不代表通过独立参考验证。部分原生预处理阶段是简化实现，并非每项指标都有独立参考数据；具体限制见能力矩阵。

## 架构与安全

    React + TypeScript 前端（浏览器或 Electron）
        -> 共享 HTTP API
    FastAPI 路由与 schema
        -> 领域 service 和 read model
    受审 Agent 生命周期
        -> Approval Gate -> Execution Ticket -> 唯一 Execution Gateway
        -> Pipeline Runtime -> 已注册 node runner
        -> 项目级状态、产物、审计和来源追溯

Rawdata 和已登记源数据保持只读；写入只能进入获批的项目输出目录。前端通过共享 API client 和批准的 Electron bridge 访问后端，不直接访问文件系统。模型输出、聊天内容和记忆都不是执行授权。

DICOM 转换默认阻断。执行需要当前 release-readiness 证据、显式确认、审计记录和安全输出路径。项目内置转换器支持经典单帧 MR 序列和 Siemens 单帧 mosaic MR 时间序列；不会调用 MATLAB、SPM 或 DPABI 可执行程序。

## 快速开始

### 环境要求

- Python 3.11 或更新版本。
- Node.js 20.x 系列需 20.19 或更新版本，或使用 22.12 及更新版本。
- 构建和运行打包后的 Electron 桌面应用需要 Windows。
- CuPy 为可选依赖，仅用于明确支持的 GPU 路径。

### 安装

    python -m venv .venv

激活环境后安装后端和前端依赖：

    # Windows PowerShell
    .\.venv\Scripts\Activate.ps1
    python -m pip install -r requirements.txt
    npm --prefix src/frontend install

    # macOS 或 Linux
    source .venv/bin/activate
    python -m pip install -r requirements.txt
    npm --prefix src/frontend install

### 启动开发应用

在一个终端启动后端：

    python -m uvicorn src.backend.app.main:app --host 127.0.0.1 --port 8000

在另一个终端启动前端：

    npm --prefix src/frontend run dev

仓库也提供 Windows 的 start.bat 和 macOS/Linux 的 start.sh。所需端口已被占用时，脚本会停止并提示，不会终止现有进程。

### 验证修改

后端检查请使用当前项目 Python 环境：

    python -m pytest --collect-only -q --basetemp=.pytest_tmp
    python -m pytest --tb=short --basetemp=.pytest_tmp

前端检查：

    npm --prefix src/frontend run format:check
    npm --prefix src/frontend run typecheck
    npm --prefix src/frontend run test
    npm --prefix src/frontend run build

pytest 退出后，请按 AGENTS.md 检查并清理本次测试产生的缓存和临时目录。

## Windows 桌面包

在仓库根目录构建 Electron 未打包应用：

    powershell -ExecutionPolicy Bypass -File desktop\packaging\build_all_windows.ps1 -DirOnly -PythonExe .\.venv\Scripts\python.exe

日常保留的打包目录为 desktop/electron/dist/win-unpacked/。可执行文件必须和旁边的 resources、Electron 运行文件一起保留，单独的 EXE 不能独立运行。日常构建应覆盖该目录；没有明确 Release 任务时，不保留带时间戳副本、安装器或 portable EXE。

重建后检查 Electron 契约并运行打包 smoke：

    npm --prefix desktop/electron run check
    powershell -ExecutionPolicy Bypass -File desktop\packaging\test_electron_packaged_smoke.ps1
    powershell -ExecutionPolicy Bypass -File desktop\packaging\test_sandbox_packaged_smoke.ps1

构建成功、sidecar 健康、打包 renderer smoke、可见 GUI 工作流和科学验证属于不同的证据等级，不能相互替代。详见[桌面应用打包](docs/桌面与前端/桌面应用打包.md)。

## 仓库目录

- src/backend/app：API、schema、领域 service、Pipeline Runtime、node registry 和科学计算 kernel。
- src/frontend/src：共享 API client、类型、国际化和功能工作区。
- desktop/electron：Electron 壳、preload bridge 和打包 renderer。
- desktop/packaging：Windows 构建和隔离 smoke 脚本。
- docs：当前架构、安全边界、能力等级、用户指南和版本发布记录。
- specs：持久规范、阶段记录和人工审核关卡。
- tests：后端单元、契约、集成测试和前端测试。

## 项目文档

- [当前项目状态](PROJECT_STATE.md)
- [文档索引](docs/文档索引.md)
- [系统架构](docs/架构与决策/系统架构.md)
- [能力矩阵](docs/项目概览/能力矩阵.md)
- [安全边界](docs/安全与审批/安全边界.md)
- [真实项目运行生命周期](docs/安全与审批/真实项目运行生命周期.md)
- [桌面应用打包](docs/桌面与前端/桌面应用打包.md)
- [发布说明：0.6.0-rc1](docs/发布记录/v0.6.0-rc1.md)
