"""ProjectSettings — 统一项目配置读取层。

提供从 YAML 加载项目配置的单一入口。
后续 LLM Planner、Plan Validator、Tool Catalog 等
模块应通过本模块读取配置，而非各自实现 _load_project_config。

Usage:
    from src.backend.app.config import ProjectSettings

    settings = ProjectSettings.from_yaml("examples/project_config_dataset.yaml")
    print(settings.runtime.work_dir)
    print(settings.safety.rawdata_readonly)
"""

from __future__ import annotations

from src.backend.app.config.settings import ProjectSettings

__all__ = ["ProjectSettings"]
