"""Cross-round improvement memory for autonomous reflect / apply (platform only)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

HISTORY_JSONL = Path("training_platform/autonomous_improvement_history.jsonl")
HISTORY_MD = Path("training_platform/autonomous_improvement_history.md")
DEFAULT_MAX_ENTRIES = 12


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _part_total(part_timing: dict[str, Any] | None) -> float | None:
    if not part_timing:
        return None
    return _safe_float(part_timing.get("total_s"))


def _pixel_distance(report: dict[str, Any] | None) -> float | None:
    if not report:
        return None
    pc = report.get("pixel_comparison") or {}
    if pc.get("status") == "missing_pixel_comparison":
        return None
    return _safe_float(pc.get("distance_px") or pc.get("distance"))


def _pixel_ok(report: dict[str, Any] | None) -> bool | None:
    if not report:
        return None
    if report.get("pixel_within_tolerance") is not None:
        return bool(report.get("pixel_within_tolerance"))
    pc = report.get("pixel_comparison") or {}
    if "within_tolerance" in pc:
        return bool(pc.get("within_tolerance"))
    return None


def _reflect_excerpt(run_dir: Path | None, max_chars: int = 1200) -> str:
    if run_dir is None:
        return ""
    md_path = run_dir / "vlm_self_reflection.md"
    if not md_path.is_file():
        return ""
    text = md_path.read_text(encoding="utf-8")
    if len(text) <= max_chars:
        return text.strip()
    return text[:max_chars].rstrip() + "\n…(truncated)"


def _outcome_label(
    apply_outcome: str,
    *,
    verify_pixel_ok: bool | None,
    verify_total_s: float | None,
    session_best_before_s: float | None,
    kept: bool,
) -> str:
    if apply_outcome.startswith("skipped"):
        return f"未执行改动（{apply_outcome}）"
    if apply_outcome == "apply_error":
        return "负收益：Cursor apply 失败"
    if apply_outcome == "reverted_pixel":
        return "负收益：verify 像素未过 10px，已 git revert"
    if apply_outcome == "reverted_not_session_best":
        base = "负收益/中性：像素 OK 但 verify 未刷新 session best，已 revert"
        if verify_total_s is not None and session_best_before_s is not None:
            base += f"（{verify_total_s:.1f}s >= best {session_best_before_s:.1f}s）"
        return base
    if apply_outcome == "reverted_no_timing":
        return "负收益：verify 缺计时，已 revert"
    if kept or apply_outcome in {"kept", "kept_no_commit_diff"}:
        if verify_total_s is not None and session_best_before_s is not None:
            return (
                f"正收益：保留改动，verify {verify_total_s:.1f}s "
                f"< 原 best {session_best_before_s:.1f}s"
            )
        return "正收益：保留改动（像素 OK）"
    if verify_pixel_ok is False:
        return "负收益：verify 像素失败"
    return f"结果：{apply_outcome}"


def build_improvement_record(
    rec: dict[str, Any],
    *,
    round_summary: dict[str, Any] | None = None,
    run_dir: Path | None = None,
    primary_report: dict[str, Any] | None = None,
    verify_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One autonomous iteration → structured history row."""
    summary = round_summary or rec.get("round_summary") or {}
    primary = summary.get("primary_run") or {}
    verify = summary.get("verify_run") or {}
    apply_plan = summary.get("planned_improvements") or []
    if not apply_plan:
        apply_plan = summary.get("improvements_applied") or []

    apply_outcome = str(rec.get("apply_outcome") or "unknown")
    verify_gate = rec.get("verify_gate") or {}
    verify_wall_s = _safe_float(verify_gate.get("verify_wall_s"))
    session_best_before = _safe_float(verify_gate.get("session_best_before_s"))
    verify_pixel_ok = _pixel_ok(verify_report) if verify_report else _pixel_ok(verify)
    primary_total = _part_total(primary.get("part_timing"))
    verify_total = _part_total(verify.get("part_timing")) or verify_wall_s
    kept = apply_outcome in {"kept", "kept_no_commit_diff"}

    primary_dist = _pixel_distance(primary_report)
    verify_dist = _pixel_distance(verify_report)

    cursor_apply = rec.get("apply") or summary.get("cursor_apply") or {}
    actions = []
    for action in apply_plan:
        if not isinstance(action, dict):
            continue
        actions.append({
            "id": action.get("id"),
            "title": action.get("title"),
            "category": action.get("category"),
            "target_files": action.get("target_files"),
        })

    record: dict[str, Any] = {
        "recorded_at": datetime.now().isoformat(timespec="seconds"),
        "iteration": rec.get("iteration"),
        "baseline_ref": rec.get("baseline_ref"),
        "apply_outcome": apply_outcome,
        "outcome_zh": _outcome_label(
            apply_outcome,
            verify_pixel_ok=verify_pixel_ok,
            verify_total_s=verify_total,
            session_best_before_s=session_best_before,
            kept=kept,
        ),
        "kept_in_repo": kept,
        "primary_run_id": (rec.get("run") or {}).get("run_id") or primary.get("run_id"),
        "verify_run_id": verify.get("run_id"),
        "good_sha_before": rec.get("good_sha_before"),
        "good_sha_after": rec.get("good_sha_after"),
        "reflection_excerpt_zh": _reflect_excerpt(run_dir),
        "actions_planned_or_applied": actions,
        "cursor_apply": {
            "status": cursor_apply.get("status"),
            "elapsed_s": cursor_apply.get("elapsed_s"),
            "model": cursor_apply.get("model"),
        },
        "metrics": {
            "primary_total_s": primary_total,
            "verify_total_s": verify_total,
            "session_best_before_s": session_best_before,
            "session_best_after_s": verify_gate.get("session_best_after_s"),
            "primary_pixel_ok": _pixel_ok(primary_report) if primary_report else None,
            "verify_pixel_ok": verify_pixel_ok,
            "primary_pixel_distance_px": primary_dist,
            "verify_pixel_distance_px": verify_dist,
            "delta_verify_total_s": (
                round(verify_total - primary_total, 2)
                if verify_total is not None and primary_total is not None
                else None
            ),
        },
    }
    return record


def append_improvement_record(record: dict[str, Any]) -> None:
    HISTORY_JSONL.parent.mkdir(parents=True, exist_ok=True)
    with HISTORY_JSONL.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    _refresh_history_markdown()


def load_improvement_history_entries(
    *,
    max_entries: int = DEFAULT_MAX_ENTRIES,
    baseline_ref: str | None = None,
) -> list[dict[str, Any]]:
    if not HISTORY_JSONL.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in HISTORY_JSONL.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        if baseline_ref and obj.get("baseline_ref") not in {None, baseline_ref}:
            continue
        rows.append(obj)
    if len(rows) <= max_entries:
        return rows
    return rows[-max_entries:]


def format_history_for_vlm(
    entries: list[dict[str, Any]],
    *,
    max_chars: int = 8000,
) -> str:
    if not entries:
        return "（尚无历史改进记录；本轮为首次或 history 文件为空。）"
    lines = [
        "# 历史自主改进记录（最近轮次，供避免重复失败方案）",
        "",
        "规则：若某 action 曾在 `kept_in_repo=false` 或 outcome 为负收益 中出现，"
        "不要再次提出实质相同的改动；可提出不同路径或明确说明为何上次失败本次可避免。",
        "",
    ]
    for entry in entries:
        it = entry.get("iteration", "?")
        lines.append(f"## 第 {it} 轮 — {entry.get('outcome_zh', entry.get('apply_outcome'))}")
        lines.append(f"- primary: `{entry.get('primary_run_id')}`")
        if entry.get("verify_run_id"):
            lines.append(f"- verify: `{entry.get('verify_run_id')}`")
        metrics = entry.get("metrics") or {}
        if metrics.get("primary_total_s") is not None:
            lines.append(f"- primary total: {metrics['primary_total_s']}s")
        if metrics.get("verify_total_s") is not None:
            lines.append(f"- verify total: {metrics['verify_total_s']}s")
        if metrics.get("delta_verify_total_s") is not None:
            lines.append(f"- verify−primary: {metrics['delta_verify_total_s']:+.1f}s")
        if metrics.get("verify_pixel_distance_px") is not None:
            lines.append(
                f"- verify pixel distance: {metrics['verify_pixel_distance_px']}px "
                f"(ok={metrics.get('verify_pixel_ok')})"
            )
        actions = entry.get("actions_planned_or_applied") or []
        if actions:
            lines.append("- 改动/计划：")
            for action in actions:
                title = action.get("title") or action.get("id") or "?"
                cat = action.get("category") or "?"
                files = ", ".join(action.get("target_files") or [])
                lines.append(f"  - [{cat}] {title}" + (f" → `{files}`" if files else ""))
        excerpt = (entry.get("reflection_excerpt_zh") or "").strip()
        if excerpt:
            short = excerpt.replace("\r\n", "\n")
            if len(short) > 500:
                short = short[:500].rstrip() + "…"
            lines.append("- 反思摘要：")
            lines.append(f"  {short.replace(chr(10), chr(10) + '  ')}")
        lines.append("")
    text = "\n".join(lines).strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "\n…(history truncated)"


def _refresh_history_markdown() -> None:
    entries = load_improvement_history_entries(max_entries=DEFAULT_MAX_ENTRIES)
    HISTORY_MD.write_text(
        format_history_for_vlm(entries, max_chars=20000) + "\n",
        encoding="utf-8",
    )


def history_payload_for_reflect(
    *,
    baseline_ref: str | None = None,
    max_entries: int = DEFAULT_MAX_ENTRIES,
) -> dict[str, Any]:
    entries = load_improvement_history_entries(
        max_entries=max_entries,
        baseline_ref=baseline_ref,
    )
    return {
        "history_file": str(HISTORY_JSONL),
        "history_markdown_file": str(HISTORY_MD),
        "entry_count": len(entries),
        "entries": entries,
        "history_markdown_zh": format_history_for_vlm(entries),
    }


def seed_improvement_history_from_rounds(
    *,
    baseline_ref: str | None = None,
    rounds_dir: Path | None = None,
) -> int:
    """Backfill history jsonl from autonomous_rounds/*.json when empty."""
    if HISTORY_JSONL.is_file() and HISTORY_JSONL.stat().st_size > 0:
        return 0
    root = rounds_dir or Path("training_platform/autonomous_rounds")
    if not root.is_dir():
        return 0
    count = 0
    for path in sorted(root.glob("round_*.json")):
        try:
            summary = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(summary, dict):
            continue
        iteration = summary.get("iteration")
        rec = {
            "iteration": iteration,
            "baseline_ref": baseline_ref,
            "apply_outcome": summary.get("apply_outcome", "unknown"),
            "good_sha_before": summary.get("good_sha_before"),
            "good_sha_after": summary.get("good_sha_after"),
            "run": {"run_id": (summary.get("primary_run") or {}).get("run_id")},
            "verify_gate": {},
            "round_summary": summary,
        }
        verify = summary.get("verify_run") or {}
        if verify.get("run_id"):
            rec["verify"] = {"agent": {"run_id": verify.get("run_id")}}
        primary_run_id = (summary.get("primary_run") or {}).get("run_id")
        run_dir = (
            Path("workspace/runs") / str(primary_run_id)
            if primary_run_id
            else None
        )
        record = build_improvement_record(
            rec,
            round_summary=summary,
            run_dir=run_dir if run_dir and run_dir.is_dir() else None,
            primary_report=None,
            verify_report=verify if verify else None,
        )
        record["seeded_from"] = str(path)
        with HISTORY_JSONL.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        count += 1
    if count:
        _refresh_history_markdown()
    return count
