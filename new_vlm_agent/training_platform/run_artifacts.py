"""Isolate per-run debug artifacts so auto_analyze never reads stale files."""

from __future__ import annotations

import shutil
from pathlib import Path

WORKSPACE_DEBUG = Path("workspace/debug")

# Files compared against golden in auto_analyze.py (keep in sync).
GOLDEN_COMPARE_FILES = (
    "case10_signal_to_tp.json",
    "case10_assembly_vlm_hints.json",
    "case10_assembly_largest_ic_box.png",
    "case10_vlm_hints.json",
    "case10_largest_ic_box.png",
    "step08_result.json",
)


def prepare_fresh_workspace_debug() -> None:
    """Remove leftover debug outputs before a new agent run."""
    if WORKSPACE_DEBUG.exists():
        shutil.rmtree(WORKSPACE_DEBUG)
    WORKSPACE_DEBUG.mkdir(parents=True, exist_ok=True)


def snapshot_workspace_debug_to_run(run_dir: Path) -> Path:
    """Copy workspace/debug into runs/<run_id>/debug for this run only."""
    dest = run_dir / "debug"
    if dest.exists():
        shutil.rmtree(dest)
    if WORKSPACE_DEBUG.exists():
        shutil.copytree(WORKSPACE_DEBUG, dest)
    else:
        dest.mkdir(parents=True, exist_ok=True)
    return dest
