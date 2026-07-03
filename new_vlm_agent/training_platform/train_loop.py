#!/usr/bin/env python3
"""
VLM Agent 训练循环（两种模式）

**仅测（默认）** — 重复跑 agent + auto_analyze，不改代码、无 verify：
    python training_platform/train_loop.py --rounds 10
    python training_platform/train_loop.py --rounds 1 --task data/cases/case_002_tp332_front/task.yaml

**测+改（--apply-verify）** — 主跑 → VLM 反思 → Cursor 改代码 → verify → 保留/回滚：
    python training_platform/train_loop.py --rounds 5 --apply-verify
    python training_platform/train_loop.py --rounds 3 --apply-verify \\
        --task data/cases/case_002_tp332_front/task.yaml

测+改等同 autonomous_loop.py，需 git 仓库 + CURSOR_API_KEY + pip install cursor-sdk。
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    from agent.config import DEFAULT_MAX_STEPS
except ImportError:
    # Baseline agent (v1-baseline) has no DEFAULT_MAX_STEPS; second-session platform used 40.
    DEFAULT_MAX_STEPS = int(os.environ.get("VLM_AGENT_MAX_STEPS", "40"))
from training_platform.reflect_theme_patch import apply_reflect_theme_patch
from training_platform.run_artifacts import (
    prepare_fresh_workspace_debug,
    snapshot_workspace_debug_to_run,
)

# ==================== 配置 ====================
DEFAULT_TASK = "data/cases/case_011/task.yaml"
RUNS_DIR = Path("workspace/runs")
REPORTS_DIR = Path("training_platform/analysis_reports")
GOLDEN_DIR = Path("training_platform/golden")
LOOP_LOG = Path("training_platform/train_loop_log.jsonl")
MAX_STEPS = DEFAULT_MAX_STEPS


def _expected_duration_hint(task_path: str) -> str:
    rel = task_path.replace("\\", "/").lower()
    if "case_002" in rel:
        return "约 15–30 秒"
    return "约 5–6 分钟"


def run_agent_once(
    task_path: str,
    round_idx: int,
    *,
    post_run_reflect: bool = False,
    label: str | None = None,
) -> Dict[str, Any]:
    """运行一次完整的 agent 流程，返回基本信息

    注意：这里故意不使用 capture_output=True，
    而是让 agent 的输出直接继承到当前终端，
    这样用户能看到 agent 完整的进度、表格、LLM 调用耗时等实时信息。
    否则会看起来像“卡住”了（其实是在后台默默运行 5 分钟）。
    """
    tag = label or f"Round {round_idx}"
    eta = _expected_duration_hint(task_path)
    print(f"\n{'='*60}")
    print(f"[{tag}] 开始运行 agent ...")
    print(f"预计耗时 {eta}，请耐心等待（会实时显示 agent 的完整输出）")
    print(f"{'='*60}\n")

    apply_reflect_theme_patch()
    prepare_fresh_workspace_debug()
    print(f"[{tag}] 已清空 workspace/debug，避免沿用上一轮产物\n")

    start = time.time()
    cmd = [sys.executable, "-m", "agent", "run", task_path, "--max-steps", str(MAX_STEPS)]
    if post_run_reflect or os.environ.get("VLM_POST_RUN_REFLECT", "").strip().lower() in {
        "1", "true", "yes", "y", "on",
    }:
        cmd.append("--post-run-reflect")
    # 关键修复：不 capture_output，让 agent 的富文本输出实时显示
    result = subprocess.run(cmd)
    elapsed = time.time() - start

    # 找到最新生成的 run 目录
    latest_run = None
    if RUNS_DIR.exists():
        runs = [d for d in RUNS_DIR.iterdir() if d.is_dir() and d.name.endswith("-task")]
        if runs:
            latest_run = max(runs, key=lambda d: d.stat().st_mtime)

    print(f"\n{'='*60}")
    print(f"[{tag}] agent 运行完成，耗时 {elapsed:.1f}s")
    if latest_run:
        print(f"[{tag}] run_id = {latest_run.name}")
        snapshot_workspace_debug_to_run(latest_run)
        print(f"[{tag}] 已快照 debug → {latest_run / 'debug'}")
    print(f"{'='*60}\n")

    return {
        "round": round_idx,
        "label": tag,
        "agent_exit_code": result.returncode,
        "agent_elapsed_sec": round(elapsed, 1),
        "run_id": latest_run.name if latest_run else None,
        "stdout_tail": "(output streamed live, not captured)",
        "stderr_tail": "",
    }


def run_auto_analyze(
    round_idx: int,
    run_id: str | None = None,
    task_path: str | None = None,
) -> Dict[str, Any]:
    """Run auto_analyze for a specific run (or latest if run_id omitted)."""
    from training_platform.auto_analyze import (
        REPORTS_DIR as ANALYZE_REPORTS_DIR,
        analyze_run,
        find_latest_run,
        update_trend,
    )

    run_dir = RUNS_DIR / run_id if run_id else find_latest_run()
    if run_dir is None or not run_dir.is_dir():
        print(f"[Round {round_idx}] auto_analyze: run 目录不存在 (run_id={run_id})")
        return {
            "analyze_exit_code": 1,
            "pixel_within_tolerance": False,
            "failure_streak": 0,
            "report": None,
        }

    print(f"[Round {round_idx}] auto_analyze run_id={run_dir.name} ...")
    report = analyze_run(run_dir, task_path=task_path)
    print(
        f"[Round {round_idx}] benchmark case={report.get('case_id')} "
        f"golden={report.get('golden_pixel')}"
    )

    ANALYZE_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = ANALYZE_REPORTS_DIR / f"{run_dir.name}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[Round {round_idx}] 分析报告: {report_path}")

    deliverable = report.get("deliverable_check") or {}
    if not deliverable.get("valid"):
        print(
            f"[Round {round_idx}] deliverable_check FAILED: "
            f"{deliverable.get('reason')} (stopped={deliverable.get('stopped_reason')})"
        )

    passed = bool(report.get("pixel_within_tolerance"))
    trend = update_trend(run_dir.name, passed)
    streak = trend.get("streak", 0)

    print(f"[Round {round_idx}] 分析完成，pixel_within_tolerance={passed}, streak={streak}")
    return {
        "analyze_exit_code": 0,
        "pixel_within_tolerance": passed,
        "failure_streak": streak,
        "report": report,
    }


def log_round(record: Dict[str, Any]):
    """把每轮结果追加到 train_loop_log.jsonl"""
    LOOP_LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOOP_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="训练循环：默认仅测；加 --apply-verify 进入测+改（主跑→改码→verify）",
    )
    parser.add_argument("--rounds", type=int, default=10, help="连续运行多少轮（默认10）")
    parser.add_argument("--task", type=str, default=DEFAULT_TASK, help="任务 yaml 路径")
    parser.add_argument(
        "--apply-verify",
        action="store_true",
        help="测+改模式：主跑 → VLM反思 → Cursor改代码 → verify（等同 autonomous_loop）",
    )
    parser.add_argument(
        "--baseline-ref",
        type=str,
        default=None,
        help="测+改时 verify 失败回滚的 git ref（默认 baseline_ref.txt）",
    )
    parser.add_argument(
        "--apply-when-inaccurate",
        action="store_true",
        help="测+改时主跑像素未过也尝试 Cursor apply（默认跳过）",
    )
    parser.add_argument(
        "--post-run-reflect",
        action="store_true",
        help="仅测模式：每轮 agent 结束后由 VLM 反思效率（测+改模式默认已开启反思）",
    )
    args = parser.parse_args()

    if args.apply_verify:
        argv = [
            "training_platform/autonomous_loop.py",
            "--rounds",
            str(args.rounds),
            "--task",
            args.task,
        ]
        if args.baseline_ref:
            argv.extend(["--baseline-ref", args.baseline_ref])
        if args.apply_when_inaccurate:
            argv.append("--apply-when-inaccurate")
        old_argv = sys.argv
        sys.argv = argv
        try:
            from training_platform.autonomous_loop import main as autonomous_main

            raise SystemExit(autonomous_main())
        finally:
            sys.argv = old_argv

    if args.post_run_reflect:
        os.environ["VLM_POST_RUN_REFLECT"] = "true"

    from training_platform.case_benchmarks import resolve_case_benchmark

    bench = resolve_case_benchmark(task_path=args.task)
    print(f"\n{'#'*60}")
    print(f"# VLM Agent 训练循环（仅测模式，无改码 / 无 verify）")
    print(f"# 总轮数: {args.rounds}")
    print(f"# 任务: {args.task}")
    print(f"# 基准: {bench.case_id} golden={bench.golden_pixel} ±10px")
    print(f"# 每轮 = 运行 agent → auto_analyze → 记日志")
    print(f"# 若要测+改，请加 --apply-verify")
    print(f"{'#'*60}\n")

    all_records = []
    for r in range(1, args.rounds + 1):
        rec = {"round": r, "start_time": datetime.now().isoformat()}

        # 1. 运行 agent
        agent_info = run_agent_once(args.task, r)
        rec.update(agent_info)

        # 2. 自动分析
        analyze_info = run_auto_analyze(
            r, run_id=agent_info.get("run_id"), task_path=args.task
        )
        rec.update(analyze_info)

        # 3. 记录本轮结果
        rec["end_time"] = datetime.now().isoformat()
        log_round(rec)
        all_records.append(rec)

        # 4. 简单总结
        print(f"[Round {r}] 本轮总结: agent={agent_info['agent_elapsed_sec']}s, "
              f"pixel_ok={analyze_info['pixel_within_tolerance']}, streak={analyze_info['failure_streak']}")

        # 5. 如果连续失败达到阈值，给出明显提示
        if analyze_info["failure_streak"] >= 2:
            print(f"\n⚠️  [Round {r}] 连续失败次数达到 {analyze_info['failure_streak']}，"
                  f"建议改用 --apply-verify 进入测+改模式。\n")

    # 最终汇总
    print(f"\n{'='*60}")
    print(f"训练循环完成！共 {args.rounds} 轮")
    print(f"详细日志: {LOOP_LOG}")
    print(f"每轮报告: {REPORTS_DIR}")
    print(f"{'='*60}")

    # 简单统计
    success_count = sum(1 for rec in all_records if rec.get("pixel_within_tolerance"))
    print(f"成功（容差内）: {success_count}/{args.rounds}")
    if success_count < args.rounds:
        print("存在失败轮次，建议查看 accuracy_trend.json 和 needs_attention.json")


if __name__ == "__main__":
    main()
