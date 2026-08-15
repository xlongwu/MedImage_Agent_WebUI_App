from __future__ import annotations

import sys
from pathlib import Path

import yaml

from src.backend.app.config import ProjectSettings
from src.backend.app.runtime.scheduler import create_scheduler_plan
from src.backend.app.schemas.pipeline_schema import load_pipeline_yaml
from src.backend.app.tools.cli_utils import emit_json_result


def _load_project_config(path: Path) -> dict:
    """Validate the config used by the scheduler without invoking Agent planning."""
    ProjectSettings.from_yaml(path)
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def main() -> int:
    project_config_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("examples/project_config_dataset.yaml")
    pipeline_path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("examples/pipeline_subject_preprocess_parallel.yaml")

    project_config = _load_project_config(project_config_path)
    pipeline = load_pipeline_yaml(pipeline_path)

    result = create_scheduler_plan(pipeline, project_config)

    return emit_json_result(result, failure_code=1)


if __name__ == "__main__":
    raise SystemExit(main())
