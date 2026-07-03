"""Project-wide logging setup (console + file).

Usage examples:
    from agent.logging_setup import setup_project_logging, get_logger

    setup_project_logging()  # call once at process startup
    log = get_logger(__name__)
    log.info("service started")

This module writes logs to:
    <debugging_bench_agent_demo>/run.log
in append mode (never overwrites existing logs).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

_LOG_INITIALIZED = False


def _detect_project_root() -> Path:
    """Best-effort project root detection for debugging_bench_agent_demo."""
    here = Path(__file__).resolve()
    for p in [here.parent, *here.parents]:
        if (p / "Inputdemo").exists() and (p / "Vlm agent").exists():
            return p
    return Path.cwd().resolve()


def get_run_log_path() -> Path:
    """Return `<project_root>/run.log`."""
    return _detect_project_root() / "run.log"


def setup_project_logging(level: int = logging.INFO) -> Path:
    """Configure root logger once with console + file handlers.

    - Millisecond precision timestamps
    - Level included in each line
    - File append mode
    """
    global _LOG_INITIALIZED
    log_path = get_run_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(level)

    formatter = logging.Formatter(
        fmt="%(asctime)s.%(msecs)03d [%(levelname)s] %(name)s (%(filename)s:%(lineno)d) - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(level)

    # Uvicorn may reconfigure logging and drop handlers; ensure handlers
    # exist every time setup is called (still avoiding duplicates).
    resolved_log = str(log_path.resolve()).lower()
    has_file = any(
        isinstance(h, logging.FileHandler)
        and str(getattr(h, "baseFilename", "")).lower() == resolved_log
        for h in root.handlers
    )
    has_console = any(
        isinstance(h, logging.StreamHandler)
        and not isinstance(h, logging.FileHandler)
        for h in root.handlers
    )

    if not has_file:
        root.addHandler(file_handler)
    if not has_console:
        root.addHandler(console_handler)

    if not _LOG_INITIALIZED:
        root.info("Logging initialized. run.log=%s pid=%s", str(log_path), os.getpid())
    _LOG_INITIALIZED = True
    return log_path


def get_logger(name: str) -> logging.Logger:
    """Get logger after ensuring project logging is configured."""
    setup_project_logging()
    return logging.getLogger(name)

