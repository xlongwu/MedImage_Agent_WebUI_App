"""Build and verify stable Approval Summary envelopes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from src.backend.app.config.settings import ProjectSettings
from src.backend.app.core.exceptions import SafetyError
from src.backend.app.planner.audit_record import stable_hash
from src.backend.app.schemas.approval_summary import ApprovalSummary, ApprovalSummarySection

_OUTPUT_KEYS = frozenset({"output_dir", "output_root", "derivatives_dir", "work_dir"})


class ApprovalSummaryService:
    def build(
        self,
        *,
        project,
        reviewed_plan,
        ttl_minutes: int = 30,
        now: datetime | None = None,
    ) -> ApprovalSummary:
        issued = (now or datetime.now(UTC)).astimezone(UTC)
        payload = reviewed_plan.payload
        plan = payload.get("plan") if isinstance(payload.get("plan"), dict) else {}
        contract = payload.get("goal_contract") if isinstance(payload.get("goal_contract"), dict) else {}
        goal_hash = str(contract.get("goal_contract_hash") or contract.get("contract_hash") or "")
        if not goal_hash:
            goal_hash = stable_hash(contract)
        nodes = tuple(
            str(node.get("id"))
            for node in plan.get("nodes", [])
            if isinstance(node, dict) and node.get("id")
        )
        selected_subjects = self._selected_subjects(plan)
        dataset_summary = (
            f"1 selected subject: {selected_subjects[0]}"
            if len(selected_subjects) == 1
            else f"{len(selected_subjects)} selected subjects: {', '.join(selected_subjects)}"
            if selected_subjects
            else f"{project.subjects_count} registered subject(s)"
        )
        subject_scope_summary = (
            f" for subject {selected_subjects[0]}"
            if len(selected_subjects) == 1
            else f" for subjects {', '.join(selected_subjects)}"
            if selected_subjects
            else ""
        )
        backends = tuple(sorted({
            str(node.get("backend"))
            for node in plan.get("nodes", [])
            if isinstance(node, dict) and node.get("backend")
        }))
        write_roots = self._portable_write_roots(
            project=project,
            plan=plan,
            project_config_path=reviewed_plan.project_config_path,
        )
        external = tuple(value for value in backends if value.startswith("matlab") or value == "dpabi")
        acpc_node = next(
            (
                node for node in plan.get("nodes", [])
                if isinstance(node, dict) and node.get("id") == "native_auto_acpc_align"
            ),
            None,
        )
        acpc_params = acpc_node.get("params", {}) if isinstance(acpc_node, dict) and isinstance(acpc_node.get("params"), dict) else {}
        acpc_limitations = (
            "AC/PC coordinates are template-back-projected estimates, not manually detected anatomical landmarks; independent manual-reference validation is pending.",
        ) if acpc_node else ()
        confirmations = self._confirmations(plan=plan, node_ids=nodes, backend_ids=backends)
        memory_context = (
            payload.get("memory_context")
            if isinstance(payload.get("memory_context"), dict)
            else {}
        )
        memory_refs = tuple(
            {
                "kind": str(item.get("kind") or ""),
                "memory_id": str(item.get("memory_id") or ""),
                "revision_hash": str(item.get("revision_hash") or ""),
                "source_ref": str(item.get("source_ref") or ""),
            }
            for item in memory_context.get("evidence_refs", [])
            if isinstance(item, dict)
        )
        influence_summary = tuple(
            f"Suggested {item.get('decision_kind')} requires confirmation in this task."
            for item in memory_context.get("decision_suggestions", [])
            if isinstance(item, dict) and item.get("decision_kind")
        )
        expires = issued + timedelta(minutes=max(1, ttl_minutes))
        base: dict[str, Any] = {
            "schema_version": 1,
            "project_id": reviewed_plan.project_id,
            "reviewed_plan_id": reviewed_plan.reviewed_plan_id,
            "plan_hash": reviewed_plan.plan_hash,
            "planning_inputs_hash": str(reviewed_plan.planning_inputs_hash or ""),
            "revision_no": reviewed_plan.revision_no,
            "parent_reviewed_plan_id": reviewed_plan.parent_reviewed_plan_id,
            "parent_plan_hash": reviewed_plan.parent_plan_hash,
            "revision_reason": reviewed_plan.revision_reason,
            "memory_context_hash": reviewed_plan.memory_context_hash,
            "memory_refs": memory_refs,
            "memory_influence_summary": influence_summary,
            "goal_contract_hash": goal_hash,
            "goal": str(payload.get("goal") or "Reviewed scientific workflow"),
            "dataset_summary": dataset_summary,
            "execution_summary": f"{len(nodes)} reviewed node(s); no dispatch before approval",
            "write_roots": write_roots,
            "rawdata_read_only": True,
            "node_ids": nodes,
            "backend_ids": backends,
            "external_tools": external,
            "limitations": tuple(str(item) for item in payload.get("limitations", []) if str(item)) + acpc_limitations,
            "science_changes": (
                (f"Subject scope: {', '.join(selected_subjects)}",)
                if selected_subjects
                else ()
            ),
            "sections": (
                ApprovalSummarySection(
                    id="scope",
                    title="Execution scope",
                    summary=(
                        f"Approve exactly {len(nodes)} reviewed node(s)"
                        f"{subject_scope_summary}."
                    ),
                ),
                ApprovalSummarySection(
                    id="safety",
                    title="Safety boundary",
                    summary="Source rawdata remains read-only; writes are limited to the listed project roots.",
                ),
                *(
                    (
                        ApprovalSummarySection(
                            id="dicom-conversion",
                            title="Native DICOM conversion",
                            summary="Use the persisted release-approved mapping package; verify rawdata unchanged before preprocessing handoff.",
                        ),
                    )
                    if "native_dicom_conversion_execute" in nodes
                    else ()
                ),
                *(
                    (
                        ApprovalSummarySection(
                            id="acpc-estimation",
                            title="Automatic ACPC estimation",
                            summary=(
                                "Use registered T1w artifact "
                                f"{str(acpc_params.get('source_t1_artifact_id') or 'unknown')} with template "
                                f"{str(acpc_params.get('template_id') or 'unknown')}; write only ACPC derivatives. "
                                "QC failure stops and requires human review."
                            ),
                            warnings=acpc_limitations,
                        ),
                    )
                    if acpc_node
                    else ()
                ),
            ),
            "confirmations": confirmations,
            "issued_at": issued,
            "expires_at": expires,
        }
        summary_hash = stable_hash(self._identity_payload(base))
        return ApprovalSummary(summary_hash=summary_hash, **base)

    @staticmethod
    def _selected_subjects(plan: dict[str, Any]) -> tuple[str, ...]:
        selected: set[str] = set()
        for node in plan.get("nodes", []):
            if not isinstance(node, dict) or not isinstance(node.get("params"), dict):
                continue
            params = node["params"]
            subject_id = str(params.get("subject_id") or "").strip()
            if subject_id:
                selected.add(subject_id)
            subject_ids = params.get("subject_ids")
            if isinstance(subject_ids, list):
                selected.update(
                    str(item).strip()
                    for item in subject_ids
                    if str(item).strip()
                )
        return tuple(sorted(selected))

    def verify(self, summary: ApprovalSummary, *, now: datetime | None = None) -> None:
        if summary.expires_at <= (now or datetime.now(UTC)):
            raise SafetyError("APPROVAL_SUMMARY_EXPIRED", code="APPROVAL_SUMMARY_EXPIRED")
        calculated = stable_hash(
            self._identity_payload(summary.model_dump(mode="json", exclude={"summary_hash"}))
        )
        if calculated != summary.summary_hash:
            raise SafetyError("APPROVAL_SUMMARY_STALE", code="APPROVAL_SUMMARY_STALE")

    @staticmethod
    def _identity_payload(value: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(value)
        for field in ("issued_at", "expires_at"):
            if isinstance(normalized.get(field), datetime):
                normalized[field] = normalized[field].isoformat().replace("+00:00", "Z")
        sections = normalized.get("sections")
        if isinstance(sections, tuple):
            normalized["sections"] = [
                item.model_dump(mode="json") if hasattr(item, "model_dump") else item
                for item in sections
            ]
        return normalized

    @staticmethod
    def _confirmations(
        *, plan: dict[str, Any], node_ids: tuple[str, ...], backend_ids: tuple[str, ...]
    ) -> dict[str, object]:
        native = "native_preproc_full_execute" in node_ids or "native_auto_acpc_align" in node_ids
        native_conversion = "native_dicom_conversion_execute" in node_ids
        high_risk = tuple(b for b in backend_ids if b in {"matlab", "matlab-spm", "matlab-dpabi", "dpabi"})
        return {
            "approved": True,
            "approved_nodes": list(node_ids),
            "rejected_nodes": [],
            "approved_backends": list(high_risk),
            "external_tool_acknowledgement": bool(high_risk),
            "rawdata_read_only_confirmed": True,
            "output_directory_confirmed": True,
            "risk_acknowledgement": bool(high_risk or native),
            "overwrite_policy": "fail_if_exists",
            "subject_scope_confirmed": True,
            "native_preprocessing_acknowledgement": native,
            "no_external_tools_confirmed": (native or native_conversion) and not high_risk,
            "conversion_scope_confirmed": native_conversion,
            "estimated_landmarks_acknowledgement": "native_auto_acpc_align" in node_ids,
        }

    def _portable_write_roots(
        self,
        *,
        project,
        plan: dict[str, Any],
        project_config_path: str | None = None,
    ) -> tuple[str, ...]:
        raw_root = project.metadata.get("project_dir") if isinstance(project.metadata, dict) else None
        if not isinstance(raw_root, str) or not raw_root.strip():
            raise SafetyError("PROJECT_DIR_REQUIRED", code="PROJECT_DIR_REQUIRED")
        project_root = Path(raw_root).expanduser().resolve()
        configured_roots: list[str] = ["work", "logs", "reports", "derivatives"]
        if project_config_path and Path(project_config_path).is_file():
            settings = ProjectSettings.from_yaml(project_config_path)
            configured_roots = [
                settings.runtime.work_dir,
                settings.runtime.log_dir,
                settings.runtime.report_dir,
                settings.runtime.derivatives_dir,
            ]
        raw_values: list[str] = list(configured_roots)
        for node in plan.get("nodes", []):
            if not isinstance(node, dict) or not isinstance(node.get("params"), dict):
                continue
            for key, raw in node["params"].items():
                if key not in _OUTPUT_KEYS or not isinstance(raw, str) or not raw.strip():
                    continue
                raw_values.append(raw)
        values: list[str] = []
        for raw in raw_values:
            candidate = Path(raw).expanduser()
            candidate = candidate.resolve() if candidate.is_absolute() else (project_root / candidate).resolve()
            try:
                relative = candidate.relative_to(project_root)
            except ValueError as exc:
                raise SafetyError("APPROVAL_WRITE_ROOT_OUTSIDE_PROJECT", code="APPROVAL_WRITE_ROOT_OUTSIDE_PROJECT") from exc
            values.append("project://" + relative.as_posix())
        return tuple(sorted(set(values)))
