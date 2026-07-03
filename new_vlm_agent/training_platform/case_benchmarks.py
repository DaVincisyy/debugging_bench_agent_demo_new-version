"""Per-case golden pixels and artifact compare rules for the training platform."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from training_platform.run_artifacts import GOLDEN_COMPARE_FILES

_REPO = Path(__file__).resolve().parents[1]
GOLDEN_DIR = _REPO / "training_platform" / "golden"
CASE011_GOLDEN_PIXEL = [2038, 1114]
CASE002_GOLDEN_PIXEL = [357, 276]

_CASE_PATH_RE = re.compile(r"data[/\\]cases[/\\](case_[^/\\]+)", re.IGNORECASE)


@dataclass(frozen=True)
class CaseBenchmark:
    case_id: str
    golden_pixel: list[int]
    compare_files: tuple[str, ...]
    golden_dir: Path
    label: str
    enforce_pixel_golden: bool = True

    @property
    def uses_full_golden_artifacts(self) -> bool:
        return len(self.compare_files) > 1


def _profile(
    case_id: str,
    golden_pixel: list[int],
    compare_files: tuple[str, ...],
    label: str,
    *,
    enforce_pixel_golden: bool = True,
) -> CaseBenchmark:
    return CaseBenchmark(
        case_id=case_id,
        golden_pixel=golden_pixel,
        compare_files=compare_files,
        golden_dir=GOLDEN_DIR,
        label=label,
        enforce_pixel_golden=enforce_pixel_golden,
    )


# Order: specific cases first.
_PROFILES: tuple[CaseBenchmark, ...] = (
    _profile(
        "case_002",
        CASE002_GOLDEN_PIXEL,
        ("step08_result.json",),
        "case_002 Step3+ (pixel-only golden)",
    ),
    _profile(
        "case_011",
        CASE011_GOLDEN_PIXEL,
        GOLDEN_COMPARE_FILES,
        "case_011 full STANDARD_WORKFLOW",
    ),
)

_OTHER_PROFILE = _profile(
    "other",
    [0, 0],
    ("step08_result.json",),
    "其它 case：仅 deliverable 检查（无独立 golden 像素）",
    enforce_pixel_golden=False,
)

_DEFAULT_PROFILE = _PROFILES[-1]  # case_011 when case 无法识别（兼容旧 run）


def _match_profile(case_token: str) -> CaseBenchmark:
    token = case_token.lower().replace("\\", "/")
    if "case_002" in token:
        return _PROFILES[0]
    if "case_011" in token:
        return _PROFILES[1]
    return _OTHER_PROFILE


def _case_token_from_task_path(task_path: str | Path) -> Optional[str]:
    text = str(task_path).replace("\\", "/").lower()
    m = _CASE_PATH_RE.search(text)
    if m:
        return m.group(1).lower()
    for profile in _PROFILES:
        if profile.case_id in text:
            return profile.case_id
    return None


def _scan_run_dir_for_case_token(run_dir: Path) -> Optional[str]:
    if not run_dir.is_dir():
        return None
    for name in ("messages.init.jsonl", "messages.final.jsonl", "step_records.jsonl", "summary.json"):
        path = run_dir / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")[:500_000]
        except OSError:
            continue
        m = _CASE_PATH_RE.search(text)
        if m:
            return m.group(1).lower()
    summary = run_dir / "summary.json"
    if summary.is_file():
        try:
            data = json.loads(summary.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
        tp_id = str((data.get("final_answer") or {}).get("tp_id") or "")
        if tp_id.upper() == "TP332":
            return "case_002"
    return None


def resolve_case_benchmark(
    *,
    task_path: str | Path | None = None,
    run_dir: Path | None = None,
) -> CaseBenchmark:
    """Pick benchmark profile from explicit task path and/or run artifacts."""
    if task_path is not None:
        token = _case_token_from_task_path(task_path)
        if token:
            return _match_profile(token)
    if run_dir is not None:
        token = _scan_run_dir_for_case_token(run_dir)
        if token:
            return _match_profile(token)
    return _DEFAULT_PROFILE
