#!/usr/bin/env python3
"""Summarize timing + Qwen tokens for the new 20-round autonomous session."""
from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ROUNDS_DIR = REPO / "training_platform" / "autonomous_rounds"
RUNS_DIR = REPO / "workspace" / "runs"
OUT_MD = REPO / "training_platform" / "autonomous_20round_tokens_v2.md"
OUT_JSON = REPO / "training_platform" / "autonomous_20round_tokens_v2.json"


def _load_json(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _tok(d: dict | None) -> int:
    if not d:
        return 0
    return int(d.get("total_tokens") or 0)


def main() -> None:
    rows: list[dict] = []
    totals = {
        "agent_loop_s": 0.0,
        "reflection_s": 0.0,
        "verify_loop_s": 0.0,
        "cursor_apply_s": 0.0,
        "agent_main_tokens": 0,
        "reflection_tokens": 0,
        "apply_plan_tokens": 0,
        "verify_agent_tokens": 0,
        "qwen_iteration_total": 0,
    }

    for it in range(1, 21):
        rd = _load_json(ROUNDS_DIR / f"round_{it:03d}.json")
        if not rd:
            continue
        pr = rd.get("primary_run") or {}
        vr = rd.get("verify_run") or {}
        pid = pr.get("run_id")
        vid = vr.get("run_id") if vr else None
        reflect = _load_json(RUNS_DIR / pid / "vlm_self_reflection.json") if pid else None

        loop_s = float((pr.get("part_timing") or {}).get("total_s") or 0)
        verify_s = float((vr.get("part_timing") or {}).get("total_s") or 0) if vr else 0.0
        refl_s = float((reflect or {}).get("reflection_timing_s") or 0)
        outcome = rd.get("apply_outcome") or ""
        apply_s = 0.0
        if not str(outcome).startswith("skipped"):
            apply_s = float((rd.get("cursor_apply") or {}).get("elapsed_s") or 0)

        qt_p = pr.get("qwen_tokens") or {}
        agent_main = _tok(qt_p.get("agent_main_loop"))
        reflection = _tok(qt_p.get("reflection"))
        apply_plan = _tok(qt_p.get("apply_plan_generation"))
        verify_agent = _tok((vr.get("qwen_tokens") or {}).get("agent_main_loop")) if vr else 0
        iter_total = _tok((rd.get("token_summary") or {}).get("qwen_iteration_total"))
        if not iter_total:
            iter_total = agent_main + reflection + apply_plan + verify_agent

        row = {
            "iter": it,
            "apply_outcome": outcome,
            "primary_run_id": pid,
            "verify_run_id": vid,
            "reflect_md_path": str(RUNS_DIR / pid / "vlm_self_reflection.md") if pid else None,
            "reflect_json_path": str(RUNS_DIR / pid / "vlm_self_reflection.json") if pid else None,
            "agent_loop_s": round(loop_s, 1),
            "reflection_s": round(refl_s, 1),
            "verify_loop_s": round(verify_s, 1),
            "cursor_apply_s": round(apply_s, 1),
            "agent_main_tokens": agent_main,
            "reflection_tokens": reflection,
            "apply_plan_tokens": apply_plan,
            "verify_agent_tokens": verify_agent,
            "qwen_iteration_total": iter_total,
        }
        rows.append(row)

        totals["agent_loop_s"] += loop_s
        totals["reflection_s"] += refl_s
        totals["verify_loop_s"] += verify_s
        totals["cursor_apply_s"] += apply_s
        totals["agent_main_tokens"] += agent_main
        totals["reflection_tokens"] += reflection
        totals["apply_plan_tokens"] += apply_plan
        totals["verify_agent_tokens"] += verify_agent
        totals["qwen_iteration_total"] += iter_total

    wall_s = (
        totals["agent_loop_s"]
        + totals["reflection_s"]
        + totals["verify_loop_s"]
        + totals["cursor_apply_s"]
    )
    payload = {
        "row_count": len(rows),
        "totals": {k: round(v, 1) if isinstance(v, float) else v for k, v in totals.items()},
        "total_wall_s": round(wall_s, 1),
        "total_wall_h": round(wall_s / 3600, 2),
        "reflect_view_pattern": "workspace/runs/<primary_run_id>/vlm_self_reflection.md",
        "rows": rows,
    }
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# 新一轮 20 轮 — 耗时与 Token 汇总",
        "",
        "## Reflect 内容在哪看",
        "",
        "每轮 **primary run** 结束后会写两份文件（同一目录）：",
        "",
        "- Markdown（人类可读）：`workspace/runs/<primary_run_id>/vlm_self_reflection.md`",
        "- JSON（含 usage / timing）：`workspace/runs/<primary_run_id>/vlm_self_reflection.json`",
        "",
        "同目录还有 `apply_plan.json`（reflect 后生成的 Cursor 改动计划）。",
        "",
        f"示例：第 1 轮 → `{rows[0]['reflect_md_path']}`" if rows else "",
        "",
        "## 总计",
        "",
        f"| 指标 | 数值 |",
        f"|------|------|",
        f"| primary agent 主循环 | {totals['agent_loop_s']:.1f}s |",
        f"| primary reflection | {totals['reflection_s']:.1f}s |",
        f"| verify agent 主循环 | {totals['verify_loop_s']:.1f}s |",
        f"| Cursor apply | {totals['cursor_apply_s']:.1f}s |",
        f"| **合计墙钟（上述四项）** | **{wall_s:.1f}s（≈{wall_s/3600:.2f}h）** |",
        f"| Qwen agent 主循环 tokens | {totals['agent_main_tokens']:,} |",
        f"| Qwen reflection tokens | {totals['reflection_tokens']:,} |",
        f"| Qwen apply_plan tokens | {totals['apply_plan_tokens']:,} |",
        f"| Qwen verify agent tokens | {totals['verify_agent_tokens']:,} |",
        f"| **Qwen 每轮 iteration 合计** | **{totals['qwen_iteration_total']:,}** |",
        "",
        "## 逐轮明细",
        "",
        "| 轮次 | outcome | agent loop(s) | reflect(s) | verify(s) | apply(s) | agent tok | reflect tok | plan tok | verify tok | iter total tok | reflect 路径 |",
        "|------|---------|---------------|------------|-----------|----------|-----------|-------------|----------|------------|----------------|--------------|",
    ]
    for r in rows:
        rel = f"runs/{r['primary_run_id']}/vlm_self_reflection.md" if r["primary_run_id"] else "—"
        lines.append(
            f"| {r['iter']} | {r['apply_outcome']} | {r['agent_loop_s']} | {r['reflection_s']} | "
            f"{r['verify_loop_s']} | {r['cursor_apply_s']} | {r['agent_main_tokens']:,} | "
            f"{r['reflection_tokens']:,} | {r['apply_plan_tokens']:,} | {r['verify_agent_tokens']:,} | "
            f"{r['qwen_iteration_total']:,} | `{rel}` |"
        )
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {OUT_MD}")
    print(f"wrote {OUT_JSON}")
    print(f"total_wall_s={wall_s:.1f} qwen_tokens={totals['qwen_iteration_total']:,}")


if __name__ == "__main__":
    main()
