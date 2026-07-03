"""Gate apply_plan + Cursor apply to small, low-risk surgical edits (platform only)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from training_platform.improvement_history import load_improvement_history_entries

# High-risk patterns that repeatedly caused pixel regressions or ghost modules.
_FORBIDDEN_RE = re.compile(
    r"|".join(
        re.escape(k)
        for k in (
            "context_manager",
            "trim_early_context",
            "prompt_limits",
            "max_context_tokens",
            "sliding.window",
            "tool_router",
            "route_tool_call",
            "pre-router",
            "pre_router",
            "orchestrator",
            "phase/manager",
            "stage_0_init",
            "main_loop.py",
            "prompt_builder",
            "consecutive_no_tool",
            "idle fuse",
            "idle_fuse",
            "force route",
            "force_route",
            "jsonschema",
            "pydantic",
            "strict_mode",
            "tool_registry.json",
            "registry.json",
            "context trim",
            "context trimming",
            "动态裁剪",
            "上下文裁剪",
            "15000",
            "15k token",
        )
    ),
    re.IGNORECASE,
)

# New Python modules under these prefixes are blocked (ghost-file risk after revert).
_BLOCK_NEW_PY_PREFIXES = (
    "agent/core/",
    "agent/guards/",
    "agent/routing/",
    "agent/validators/",
    "agent/pipeline/",
    "agent/execution/",
    "agent/phase/",
    "agent/schemas/",
    "agent/config/",
)

# Only these prefixes may reference not-yet-existing files.
_ALLOW_NEW_FILE_PREFIXES = (
    "agent/prompts/",
    "data/skills/",
    "training_platform/",
)

_MAX_TARGET_FILES = 1
_MAX_CONCRETE_STEPS = 4


def _norm_path(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")


def _action_blob(action: dict[str, Any]) -> str:
    parts = [
        str(action.get("title") or ""),
        str(action.get("category") or ""),
        " ".join(str(x) for x in (action.get("concrete_steps") or [])),
        " ".join(_norm_path(p) for p in (action.get("target_files") or [])),
    ]
    return "\n".join(parts)


def _failed_history_fingerprints(
    baseline_ref: str | None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for entry in load_improvement_history_entries(baseline_ref=baseline_ref):
        if entry.get("kept_in_repo"):
            continue
        if str(entry.get("apply_outcome", "")).startswith("skipped"):
            continue
        for action in entry.get("actions_planned_or_applied") or []:
            if not isinstance(action, dict):
                continue
            files = tuple(sorted(_norm_path(p) for p in (action.get("target_files") or [])))
            rows.append({
                "iteration": entry.get("iteration"),
                "title": action.get("title"),
                "target_files": files,
                "outcome": entry.get("outcome_zh") or entry.get("apply_outcome"),
            })
    return rows


def _matches_failed_history(
    action: dict[str, Any],
    failed_rows: list[dict[str, Any]],
) -> str | None:
    files = tuple(sorted(_norm_path(p) for p in (action.get("target_files") or [])[:1]))
    title = str(action.get("title") or "").strip().lower()
    for row in failed_rows:
        if files and row.get("target_files") == files:
            return (
                f"与第 {row.get('iteration')} 轮 revert 改动 target 相同"
                f"（{', '.join(files)}）"
            )
        row_title = str(row.get("title") or "").strip().lower()
        if title and row_title and title == row_title:
            return f"与第 {row.get('iteration')} 轮 revert 改动 title 相同"
    return None


def _validate_target_file(repo_root: Path, rel: str) -> str | None:
    rel = _norm_path(rel)
    if not rel or ".." in rel.split("/"):
        return "非法路径"
    path = repo_root / rel
    if path.is_file():
        return None
    if rel.endswith(".py") and rel.startswith("agent/"):
        if any(rel.startswith(p) for p in _BLOCK_NEW_PY_PREFIXES):
            return f"禁止新建 agent 子模块: {rel}"
        return f"禁止新建 agent Python 文件: {rel}"
    if not any(rel.startswith(p) for p in _ALLOW_NEW_FILE_PREFIXES):
        return f"target 不存在且不在允许新建的前缀下: {rel}"
    return None


def validate_action(
    action: dict[str, Any],
    repo_root: Path,
    *,
    failed_rows: list[dict[str, Any]] | None = None,
) -> tuple[bool, str]:
    if not isinstance(action, dict):
        return False, "action 非对象"
    if str(action.get("risk") or "low").lower() != "low":
        return False, "risk 必须为 low"
    if str(action.get("priority") or "") != "P0":
        return False, "priority 必须为 P0"

    blob = _action_blob(action)
    if _FORBIDDEN_RE.search(blob):
        return False, "命中高风险禁用关键词（context trim / router / 新编排层等）"

    steps = action.get("concrete_steps") or []
    if not isinstance(steps, list) or not steps:
        return False, "concrete_steps 为空"
    if len(steps) > _MAX_CONCRETE_STEPS:
        return False, f"concrete_steps 超过 {_MAX_CONCRETE_STEPS} 条（改动过大）"

    targets = action.get("target_files") or []
    if not isinstance(targets, list) or not targets:
        return False, "target_files 为空"
    if len(targets) > _MAX_TARGET_FILES:
        return False, f"target_files 超过 {_MAX_TARGET_FILES} 个（只允许单文件 surgical）"

    for rel in targets[:_MAX_TARGET_FILES]:
        err = _validate_target_file(repo_root, str(rel))
        if err:
            return False, err

    if failed_rows:
        hist_err = _matches_failed_history(action, failed_rows)
        if hist_err:
            return False, hist_err

    return True, "ok"


def filter_apply_plan(
    plan: dict[str, Any],
    repo_root: Path,
    *,
    baseline_ref: str | None = None,
) -> dict[str, Any]:
    """Return plan with only guard-approved actions + audit report."""
    actions_in = list(plan.get("actions") or [])
    failed_rows = _failed_history_fingerprints(baseline_ref)
    kept: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []

    for action in actions_in:
        if not isinstance(action, dict):
            removed.append({"action": action, "reason": "非 dict"})
            continue
        ok, reason = validate_action(
            action,
            repo_root,
            failed_rows=failed_rows,
        )
        if ok:
            trimmed = dict(action)
            trimmed["target_files"] = [
                _norm_path(p) for p in (action.get("target_files") or [])[:_MAX_TARGET_FILES]
            ]
            trimmed["concrete_steps"] = list((action.get("concrete_steps") or [])[:_MAX_CONCRETE_STEPS])
            kept.append(trimmed)
        else:
            removed.append({
                "id": action.get("id"),
                "title": action.get("title"),
                "target_files": action.get("target_files"),
                "reason": reason,
            })

    out_plan = dict(plan)
    out_plan["actions"] = kept
    out_plan["guard_filtered"] = bool(removed)
    if removed:
        out_plan["guard_removed_actions"] = removed

    return {
        "plan": out_plan,
        "report": {
            "input_action_count": len(actions_in),
            "kept_action_count": len(kept),
            "removed_actions": removed,
            "failed_history_rows": len(failed_rows),
        },
    }


def guard_apply_plan(
    plan: dict[str, Any],
    repo_root: Path,
    *,
    baseline_ref: str | None = None,
) -> dict[str, Any]:
    return filter_apply_plan(plan, repo_root, baseline_ref=baseline_ref)
