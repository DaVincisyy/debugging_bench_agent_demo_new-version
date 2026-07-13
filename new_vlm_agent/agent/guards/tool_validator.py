"""Phase tool whitelist + JSON Schema arg validation (pre-execution hook)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

try:
    import jsonschema
except ImportError:  # pragma: no cover
    jsonschema = None  # type: ignore[assignment]

# Phase → allowed tool names (step_id keys; group aliases resolved in validate_tool_call).
PHASE_TOOL_WHITELIST: dict[str, frozenset[str]] = {
    "part0_signal_to_tp": frozenset({
        "read_text_file", "view_image", "search_pdf_text", "save_text_file",
        "run_python", "list_files",
    }),
    "part0_pdf_search_and_mark": frozenset({
        "search_pdf_text", "run_python", "mark_tp_on_assembly_from_pdf_hit",
        "pdf_page_to_image", "view_image",
    }),
    "partb_locator_largest_ic": frozenset({
        "view_image", "save_text_file", "run_python",
        "detect_largest_ic_on_assembly_from_vlm_hint", "annotate_image", "read_text_file",
    }),
    "parta_board_largest_ic": frozenset({
        "run_python", "detect_largest_ic_on_board_full", "detect_largest_ic_on_board_from_vlm_hint", "view_image",
        "save_text_file", "annotate_image", "read_text_file",
    }),
    "partd_case12_align_and_finish": frozenset({
        "case12_build_and_align_from_step02_anchors", "emit_step08_from_case12_aligned",
        "run_python", "annotate_image", "save_text_file", "finish", "view_image", "read_text_file",
    }),
}

# Group aliases (part0 / partB / …) map to union of step whitelists when step_id unknown.
_PHASE_GROUP_ALIASES: dict[str, tuple[str, ...]] = {
    "part0": ("part0_signal_to_tp", "part0_pdf_search_and_mark"),
    "partb": ("partb_locator_largest_ic",),
    "parta": ("parta_board_largest_ic",),
    "partd": ("partd_case12_align_and_finish",),
}

_HINTS_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["approx_bbox_norm"],
    "properties": {
        "approx_bbox_norm": {
            "type": "array",
            "minItems": 4,
            "maxItems": 4,
            "items": {"type": "number"},
        },
        "region_hint": {"type": "string", "minLength": 1},
        "relative_to_tp": {"type": "string", "minLength": 1},
        "visual_cues": {"type": "string", "minLength": 1},
        "reference_text": {"type": "string", "minLength": 1},
    },
    "anyOf": [
        {"required": ["region_hint"]},
        {"required": ["relative_to_tp"]},
        {"required": ["visual_cues"]},
        {"required": ["reference_text"]},
    ],
}

_TOOL_ARG_SCHEMAS: dict[str, dict[str, Any]] = {
    "save_text_file": {
        "type": "object",
        "required": ["path", "content"],
        "properties": {
            "path": {"type": "string", "minLength": 1},
            "content": {"type": ["string", "object", "array", "number", "boolean", "null"]},
        },
    },
    "view_image": {
        "type": "object",
        "required": ["path"],
        "properties": {
            "path": {"type": "string", "minLength": 1},
            "note": {"type": "string"},
        },
    },
    "detect_largest_ic_on_assembly_from_vlm_hint": {
        "type": "object",
        "properties": {
            "vlm_hints_path": {"type": "string"},
            "region_hint": {"type": "string", "minLength": 1},
            "work_margin_ratio": {"type": "number"},
        },
    },
    "detect_largest_ic_on_board_full": {
        "type": "object",
        "properties": {
            "board_path": {"type": "string"},
            "out_debug_path": {"type": "string"},
            "out_box_path": {"type": "string"},
            "out_json_path": {"type": "string"},
            "out_landscape_path": {"type": "string"},
        },
    },
    "detect_largest_ic_on_board_from_vlm_hint": {
        "type": "object",
        "properties": {
            "vlm_hints_path": {"type": "string"},
            "region_hint": {"type": "string", "minLength": 1},
            "work_margin_ratio": {"type": "number"},
        },
    },
}


def _allowed_tools_for_phase(phase: str) -> frozenset[str] | None:
    key = (phase or "").strip()
    if not key:
        return None
    if key in PHASE_TOOL_WHITELIST:
        return PHASE_TOOL_WHITELIST[key]
    group = key.lower()
    if group in _PHASE_GROUP_ALIASES:
        merged: set[str] = set()
        for sid in _PHASE_GROUP_ALIASES[group]:
            merged |= set(PHASE_TOOL_WHITELIST.get(sid, frozenset()))
        return frozenset(merged) if merged else None
    return None


def _validate_with_jsonschema(schema: dict[str, Any], instance: Any) -> str | None:
    if jsonschema is None:
        return None
    try:
        jsonschema.validate(instance=instance, schema=schema)
    except jsonschema.ValidationError as exc:  # type: ignore[union-attr]
        return str(exc.message)
    return None


def _hints_path_needs_semantic_keys(path: str) -> bool:
    p = path.replace("\\", "/").lower()
    return p.endswith(
        (
            "debug/case10_assembly_vlm_hints.json",
            "debug/case10_vlm_hints.json",
            "debug/case10_board_vlm_hints.json",
        )
    )


def _validate_hints_content(content: Any) -> str | None:
    if isinstance(content, str):
        try:
            obj = json.loads(content)
        except json.JSONDecodeError as exc:
            return f"hints JSON parse error: {exc}"
    elif isinstance(content, dict):
        obj = content
    else:
        return "hints content must be JSON string or object"
    err = _validate_with_jsonschema(_HINTS_JSON_SCHEMA, obj)
    if err:
        return f"hints JSON schema: {err}"
    if jsonschema is None:
        if not any(k in obj for k in ("region_hint", "relative_to_tp", "visual_cues", "reference_text")):
            return "hints JSON must include region_hint / relative_to_tp / visual_cues / reference_text"
    return None


def validate_tool_call(phase: str, tool_name: str, args: dict[str, Any] | None) -> tuple[bool, str | None]:
    """Return (True, None) if allowed; (False, error_message) if blocked."""
    tool = (tool_name or "").strip()
    if not tool:
        return False, "[tool-validator] missing tool name"

    payload = args if isinstance(args, dict) else {}

    allowed = _allowed_tools_for_phase(phase)
    if allowed is not None and tool not in allowed:
        return (
            False,
            json.dumps(
                {
                    "ok": False,
                    "error": "tool_not_allowed_in_phase",
                    "phase": phase,
                    "tool": tool,
                    "allowed_tools": sorted(allowed),
                    "message": (
                        f"Tool `{tool}` is not in the phase whitelist for `{phase}`. "
                        "Pick an allowed tool and retry once."
                    ),
                },
                ensure_ascii=False,
            ),
        )

    base_schema = _TOOL_ARG_SCHEMAS.get(tool)
    if base_schema is not None:
        err = _validate_with_jsonschema(base_schema, payload)
        if err:
            return (
                False,
                json.dumps(
                    {
                        "ok": False,
                        "error": "invalid_tool_arguments",
                        "tool": tool,
                        "schema_error": err,
                        "message": f"Tool `{tool}` arguments failed schema validation.",
                    },
                    ensure_ascii=False,
                ),
            )
        if jsonschema is None and tool == "view_image" and not str(payload.get("path", "")).strip():
            return False, json.dumps(
                {"ok": False, "error": "invalid_tool_arguments", "tool": tool,
                 "message": "view_image requires non-empty `path`."},
                ensure_ascii=False,
            )

    if tool == "save_text_file":
        path = str(payload.get("path", ""))
        if _hints_path_needs_semantic_keys(path):
            hint_err = _validate_hints_content(payload.get("content"))
            if hint_err:
                return (
                    False,
                    json.dumps(
                        {
                            "ok": False,
                            "error": "hints_json_contract",
                            "path": path,
                            "detail": hint_err,
                            "message": (
                                "save_text_file hints JSON must include approx_bbox_norm and "
                                "at least one of region_hint / relative_to_tp / visual_cues / reference_text."
                            ),
                        },
                        ensure_ascii=False,
                    ),
                )

    return True, None


def tool_constraints_prompt_block() -> str:
    """[TOOL_CONSTRAINTS] appendix for system prompt."""
    lines = [
        "## [TOOL_CONSTRAINTS]",
        "",
        "Per-phase allowed tools (runtime whitelist; illegal calls are rejected before execution):",
        "",
        "### part0_signal_to_tp",
        "- Tools: read_text_file, view_image, search_pdf_text, save_text_file, run_python, list_files",
        "",
        "### part0_pdf_search_and_mark",
        "- Tools: search_pdf_text, run_python, mark_tp_on_assembly_from_pdf_hit, pdf_page_to_image, view_image",
        "",
        "### partb_locator_largest_ic",
        "- Tools: view_image, save_text_file, run_python, detect_largest_ic_on_assembly_from_vlm_hint, annotate_image, read_text_file",
        "- save_text_file → debug/case10_assembly_vlm_hints.json example:",
        "```json",
        json.dumps(
            {
                "approx_bbox_norm": [0.35, 0.40, 0.55, 0.60],
                "region_hint": "largest QFP near green TP, upper-right quadrant",
                "visual_cues": "rectangular plastic package, dense pin rows on four sides",
            },
            ensure_ascii=False,
            indent=2,
        ),
        "```",
        "",
        "### parta_board_largest_ic",
        "- Tools: run_python, detect_largest_ic_on_board_full, detect_largest_ic_on_board_from_vlm_hint, view_image, save_text_file, annotate_image, read_text_file",
        "- Primary path: call `detect_largest_ic_on_board_full` (no VLM hints; auto PCB mask + QFP filters on full board photo).",
        "- Optional: `view_image` on `debug/case10_largest_ic_box.png` after detection for QC.",
        "",
        "### partd_case12_align_and_finish",
        "- Tools: case12_build_and_align_from_step02_anchors, emit_step08_from_case12_aligned, run_python, annotate_image, save_text_file, finish, view_image",
        "",
        "Invalid tool name or args → standardized JSON error in tool result; fix args and continue (no full-run retry).",
    ]
    return "\n".join(lines)
