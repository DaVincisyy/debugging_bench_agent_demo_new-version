#!/usr/bin/env python3
"""
VLM Agent 训练平台 - 自动分析脚本

用法：
    python training_platform/auto_analyze.py

功能：
1. 自动找到 workspace/runs/ 里最新的 run
2. 对比 6 个 golden 文件，计算差异（带像素容差）
3. 生成结构化分析报告（analysis_reports/<run_id>.json）
4. 记录准确性趋势（连续失败计数）
5. 用户无需手动叫 Cursor Agent，跑完后执行本脚本即可

容差策略：
- 像素坐标：允许 ±TOLERANCE_PX（默认 40px），超出才算失败
- 多次失败才告警：连续 FAILURE_STREAK_THRESHOLD（默认 2）次超出容差才写入 needs_attention.json
"""

import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ==================== 配置区（可调） ====================
GOLDEN_DIR = Path("training_platform/golden")
RUNS_DIR = Path("workspace/runs")
REPORTS_DIR = Path("training_platform/analysis_reports")
ATTENTION_FILE = Path("training_platform/needs_attention.json")
TREND_FILE = Path("training_platform/accuracy_trend.json")

# 像素容差：只要 |dx| 和 |dy| 都 <= 此值，就认为准确性可接受
# 已调整为 10px（用户要求更严格）
TOLERANCE_PX = 10

# 连续失败阈值：连续多少次超出容差才真正告警
# 容差已收紧到 10px，阈值保持 2（连续 2 次才告警，避免单次抖动误判）
FAILURE_STREAK_THRESHOLD = 2

from training_platform.case_benchmarks import CaseBenchmark, resolve_case_benchmark
from training_platform.run_artifacts import GOLDEN_COMPARE_FILES

# 默认全量对比文件（case_011）；其它 case 见 case_benchmarks.py
GOLDEN_FILES = list(GOLDEN_COMPARE_FILES)

# 仅当 run 正常交付时才允许 pixel 判 pass（防止误用旧 step08）
_ACCEPTABLE_STOP_REASONS = frozenset(
    {"finish-tool-called", "max-steps-reached-forced-submit"}
)

# step08_result.json 里像素坐标的 key 路径
PIXEL_KEY_PATH = ["pixel"]


def find_latest_run() -> Optional[Path]:
    """找到 workspace/runs/ 里最新的 run 目录"""
    if not RUNS_DIR.exists():
        return None
    runs = [d for d in RUNS_DIR.iterdir() if d.is_dir() and d.name.endswith("-task")]
    if not runs:
        return None
    return max(runs, key=lambda d: d.stat().st_mtime)


def load_json_safe(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def get_nested(data: Dict[str, Any], keys: List[str]) -> Any:
    """安全获取嵌套字段"""
    cur = data
    for k in keys:
        if isinstance(cur, dict) and k in cur:
            cur = cur[k]
        else:
            return None
    return cur


def compare_pixel(golden: Dict[str, Any], actual: Dict[str, Any]) -> Dict[str, Any]:
    """对比 step08_result.json 的像素坐标，返回差异信息"""
    g_pixel = get_nested(golden, PIXEL_KEY_PATH)
    a_pixel = get_nested(actual, PIXEL_KEY_PATH)

    if g_pixel is None or a_pixel is None:
        return {"status": "missing_key", "golden": g_pixel, "actual": a_pixel}

    if not (isinstance(g_pixel, list) and isinstance(a_pixel, list) and len(g_pixel) == 2 and len(a_pixel) == 2):
        return {"status": "invalid_format", "golden": g_pixel, "actual": a_pixel}

    dx = a_pixel[0] - g_pixel[0]
    dy = a_pixel[1] - g_pixel[1]
    dist = (dx**2 + dy**2) ** 0.5
    within_tolerance = abs(dx) <= TOLERANCE_PX and abs(dy) <= TOLERANCE_PX

    return {
        "status": "ok",
        "golden": g_pixel,
        "actual": a_pixel,
        "dx": dx,
        "dy": dy,
        "distance": round(dist, 1),
        "within_tolerance": within_tolerance,
        "tolerance_px": TOLERANCE_PX,
    }


def compare_json_files(
    golden_path: Path,
    actual_path: Path,
    *,
    profile: CaseBenchmark | None = None,
) -> Dict[str, Any]:
    """通用 JSON 文件对比（结构 + 关键字段）"""
    a = load_json_safe(actual_path)
    if a is None:
        return {"status": "load_error", "golden_exists": golden_path.is_file(), "actual_exists": False}

    # 特殊处理 step08_result.json（可按 case 使用独立 golden 像素）
    if golden_path.name == "step08_result.json":
        if profile and not profile.enforce_pixel_golden:
            a_pixel = get_nested(a, PIXEL_KEY_PATH)
            if a_pixel is None:
                return {"status": "missing_key", "golden": None, "actual": a_pixel}
            return {
                "status": "ok",
                "golden": None,
                "actual": a_pixel,
                "within_tolerance": None,
                "skipped_golden_compare": True,
                "reason": profile.label,
            }
        if profile and not profile.uses_full_golden_artifacts:
            g = {"pixel": list(profile.golden_pixel)}
        else:
            g = load_json_safe(golden_path)
            if g is None:
                return {
                    "status": "load_error",
                    "golden_exists": False,
                    "actual_exists": True,
                }
        return compare_pixel(g, a)

    g = load_json_safe(golden_path)
    if g is None:
        return {"status": "load_error", "golden_exists": False, "actual_exists": True}

    # 其他 JSON 只做存在性 + 大小检查（更复杂的结构对比可后续扩展）
    return {
        "status": "ok",
        "golden_keys": list(g.keys()) if isinstance(g, dict) else "not_dict",
        "actual_keys": list(a.keys()) if isinstance(a, dict) else "not_dict",
        "size_match": len(json.dumps(g)) == len(json.dumps(a)),
    }


def compare_image_files(golden_path: Path, actual_path: Path) -> Dict[str, Any]:
    """图片文件只对比存在性和文件大小（像素级内容对比成本高，先做基础检查）"""
    if not golden_path.exists() or not actual_path.exists():
        return {"status": "missing", "golden_exists": golden_path.exists(), "actual_exists": actual_path.exists()}
    return {
        "status": "ok",
        "golden_size": golden_path.stat().st_size,
        "actual_size": actual_path.stat().st_size,
        "size_match": golden_path.stat().st_size == actual_path.stat().st_size,
    }


def _load_run_summary(run_dir: Path) -> Optional[Dict[str, Any]]:
    summary_path = run_dir / "summary.json"
    if not summary_path.is_file():
        return None
    return load_json_safe(summary_path)


def _check_run_deliverable(
    run_dir: Path,
    pixel_result: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Ensure step08 / pixel came from this run, not a previous workspace state."""
    summary = _load_run_summary(run_dir)
    stopped = (summary or {}).get("stopped_reason")
    final_answer = (summary or {}).get("final_answer") if summary else None
    step08_path = run_dir / "debug" / "step08_result.json"

    if summary is None:
        return {
            "valid": False,
            "reason": "summary.json missing",
            "stopped_reason": stopped,
        }
    if not step08_path.is_file():
        return {
            "valid": False,
            "reason": "step08_result.json not produced in this run",
            "stopped_reason": stopped,
        }
    if stopped not in _ACCEPTABLE_STOP_REASONS:
        return {
            "valid": False,
            "reason": f"run stopped early ({stopped})",
            "stopped_reason": stopped,
        }
    if not isinstance(final_answer, dict) or not final_answer.get("pixel"):
        return {
            "valid": False,
            "reason": "final_answer.pixel missing in summary.json",
            "stopped_reason": stopped,
        }
    if pixel_result and pixel_result.get("status") == "ok":
        fa_pixel = final_answer.get("pixel")
        actual_pixel = pixel_result.get("actual")
        if (
            isinstance(fa_pixel, list)
            and isinstance(actual_pixel, list)
            and len(fa_pixel) == 2
            and len(actual_pixel) == 2
            and (fa_pixel[0] != actual_pixel[0] or fa_pixel[1] != actual_pixel[1])
        ):
            return {
                "valid": False,
                "reason": "summary.final_answer.pixel disagrees with step08_result.json",
                "stopped_reason": stopped,
                "final_answer_pixel": fa_pixel,
                "step08_pixel": actual_pixel,
            }
    return {
        "valid": True,
        "reason": "ok",
        "stopped_reason": stopped,
    }


def analyze_run(
    run_dir: Path,
    *,
    task_path: str | None = None,
) -> Dict[str, Any]:
    """对单个 run 做完整分析（只读 runs/<run_id>/debug，禁止回退 workspace/debug）"""
    profile = resolve_case_benchmark(task_path=task_path, run_dir=run_dir)
    run_debug = run_dir / "debug"

    results = []
    pixel_result = None

    for fname in profile.compare_files:
        golden_path = profile.golden_dir / fname
        actual_path = run_debug / fname

        if not actual_path.is_file():
            results.append({"file": fname, "status": "actual_missing"})
            continue

        if fname.endswith(".json"):
            cmp = compare_json_files(golden_path, actual_path, profile=profile)
        else:
            cmp = compare_image_files(golden_path, actual_path)

        cmp["file"] = fname
        results.append(cmp)

        if fname == "step08_result.json" and cmp.get("status") == "ok":
            pixel_result = cmp

    deliverable = _check_run_deliverable(run_dir, pixel_result)
    if profile.enforce_pixel_golden:
        pixel_within = (
            bool(pixel_result["within_tolerance"])
            if pixel_result and deliverable.get("valid")
            else False
        )
    else:
        pixel_within = bool(deliverable.get("valid"))
    overall_pass = all(
        r.get("status") == "ok"
        and (r.get("within_tolerance", True) if "within_tolerance" in r else True)
        for r in results
    ) and deliverable.get("valid")

    return {
        "run_id": run_dir.name,
        "timestamp": datetime.now().isoformat(),
        "case_id": profile.case_id,
        "benchmark_label": profile.label,
        "golden_pixel": list(profile.golden_pixel),
        "tolerance_px": TOLERANCE_PX,
        "deliverable_check": deliverable,
        "pixel_comparison": pixel_result,
        "file_comparisons": results,
        "overall_pass_within_tolerance": overall_pass,
        "pixel_within_tolerance": pixel_within,
    }


def update_trend(run_id: str, passed: bool) -> Dict[str, Any]:
    """更新连续失败计数"""
    trend = {"streak": 0, "history": []}
    if TREND_FILE.exists():
        try:
            trend = json.loads(TREND_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass

    trend["history"].append({"run_id": run_id, "passed": passed, "ts": datetime.now().isoformat()})
    # 只保留最近 20 条
    trend["history"] = trend["history"][-20:]

    if passed:
        trend["streak"] = 0
    else:
        trend["streak"] = trend.get("streak", 0) + 1

    TREND_FILE.write_text(json.dumps(trend, indent=2, ensure_ascii=False), encoding="utf-8")
    return trend


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Analyze agent run vs golden files")
    parser.add_argument(
        "--run-id",
        default=None,
        help="Specific run id under workspace/runs/ (default: latest by mtime)",
    )
    parser.add_argument(
        "--task",
        default=None,
        help="Task yaml path used for this run (selects per-case golden pixel)",
    )
    args = parser.parse_args()

    print("[auto_analyze] 开始自动分析 run ...")
    if args.run_id:
        latest = RUNS_DIR / args.run_id
        if not latest.is_dir():
            print(f"[auto_analyze] run 目录不存在: {latest}")
            sys.exit(1)
    else:
        latest = find_latest_run()
    if latest is None:
        print("[auto_analyze] 未找到任何 run 目录，退出。")
        sys.exit(1)

    print(f"[auto_analyze] run: {latest.name}")
    report = analyze_run(latest, task_path=args.task)
    print(
        f"[auto_analyze] case={report.get('case_id')} "
        f"golden={report.get('golden_pixel')} ±{TOLERANCE_PX}px"
    )

    # 写报告
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORTS_DIR / f"{latest.name}.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[auto_analyze] 分析报告已生成: {report_path}")

    # 更新趋势
    passed = report.get("pixel_within_tolerance", False)
    deliverable = report.get("deliverable_check") or {}
    if not deliverable.get("valid"):
        print(
            "[auto_analyze] deliverable_check FAILED: "
            f"{deliverable.get('reason')} (stopped={deliverable.get('stopped_reason')})"
        )
    trend = update_trend(latest.name, passed)
    print(f"[auto_analyze] 当前连续失败 streak = {trend['streak']}")

    # 判断是否需要人工关注
    if trend["streak"] >= FAILURE_STREAK_THRESHOLD and not passed:
        attention = {
            "last_updated": datetime.now().isoformat(),
            "streak": trend["streak"],
            "threshold": FAILURE_STREAK_THRESHOLD,
            "latest_run": latest.name,
            "message": f"连续 {trend['streak']} 次超出像素容差（{TOLERANCE_PX}px），建议 Cursor Agent 重点分析。",
        }
        ATTENTION_FILE.write_text(json.dumps(attention, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[auto_analyze] ⚠️  已写入 needs_attention.json（连续失败 {trend['streak']} 次）")
    else:
        if ATTENTION_FILE.exists():
            ATTENTION_FILE.unlink()
        print("[auto_analyze] 本次通过容差检查，无需告警。")

    print("[auto_analyze] 完成。Cursor Agent 会定期查看 reports/ 并提出改进建议。")


if __name__ == "__main__":
    main()
