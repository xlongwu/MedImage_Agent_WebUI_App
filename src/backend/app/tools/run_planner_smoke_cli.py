from __future__ import annotations

import argparse
from typing import Any

from src.backend.app.planner.pipeline_planner import draft_pipeline_plan, validate_pipeline_plan
from src.backend.app.tools.cli_utils import emit_json_result


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a Planner draft/validate smoke check.")
    parser.add_argument("--disease-type", default="Alzheimer")
    parser.add_argument("--modality", default="rs-fMRI")
    parser.add_argument("--downstream-task", default="ALFF/fALFF analysis")
    parser.add_argument("--available-data", default="T1w,BOLD")
    parser.add_argument("--constraints", default="")
    parser.add_argument(
        "--pipeline-path",
        default="",
        help="Optional explicit examples/*.yaml template path.",
    )
    args = parser.parse_args()

    payload: dict[str, Any] = {
        "disease_type": args.disease_type,
        "modality": args.modality,
        "downstream_task": args.downstream_task,
        "available_data": [item.strip() for item in args.available_data.split(",") if item.strip()],
        "constraints": [item.strip() for item in args.constraints.split(",") if item.strip()],
    }
    if args.pipeline_path:
        payload["pipeline_path"] = args.pipeline_path
    draft = draft_pipeline_plan(payload)
    validation = validate_pipeline_plan({"draft": draft})
    result = {"ok": bool(draft.get("ok")) and bool(validation.get("ok")), "draft": draft, "validation": validation}
    return emit_json_result(result, failure_code=1)


if __name__ == "__main__":
    raise SystemExit(main())
