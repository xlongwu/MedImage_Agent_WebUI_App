"""ERROR_KB schema validator."""
from __future__ import annotations

from typing import Any

from src.backend.app.tools.error_classifier import _load_error_kb


def list_error_kb_entries(kb_path: str | None = None) -> dict[str, Any]:
    """List every ERROR_KB category with its decision-relevant metadata."""
    kb = _load_error_kb(kb_path)
    categories = kb.get("categories", {})

    entries: list[dict[str, Any]] = []
    for name, cat in categories.items():
        entries.append({
            "category": name,
            "severity": cat.get("severity", "unknown"),
            "retryable": bool(cat.get("retryable", False)),
            "human_action_required": bool(cat.get("human_action_required", True)),
            "patterns": list(cat.get("patterns", [])),
            "likely_causes": list(cat.get("likely_causes", [])),
            "suggested_fixes": list(cat.get("suggested_fixes", [])),
            "affected_backends": list(cat.get("affected_backends", [])),
        })

    return {
        "version": kb.get("version", "unknown"),
        "categories_count": len(entries),
        "entries": entries,
    }


def validate_error_kb(kb_path: str | None = None) -> dict[str, Any]:
    kb = _load_error_kb(kb_path)
    errors: list[str] = []
    warnings: list[str] = []

    version = kb.get("version", "unknown")
    if version != "0.2.0":
        warnings.append(f"ERROR_KB version is {version}, expected 0.2.0")

    categories = kb.get("categories", {})
    if not categories:
        errors.append("No categories defined")
    else:
        required_fields = ["severity", "retryable", "patterns", "suggested_fixes"]
        for name, cat in categories.items():
            for field in required_fields:
                if field not in cat:
                    errors.append(f"Category '{name}': missing '{field}'")
            if not isinstance(cat.get("patterns"), list) or len(cat.get("patterns", [])) == 0:
                errors.append(f"Category '{name}': patterns must be non-empty list")

    return {
        "ok": len(errors) == 0,
        "version": version,
        "categories_count": len(categories),
        "errors": errors,
        "warnings": warnings,
    }
