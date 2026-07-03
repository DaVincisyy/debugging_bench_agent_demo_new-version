#!/usr/bin/env python3
"""
Autonomous optimization loop (unattended).

Each iteration:
  1. agent run + VLM self-reflection + apply_plan.json
  2. auto_analyze (10px golden)
  3. Cursor SDK apply (if accurate + has P0 actions)
  4. verify: agent run + auto_analyze
  5. keep (git commit) or revert to last good SHA

Prerequisites:
  - git repo with baseline tagged or training_platform/baseline_ref.txt
  - CURSOR_API_KEY in environment (Cursor Pro usage pool)
  - pip install cursor-sdk
  - .env with VLM_* for the TP localization agent

Usage:
    set CURSOR_API_KEY=cursor_...
    python training_platform/autonomous_loop.py --rounds 5

    # Use your baseline tag instead of HEAD:
    python training_platform/autonomous_loop.py --rounds 10 --baseline-ref v1-baseline
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# Allow imports from repo root when run as script
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from training_platform.apply_plan_guard import guard_apply_plan
from training_platform.cursor_apply import apply_from_plan
from training_platform.git_state import (
    commit_all,
    ensure_git_repo,
    reset_hard,
    resolve_ref,
    working_tree_dirty,
)
from training_platform.train_loop import (
    DEFAULT_TASK,
    LOOP_LOG,
    RUNS_DIR,
    run_agent_once,
    run_auto_analyze,
)
from training_platform.case_benchmarks import resolve_case_benchmark
from training_platform.improvement_history import (
    append_improvement_record,
    build_improvement_record,
    history_payload_for_reflect,
    seed_improvement_history_from_rounds,
)
from training_platform.optimization_directives import OPTIMIZATION_THEMES_ZH
from training_platform.reflect_theme_patch import apply_reflect_theme_patch, set_reflect_context
from training_platform.round_metrics import build_iteration_summary

AUTONOMOUS_LOG = Path("training_platform/autonomous_log.jsonl")
AUTONOMOUS_ROUNDS_DIR = Path("training_platform/autonomous_rounds")
BASELINE_REF_FILE = Path("training_platform/baseline_ref.txt")


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(_REPO_ROOT / ".env", override=False)
    except Exception:  # noqa: BLE001
        pass


def _read_default_baseline_ref() -> str:
    if BASELINE_REF_FILE.is_file():
        for line in BASELINE_REF_FILE.read_text(encoding="utf-8").splitlines():
            text = line.strip()
            if text and not text.startswith("#"):
                return text
    return "HEAD"


def _log_record(record: dict[str, Any]) -> None:
    AUTONOMOUS_LOG.parent.mkdir(parents=True, exist_ok=True)
    with AUTONOMOUS_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _run_dir_for_id(run_id: str | None) -> Path | None:
    if not run_id:
        return None
    p = RUNS_DIR / run_id
    return p if p.is_dir() else None


def _verify_wall_seconds(
    verify_agent: dict[str, Any],
    verify_report: dict[str, Any] | None,
) -> float | None:
    """Wall-clock verify run duration (preferred) or part_timing total fallback."""
    elapsed = verify_agent.get("agent_elapsed_sec")
    if elapsed is not None:
        return float(elapsed)
    run_id = (verify_report or {}).get("run_id") or verify_agent.get("run_id")
    if not run_id:
        return None
    summary_path = RUNS_DIR / str(run_id) / "summary.json"
    if not summary_path.is_file():
        return None
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        total = (summary.get("part_timing") or {}).get("total_s")
        if total is not None:
            return float(total)
    except Exception:  # noqa: BLE001
        return None
    return None


def _append_efficiency_note(
    run_id: str,
    apply_plan: dict[str, Any],
    verify: dict[str, Any],
    *,
    verify_wall_s: float | None = None,
    session_best_s: float | None = None,
) -> None:
    log_path = Path("training_platform/EFFICIENCY_LOG.md")
    if not log_path.is_file():
        return
    titles = [
        str(a.get("title", ""))
        for a in (apply_plan.get("actions") or [])
        if isinstance(a, dict)
    ]
    line = (
        f"\n## {datetime.now().strftime('%Y-%m-%d %H:%M')} | autonomous_loop | kept\n"
        f"- run_id: `{run_id}`\n"
        f"- actions: {', '.join(titles) or '(none)'}\n"
        f"- verify pixel_ok: {verify.get('pixel_within_tolerance')}\n"
        f"- verify wall_s: {verify_wall_s}\n"
        f"- session_best_verify_s: {session_best_s}\n"
        f"- verify report run: {(verify.get('report') or {}).get('run_id', '?')}\n"
    )
    with log_path.open("a", encoding="utf-8") as f:
        f.write(line)


def main() -> int:
    parser = argparse.ArgumentParser(description="Autonomous run → reflect → apply → verify loop")
    parser.add_argument("--rounds", type=int, default=5, help="Iterations (default 5)")
    parser.add_argument("--task", type=str, default=DEFAULT_TASK, help="Task YAML path")
    parser.add_argument(
        "--baseline-ref",
        type=str,
        default=None,
        help="Git ref to reset on failed verify (default: training_platform/baseline_ref.txt)",
    )
    parser.add_argument(
        "--cursor-model",
        type=str,
        default=os.environ.get("CURSOR_APPLY_MODEL", "composer-2.5"),
        help="Cursor SDK model id",
    )
    parser.add_argument(
        "--apply-when-inaccurate",
        action="store_true",
        help="Allow Cursor apply even when pre-apply 10px check fails (default: skip)",
    )
    args = parser.parse_args()

    _load_dotenv()
    repo_root = _REPO_ROOT
    os.chdir(repo_root)
    apply_reflect_theme_patch()
    ensure_git_repo(repo_root)

    baseline_ref = args.baseline_ref or _read_default_baseline_ref()
    good_sha = resolve_ref(repo_root, baseline_ref)
    set_reflect_context(baseline_ref=baseline_ref)
    seed_improvement_history_from_rounds(baseline_ref=baseline_ref)

    skip_when_bad = not args.apply_when_inaccurate
    bench = resolve_case_benchmark(task_path=args.task)

    print(f"\n{'#' * 60}")
    print("# Autonomous loop: run → reflect → apply → verify → keep/revert")
    print(f"# rounds={args.rounds} task={args.task}")
    print(f"# benchmark: {bench.case_id} | {bench.label}")
    print(f"# golden pixel={bench.golden_pixel} ±10px")
    print(f"# baseline_ref={baseline_ref} → {good_sha[:12]}")
    print(f"# good_sha updates after each successful verify+commit")
    print(f"# logs: {AUTONOMOUS_LOG}")
    print(f"# round details: {AUTONOMOUS_ROUNDS_DIR}/round_NNN.json")
    print(f"# requires: CURSOR_API_KEY in .env or env, pip install cursor-sdk")
    print(f"# keep gate: pixel 10px OK AND verify wall_s < session_best (strict)")
    print(f"# apply: Cursor reads vlm_self_reflection.md first; apply_plan is secondary hint")
    print(f"# apply guard: max 1 file / 1 P0; history in {Path('training_platform/autonomous_improvement_history.md')}")
    print(f"# optimization themes: (1) context merge (2) tool merge (3) finish review")
    print(f"{'#' * 60}\n")
    print(OPTIMIZATION_THEMES_ZH)
    print()

    session_best_verify_sec: float | None = None

    for r in range(1, args.rounds + 1):
        rec: dict[str, Any] = {
            "iteration": r,
            "start_time": datetime.now().isoformat(timespec="seconds"),
            "good_sha_before": good_sha,
            "baseline_ref": baseline_ref,
        }

        # --- 1. Run + reflect ---
        agent_info = run_agent_once(
            args.task,
            r,
            post_run_reflect=True,
            label=f"Iter {r} · run",
        )
        rec["run"] = agent_info
        run_dir = _run_dir_for_id(agent_info.get("run_id"))

        # --- 2. Analyze ---
        analyze_info = run_auto_analyze(
            r, run_id=agent_info.get("run_id"), task_path=args.task
        )
        rec["pre_apply_analyze"] = {
            "pixel_within_tolerance": analyze_info.get("pixel_within_tolerance"),
            "failure_streak": analyze_info.get("failure_streak"),
            "report_run_id": (analyze_info.get("report") or {}).get("run_id"),
            "deliverable_valid": (
                (analyze_info.get("report") or {}).get("deliverable_check") or {}
            ).get("valid"),
        }

        apply_plan_path = (run_dir / "apply_plan.json") if run_dir else None
        apply_outcome = "skipped"
        apply_plan_data: dict[str, Any] | None = None
        cursor_apply_result_data: dict[str, Any] | None = None
        verify_run_id: str | None = None
        verify_report: dict[str, Any] | None = None

        if skip_when_bad and not analyze_info.get("pixel_within_tolerance"):
            apply_outcome = "skipped_inaccurate_pre_apply"
            print(f"[Iter {r}] Pre-apply accuracy failed — skip Cursor apply.")
        elif apply_plan_path is None or not apply_plan_path.is_file():
            apply_outcome = "skipped_no_apply_plan"
            print(f"[Iter {r}] No apply_plan.json — skip apply.")
        else:
            plan = json.loads(apply_plan_path.read_text(encoding="utf-8"))
            reflection_path = run_dir / "vlm_self_reflection.md"
            guarded = guard_apply_plan(plan, repo_root, baseline_ref=baseline_ref)
            plan = guarded["plan"]
            plan["guard_report"] = guarded["report"]
            guarded_path = apply_plan_path.parent / "apply_plan.guarded.json"
            guarded_path.write_text(
                json.dumps(plan, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            apply_plan_data = plan
            history_md = history_payload_for_reflect(baseline_ref=baseline_ref).get(
                "history_markdown_zh", ""
            )

            has_reflection = reflection_path.is_file()
            has_actions = bool(plan.get("actions"))
            if not has_actions and not has_reflection:
                apply_outcome = "skipped_empty_actions"
                print(f"[Iter {r}] apply_plan has no actions and no reflection — skip apply.")
            elif (
                guarded["report"].get("input_action_count", 0) > 0
                and not has_actions
                and not has_reflection
            ):
                apply_outcome = "skipped_guard_blocked_all"
                removed = guarded["report"].get("removed_actions") or []
                print(
                    f"[Iter {r}] apply_plan guard blocked all {len(removed)} action(s) — skip apply."
                )
                for item in removed[:3]:
                    print(f"  - {item.get('title')}: {item.get('reason')}")
            else:
                if not has_actions and has_reflection:
                    removed = guarded["report"].get("removed_actions") or []
                    if removed:
                        print(
                            f"[Iter {r}] apply_plan guard blocked {len(removed)} action(s); "
                            f"falling back to reflection-only apply."
                        )
                    else:
                        print(
                            f"[Iter {r}] apply_plan empty — "
                            f"Cursor will use reflection only: {reflection_path.name}"
                        )
                # --- 3. Apply ---
                apply_result = apply_from_plan(
                    guarded_path,
                    repo_root,
                    model=args.cursor_model,
                    reflection_path=reflection_path if has_reflection else None,
                    history_markdown=str(history_md or ""),
                )
                cursor_apply_result_data = apply_result
                rec["apply"] = apply_result
                rec["apply_plan_guard"] = guarded["report"]
                (apply_plan_path.parent / "cursor_apply_result.json").write_text(
                    json.dumps(apply_result, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )

                if apply_result.get("status") != "ok":
                    apply_outcome = "apply_error"
                    print(f"[Iter {r}] Cursor apply failed: {apply_result.get('error')}")
                else:
                    # --- 4. Verify ---
                    verify_agent = run_agent_once(
                        args.task,
                        r,
                        post_run_reflect=False,
                        label=f"Iter {r} · verify",
                    )
                    verify_analyze = run_auto_analyze(
                        r,
                        run_id=verify_agent.get("run_id"),
                        task_path=args.task,
                    )
                    verify_report = verify_analyze.get("report")
                    verify_run_id = (
                        (verify_report or {}).get("run_id")
                        or verify_agent.get("run_id")
                    )
                    rec["verify"] = {
                        "agent": verify_agent,
                        "analyze": {
                            "pixel_within_tolerance": verify_analyze.get(
                                "pixel_within_tolerance"
                            ),
                            "failure_streak": verify_analyze.get("failure_streak"),
                            "report_run_id": (verify_analyze.get("report") or {}).get(
                                "run_id"
                            ),
                            "deliverable_valid": (
                                (verify_analyze.get("report") or {}).get(
                                    "deliverable_check"
                                )
                                or {}
                            ).get("valid"),
                        },
                    }

                    verify_sec = _verify_wall_seconds(verify_agent, verify_report)
                    pixel_ok = bool(verify_analyze.get("pixel_within_tolerance"))
                    rec["verify_gate"] = {
                        "verify_wall_s": verify_sec,
                        "session_best_before_s": session_best_verify_sec,
                        "pixel_within_tolerance": pixel_ok,
                    }

                    if not pixel_ok:
                        reset_hard(repo_root, good_sha)
                        apply_outcome = "reverted_pixel"
                        print(
                            f"[Iter {r}] Verify FAILED (pixel) — reverted to good_sha {good_sha[:12]}"
                        )
                    elif verify_sec is None:
                        reset_hard(repo_root, good_sha)
                        apply_outcome = "reverted_no_timing"
                        print(f"[Iter {r}] Verify timing missing — reverted")
                    elif (
                        session_best_verify_sec is None
                        or verify_sec < session_best_verify_sec
                    ):
                        msg = (
                            f"autonomous iter {r}: kept after verify "
                            f"({verify_sec:.1f}s < best "
                            f"{session_best_verify_sec if session_best_verify_sec is not None else 'inf'})"
                        )
                        new_sha = commit_all(repo_root, msg)
                        session_best_verify_sec = verify_sec
                        rec["verify_gate"]["session_best_after_s"] = session_best_verify_sec
                        if new_sha:
                            good_sha = new_sha
                            apply_outcome = "kept"
                            _append_efficiency_note(
                                str(agent_info.get("run_id")),
                                plan,
                                verify_analyze,
                                verify_wall_s=verify_sec,
                                session_best_s=session_best_verify_sec,
                            )
                            print(
                                f"[Iter {r}] Verify OK + new session best "
                                f"{verify_sec:.1f}s — committed {good_sha[:12]}"
                            )
                        else:
                            apply_outcome = "kept_no_commit_diff"
                            print(f"[Iter {r}] Verify OK — no file changes to commit.")
                    else:
                        reset_hard(repo_root, good_sha)
                        apply_outcome = "reverted_not_session_best"
                        print(
                            f"[Iter {r}] Verify slower than session best "
                            f"({verify_sec:.1f}s >= {session_best_verify_sec:.1f}s) — reverted"
                        )

        rec["apply_outcome"] = apply_outcome
        rec["good_sha_after"] = good_sha
        rec["end_time"] = datetime.now().isoformat(timespec="seconds")

        if apply_plan_data is None and apply_plan_path and apply_plan_path.is_file():
            try:
                apply_plan_data = json.loads(apply_plan_path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                pass

        round_summary = build_iteration_summary(
            r,
            apply_outcome=apply_outcome,
            primary_run_id=agent_info.get("run_id"),
            primary_report=analyze_info.get("report"),
            verify_run_id=verify_run_id,
            verify_report=verify_report,
            apply_plan=apply_plan_data,
            cursor_apply_result=cursor_apply_result_data,
            good_sha_before=rec["good_sha_before"],
            good_sha_after=rec["good_sha_after"],
        )
        rec["round_summary"] = round_summary
        AUTONOMOUS_ROUNDS_DIR.mkdir(parents=True, exist_ok=True)
        round_path = AUTONOMOUS_ROUNDS_DIR / f"round_{r:03d}.json"
        round_path.write_text(
            json.dumps(round_summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        try:
            append_improvement_record(
                build_improvement_record(
                    rec,
                    round_summary=round_summary,
                    run_dir=run_dir,
                    primary_report=analyze_info.get("report"),
                    verify_report=verify_report,
                )
            )
        except Exception as e:  # noqa: BLE001
            print(f"[Iter {r}] improvement history append failed: {e}")

        _log_record(rec)

        # Also append summary line to train_loop_log for compatibility
        with LOOP_LOG.open("a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "autonomous_iteration": r,
                        "apply_outcome": apply_outcome,
                        "pre_pixel_ok": analyze_info.get("pixel_within_tolerance"),
                        "run_id": agent_info.get("run_id"),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

        print(
            f"[Iter {r}] done — outcome={apply_outcome}, "
            f"good_sha={good_sha[:12]}\n"
        )

    print(f"\n{'=' * 60}")
    print(f"Autonomous loop finished ({args.rounds} iterations)")
    print(f"Log: {AUTONOMOUS_LOG}")
    print(f"Round details: {AUTONOMOUS_ROUNDS_DIR}/")
    print(f"Final good SHA: {good_sha}")
    if working_tree_dirty(repo_root):
        print("Note: working tree has uncommitted changes.")
    print(f"{'=' * 60}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
