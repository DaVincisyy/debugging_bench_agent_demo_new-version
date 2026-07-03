#!/usr/bin/env python3
"""Run train_loop (agent + auto_analyze) on every data/cases/*/task.yaml and emit a report table."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from training_platform.train_loop import run_agent_once, run_auto_analyze

from training_platform.case_benchmarks import CASE002_GOLDEN_PIXEL, CASE011_GOLDEN_PIXEL

GOLDEN_PIXEL_CASE011 = CASE011_GOLDEN_PIXEL
CASE002_EXPECTED_PIXEL = CASE002_GOLDEN_PIXEL
TOLERANCE_PX = 10

OUT_JSONL = Path("training_platform/batch_all_cases_results.jsonl")
OUT_MD = Path("training_platform/batch_all_cases_table.md")


def discover_tasks() -> list[Path]:
    root = _REPO / "data" / "cases"
    tasks = sorted(root.rglob("task.yaml"), key=lambda p: p.as_posix().lower())
    return tasks


def case_pass(task_path: Path, report: dict | None, agent_info: dict) -> tuple[bool, str]:
    if not report:
        return False, "no report"
    deliverable = report.get("deliverable_check") or {}
    if not deliverable.get("valid"):
        return False, str(deliverable.get("reason") or "deliverable invalid")
    if agent_info.get("agent_exit_code") not in (0, None):
        return False, f"exit {agent_info.get('agent_exit_code')}"

    rel = task_path.as_posix().replace("\\", "/")
    if report.get("pixel_within_tolerance") is not None:
        px_cmp = report.get("pixel_comparison") or {}
        actual = px_cmp.get("actual")
        golden = report.get("golden_pixel") or (
            CASE002_EXPECTED_PIXEL if "case_002" in rel else GOLDEN_PIXEL_CASE011
        )
        note = (
            f"{report.get('case_id', 'case')} pixel {actual} vs {golden} "
            f"(±{TOLERANCE_PX}px)"
        )
        return bool(report.get("pixel_within_tolerance")), note
    # Legacy fallback (old reports without case_id)
    px_cmp = report.get("pixel_comparison") or {}
    actual = px_cmp.get("actual")
    if "case_002" in rel:
        if not actual or len(actual) != 2:
            return False, "missing pixel"
        ok = abs(actual[0] - CASE002_EXPECTED_PIXEL[0]) <= TOLERANCE_PX and abs(
            actual[1] - CASE002_EXPECTED_PIXEL[1]
        ) <= TOLERANCE_PX
        return ok, f"case002 pixel {actual} vs {CASE002_EXPECTED_PIXEL}"
    if "case_011" in rel:
        return bool(report.get("pixel_within_tolerance")), "case011 golden ±10px"
    return True, "deliverable OK (no case-specific golden)"


def format_result(passed: bool, note: str) -> str:
    return f"✅ 通过（{note}）" if passed else f"❌ 未通过（{note}）"


def run_id_short(run_id: str | None) -> str:
    if not run_id:
        return "—"
    # e.g. 20260630-114604-task -> 114604
    parts = run_id.split("-")
    return parts[1] if len(parts) >= 2 else run_id[:6]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="Max cases (0=all)")
    args = parser.parse_args()

    tasks = discover_tasks()
    if args.limit:
        tasks = tasks[: args.limit]

    OUT_JSONL.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSONL.write_text("", encoding="utf-8")
    rows: list[dict] = []

    print(f"Batch: {len(tasks)} case(s)\n")

    for i, task in enumerate(tasks, 1):
        rel = task.relative_to(_REPO).as_posix()
        print(f"\n{'='*60}\n[{i}/{len(tasks)}] {rel}\n{'='*60}")
        rec = {
            "index": i,
            "task": rel,
            "start_time": datetime.now().isoformat(timespec="seconds"),
        }
        agent_info = run_agent_once(str(task), i, label=f"Case {i}")
        rec.update(agent_info)
        analyze_info = run_auto_analyze(
            i, run_id=agent_info.get("run_id"), task_path=str(task_path)
        )
        rec.update(analyze_info)
        report = analyze_info.get("report") or {}
        passed, note = case_pass(task, report, agent_info)
        rec["case_pass"] = passed
        rec["case_pass_note"] = note
        rec["end_time"] = datetime.now().isoformat(timespec="seconds")
        rows.append(rec)
        with OUT_JSONL.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # Markdown table (screenshot-like columns, adapted for per-case batch)
    lines = [
        "# 测试平台 — 全量 Case 批量跑分表",
        "",
        f"生成时间: {datetime.now().isoformat(timespec='seconds')}",
        f"任务数: {len(rows)} | 基准: case_011 golden `{GOLDEN_PIXEL_CASE011}` ±{TOLERANCE_PX}px | case_002 期望 `{CASE002_EXPECTED_PIXEL}`",
        "",
        "| 轮次 | Case | 主跑 run | 主跑用时 | 分析 run | 像素(actual) | 平台 pixel_ok | 结果 | 说明 |",
        "|------|------|----------|----------|----------|--------------|---------------|------|------|",
    ]
    for r in rows:
        report = r.get("report") or {}
        px = (report.get("pixel_comparison") or {}).get("actual")
        px_s = str(px) if px else "—"
        plat_ok = "是" if r.get("pixel_within_tolerance") else "否"
        case_name = Path(r["task"]).parent.name
        lines.append(
            "| {idx} | {case} | {main} | {main_t:.1f}s | {ana} | {px} | {plat} | {res} | {note} |".format(
                idx=r["index"],
                case=case_name,
                main=run_id_short(r.get("run_id")),
                main_t=float(r.get("agent_elapsed_sec") or 0),
                ana=run_id_short((report or {}).get("run_id")),
                px=px_s,
                plat=plat_ok,
                res="✅ 通过" if r.get("case_pass") else "❌ 未通过",
                note=r.get("case_pass_note", ""),
            )
        )

    passed_n = sum(1 for r in rows if r.get("case_pass"))
    lines.extend(
        [
            "",
            f"**汇总**: 通过 {passed_n}/{len(rows)}",
            "",
            "说明:",
            "- `平台 pixel_ok` 仅在与 **case_011 golden** 对比时有意义；其他 case 请看「结果」列（case_002 用独立期望像素）。",
            "- 原始 JSONL: `training_platform/batch_all_cases_results.jsonl`",
        ]
    )
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nWrote {OUT_MD} and {OUT_JSONL}")
    return 0 if passed_n == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
