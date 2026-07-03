#!/usr/bin/env python3
"""Extract before/after wall times from latest autonomous 20-round session."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ROUNDS_DIR = REPO / "training_platform" / "autonomous_rounds"
LOG_PATH = REPO / "training_platform" / "autonomous_log.jsonl"
RUNS_DIR = REPO / "workspace" / "runs"
BASELINE_SHA_PREFIX = "43b0189"


def _load_log_records() -> list[dict]:
    if not LOG_PATH.is_file():
        return []
    out = []
    for line in LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _wall_from_log(rec: dict, *, verify: bool) -> float | None:
    if verify:
        agent = (rec.get("verify") or {}).get("agent") or {}
        v = agent.get("agent_elapsed_sec")
    else:
        v = (rec.get("run") or {}).get("agent_elapsed_sec")
    return float(v) if v is not None else None


def _wall_from_summary(run_id: str | None) -> float | None:
    if not run_id:
        return None
    p = RUNS_DIR / run_id / "summary.json"
    if not p.is_file():
        return None
    try:
        s = json.loads(p.read_text(encoding="utf-8"))
        t = (s.get("part_timing") or {}).get("total_s")
        return float(t) if t is not None else None
    except Exception:
        return None


def _pick_new_session_records(records: list[dict]) -> list[dict]:
    """One record per iteration for the session starting 2026-06-08 from baseline."""
    by_iter: dict[int, dict] = {}
    session_start: str | None = None
    for rec in records:
        it = rec.get("iteration")
        if not isinstance(it, int) or not (1 <= it <= 20):
            continue
        baseline = str(rec.get("good_sha_before") or "")
        start = str(rec.get("start_time") or "")
        if it == 1 and baseline.startswith(BASELINE_SHA_PREFIX):
            session_start = start
            by_iter[it] = rec
    if session_start is None:
        # fallback: any records dated 2026-06-08+
        for rec in records:
            it = rec.get("iteration")
            start = str(rec.get("start_time") or "")
            if isinstance(it, int) and 1 <= it <= 20 and start >= "2026-06-08":
                by_iter[it] = rec
    else:
        for rec in records:
            it = rec.get("iteration")
            start = str(rec.get("start_time") or "")
            if (
                isinstance(it, int)
                and 1 <= it <= 20
                and start >= session_start
            ):
                by_iter[it] = rec
    return [by_iter[i] for i in sorted(by_iter)]


def _load_round(it: int) -> dict | None:
    p = ROUNDS_DIR / f"round_{it:03d}.json"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def main() -> None:
    records = _pick_new_session_records(_load_log_records())
    rows = []

    for it in range(1, 21):
        rd = _load_round(it)
        rec = next((r for r in records if r.get("iteration") == it), {})
        if not rd and not rec:
            continue
        pr = (rd or {}).get("primary_run") or {}
        vr = (rd or {}).get("verify_run") or {}

        primary_id = (rec.get("run") or {}).get("run_id") or pr.get("run_id")
        verify_id = ((rec.get("verify") or {}).get("agent") or {}).get("run_id")
        if not verify_id and vr:
            verify_id = vr.get("run_id")

        before_part = (pr.get("part_timing") or {}).get("total_s")
        after_part = (vr.get("part_timing") or {}).get("total_s")

        # 改前：仅 agent 主循环 part_timing（不含 post_run_reflect）
        before_agent_loop = (
            float(before_part)
            if before_part is not None
            else _wall_from_summary(primary_id)
        )
        after_wall = (
            float(after_part)
            if after_part is not None
            else _wall_from_log(rec, verify=True)
            or _wall_from_summary(verify_id)
        )

        pixel = (vr.get("pixel_comparison") or pr.get("pixel_comparison") or {})
        deliverable = None
        if verify_id:
            from training_platform.auto_analyze import analyze_run

            run_dir = RUNS_DIR / verify_id
            if run_dir.is_dir():
                rep = analyze_run(run_dir)
                deliverable = rep.get("deliverable_check")
                pixel = rep.get("pixel_comparison") or pixel

        changes = []
        for a in ((rd or {}).get("improvements_applied") or []):
            changes.append(
                {
                    "title": a.get("title"),
                    "targets": a.get("target_files"),
                }
            )

        delta = None
        if before_agent_loop is not None and after_wall is not None:
            delta = round(after_wall - before_agent_loop, 1)

        rows.append(
            {
                "iter": it,
                "apply_outcome": rec.get("apply_outcome") or (rd or {}).get("apply_outcome"),
                "primary_run_id": primary_id,
                "verify_run_id": verify_id,
                "before_agent_loop_s": round(before_agent_loop, 1)
                if before_agent_loop is not None
                else None,
                "after_wall_s": round(after_wall, 1) if after_wall is not None else None,
                "delta_wall_s": delta,
                "before_wall_s_incl_reflect": round(
                    (rec.get("run") or {}).get("agent_elapsed_sec"), 1
                )
                if (rec.get("run") or {}).get("agent_elapsed_sec") is not None
                else None,
                "after_part_s": round(after_part, 1) if after_part is not None else None,
                "pixel_dist_px": pixel.get("distance"),
                "pixel_ok": pixel.get("within_tolerance"),
                "deliverable_valid": (deliverable or {}).get("valid"),
                "deliverable_reason": (deliverable or {}).get("reason"),
                "changes": changes,
            }
        )

    out_json = REPO / "training_platform" / "autonomous_20round_report_v2.json"
    out_md = REPO / "training_platform" / "autonomous_20round_report_v2.md"
    payload = {
        "session_start": records[0].get("start_time") if records else None,
        "row_count": len(rows),
        "rows": rows,
    }
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# 新一轮 Autonomous 20 轮 — 改前/改后耗时",
        "",
        f"会话起始: {payload.get('session_start')} | 轮次数: {len(rows)}",
        "",
        "改前 = primary 的 `part_timing.total_s`（不含 reflect）。改后 = verify 的 `part_timing.total_s`。",
        "",
        "| 轮次 | outcome | 改前 agent loop(s) | 改后 verify(s) | Δ(s) | 像素 | deliverable | 改动 |",
        "|------|---------|-------------------|----------------|------|------|-------------|------|",
    ]
    for r in rows:
        ch = "; ".join(
            (c.get("title") or "")[:36] for c in (r.get("changes") or [])[:2]
        ) or "—"
        lines.append(
            f"| {r['iter']} | {r.get('apply_outcome')} | "
            f"{r.get('before_agent_loop_s')} | {r.get('after_wall_s')} | "
            f"{r.get('delta_wall_s')} | "
            f"{r.get('pixel_dist_px')}px ({'OK' if r.get('pixel_ok') else 'FAIL'}) | "
            f"{'OK' if r.get('deliverable_valid') else r.get('deliverable_reason', 'n/a')} | "
            f"{ch} |"
        )
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {out_json}")
    print(f"wrote {out_md}")
    print()
    print(f"{'iter':>4}  {'before_loop':>11}  {'after':>8}  {'delta':>8}  outcome")
    for r in rows:
        print(
            f"{r['iter']:4d}  "
            f"{r.get('before_agent_loop_s') or '-':>11}  "
            f"{r.get('after_wall_s') or '-':>8}  "
            f"{r.get('delta_wall_s') or '-':>8}  "
            f"{r.get('apply_outcome')}"
        )


if __name__ == "__main__":
    main()
