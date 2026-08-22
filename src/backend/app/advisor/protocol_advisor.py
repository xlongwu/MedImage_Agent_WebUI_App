"""Protocol Advisor — recommend preprocessing pipeline templates."""
from __future__ import annotations

from typing import Any

from src.backend.app.core.config_schema import AgentModelRuntimeConfig

from src.backend.app.advisor.advisor_safety import (
    advisor_fallback,
    get_llm_config,
    is_llm_enabled,
    wrap_advisor_response,
)


def advise_protocol(
    modality: str = "rs-fMRI",
    task_goal: str = "",
    tr: float = 2.0,
    slice_count: int = 32,
    has_fieldmap: bool = False,
    available_data: list[str] | None = None,
    constraints: list[str] | None = None,
) -> dict[str, Any]:
    available_data = available_data or ["T1w", "BOLD"]
    constraints = constraints or []

    if not is_llm_enabled():
        return _deterministic_protocol_advice(
            modality, task_goal, tr, slice_count, has_fieldmap, available_data, constraints
        )

    # LLM path (when configured)
    try:
        return _llm_protocol_advice(
            modality, task_goal, tr, slice_count, has_fieldmap, available_data, constraints
        )
    except Exception:
        return advisor_fallback("protocol")


def _deterministic_protocol_advice(
    modality: str, task_goal: str, tr: float, slice_count: int,
    has_fieldmap: bool, available_data: list[str], constraints: list[str],
) -> dict[str, Any]:
    has_matlab = "no MATLAB license" not in str(constraints).lower() and "matlab not available" not in str(constraints).lower()

    if modality == "rs-fMRI":
        if has_matlab:
            template = "rsfmri_spm_standard_v1"
        else:
            template = "rsfmri_python_quickstart"
    else:
        template = "generic_preprocessing"

    suggestions = {
        "slice_timing_reference": "middle_slice",
        "smoothing_fwhm": [6, 6, 6],
        "filter_band": [0.01, 0.08],
        "nuisance_model": "friston24",
        "tr": tr,
    }

    warnings = []
    if not has_fieldmap:
        warnings.append("No fieldmap available; distortion correction will be skipped.")
    if not has_matlab:
        warnings.append("MATLAB not available; using Python-only pipeline (limited normalization).")

    return wrap_advisor_response({
        "recommended_pipeline_template": template,
        "parameter_suggestions": suggestions,
        "warnings": warnings,
        "unsupported_items": [
            "Fieldmap distortion correction (requires fieldmap data)",
        ] if not has_fieldmap else [],
    }, "protocol")


def _llm_protocol_advice(
    modality: str, task_goal: str, tr: float, slice_count: int,
    has_fieldmap: bool, available_data: list[str], constraints: list[str],
) -> dict[str, Any]:
    import json

    config = get_llm_config()
    prompt = f"""You are a medical imaging protocol advisor. Recommend ONLY, never execute.
Modality: {modality}
Goal: {task_goal}
TR: {tr}s, Slices: {slice_count}
Fieldmap: {has_fieldmap}
Available data: {available_data}
Constraints: {constraints}

Respond with JSON containing: recommended_pipeline_template, parameter_suggestions, warnings (list), unsupported_items (list)."""

    response = _call_llm(config, prompt)
    try:
        data = json.loads(response)
    except json.JSONDecodeError:
        data = {"raw_response": response}

    return wrap_advisor_response(data, "protocol")


def _call_llm(config: AgentModelRuntimeConfig, prompt: str) -> str:
    from src.backend.app.planner.llm_provider import call_openai_compatible_chat

    result = call_openai_compatible_chat(
        messages=[
            {
                "role": "system",
                "content": "Provide advice only. Return one JSON object and never authorize execution.",
            },
            {"role": "user", "content": prompt},
        ],
        config=config,
        temperature=0.3,
        max_output_tokens=min(500, config.max_output_tokens),
        response_format={"type": "json_object"},
    )
    if not result.ok:
        raise RuntimeError(result.errors[0] if result.errors else "AGENT_MODEL_PROVIDER_FAILED")
    return result.content
