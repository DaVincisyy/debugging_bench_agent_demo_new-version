"""Add standing optimization theme signals to post-run efficiency digest (platform only)."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

_FINISH_PREFLIGHT_TOOLS = frozenset({
    "view_image",
    "annotate_image",
    "read_text_file",
    "write_text_file",
})

_THEME_LABELS: dict[str, str] = {
    "direction_1_workflow_merge": "根据上下文合并流程",
    "direction_2_tool_merge": "合并工具使用",
    "direction_3_finish_review": "finish 阶段审查",
}


def _iter_strings(obj: Any) -> list[str]:
    out: list[str] = []
    if isinstance(obj, str):
        out.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            out.extend(_iter_strings(v))
    elif isinstance(obj, list):
        for v in obj:
            out.extend(_iter_strings(v))
    return out


def _tool_arguments(call: dict[str, Any]) -> dict[str, Any]:
    args = call.get("arguments")
    return args if isinstance(args, dict) else {}


def _count_part_transitions(step_rows: list[dict[str, Any]]) -> int:
    if len(step_rows) < 2:
        return 0
    count = 0
    prev = step_rows[0].get("part")
    for row in step_rows[1:]:
        cur = row.get("part")
        if cur != prev:
            count += 1
        prev = cur
    return count


def _unknown_part_rows(step_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [r for r in step_rows if r.get("part") == "unknown"]
    return sorted(
        rows,
        key=lambda r: float(r.get("step_total_s") or 0.0),
        reverse=True,
    )[:4]


def _deterministic_io_via_llm(step_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in step_rows:
        tools = row.get("tools") or []
        if len(tools) != 1:
            continue
        if tools[0] not in {"save_text_file", "read_text_file"}:
            continue
        llm_s = float(row.get("llm_s") or 0.0)
        if llm_s < 2.0:
            continue
        out.append({
            "step": row.get("step"),
            "part": row.get("part"),
            "tool": tools[0],
            "llm_s": llm_s,
            "prompt_tokens": row.get("prompt_tokens"),
        })
    return sorted(out, key=lambda r: float(r.get("llm_s") or 0.0), reverse=True)[:6]


def _view_image_paths(result: Any) -> list[str]:
    paths: list[str] = []
    for step in result.steps:
        for call in step.tool_calls or []:
            if call.get("name") != "view_image":
                continue
            for s in _iter_strings(_tool_arguments(call)):
                if s.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                    paths.append(s)
    return paths


def _tool_result_image_attachments(result: Any) -> dict[str, Any]:
    total = 0
    by_tool: Counter[str] = Counter()
    heavy_steps: list[dict[str, Any]] = []
    for step in result.steps:
        step_images = 0
        for tr in step.tool_results or []:
            images = tr.get("images") or []
            n = len(images) if isinstance(images, list) else 0
            if n:
                tool = str(tr.get("name") or "?")
                by_tool[tool] += n
                step_images += n
        if step_images:
            total += step_images
            heavy_steps.append({
                "step": step.index,
                "image_attachments": step_images,
                "tools": [c.get("name") for c in (step.tool_calls or [])],
            })
    heavy_steps.sort(key=lambda r: r["image_attachments"], reverse=True)
    return {
        "total": total,
        "by_tool": dict(by_tool),
        "heaviest_steps": heavy_steps[:5],
    }


def _finish_signals(step_rows: list[dict[str, Any]]) -> dict[str, Any]:
    finish_rows = [r for r in step_rows if "finish" in (r.get("tools") or [])]
    finish_row = finish_rows[0] if finish_rows else None
    prefinish: list[dict[str, Any]] = []
    for row in step_rows:
        if row.get("part") != "partd":
            continue
        tools = row.get("tools") or []
        if not tools or tools == ["finish"]:
            continue
        if any(t in _FINISH_PREFLIGHT_TOOLS for t in tools):
            prefinish.append({
                "step": row.get("step"),
                "tools": tools,
                "step_total_s": row.get("step_total_s"),
                "llm_s": row.get("llm_s"),
                "prompt_tokens": row.get("prompt_tokens"),
            })
    partd_rows = [r for r in step_rows if r.get("part") == "partd"]
    partd_llm_s = round(
        sum(float(r.get("llm_s") or 0.0) for r in partd_rows),
        2,
    )
    return {
        "finish_step": finish_row,
        "partd_step_count": len(partd_rows),
        "partd_llm_s_total": partd_llm_s,
        "prefinish_redundant_tools": prefinish[:6],
    }


def build_optimization_theme_signals(
    result: Any,
    digest: dict[str, Any],
    phase_resets: list[dict[str, Any]] | None = None,
    run_dir: Path | None = None,
) -> dict[str, Any]:
    """Structured evidence for long-term optimization directions 1–4."""
    phase_resets = phase_resets or []
    step_rows = digest.get("all_steps") or []
    tool_counts = digest.get("tool_call_counts") or {}
    part_timing = digest.get("part_timing") or {}

    view_paths = _view_image_paths(result)
    view_path_counts = Counter(view_paths)
    duplicate_views = [
        {"path": p, "count": c}
        for p, c in view_path_counts.items()
        if c > 1
    ]

    save_rows = [
        r for r in step_rows
        if (r.get("tools") or []) == ["save_text_file"]
    ]
    save_llm_s = round(
        sum(float(r.get("llm_s") or 0.0) for r in save_rows),
        2,
    )

    image_attach = _tool_result_image_attachments(result)
    finish = _finish_signals(step_rows)
    unknown_rows = _unknown_part_rows(step_rows)

    return {
        "direction_1_workflow_merge": {
            "label_zh": _THEME_LABELS["direction_1_workflow_merge"],
            "phase_transition_count": _count_part_transitions(step_rows),
            "phase_context_reset_count": len(phase_resets),
            "phase_context_resets": phase_resets[:8],
            "unknown_part_step_count": len([r for r in step_rows if r.get("part") == "unknown"]),
            "unknown_part_total_s": part_timing.get("unknown_s"),
            "slowest_unknown_steps": unknown_rows,
            "part_timing_s": part_timing,
        },
        "direction_2_tool_merge": {
            "label_zh": _THEME_LABELS["direction_2_tool_merge"],
            "view_image_count": int(tool_counts.get("view_image", 0)),
            "duplicate_view_image_paths": duplicate_views[:6],
            "save_text_file_count": int(tool_counts.get("save_text_file", 0)),
            "read_text_file_count": int(tool_counts.get("read_text_file", 0)),
            "save_text_file_llm_s_total": save_llm_s,
            "deterministic_io_via_llm_steps": _deterministic_io_via_llm(step_rows),
            "tool_result_image_attachments": image_attach,
        },
        "direction_3_finish_review": {
            "label_zh": _THEME_LABELS["direction_3_finish_review"],
            **finish,
        },
        "reflect_instructions_zh": (
            "写「## 对照长期优化方向（1–3）」时，必须逐条引用本对象对应 direction_* 字段中的证据；"
            "不可省略任一条。P0/P1 优先级须标注方向编号（如 P0·方向2）。"
            "debug PNG 延后不在 autonomous 自动改动范围内。"
        ),
    }


def enrich_efficiency_digest(
    digest: dict[str, Any],
    result: Any,
    phase_resets: list[dict[str, Any]] | None = None,
    run_dir: Path | None = None,
) -> dict[str, Any]:
    digest["optimization_theme_signals"] = build_optimization_theme_signals(
        result,
        digest,
        phase_resets=phase_resets,
        run_dir=run_dir,
    )
    return digest
