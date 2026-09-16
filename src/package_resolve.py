"""Central package dimension / weight resolution for product cloning."""

from __future__ import annotations

from typing import Any, Literal

SourceType = Literal["connected_store", "daraz_url", "community"]

PackageField = Literal[
    "package_weight", "package_length", "package_width", "package_height"
]

PACKAGE_FIELDS: tuple[PackageField, ...] = (
    "package_weight",
    "package_length",
    "package_width",
    "package_height",
)


def _valid_number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if num <= 0:
        return None
    return num


def resolve_package_field(
    *,
    source_type: SourceType,
    source_value: Any,
    workspace_default: Any,
    field: PackageField,
) -> dict[str, Any]:
    """Resolve one package field with source-type priority.

    CONNECTED: source → workspace default → missing (error)
    PUBLIC/COMMUNITY: workspace default → missing (error)
    Never returns zero silently.
    """
    src = _valid_number(source_value)
    default = _valid_number(workspace_default)

    if source_type == "connected_store":
        if src is not None:
            return {
                "value": src,
                "source": "connected_source",
                "field": field,
                "warning": None,
                "error": None,
            }
        if default is not None:
            return {
                "value": default,
                "source": "workspace_default",
                "field": field,
                "warning": f"Source {field} missing → using workspace default",
                "error": None,
            }
        return {
            "value": None,
            "source": "missing",
            "field": field,
            "warning": None,
            "error": f"{field} required before creation (source and defaults missing)",
        }

    # public_url / community — never invent from untrusted source pages
    if default is not None:
        return {
            "value": default,
            "source": "workspace_default",
            "field": field,
            "warning": f"Using workspace default for {field} ({source_type})",
            "error": None,
        }
    return {
        "value": None,
        "source": "missing",
        "field": field,
        "warning": None,
        "error": f"{field} required before creation (workspace default missing)",
    }


def resolve_variant_package(
    *,
    source_type: SourceType,
    variant: dict[str, Any],
    defaults: dict[str, Any],
) -> dict[str, Any]:
    """Resolve all four package fields for one variant."""
    resolved: dict[str, Any] = {}
    warnings: list[str] = []
    errors: list[str] = []
    for field in PACKAGE_FIELDS:
        default_key = f"default_{field}"
        result = resolve_package_field(
            source_type=source_type,
            source_value=variant.get(field),
            workspace_default=defaults.get(default_key),
            field=field,
        )
        resolved[field] = result["value"]
        resolved[f"{field}_source"] = result["source"]
        if result["warning"]:
            warnings.append(result["warning"])
        if result["error"]:
            errors.append(result["error"])
    return {"values": resolved, "warnings": warnings, "errors": errors}
