"""Cross-phase artifact handoff helpers."""

from __future__ import annotations

import re

_DEBUG_HANDOFF_RE = re.compile(
    r".*("
    r"_opencv_debug|_work_roi|_schematic_search|schematic_page|"
    r"_temp|scratch_|_debug"
    r")\.(png|jpg|jpeg|json)$",
    re.IGNORECASE,
)


def filter_handoff_artifact_paths(paths: list[str]) -> list[str]:
    """Drop debug/scratch intermediates from phase handoff (paths only, no file I/O)."""
    out: list[str] = []
    for rel in paths:
        norm = rel.replace("\\", "/")
        if _DEBUG_HANDOFF_RE.search(norm):
            continue
        out.append(norm)
    return out
