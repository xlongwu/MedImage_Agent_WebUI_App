"""Pure execution-precondition checks shared by approval services."""

from __future__ import annotations

from typing import Any

_PREPROCESSED_INPUT_KEYS = frozenset({"input_nii", "input_bold", "input_file", "input_path", "preprocessed_input"})
_REALIGNMENT_PRODUCERS = frozenset({"spm_realign_subject", "spm_smooth_subject", "native_preproc_full_execute"})
_NATIVE_REHO_PREREQUISITE_STAGES = (
    "realignment", "motion_qc", "nuisance_regression", "detrending", "temporal_filtering",
)


def execution_prerequisite_issue(plan: dict[str, Any]) -> str | None:
    """Return a user-facing block reason for an unsafe scientific plan."""
    nodes = [node for node in plan.get("nodes", []) if isinstance(node, dict)]
    for node in nodes:
        if node.get("id") != "native_preproc_full_execute":
            continue
        params = node.get("params") if isinstance(node.get("params"), dict) else {}
        stages = params.get("stage_overrides") if isinstance(params.get("stage_overrides"), dict) else {}
        if stages.get("reho") is not False:
            disabled_stages = [stage for stage in _NATIVE_REHO_PREREQUISITE_STAGES if stages.get(stage) is False]
            if disabled_stages:
                return (
                    "ReHo execution requires the native preprocessing stages "
                    f"{', '.join(_NATIVE_REHO_PREREQUISITE_STAGES)}; explicitly disabled: "
                    f"{', '.join(disabled_stages)}."
                )
    reho_index = next((i for i, node in enumerate(nodes) if str(node.get("id") or "") == "reho_subject"), None)
    if reho_index is None:
        return None
    upstream = nodes[:reho_index]
    if any(str(node.get("id") or "") in _REALIGNMENT_PRODUCERS for node in upstream):
        return None
    for node in nodes[: reho_index + 1]:
        params = node.get("params") if isinstance(node.get("params"), dict) else {}
        if any(str(params.get(key) or "").strip() for key in _PREPROCESSED_INPUT_KEYS):
            return None
    return "ReHo execution requires a realignment or smoothing producer, or an explicit reviewed preprocessed BOLD input."
