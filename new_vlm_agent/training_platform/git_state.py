#!/usr/bin/env python3
"""Minimal git helpers for autonomous_loop revert/keep."""

from __future__ import annotations

import subprocess
from pathlib import Path


def _run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def ensure_git_repo(repo_root: Path) -> None:
    r = _run_git(["rev-parse", "--is-inside-work-tree"], repo_root)
    if r.returncode != 0 or r.stdout.strip() != "true":
        raise RuntimeError(f"Not a git repository: {repo_root}")


def resolve_ref(repo_root: Path, ref: str) -> str:
    r = _run_git(["rev-parse", ref], repo_root)
    if r.returncode != 0:
        raise RuntimeError(f"Cannot resolve git ref {ref!r}: {r.stderr.strip()}")
    return r.stdout.strip()


def reset_hard(repo_root: Path, sha: str) -> None:
    r = _run_git(["reset", "--hard", sha], repo_root)
    if r.returncode != 0:
        raise RuntimeError(f"git reset --hard failed: {r.stderr.strip()}")


def commit_all(repo_root: Path, message: str) -> str | None:
    _run_git(["add", "-A"], repo_root)
    diff = _run_git(["diff", "--cached", "--quiet"], repo_root)
    if diff.returncode == 0:
        return None
    r = _run_git(["commit", "-m", message], repo_root)
    if r.returncode != 0:
        raise RuntimeError(f"git commit failed: {r.stderr.strip()}")
    return resolve_ref(repo_root, "HEAD")


def working_tree_dirty(repo_root: Path) -> bool:
    r = _run_git(["status", "--porcelain"], repo_root)
    return bool(r.stdout.strip())
