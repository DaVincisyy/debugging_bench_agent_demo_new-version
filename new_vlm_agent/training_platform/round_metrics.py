"""Collect per-round metrics for autonomous_loop reports."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else None
    except Exception:  # noqa: BLE001
        return None


def _sum_usage(usages: list[dict[str, Any] | None]) -> dict[str, int]:
    prompt = completion = total = reasoning = 0
    for u in usages:
        if not u:
            continue
        prompt += int(u.get("prompt_tokens") or 0)
        completion += int(u.get("completion_tokens") or 0)
        total += int(u.get("total_tokens") or 0)
        details = u.get("completion_tokens_details") or {}
        if isinstance(details, dict):
            reasoning += int(details.get("reasoning_tokens") or 0)
    out: dict[str, int] = {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total or (prompt + completion),
    }
    if reasoning:
        out["reasoning_tokens"] = reasoning
    return out


def extract_step_details(summary: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not summary:
        return []
    rows: list[dict[str, Any]] = []
    for st in summary.get("steps") or []:
        if not isinstance(st, dict):
            continue
        timing = st.get("timing") or {}
        usage = st.get("usage") or {}
        tools = [
            c.get("name")
            for c in (st.get("tool_calls") or [])
            if isinstance(c, dict) and c.get("name")
        ]
        rows.append({
            "step": st.get("index"),
            "tools": tools,
            "step_total_s": timing.get("step_total_s"),
            "llm_s": timing.get("llm_s"),
            "tools_s": timing.get("tools_s"),
            "images_attach_s": timing.get("images_attach_s"),
            "llm_est_prefill_s": timing.get("llm_est_prefill_s"),
            "llm_est_decode_s": timing.get("llm_est_decode_s"),
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "total_tokens": usage.get("total_tokens"),
        })
    return rows


def extract_finish_pixel(summary: dict[str, Any] | None) -> list[int] | None:
    if not summary:
        return None
    ans = summary.get("final_answer")
    if isinstance(ans, dict):
        px = ans.get("pixel")
        if isinstance(px, list) and len(px) == 2:
            return [int(px[0]), int(px[1])]
    return None


def extract_pixel_from_report(report: dict[str, Any] | None) -> dict[str, Any]:
    if not report:
        return {"status": "missing_report"}
    pc = report.get("pixel_comparison")
    if not isinstance(pc, dict):
        return {"status": "missing_pixel_comparison"}
    return {
        "status": pc.get("status", "ok"),
        "golden_pixel": pc.get("golden"),
        "actual_pixel": pc.get("actual"),
        "dx": pc.get("dx"),
        "dy": pc.get("dy"),
        "distance_px": pc.get("distance"),
        "within_tolerance": pc.get("within_tolerance"),
        "tolerance_px": pc.get("tolerance_px"),
    }


def extract_improvements(apply_plan: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not apply_plan:
        return []
    out: list[dict[str, Any]] = []
    for a in apply_plan.get("actions") or []:
        if not isinstance(a, dict):
            continue
        out.append({
            "id": a.get("id"),
            "priority": a.get("priority"),
            "category": a.get("category"),
            "title": a.get("title"),
            "target_files": a.get("target_files"),
            "concrete_steps": a.get("concrete_steps"),
            "risk": a.get("risk"),
        })
    return out


def collect_qwen_usage_for_run_dir(run_dir: Path) -> dict[str, Any]:
    """Qwen/VLM API tokens: agent steps + reflection + apply_plan generation."""
    summary = _load_json(run_dir / "summary.json")
    reflect = _load_json(run_dir / "vlm_self_reflection.json")
    plan = _load_json(run_dir / "apply_plan.json")

    step_usages = [
        (st.get("usage") if isinstance(st, dict) else None)
        for st in (summary or {}).get("steps") or []
    ]
    agent_totals = _sum_usage([u for u in step_usages if isinstance(u, dict)])

    reflect_usage = (reflect or {}).get("usage") if isinstance(reflect, dict) else None
    plan_usage = (plan or {}).get("generation_usage") if isinstance(plan, dict) else None

    combined = _sum_usage([
        agent_totals if agent_totals.get("total_tokens") else None,
        reflect_usage if isinstance(reflect_usage, dict) else None,
        plan_usage if isinstance(plan_usage, dict) else None,
    ])
    # Re-sum properly from parts
    parts = [
        u for u in (
            agent_totals,
            reflect_usage if isinstance(reflect_usage, dict) else None,
            plan_usage if isinstance(plan_usage, dict) else None,
        )
        if u
    ]
    combined = _sum_usage(parts)

    return {
        "agent_main_loop": agent_totals,
        "reflection": _sum_usage([reflect_usage if isinstance(reflect_usage, dict) else None]),
        "apply_plan_generation": _sum_usage([plan_usage if isinstance(plan_usage, dict) else None]),
        "combined": combined,
    }


def collect_run_metrics(run_dir: Path, analyze_report: dict[str, Any] | None) -> dict[str, Any]:
    summary = _load_json(run_dir / "summary.json")
    apply_plan = _load_json(run_dir / "apply_plan.json")
    return {
        "run_id": run_dir.name,
        "part_timing": (summary or {}).get("part_timing"),
        "stopped_reason": (summary or {}).get("stopped_reason"),
        "finish_pixel": extract_finish_pixel(summary),
        "golden_pixel": extract_pixel_from_report(analyze_report).get("golden_pixel"),
        "pixel_comparison": extract_pixel_from_report(analyze_report),
        "qwen_tokens": collect_qwen_usage_for_run_dir(run_dir),
        "step_details": extract_step_details(summary),
        "planned_improvements": extract_improvements(apply_plan),
    }


def build_iteration_summary(
    iteration: int,
    *,
    apply_outcome: str,
    primary_run_id: str | None,
    primary_report: dict[str, Any] | None,
    verify_run_id: str | None,
    verify_report: dict[str, Any] | None,
    apply_plan: dict[str, Any] | None,
    cursor_apply_result: dict[str, Any] | None,
    good_sha_before: str,
    good_sha_after: str,
) -> dict[str, Any]:
    primary_dir = Path("workspace/runs") / primary_run_id if primary_run_id else None
    verify_dir = Path("workspace/runs") / verify_run_id if verify_run_id else None

    primary_metrics = (
        collect_run_metrics(primary_dir, primary_report)
        if primary_dir and primary_dir.is_dir()
        else {}
    )
    verify_metrics = (
        collect_run_metrics(verify_dir, verify_report)
        if verify_dir and verify_dir.is_dir()
        else {}
    )

    qwen_combined = _sum_usage([
        (primary_metrics.get("qwen_tokens") or {}).get("combined"),
        (verify_metrics.get("qwen_tokens") or {}).get("combined"),
    ])

    return {
        "iteration": iteration,
        "apply_outcome": apply_outcome,
        "good_sha_before": good_sha_before,
        "good_sha_after": good_sha_after,
        "primary_run": primary_metrics,
        "verify_run": verify_metrics if verify_run_id else None,
        "improvements_applied": extract_improvements(apply_plan),
        "cursor_apply": {
            "status": (cursor_apply_result or {}).get("status"),
            "model": (cursor_apply_result or {}).get("model"),
            "elapsed_s": (cursor_apply_result or {}).get("elapsed_s"),
            "usage": (cursor_apply_result or {}).get("usage"),
        } if cursor_apply_result else None,
        "token_summary": {
            "qwen_primary_run": (primary_metrics.get("qwen_tokens") or {}).get("combined"),
            "qwen_verify_run": (verify_metrics.get("qwen_tokens") or {}).get("combined"),
            "qwen_iteration_total": qwen_combined,
            "cursor_apply": (cursor_apply_result or {}).get("usage"),
        },
    }
