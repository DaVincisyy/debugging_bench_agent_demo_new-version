"""Built-in tools available to the VLM agent.

These are deliberately small, composable primitives. The VLM drives the
workflow by chaining them, which mirrors how a human would debug a
locator-to-camera-pixel mapping:

    list_files → read_text_file → view_image → crop_image → run_python →
    annotate_image → view_image → finish

The tools are sandboxed inside `workspace_dir` for any path they WRITE.
Reads are allowed from anywhere so that input files (schematics, locator
images, camera photos) can live outside the workspace.
"""

from __future__ import annotations

import io
import json
import math
import os
import re
import shlex
import shutil
import subprocess
import sys
import textwrap
import threading
import time
import builtins
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .tools import Tool, ToolRegistry, ToolResult
from .utils import pdf_raster_max_pixels, truncate, write_debug_image


# --------------------------------------------------------------------------- #
# Path helpers
# --------------------------------------------------------------------------- #

_RUNTIME_PROJECT_ROOT = Path.cwd().resolve()
_RUNTIME_WORKSPACE = (_RUNTIME_PROJECT_ROOT / "workspace").resolve()
_RUNTIME_INPUT_PATHS: dict[str, str] = {}
_RUNTIME_WORKFLOW_MODE: str = "default"
_RUNTIME_RUN_STARTED_AT: float = 0.0

# Thread-local storage so concurrent child runs don't cross-contaminate
# each other's workspace / project-root resolution via _resolve_read.
_tls = threading.local()

def _tl_workspace() -> Path:
    return getattr(_tls, "workspace", None) or _RUNTIME_WORKSPACE

def _tl_project_root() -> Path:
    return getattr(_tls, "project_root", None) or _RUNTIME_PROJECT_ROOT

def _tl_input_paths() -> dict[str, str]:
    return getattr(_tls, "input_paths", None) or _RUNTIME_INPUT_PATHS

def _tl_workflow_mode() -> str:
    return getattr(_tls, "workflow_mode", None) or _RUNTIME_WORKFLOW_MODE

def _tl_run_started_at() -> float:
    return getattr(_tls, "run_started_at", _RUNTIME_RUN_STARTED_AT)


# Part B StepB3 — prove `view_image` ran on the current on-disk red-box PNG
# after the last `annotate_image` (QC_REVISE must re-annotate then re-view).
_STEPSB3_VIEW_GATE_REL = Path("debug") / "case10_stepb3_viewed_largest_ic_box.json"


def _is_stepb3_assembly_largest_ic_box_png(p: Path) -> bool:
    return p.name.lower() == "case10_assembly_largest_ic_box.png"


def _stepb3_view_gate_file(workspace: Path) -> Path:
    return (workspace.resolve() / _STEPSB3_VIEW_GATE_REL).resolve()


def _stepb3_invalidate_view_gate(workspace: Path) -> None:
    gate = _stepb3_view_gate_file(workspace)
    try:
        if gate.is_file():
            gate.unlink()
    except OSError:
        pass


def _stepb3_record_view_gate(workspace: Path, png_resolved: Path) -> None:
    gate = _stepb3_view_gate_file(workspace)
    gate.parent.mkdir(parents=True, exist_ok=True)
    mtime = png_resolved.stat().st_mtime
    gate.write_text(
        json.dumps(
            {
                "png_mtime": mtime,
                "png_path": str(png_resolved).replace("\\", "/"),
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def set_runtime_context(project_root: Path,
                        workspace: Path,
                        input_paths: dict[str, str] | None = None,
                        workflow_mode: str | None = None,
                        run_started_at: float | None = None) -> None:
    """Called by Agent at run start so tools share the same path context."""
    # Write both global defaults AND thread-local overrides.
    # Global defaults keep single-run / CLI paths working; thread-local
    # overrides prevent concurrent child-agent runs from stepping on each
    # other's workspace resolution through _resolve_read.
    global _RUNTIME_PROJECT_ROOT, _RUNTIME_WORKSPACE, _RUNTIME_INPUT_PATHS
    global _RUNTIME_WORKFLOW_MODE, _RUNTIME_RUN_STARTED_AT
    _RUNTIME_PROJECT_ROOT = project_root.resolve()
    _RUNTIME_WORKSPACE = workspace.resolve()
    _RUNTIME_INPUT_PATHS = dict(input_paths or {})
    _RUNTIME_WORKFLOW_MODE = (workflow_mode or "default").strip() or "default"
    _RUNTIME_RUN_STARTED_AT = float(run_started_at or 0.0)
    # Thread-local overrides — takes priority in _tl_* helpers
    _tls.project_root = _RUNTIME_PROJECT_ROOT
    _tls.workspace = _RUNTIME_WORKSPACE
    _tls.input_paths = _RUNTIME_INPUT_PATHS
    _tls.workflow_mode = _RUNTIME_WORKFLOW_MODE
    _tls.run_started_at = _RUNTIME_RUN_STARTED_AT


def _is_stale_runtime_debug_file(p: Path) -> bool:
    if _tl_run_started_at() <= 0:
        return False
    try:
        rp = p.resolve()
    except Exception:
        return False
    try:
        rel = rp.relative_to(_tl_workspace())
    except Exception:
        return False
    parts = [x.lower() for x in rel.parts]
    if "debug" not in parts:
        return False
    if rp.is_dir():
        return False
    try:
        st = rp.stat()
        latest_fs_ts = max(float(st.st_mtime), float(st.st_ctime))
        return latest_fs_ts + 1e-3 < _tl_run_started_at()
    except OSError:
        return False


def _is_vlm_test_workflow_mode() -> bool:
    return _tl_workflow_mode().strip() == "vlm_test"


def _annotate_source_smells_physical_board_workflow(norm_lower: str) -> bool:
    """Workspace-relative paths tied to normalized **physical** board raster (Part A baseline)."""
    return (
        "case10_board_landscape" in norm_lower
        or "step02_board_front_anchor" in norm_lower
    )


def _python_snippet_hints_physical_board_raster(norm_lower_snippet: str) -> bool:
    """Heuristic: snippet references normalized physical-board files or front camera path keys."""
    return bool(
        re.search(
            r"case10_board_landscape|step02_board_front_anchor|front_board_photo",
            norm_lower_snippet,
            flags=re.I,
        )
        or (
            ("input_paths" in norm_lower_snippet or "INPUT_PATHS" in norm_lower_snippet)
            and "front_board" in norm_lower_snippet
        )
    )


def _vlm_test_run_python_guard_physical_board_ic_geometry(code: str) -> ToolResult | None:
    """Forbid CV-based IC localization overlays on physical-board anchors (vlm_test)."""
    if not _is_vlm_test_workflow_mode():
        return None
    compact = " ".join(code.replace("\\", "/").lower().split())
    if not _python_snippet_hints_physical_board_raster(compact):
        return None

    reasons: list[str] = []

    # Drawing chip-scale boxes onto the cleaned board photo defeats VLM correspondence.
    if re.search(r"\bcv2\s*\.\s*rectangle\s*\(", compact):
        reasons.append("`cv2.rectangle`")

    contourish = (
        r"\bcv2\s*\.\s*(?:findcontours|connectedcomponents\b|connectedcomponentswithstats|canny\b|"
        r"watershed\b|grabcut\b)"
    )
    if re.search(contourish, compact):
        reasons.append("`cv2` contour/segmentation (`findContours`/connectedComponents/Canny/...)")

    if re.search(r"\bcv2\s*\.\s*inrange\s*\(", compact):
        reasons.append("`cv2.inRange`")

    if re.search(
        r"cvtcolor\s*\([^)]*color_bgr2hsv|\bcolor_bgr2hsv\b|\bcolour_bgr2hsv\b|\bBGR2HSV\b",
        compact,
        flags=re.I,
    ):
        reasons.append("HSV color conversion targeting masks")

    if re.search(
        r"\bcv2\s*\.\s*(?:boundingrect|minarearect|moments|contourarea|arclength|"
        r"minenclosingcircle|fitellipse|approxpolydp)\s*\(",
        compact,
    ):
        reasons.append("`cv2` contour metrics (`boundingRect`/`moments`/...)")

    if re.search(r"\bdraw\s*\.\s*rectangle\s*\(", compact):
        reasons.append("PIL `.draw.rectangle` chip overlays")

    if not reasons:
        return None

    joined = "; ".join(sorted(set(reasons)))
    return ToolResult(
        text=(
            "[vlm_test-guard] Do **not** use classical CV (or scripted box drawing on pixels) "
            "on **`case10_board_landscape`** / **`step02_board_front_anchor`** / **`front_board_photo`** "
            f"to **localize or mark the IC** before Part D correspondence. Blocked for {joined}. "
            "Instead: **`view_image`** → **`save_text_file` → `debug/case12_board_largest_ic_bbox_vlm.json`** "
            "→ **`run_align_locator_graph_to_board_ic_bbox_vlm`** "
            "(numbers must come from **multimodal visual reasoning**, not OpenCV)."
        ),
        ok=False,
    )


def _resolve_read(path: str) -> Path:
    p = Path(path).expanduser()
    candidates: list[Path] = []
    stale_candidates: list[Path] = []

    if p.is_absolute():
        candidates.append(p)
    else:
        # Treat plain input key names as indirection to INPUT_PATHS.
        key = str(p)
        mapped = _tl_input_paths().get(key)
        if isinstance(mapped, str) and mapped.strip():
            mp = Path(mapped).expanduser()
            candidates.append(mp)
        # Accept model literals like INPUT_PATHS.front_board_photo or
        # INPUT_PATHS['front_board_photo'] as input-path indirection.
        key_txt = key.replace("\\", "/").strip()
        dotted_key = None
        m = re.fullmatch(r"(?i)input_paths\.([A-Za-z0-9_-]+)", key_txt)
        if m:
            dotted_key = m.group(1)
        else:
            m2 = re.fullmatch(r"(?i)input_paths\[['\"]([^'\"]+)['\"]\]", key_txt)
            if m2:
                dotted_key = m2.group(1)
        if dotted_key:
            mv2 = _tl_input_paths().get(dotted_key)
            if isinstance(mv2, str) and mv2.strip():
                candidates.append(Path(mv2).expanduser())
        # Map common mistaken forms like inputs/front_board_photo.png -> INPUT_PATHS['front_board_photo'].
        norm = str(p).replace("\\", "/")
        low = norm.lower()
        if low.startswith("inputs/") or low.startswith("input/"):
            leaf = Path(norm).name
            stem = Path(leaf).stem
            for k in (leaf, stem):
                mv = _tl_input_paths().get(k)
                if isinstance(mv, str) and mv.strip():
                    candidates.append(Path(mv).expanduser())
            # Fuzzy contains match by stem.
            if stem:
                for k, v in _tl_input_paths().items():
                    if not isinstance(v, str):
                        continue
                    if stem.lower() in str(k).lower():
                        candidates.append(Path(v).expanduser())
        # 1) Relative to workspace (tools write here; always check first)
        candidates.append(_tl_workspace() / p)
        # 2) As provided (relative to process cwd, usually project root)
        candidates.append((Path.cwd() / p))
        # 3) Common model mistake: prefixes with workspace/
        text = str(p).replace("\\", "/")
        if text.startswith("workspace/"):
            stripped = Path(text[len("workspace/"):])
            candidates.append(_tl_workspace() / stripped)
            candidates.append(Path.cwd() / stripped)
        # 4) Relative to project root explicitly
        candidates.append(_tl_project_root() / p)

    seen: set[str] = set()
    for c in candidates:
        cr = c.resolve()
        key = str(cr).lower()
        if key in seen:
            continue
        seen.add(key)
        if cr.exists():
            if _is_stale_runtime_debug_file(cr):
                stale_candidates.append(cr)
                continue
            return cr

    if stale_candidates:
        raise FileNotFoundError(
            "Refusing to read stale debug artifact from previous runs. "
            "Regenerate it in current run. Stale path examples: "
            + ", ".join(str(c) for c in stale_candidates[:3])
        )

    raise FileNotFoundError(
        f"File does not exist: {p}. Tried: " + ", ".join(str(c) for c in candidates[:6])
    )


def _normalize_runtime_path(path_like: Any) -> str:
    """Best-effort normalize of model-generated path strings."""
    if not isinstance(path_like, (str, os.PathLike)):
        return str(path_like)
    p = Path(path_like).expanduser()
    if p.is_absolute():
        return str(p)
    raw = str(p).replace("\\", "/")
    if raw.startswith("workspace/"):
        raw = raw[len("workspace/"):]
        return str((_tl_workspace() / raw).resolve())
    # Keep relative as-is; reader wrappers will try multiple roots.
    return str(p)


@contextmanager
def _fitz_render_prefs(
    graphics_min_line_width: float | None,
    aa_level: int | None,
):
    """Temporarily tweak MuPDF vector rasterization (e.g. faint colored silkscreen lines)."""
    try:
        import fitz  # type: ignore
    except Exception:
        yield
        return
    prev = fitz.TOOLS.show_aa_level()
    try:
        if graphics_min_line_width is not None:
            fitz.TOOLS.set_graphics_min_line_width(float(graphics_min_line_width))
        if aa_level is not None:
            fitz.TOOLS.set_aa_level(int(aa_level))
        yield
    finally:
        try:
            fitz.TOOLS.set_aa_level(int(prev["graphics"]))
            fitz.TOOLS.set_graphics_min_line_width(
                float(prev["graphics_min_line_width"]))
        except Exception:
            pass


def _resolve_write(workspace: Path, path: str) -> Path:
    """Writes must land inside the workspace (including subdirs)."""
    raw = str(path).replace("\\", "/")
    # Models often prepend "workspace/" although the tool already writes
    # relative paths under workspace. Normalize to avoid workspace/workspace/*.
    if raw.startswith("workspace/"):
        raw = raw[len("workspace/"):]
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = workspace / p
    p = p.resolve()
    ws = workspace.resolve()
    if ws not in p.parents and p != ws:
        raise PermissionError(
            f"Refusing to write outside workspace: {p} (workspace={ws})"
        )
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _step03_mapping_obj(workspace: Path) -> dict[str, Any] | None:
    p = workspace / "debug" / "step03_mapping.json"
    if not p.is_file():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else None
    except Exception:
        return None


def _is_vlm_neighborhood_mapping(m: dict[str, Any] | None) -> bool:
    if not m:
        return False
    return m.get("mapping_method") == "vlm_neighborhood_layout_match"


def _check_step3b_written(workspace: Path) -> ToolResult | None:
    """Step3B ``progress/step_03.md`` gate is disabled; use ``debug/`` artifacts + finish contract."""
    return None


def _require_step3b_vlm_or_locator_roi(workspace: Path, *, early_board_roi: bool) -> ToolResult | None:
    """Gate follow-up steps until Step3B exists when VLM layout mapping or locator ROI flow applies."""
    m = _step03_mapping_obj(workspace)
    loc_roi = (workspace / "debug" / "step03_locator_roi.png").is_file()
    if early_board_roi:
        if loc_roi:
            return _check_step3b_written(workspace)
        return None
    if _is_vlm_neighborhood_mapping(m):
        return _check_step3b_written(workspace)
    if m is None and loc_roi:
        return _check_step3b_written(workspace)
    return None


def _check_step4_locator_landmarks_present(workspace: Path) -> ToolResult | None:
    """VLM prior: require Step4 locator landmark overlay before writing mapping JSON."""
    p = workspace / "debug" / "step04_locator_landmarks.png"
    if not p.is_file():
        return ToolResult(
            text=(
                "[step4-gate] Missing `debug/step04_locator_landmarks.png`. "
                "For `vlm_neighborhood_layout_match`: run Step4 — `annotate_image` on "
                "`debug/step03_locator_roi.png` with at least one red `{bbox:[x1,y1,x2,y2]}` "
                "around signature parts near the green TP, "
                "`out_path` = `debug/step04_locator_landmarks.png`, **before** `step03_mapping.json`."
            ),
            ok=False,
        )
    if p.stat().st_size < 400:
        return ToolResult(
            text="[step4-gate] `debug/step04_locator_landmarks.png` is too small or invalid.",
            ok=False,
        )
    roi = workspace / "debug" / "step03_locator_roi.png"
    if roi.is_file() and p.stat().st_mtime + 1e-3 < roi.stat().st_mtime:
        return ToolResult(
            text=(
                "[step4-gate] Regenerate `debug/step04_locator_landmarks.png` after "
                "`debug/step03_locator_roi.png` (landmarks file must not be stale)."
            ),
            ok=False,
        )
    return None


def _require_before_board_step04_roi_crop(workspace: Path) -> ToolResult | None:
    """Board ROI for Step5+ pipeline: Step3B done and Step3 mapping on disk when locator ROI flow."""
    blocked = _require_step3b_vlm_or_locator_roi(workspace, early_board_roi=False)
    if blocked is not None:
        return blocked
    if (workspace / "debug" / "step03_locator_roi.png").is_file():
        if _step03_mapping_obj(workspace) is None:
            return ToolResult(
                text=(
                    "[step4-board-crop-gate] Write `debug/step03_mapping.json` (full-board prior) "
                    "before cropping `debug/step04_roi_crop.png` from the board photo."
                ),
                ok=False,
            )
    return None


# --------------------------------------------------------------------------- #
# File tools
# --------------------------------------------------------------------------- #

def _tool_list_files(path: str = ".", max_entries: int = 200) -> ToolResult:
    p = _resolve_read(path)
    if p.is_file():
        return ToolResult(text=f"{p} (file, {p.stat().st_size} bytes)")
    entries = []
    for i, child in enumerate(sorted(p.iterdir())):
        if i >= max_entries:
            entries.append(f"... ({max_entries}+ entries, truncated)")
            break
        tag = "DIR " if child.is_dir() else "FILE"
        size = "" if child.is_dir() else f" {child.stat().st_size}B"
        entries.append(f"{tag} {child.name}{size}")
    body = "\n".join(entries) if entries else "(empty)"
    return ToolResult(text=f"Listing {p}:\n{body}")


def _tool_read_text_file(path: str, start_line: int = 1,
                         end_line: int | None = None,
                         max_chars: int = 6000) -> ToolResult:
    p = _resolve_read(path)
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception as e:  # noqa: BLE001
        return ToolResult(text=f"[read-error] {e}", ok=False)
    lines = text.splitlines()
    s = max(1, int(start_line)) - 1
    e = len(lines) if end_line is None else min(len(lines), int(end_line))
    snippet = "\n".join(f"{i+1:>5}│ {ln}" for i, ln in enumerate(lines[s:e], start=s))
    return ToolResult(
        text=f"{p} lines {s+1}..{e} of {len(lines)}:\n{truncate(snippet, max_chars)}"
    )


def _pdf_search_query_variants(query: str, case_sensitive: bool) -> list[str]:
    """Literal needles for MuPDF search (case-insensitive → try several spellings)."""
    q = query.strip()
    if not q:
        return []
    if case_sensitive:
        return [q]
    variants: set[str] = {q, q.lower(), q.upper()}
    if len(q) > 1:
        variants.add(q[0].upper() + q[1:].lower())
    return [v for v in variants if v]


def _pdf_rect_dedupe_key(rect: Any, tol: float = 0.35) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1)
    return (
        round(x0 / tol) * tol,
        round(y0 / tol) * tol,
        round(x1 / tol) * tol,
        round(y1 / tol) * tol,
    )


_TP_LABEL_QUERY_RE = re.compile(r"^TP\d+$", re.IGNORECASE)


def _tp_label_pdf_hit_is_exact_token(page: Any, rect: Any, query_tp: str) -> bool:
    """Drop PyMuPDF substring hits: ``TP1`` must not match the ``TP1`` inside ``TP105``.

    If the clip yields no ``TP\\d+`` tokens (weird spans), keep the hit.
    """
    import fitz  # type: ignore

    want = query_tp.strip().upper()
    exp = fitz.Rect(rect)
    span_w = max(3.0, (float(rect.x1) - float(rect.x0)) * 0.65)
    span_h = max(3.0, (float(rect.y1) - float(rect.y0)) * 0.65)
    exp.x0 -= span_w
    exp.y0 -= span_h
    exp.x1 += span_w
    exp.y1 += span_h
    exp &= page.rect
    try:
        text = page.get_text("text", clip=exp) or ""
    except Exception:
        text = ""
    tokens = re.findall(r"TP\d+", text, flags=re.IGNORECASE)
    if not tokens:
        return True
    return want in {t.upper() for t in tokens}


def _tool_search_pdf_text(workspace: Path,
                          pdf_path: str,
                          query: str,
                          case_sensitive: bool = False,
                          max_results: int = 20,
                          context_chars: int = 80,
                          out_json_path: str = "",
                          page_filter: list[int] | None = None,
                          page_filter_reason: str = "") -> ToolResult:
    """Search text in a PDF and return hit pages, snippets, and bbox per hit (PDF points).

    Coordinates are PyMuPDF page space: origin top-left, x right, y down, unit = pt.
    To map a rect to pixels from `pdf_page_to_image` at ``dpi``, use factor ``dpi / 72``.
    """
    src = _resolve_read(pdf_path)
    if src.suffix.lower() != ".pdf":
        return ToolResult(
            text=f"[pdf-search-error] input is not a PDF: {src}",
            ok=False,
        )
    query = str(query or "").strip()
    if not query:
        # Delete stale search JSON so downstream mark_tp doesn't consume bad data
        if out_json_path:
            try:
                p = _resolve_write(workspace, str(out_json_path))
                if p.exists():
                    p.unlink()
            except Exception:
                pass
        return ToolResult(
            text="[pdf-search-error] query must be a non-empty string. Old search file deleted; retry with a valid TP label.",
            ok=False,
        )
    try:
        max_results = max(1, int(max_results))
    except (TypeError, ValueError):
        max_results = 20
    try:
        context_chars = max(10, int(context_chars))
    except (TypeError, ValueError):
        context_chars = 80
    normalized_page_filter: list[int] = []
    if page_filter is not None:
        try:
            normalized_page_filter = sorted({
                int(page) for page in page_filter if int(page) >= 1
            })
        except (TypeError, ValueError):
            return ToolResult(
                text="[pdf-search-error] page_filter must contain positive 1-based page numbers.",
                ok=False,
            )
        if not normalized_page_filter:
            return ToolResult(
                text="[pdf-search-error] page_filter was provided but contains no valid pages.",
                ok=False,
            )

    try:
        import fitz  # type: ignore
    except Exception as e:  # noqa: BLE001
        return ToolResult(
            text=f"[pdf-search-error] PyMuPDF (fitz) unavailable: {e}",
            ok=False,
        )

    needles = _pdf_search_query_variants(query, case_sensitive)
    if not needles:
        if out_json_path:
            try:
                p = _resolve_write(workspace, str(out_json_path))
                if p.exists():
                    p.unlink()
            except Exception:
                pass
        return ToolResult(
            text="[pdf-search-error] query must be a non-empty string. Old search file deleted; retry with a valid TP label.",
            ok=False,
        )

    hits: list[dict[str, Any]] = []
    hit_pages: list[int] = []
    margin_pt = max(72.0, min(144.0, float(context_chars) * 0.9))

    try:
        doc = fitz.open(str(src))
        for i in range(doc.page_count):
            if len(hits) >= max_results:
                break
            page = doc.load_page(i)
            page_no = i + 1
            if normalized_page_filter and page_no not in normalized_page_filter:
                continue
            seen_keys: set[tuple[float, float, float, float]] = set()
            page_rects: list[tuple[Any, str]] = []

            for needle in needles:
                if len(hits) + len(page_rects) >= max_results:
                    break
                try:
                    found = page.search_for(needle, quads=False)
                except Exception:
                    found = []
                for rect in found:
                    if len(hits) + len(page_rects) >= max_results:
                        break
                    if _TP_LABEL_QUERY_RE.match(query.strip()) and not _tp_label_pdf_hit_is_exact_token(
                        page, rect, query.strip()
                    ):
                        continue
                    key = _pdf_rect_dedupe_key(rect)
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    page_rects.append((rect, needle))

            for rect, needle in page_rects:
                if len(hits) >= max_results:
                    break
                exp = fitz.Rect(rect)
                exp.x0 -= margin_pt
                exp.y0 -= margin_pt
                exp.x1 += margin_pt
                exp.y1 += margin_pt
                exp &= page.rect
                try:
                    snippet_raw = page.get_text("text", clip=exp) or ""
                except Exception:
                    snippet_raw = ""
                snippet = truncate(
                    snippet_raw.replace("\n", " ").strip(),
                    max(180, context_chars * 3),
                )
                hits.append({
                    "page": page_no,
                    "match": needle,
                    "rect_pdf": [
                        round(float(rect.x0), 4),
                        round(float(rect.y0), 4),
                        round(float(rect.x1), 4),
                        round(float(rect.y1), 4),
                    ],
                    "snippet": snippet,
                })
                if page_no not in hit_pages:
                    hit_pages.append(page_no)
        doc.close()
    except Exception as e:  # noqa: BLE001
        return ToolResult(
            text=f"[pdf-search-error] failed while scanning PDF: {e}",
            ok=False,
        )

    payload = {
        "pdf_path": str(src),
        "query": query,
        "case_sensitive": bool(case_sensitive),
        "rect_coord_system": "pymupdf_page_pt_top_left_y_down",
        "rect_to_raster_px": "rect_pdf * (dpi / 72) when using pdf_page_to_image(dpi)",
        "hit_count": len(hits),
        "hit_pages": hit_pages,
        "page_filter": normalized_page_filter,
        "page_filter_reason": str(page_filter_reason or "").strip(),
        "hits": hits,
    }

    saved_json = ""
    if str(out_json_path).strip():
        try:
            out = _resolve_write(workspace, out_json_path)
            out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            saved_json = str(out)
        except Exception as e:  # noqa: BLE001
            return ToolResult(
                text=f"[pdf-search-error] failed to write out_json_path: {e}",
                ok=False,
            )

    if not hits:
        msg = (
            f"No text hits for query {query!r} in PDF.\n"
            f"pdf={src}\n"
            "Tip: verify OCR/text layer exists; if not, use page image workflow."
        )
        if saved_json:
            msg += f"\nSaved: {saved_json}"
        return ToolResult(text=msg, ok=True)

    top = "\n".join(
        f"- page {h['page']} rect_pdf={h['rect_pdf']}: ...{truncate(h['snippet'], 130)}..."
        for h in hits[: min(5, len(hits))]
    )
    msg = (
        f"PDF text search hits for query {query!r}.\n"
        f"pdf={src}\n"
        f"page_filter={normalized_page_filter or 'all'}"
        + (f" reason={page_filter_reason}" if page_filter_reason else "")
        + "\n"
        f"hit_pages={hit_pages}\n"
        f"hit_count={len(hits)}\n"
        f"top_hits:\n{top}"
    )
    if saved_json:
        msg += f"\nSaved: {saved_json}"
    return ToolResult(text=msg)


def _tool_save_text_file(workspace: Path, path: str, content: str) -> ToolResult:
    p = _resolve_write(workspace, path)
    if _is_vlm_test_workflow_mode() and p.name.lower() == "case10_largest_ic.json":
        return ToolResult(
            text=(
                "[vlm_test-guard] `case10_largest_ic.json` is forbidden in `workflow_mode=vlm_test` "
                "(Part A board IC annotate/OpenCV artifacts are skipped). "
                "Do not write Part A locator-style IC JSON."
            ),
            ok=False,
        )
    if p.name == "step03_mapping.json" and "vlm_neighborhood_layout_match" in content:
        blocked = _check_step3b_written(workspace)
        if blocked is not None:
            return blocked
        blocked = _check_step4_locator_landmarks_present(workspace)
        if blocked is not None:
            return blocked
    p.write_text(content, encoding="utf-8")
    return ToolResult(text=f"Wrote {len(content)} chars to {p}")


def _tool_mark_tp_on_assembly_from_pdf_hit(
    workspace: Path,
    search_json_path: str = "debug/case10_target_tp_pdf_search.json",
    assembly_png_path: str = "debug/case10_assembly_drawing.png",
    assembly_pdf_path: str = "",
    hit_index: int = 0,
    roi_half: int = 50,
    out_work_roi_path: str = "debug/case10_target_tp_work_roi.png",
    out_marked_path: str = "debug/case10_assembly_drawing_tp_marked.png",
) -> ToolResult:
    """Use PDF hit rect to mark TP circle on assembly drawing PNG.

    Workflow:
    1) Read `search_json_path` and pick one hit (`hit_index`)
    2) Map hit `rect_pdf` center to assembly PNG pixels using PDF page size
    3) Detect circular pad inside fixed ROI around mapped center
    4) Draw green circle and save `out_marked_path` (work ROI is in-memory only)
    """
    try:
        import cv2  # type: ignore
        import fitz  # type: ignore
        import numpy as np  # type: ignore
    except Exception as e:  # noqa: BLE001
        return ToolResult(text=f"[mark-tp-tool] missing dependency: {e}", ok=False)

    canonical_rel = "debug/case10_assembly_drawing.png"
    canonical_png = _resolve_write(workspace, canonical_rel)

    def _materialize_canonical(src_path: Path) -> Path:
        """Ensure canonical Part0 drawing artifact exists for downstream checks."""
        try:
            if src_path.resolve() == canonical_png.resolve():
                return src_path
        except Exception:
            pass
        # Use copyfile (not copy2) so mtime is current run.
        shutil.copyfile(src_path, canonical_png)
        return canonical_png

    def _ensure_fresh_assembly_png() -> Path:
        """Return a current-run assembly PNG and materialize canonical debug path."""
        # First try as requested.
        try:
            p = _resolve_read(assembly_png_path)
            if not _is_stale_runtime_debug_file(p):
                return _materialize_canonical(p)
        except Exception:
            pass

        # If stale/missing, refresh from provided page-1 PNG input when available.
        src = _tl_input_paths().get("assembly_drawing_page1_png")
        if isinstance(src, str) and src.strip():
            src_p = _resolve_read(src)
            shutil.copyfile(src_p, canonical_png)
            return canonical_png

        # Fallback to the original behavior (will raise meaningful error).
        p = _resolve_read(assembly_png_path)
        return _materialize_canonical(p)

    try:
        search_p = _resolve_read(search_json_path)
        search_obj = json.loads(search_p.read_text(encoding="utf-8"))
        hits = search_obj.get("hits")
        if not isinstance(hits, list) or not hits:
            pdf_path_use = assembly_pdf_path.strip() if isinstance(assembly_pdf_path, str) else ""
            if not pdf_path_use:
                pdf_path_use = str(search_obj.get("pdf_path") or "")
            page_num = 1
            try:
                sig_p = _resolve_read("debug/case10_signal_to_tp.json")
                sig_obj = json.loads(sig_p.read_text(encoding="utf-8"))
                for key in ("locator_page", "assembly_page", "schematic_page"):
                    v = sig_obj.get(key)
                    if isinstance(v, int) and v >= 1:
                        page_num = v
                        break
            except Exception:
                pass
            if pdf_path_use:
                try:
                    pdf_p = _resolve_read(pdf_path_use)
                    raster = _tool_pdf_page_to_image(
                        workspace,
                        pdf_path=str(pdf_p),
                        page=page_num,
                        out_path=canonical_rel,
                        dpi=864,
                    )
                    raster_note = raster.text or "(raster attempted)"
                except Exception as e:  # noqa: BLE001
                    raster_note = f"(raster failed: {e})"
            else:
                raster_note = "(assembly_pdf_path missing)"
            return ToolResult(
                text=(
                    "[mark-tp-tool] PDF text search returned 0 hits for this TP label "
                    "(often graphic-only silkscreen).\n"
                    f"{raster_note}\n"
                    "Next (required): `view_image` on `debug/case10_assembly_drawing.png`, "
                    "locate tp_id visually, then `run_python` Step0C to draw the green circle "
                    "at your estimated pixel center (or call `annotate_image` + save marked PNG)."
                ),
                ok=False,
            )
        if hit_index < 0 or hit_index >= len(hits):
            return ToolResult(
                text=f"[mark-tp-tool] hit_index out of range: {hit_index}, hits={len(hits)}",
                ok=False,
            )
        hit = hits[hit_index]
        if not isinstance(hit, dict):
            return ToolResult(text="[mark-tp-tool] selected hit is not an object.", ok=False)
        rect_pdf = hit.get("rect_pdf")
        if (
            not isinstance(rect_pdf, list)
            or len(rect_pdf) != 4
            or not all(isinstance(v, (int, float)) for v in rect_pdf)
        ):
            return ToolResult(
                text="[mark-tp-tool] selected hit missing valid rect_pdf [x0,y0,x1,y1].",
                ok=False,
            )
        page_num = int(hit.get("page", 1))
        pdf_path_use = assembly_pdf_path.strip() if isinstance(assembly_pdf_path, str) else ""
        if not pdf_path_use:
            pdf_path_use = str(search_obj.get("pdf_path") or "")
        if not pdf_path_use:
            return ToolResult(
                text="[mark-tp-tool] assembly_pdf_path not provided and pdf_path missing in search JSON.",
                ok=False,
            )
        pdf_p = _resolve_read(pdf_path_use)
        raster = _tool_pdf_page_to_image(
            workspace,
            pdf_path=str(pdf_p),
            page=page_num,
            out_path=canonical_rel,
            dpi=864,
        )
        if not raster.ok:
            return ToolResult(
                text=f"[mark-tp-tool] failed to rasterize assembly page {page_num}: {raster.text}",
                ok=False,
            )
        png_p = canonical_png
    except Exception as e:  # noqa: BLE001
        return ToolResult(text=f"[mark-tp-tool] input resolve/read failed: {e}", ok=False)

    img = cv2.imread(str(png_p))
    if img is None:
        return ToolResult(text=f"[mark-tp-tool] failed to read PNG: {png_p}", ok=False)
    H, W = img.shape[:2]

    try:
        doc = fitz.open(str(pdf_p))
        page = doc.load_page(max(0, page_num - 1))
        pw, ph = float(page.rect.width), float(page.rect.height)
        doc.close()
    except Exception as e:  # noqa: BLE001
        return ToolResult(text=f"[mark-tp-tool] failed to read PDF page size: {e}", ok=False)
    if pw <= 0 or ph <= 0:
        return ToolResult(text="[mark-tp-tool] invalid PDF page dimensions.", ok=False)

    sx, sy = W / pw, H / ph
    x0, y0, x1, y1 = [float(v) for v in rect_pdf]
    cx_pdf = (x0 + x1) / 2.0
    cy_pdf = (y0 + y1) / 2.0
    cx = int(round(cx_pdf * sx))
    cy = int(round(cy_pdf * sy))

    half = max(12, int(roi_half))
    wl = max(0, cx - half)
    wt = max(0, cy - half)
    wr = min(W, cx + half)
    wb = min(H, cy + half)
    if wr <= wl or wb <= wt:
        return ToolResult(text="[mark-tp-tool] invalid ROI after clamp.", ok=False)

    roi = img[wt:wb, wl:wr].copy()

    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    _, th = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY_INV)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    th = cv2.dilate(th, kernel, iterations=1)
    contours, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    refx, refy = cx - wl, cy - wt
    cands: list[dict[str, float]] = []
    for cnt in contours:
        area = float(cv2.contourArea(cnt))
        if area < 30:
            continue
        peri = float(cv2.arcLength(cnt, True))
        if peri <= 0:
            continue
        circ = 4.0 * math.pi * area / (peri * peri)
        x, y, ww, hh = cv2.boundingRect(cnt)
        aspect = min(ww, hh) / max(ww, hh) if max(ww, hh) > 0 else 0.0
        (xc, yc), r = cv2.minEnclosingCircle(cnt)
        circle_area = math.pi * r * r
        area_ratio = area / circle_area if circle_area > 0 else 0.0
        if circ >= 0.55 and aspect >= 0.55 and 0.10 <= area_ratio <= 1.12:
            dist = float(((xc - refx) ** 2 + (yc - refy) ** 2) ** 0.5)
            cands.append({"xc": float(xc), "yc": float(yc), "r": float(r), "dist": dist, "circ": float(circ)})

    if not cands:
        # PDF text rects are often tiny; pad center is still the best anchor for Step0C.
        gr = max(10, min(28, half // 3))
        gx, gy = cx, cy
        step0c_path = "pdf_center_fallback"
        roi_bgr = img[wt:wb, wl:wr].copy()
        out_roi = _resolve_write(workspace, out_work_roi_path)
        write_debug_image(out_roi, roi_bgr)
    else:
        best = min(cands, key=lambda c: c["dist"])
        gx = int(round(wl + best["xc"]))
        gy = int(round(wt + best["yc"]))
        gr = max(3, int(round(best["r"])))
        step0c_path = "gray_thresh"
        roi_bgr = img[wt:wb, wl:wr].copy()
        out_roi = _resolve_write(workspace, out_work_roi_path)
        write_debug_image(out_roi, roi_bgr)

    marked = img.copy()
    cv2.circle(marked, (gx, gy), gr, (0, 255, 0), 3)
    out_mark = _resolve_write(workspace, out_marked_path)
    if not write_debug_image(out_mark, marked, max_pixels=pdf_raster_max_pixels()):
        return ToolResult(text=f"[mark-tp-tool] failed to write marked image: {out_mark}", ok=False)

    return ToolResult(
        text=(
            "TP marking from PDF hit completed.\n"
            f"step0c_path={step0c_path}\n"
            f"pdf={pdf_p}\n"
            f"png={png_p}\n"
            f"rect_pdf={rect_pdf}\n"
            f"selected_page={page_num}\n"
            f"scale=({sx:.6f}, {sy:.6f})\n"
            f"roi=[{wl},{wt},{wr},{wb}] size={wr-wl}x{wb-wt}\n"
            f"tp_center=({gx},{gy}) radius={gr}\n"
            f"tp_work_roi=[{wl},{wt},{wr},{wb}] size={wr-wl}x{wb-wt}\n"
            f"work_roi_png={out_roi}\n"
            f"saved:\n- {out_mark}"
        ),
        images=[],
    )


def _tool_detect_largest_ic_on_assembly_from_vlm_hint(
    workspace: Path,
    assembly_path: str = "debug/case10_assembly_drawing_tp_marked.png",
    hints_json_path: str = "debug/case10_assembly_vlm_hints.json",
    work_margin_ratio: float = 0.2,
    out_debug_path: str = "debug/case10_assembly_opencv_debug.png",
    out_box_path: str = "debug/case10_assembly_largest_ic_box.png",
    out_json_path: str = "debug/case10_assembly_largest_ic.json",
) -> ToolResult:
    """Deterministic PartB tool using the 20260527-135827 successful baseline."""
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
    except Exception as e:  # noqa: BLE001
        return ToolResult(text=f"[assembly-ic-tool] missing dependency: {e}", ok=False)

    try:
        img_p = _resolve_read(assembly_path)
        hints_p = _resolve_read(hints_json_path)
        hints = json.loads(hints_p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return ToolResult(text=f"[assembly-ic-tool] failed to load inputs: {e}", ok=False)

    img = cv2.imread(str(img_p))
    if img is None:
        return ToolResult(text=f"[assembly-ic-tool] failed to read image: {img_p}", ok=False)
    H, W = img.shape[:2]

    vlm = hints.get("vlm_roi")
    if isinstance(vlm, dict):
        try:
            l = int(vlm["left"])
            t = int(vlm["top"])
            r = int(vlm["right"])
            b = int(vlm["bottom"])
        except Exception as e:  # noqa: BLE001
            return ToolResult(text=f"[assembly-ic-tool] invalid `vlm_roi`: {e}", ok=False)
    else:
        norm = hints.get("approx_bbox_norm")
        if not (
            isinstance(norm, list)
            and len(norm) == 4
            and all(isinstance(v, (int, float)) for v in norm)
        ):
            return ToolResult(
                text="[assembly-ic-tool] hints JSON must contain `vlm_roi` or `approx_bbox_norm`.",
                ok=False,
            )
        x1n, y1n, x2n, y2n = [float(v) for v in norm]
        l = int(round(x1n * W))
        t = int(round(y1n * H))
        r = int(round(x2n * W))
        b = int(round(y2n * H))

    l = max(0, min(l, W))
    t = max(0, min(t, H))
    r = max(0, min(r, W))
    b = max(0, min(b, H))
    if r <= l or b <= t:
        return ToolResult(text="[assembly-ic-tool] invalid VLM ROI after clamp.", ok=False)
    vlm_roi = [l, t, r, b]

    rw = r - l
    rh = b - t
    ratio = max(0.05, min(0.6, float(work_margin_ratio)))
    pad = max(64, int(round(ratio * max(rw, rh))))
    wl, wt = max(0, l - pad), max(0, t - pad)
    wr, wb = min(W, r + pad), min(H, b + pad)
    if wr <= wl or wb <= wt:
        return ToolResult(text="[assembly-ic-tool] invalid work ROI after expansion.", ok=False)
    work_roi = [wl, wt, wr, wb]
    work = img[wt:wb, wl:wr]

    gray = cv2.cvtColor(work, cv2.COLOR_BGR2GRAY)
    _, mask = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY_INV)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    mask = cv2.dilate(mask, kernel, iterations=1)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidates: list[dict[str, Any]] = []
    for cnt in contours:
        area = float(cv2.contourArea(cnt))
        if area < 5000:
            continue
        x, y, cw, ch = cv2.boundingRect(cnt)
        if ch <= 0:
            continue
        ar = cw / float(ch)
        if not (0.5 <= ar <= 1.5):
            continue
        gx, gy = wl + x, wt + y
        candidates.append(
            {
                "bbox": [gx, gy, gx + cw, gy + ch],
                "bbox_area": float(cw * ch),
                "contour_area": float(area),
                "aspect_ratio": float(ar),
            }
        )
    candidates.sort(key=lambda c: float(c["bbox_area"]), reverse=True)
    if not candidates:
        return ToolResult(
            text=(
                "[assembly-ic-tool] No candidate found in work ROI.\n"
                f"image={W}x{H}\nvlm_roi_px={vlm_roi}\nwork_roi_px={work_roi}"
            ),
            ok=False,
        )

    final = [int(v) for v in candidates[0]["bbox"]]

    marked = img.copy()
    cv2.rectangle(marked, (final[0], final[1]), (final[2], final[3]), (0, 0, 255), 4)
    out_box = _resolve_write(workspace, out_box_path)
    write_debug_image(out_box, marked)

    payload = {
        "vlm_roi_px": vlm_roi,
        "work_roi_px": work_roi,
        "largest_ic_bbox": final,
        "bbox": final,
        "image_size": [W, H],
        "opencv_params": {
            "threshold_binary_inv": 180,
            "kernel_shape": "MORPH_RECT",
            "kernel_size": [3, 3],
            "dilate_iter": 1,
            "min_contour_area": 5000,
            "aspect_ratio": [0.5, 1.5],
            "ranking": "max_bbox_area",
        },
        "candidate_count": len(candidates),
    }
    out_json = _resolve_write(workspace, out_json_path)
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    return ToolResult(
        text=(
            "Assembly largest-IC detection completed.\n"
            f"image={W}x{H}\n"
            f"vlm_roi_px={vlm_roi}\n"
            f"work_roi_px={work_roi}\n"
            f"final_bbox={final}\n"
            f"candidate_count={len(candidates)}"
        ),
        images=[],
    )


def _load_board_image_landscape(
    board_path: str,
    cv2: Any,
) -> tuple[Any, Path, bool]:
    """Load board photo; rotate to landscape (width >= height) when needed."""
    try:
        board_p = _resolve_read(board_path)
    except Exception:
        fb = _tl_input_paths().get("front_board_photo")
        if isinstance(fb, str) and fb.strip():
            board_p = _resolve_read(fb)
        else:
            raise
    img = cv2.imread(str(board_p))
    if img is None:
        raise RuntimeError(f"failed to read board image: {board_p}")
    h, w = img.shape[:2]
    rotated = False
    if w < h:
        img = cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
        rotated = True
    return img, board_p, rotated


def _detect_pcb_region_bbox(img: Any, cv2: Any, np: Any) -> tuple[list[int], Any]:
    """Segment dominant green PCB area; exclude border-hugging environment blobs."""
    h, w = img.shape[:2]
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array([35, 30, 30], dtype=np.uint8), np.array([90, 255, 255], dtype=np.uint8))
    k_close = cv2.getStructuringElement(cv2.MORPH_RECT, (21, 21))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k_close, iterations=2)
    k_open = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k_open, iterations=1)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    img_area = float(h * w)
    kept: list[dict[str, Any]] = []
    for cnt in contours:
        area = float(cv2.contourArea(cnt))
        if area < img_area * 0.08:
            continue
        x, y, cw, ch = cv2.boundingRect(cnt)
        if cw <= 0 or ch <= 0:
            continue
        ar = cw / float(ch)
        touch_l = x <= 8
        touch_t = y <= 8
        touch_r = (x + cw) >= (w - 8)
        touch_b = (y + ch) >= (h - 8)
        touch_cnt = int(touch_l) + int(touch_t) + int(touch_r) + int(touch_b)
        if touch_cnt >= 3 and (ar > 1.8 or ar < 0.55):
            continue
        kept.append({"bbox": [x, y, x + cw, y + ch], "area": area})

    if not kept:
        return [0, 0, w, h], mask
    kept.sort(key=lambda c: float(c["area"]), reverse=True)
    return [int(v) for v in kept[0]["bbox"]], mask


def _board_ic_candidate_score(candidate: dict[str, Any]) -> float:
    area = float(candidate["area"])
    ar = float(candidate["aspect"])
    touch = int(candidate["touch_cnt"])
    compact = min(ar, 1.0 / ar) if ar > 0 else 0.0
    score = area * compact
    if touch >= 3 and ar > 1.3:
        score *= 0.05
    elif touch >= 3 and ar < (1.0 / 1.3):
        score *= 0.05
    elif touch >= 2 and (ar > 1.55 or ar < 0.65):
        score *= 0.2
    return score


def _find_largest_ic_in_roi(
    img: Any,
    work_roi: list[int],
    cv2: Any,
    np: Any,
    *,
    opencv_ballpark: dict[str, Any] | None = None,
    pcb_mask: Any | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any], int]:
    """Run OpenCV IC contour search inside work_roi; return best candidate + debug info."""
    bp = opencv_ballpark if isinstance(opencv_ballpark, dict) else {}
    wx1, wy1, wx2, wy2 = [int(v) for v in work_roi]
    roi = img[wy1:wy2, wx1:wx2]
    rh, rw = roi.shape[:2]
    roi_area = float(max(1, rw * rh))

    use_hsv = bool(bp.get("prefer_hsv_dark_package", True))
    if use_hsv:
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        vu = int(bp.get("hsv_upper_v", 60))
        bw = cv2.inRange(hsv, (0, 0, 0), (180, 255, max(1, vu)))
        ksz = int(bp.get("morph_kernel_size", 5))
        di = int(bp.get("morph_dilate_iter", 2))
        ei = int(bp.get("morph_erode_iter", 1))
        threshold_used = None
    else:
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        tv = int(bp.get("fixed_thresh_inv_dark", 60))
        _, bw = cv2.threshold(gray, tv, 255, cv2.THRESH_BINARY_INV)
        dker = bp.get("dilate_kernel")
        if isinstance(dker, list) and len(dker) >= 2 and isinstance(dker[0], (int, float)):
            ksz = int(dker[0])
        else:
            ksz = int(bp.get("morph_kernel_size", 3))
        di = int(bp.get("dilate_iter", bp.get("morph_dilate_iter", 1)))
        ei = int(bp.get("erode_iter", bp.get("morph_erode_iter", 0)))
        threshold_used = tv

    if pcb_mask is not None:
        roi_pcb = pcb_mask[wy1:wy2, wx1:wx2]
        if roi_pcb.shape[:2] == bw.shape[:2]:
            bw = cv2.bitwise_and(bw, roi_pcb)

    k = max(3, int(ksz) | 1)
    ker = cv2.getStructuringElement(cv2.MORPH_RECT, (k, k))
    bw = cv2.dilate(bw, ker, iterations=max(0, int(di)))
    if int(ei) > 0:
        bw = cv2.erode(bw, ker, iterations=int(ei))

    contours, _ = cv2.findContours(bw, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    total_contours = len(contours)
    min_a = float(bp.get("min_contour_area", 10000))
    asp = bp.get("aspect_ratio")
    if isinstance(asp, (list, tuple)) and len(asp) == 2:
        ar_lo, ar_hi = float(asp[0]), float(asp[1])
    else:
        ar_lo, ar_hi = 0.65, 1.55
    ss = bp.get("package_short_side_px")
    if isinstance(ss, (list, tuple)) and len(ss) == 2:
        ss_lo, ss_hi = int(ss[0]), int(ss[1])
    else:
        ss_lo, ss_hi = 200, 1000

    candidates: list[dict[str, Any]] = []
    for cnt in contours:
        area = float(cv2.contourArea(cnt))
        if area < min_a:
            continue
        x, y, cw, ch = cv2.boundingRect(cnt)
        if ch <= 0:
            continue
        ar = cw / float(ch)
        short = min(cw, ch)
        if ar < ar_lo or ar > ar_hi or short < ss_lo or short > ss_hi:
            continue
        rect_area = float(max(1, cw * ch))
        cover_ratio = rect_area / roi_area
        touch_l = x <= 2
        touch_t = y <= 2
        touch_r = (x + cw) >= (rw - 2)
        touch_b = (y + ch) >= (rh - 2)
        touch_cnt = int(touch_l) + int(touch_t) + int(touch_r) + int(touch_b)
        if cover_ratio > 0.90 and touch_cnt >= 3:
            continue
        if touch_cnt >= 3 and (ar > 1.55 or ar < 0.65):
            continue
        cand = {
            "x": int(x), "y": int(y), "w": int(cw), "h": int(ch),
            "area": float(area), "aspect": float(ar),
            "cover_ratio": float(cover_ratio), "touch_cnt": int(touch_cnt),
            "bbox_area": float(cw * ch),
        }
        cand["score"] = _board_ic_candidate_score(cand)
        candidates.append(cand)

    candidates.sort(key=lambda c: float(c["score"]), reverse=True)
    best = candidates[0] if candidates else None
    debug = {
        "use_hsv_dark_package": bool(use_hsv),
        "hsv_upper_v": int(bp.get("hsv_upper_v", 60)) if use_hsv else None,
        "fixed_thresh_inv_dark": int(threshold_used) if threshold_used is not None else None,
        "kernel_size": int(k),
        "dilate_iter": int(di),
        "erode_iter": int(ei),
        "min_contour_area": float(min_a),
        "aspect_ratio": [ar_lo, ar_hi],
        "package_short_side_px": [ss_lo, ss_hi],
        "mask": bw,
        "threshold_used": threshold_used,
    }
    return best, debug, total_contours


def _tool_detect_largest_ic_on_board_full(
    workspace: Path,
    board_path: str = "INPUT_PATHS.front_board_photo",
    out_debug_path: str = "debug/case10_opencv_debug.png",
    out_box_path: str = "debug/case10_largest_ic_box.png",
    out_json_path: str = "debug/case10_largest_ic.json",
    out_landscape_path: str = "debug/case10_board_landscape.png",
) -> ToolResult:
    """PartA full-board IC detection: PCB mask + QFP filters, no VLM hints."""
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
    except Exception as e:  # noqa: BLE001
        return ToolResult(text=f"[board-ic-full] missing dependency: {e}", ok=False)

    try:
        img, board_p, rotated = _load_board_image_landscape(board_path, cv2)
    except Exception as e:  # noqa: BLE001
        return ToolResult(text=f"[board-ic-full] failed to load board image: {e}", ok=False)

    H, W = img.shape[:2]
    landscape_p = _resolve_write(workspace, out_landscape_path)
    write_debug_image(landscape_p, img)

    pcb_bbox, pcb_mask = _detect_pcb_region_bbox(img, cv2, np)
    px1, py1, px2, py2 = pcb_bbox
    pw, ph = px2 - px1, py2 - py1
    pad = max(32, int(0.02 * max(pw, ph)))
    work_roi = [
        max(0, px1 - pad),
        max(0, py1 - pad),
        min(W, px2 + pad),
        min(H, py2 + pad),
    ]

    opencv_ballpark = {
        "prefer_hsv_dark_package": True,
        "hsv_upper_v": 60,
        "morph_kernel_size": 5,
        "morph_dilate_iter": 2,
        "morph_erode_iter": 1,
        "min_contour_area": 10000,
        "aspect_ratio": [0.65, 1.55],
        "package_short_side_px": [200, 1000],
    }
    best, dbg, total_contours = _find_largest_ic_in_roi(
        img, work_roi, cv2, np,
        opencv_ballpark=opencv_ballpark,
        pcb_mask=pcb_mask,
    )

    if best is None:
        return ToolResult(
            text=(
                "[board-ic-full] No QFP-like IC found on PCB region.\n"
                f"image={W}x{H} rotated={rotated}\n"
                f"pcb_bbox_px={pcb_bbox}\n"
                f"work_roi_px={work_roi}\n"
                f"contours={total_contours}"
            ),
            ok=False,
        )

    wx1, wy1, _, _ = work_roi
    fx1 = wx1 + int(best["x"])
    fy1 = wy1 + int(best["y"])
    fx2 = fx1 + int(best["w"])
    fy2 = fy1 + int(best["h"])
    final_bbox = [fx1, fy1, fx2, fy2]

    marked = img.copy()
    cv2.rectangle(marked, (px1, py1), (px2, py2), (0, 255, 0), 2)
    cv2.rectangle(marked, (fx1, fy1), (fx2, fy2), (0, 0, 255), 4)
    out_box = _resolve_write(workspace, out_box_path)
    write_debug_image(out_box, marked)

    debug_img = img.copy()
    cv2.rectangle(debug_img, (work_roi[0], work_roi[1]), (work_roi[2], work_roi[3]), (255, 165, 0), 2)
    cv2.rectangle(debug_img, (fx1, fy1), (fx2, fy2), (0, 0, 255), 3)
    out_dbg = _resolve_write(workspace, out_debug_path)
    write_debug_image(out_dbg, debug_img)

    payload = {
        "detection_mode": "full_board_pcb_mask",
        "pcb_bbox_px": pcb_bbox,
        "work_roi_px": work_roi,
        "largest_ic_bbox": final_bbox,
        "bbox": final_bbox,
        "area": float(best["area"]),
        "score": round(float(best["score"]), 2),
        "threshold_used": dbg.get("threshold_used"),
        "aspect": round(float(best["aspect"]), 4),
        "cover_ratio": round(float(best["cover_ratio"]), 4),
        "touch_edges": int(best["touch_cnt"]),
        "board_rotated_to_landscape": bool(rotated),
        "candidate_stats": {
            "total_contours": int(total_contours),
            "kept_candidates": 1,
        },
        "opencv_params": {k: v for k, v in dbg.items() if k != "mask"},
        "image_size": [W, H],
        "source_image": str(board_p),
    }
    out_json = _resolve_write(workspace, out_json_path)
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    return ToolResult(
        text=(
            "Board full-image largest-IC detection completed.\n"
            f"image={W}x{H} rotated={rotated}\n"
            f"pcb_bbox_px={pcb_bbox}\n"
            f"work_roi_px={work_roi}\n"
            f"final_bbox={final_bbox}\n"
            f"aspect={float(best['aspect']):.3f} touch_edges={int(best['touch_cnt'])}\n"
            f"score={float(best['score']):.1f}"
        ),
        images=[],
    )


def _tool_detect_largest_ic_on_board_from_vlm_hint(
    workspace: Path,
    board_path: str = "INPUT_PATHS.front_board_photo",
    hints_json_path: str = "debug/case10_vlm_hints.json",
    work_margin_ratio: float = 0.2,
    out_debug_path: str = "debug/case10_opencv_debug.png",
    out_box_path: str = "debug/case10_largest_ic_box.png",
    out_json_path: str = "debug/case10_largest_ic.json",
) -> ToolResult:
    """Detect largest IC on board image using VLM ROI hints.

    Uses the historically stable baseline:
    - threshold(binary_inv) with low values (multi-try around 40)
    - 5x5 morphology, dilate=3 then erode=2
    - contour area + rectangularity/aspect screening
    """
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
    except Exception as e:  # noqa: BLE001
        return ToolResult(text=f"[board-ic-tool] missing dependency: {e}", ok=False)

    try:
        try:
            board_p = _resolve_read(board_path)
        except Exception:
            fb = _tl_input_paths().get("front_board_photo")
            if isinstance(fb, str) and fb.strip():
                board_p = _resolve_read(fb)
            else:
                raise
        try:
            hints_p = _resolve_read(hints_json_path)
        except Exception:
            hints_p = _resolve_read("debug/case10_board_vlm_hints.json")
        hints = json.loads(hints_p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return ToolResult(text=f"[board-ic-tool] failed to load inputs: {e}", ok=False)

    img = cv2.imread(str(board_p))
    if img is None:
        return ToolResult(text=f"[board-ic-tool] failed to read board image: {board_p}", ok=False)
    H, W = img.shape[:2]
    # Use normalized hints only (approx_bbox_norm) to avoid inconsistent pixel fields.
    bbox_norm = hints.get("approx_bbox_norm")
    if (
        isinstance(bbox_norm, list)
        and len(bbox_norm) == 4
        and all(isinstance(v, (int, float)) for v in bbox_norm)
    ):
        x1n, y1n, x2n, y2n = [float(v) for v in bbox_norm]
        if not (0.0 <= x1n <= 1.0 and 0.0 <= y1n <= 1.0 and 0.0 <= x2n <= 1.0 and 0.0 <= y2n <= 1.0):
            return ToolResult(text="[board-ic-tool] approx_bbox_norm values must be in [0,1].", ok=False)
        if x2n <= x1n or y2n <= y1n:
            return ToolResult(text="[board-ic-tool] approx_bbox_norm must satisfy x2>x1 and y2>y1.", ok=False)
        x1 = int(round(x1n * W))
        y1 = int(round(y1n * H))
        x2 = int(round(x2n * W))
        y2 = int(round(y2n * H))
    else:
        return ToolResult(
            text=(
                "[board-ic-tool] hints JSON must contain numeric `approx_bbox_norm` "
                "[x1n,y1n,x2n,y2n]."
            ),
            ok=False,
        )
    x1 = max(0, min(W, x1))
    y1 = max(0, min(H, y1))
    x2 = max(0, min(W, x2))
    y2 = max(0, min(H, y2))
    if x2 <= x1 or y2 <= y1:
        return ToolResult(text="[board-ic-tool] invalid VLM ROI after clamp.", ok=False)
    vlm_roi = [x1, y1, x2, y2]

    margin_ratio = max(0.05, min(0.6, float(work_margin_ratio)))
    mx = int(round((x2 - x1) * margin_ratio))
    my = int(round((y2 - y1) * margin_ratio))
    wx1 = max(0, x1 - mx)
    wy1 = max(0, y1 - my)
    wx2 = min(W, x2 + mx)
    wy2 = min(H, y2 + my)
    if wx2 <= wx1 or wy2 <= wy1:
        return ToolResult(text="[board-ic-tool] invalid work ROI after expansion.", ok=False)
    work_roi = [wx1, wy1, wx2, wy2]
    roi = img[wy1:wy2, wx1:wx2]
    rh, rw = roi.shape[:2]
    roi_area = float(max(1, rw * rh))
    bp = hints.get("opencv_ballpark") if isinstance(hints.get("opencv_ballpark"), dict) else {}
    if "aspect_ratio" not in bp:
        bp = {**bp, "aspect_ratio": [0.65, 1.55]}
    if "package_short_side_px" not in bp:
        bp = {**bp, "package_short_side_px": [200, 1000]}

    best, dbg, total_contours = _find_largest_ic_in_roi(
        img, work_roi, cv2, np, opencv_ballpark=bp,
    )
    use_hsv = bool(dbg.get("use_hsv_dark_package"))
    threshold_used = dbg.get("threshold_used")
    k = int(dbg.get("kernel_size", 3))
    di = int(dbg.get("dilate_iter", 1))
    ei = int(dbg.get("erode_iter", 0))
    ar_lo, ar_hi = dbg.get("aspect_ratio", [0.65, 1.55])
    ss_lo, ss_hi = dbg.get("package_short_side_px", [200, 1000])
    min_a = float(dbg.get("min_contour_area", 10000))

    if best is None:
        return ToolResult(
            text=(
                "[board-ic-tool] No IC contour found in work ROI.\n"
                f"image={W}x{H}\n"
                f"vlm_roi_px={vlm_roi}\n"
                f"work_roi_px={work_roi}\n"
                f"params: use_hsv={use_hsv} k={k} dilate={di} erode={ei} min_area={min_a}"
            ),
            ok=False,
        )

    fx1 = wx1 + int(best["x"])
    fy1 = wy1 + int(best["y"])
    fx2 = fx1 + int(best["w"])
    fy2 = fy1 + int(best["h"])
    final_bbox = [fx1, fy1, fx2, fy2]

    marked = img.copy()
    cv2.rectangle(marked, (fx1, fy1), (fx2, fy2), (0, 0, 255), 4)
    out_box = _resolve_write(workspace, out_box_path)
    write_debug_image(out_box, marked)

    out_json = _resolve_write(workspace, out_json_path)
    payload = {
        "vlm_roi_px": vlm_roi,
        "work_roi_px": work_roi,
        "largest_ic_bbox": final_bbox,
        "bbox": final_bbox,
        "area": float(best["area"]),
        "threshold_used": threshold_used,
        "aspect": round(float(best["aspect"]), 4),
        "cover_ratio": round(float(best["cover_ratio"]), 4),
        "touch_edges": int(best["touch_cnt"]),
        "candidate_stats": {"total_contours": int(total_contours), "kept_candidates": 1},
        "opencv_params": {
            "use_hsv_dark_package": bool(use_hsv),
            "hsv_upper_v": int(bp.get("hsv_upper_v", 60)) if use_hsv else None,
            "fixed_thresh_inv_dark": int(threshold_used) if threshold_used is not None else None,
            "kernel_size": int(k),
            "dilate_iter": int(di),
            "erode_iter": int(ei),
            "min_contour_area": float(min_a),
            "aspect_ratio": [ar_lo, ar_hi],
            "package_short_side_px": [ss_lo, ss_hi],
        },
        "image_size": [W, H],
    }
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    return ToolResult(
        text=(
            "Board largest-IC detection completed.\n"
            f"image={W}x{H}\n"
            f"vlm_roi_px={vlm_roi}\n"
            f"work_roi_px={work_roi}\n"
            f"final_bbox={final_bbox}\n"
            f"area={float(best['area']):.1f} threshold={threshold_used}\n"
            f"cover_ratio={float(best['cover_ratio']):.3f} touch_edges={int(best['touch_cnt'])}\n"
            f"candidates(total={int(total_contours)}, kept=1)"
        ),
        images=[],
    )


def _tool_case12_build_and_align_from_step02_anchors(
    workspace: Path,
    locator_anchor_path: str = "debug/step02_locator_front_anchor.png",
    board_anchor_path: str = "debug/step02_board_front_anchor.png",
) -> ToolResult:
    """PartD dedicated tool: build locator graph and align to board from two anchors."""
    try:
        from case12_step02_graph import (
            run_align_locator_graph_to_board_ic_bbox,
            run_build_step02_locator_graph,
        )
    except Exception as e:  # noqa: BLE001
        return ToolResult(text=f"[case12-step02-tool] import failed: {e}", ok=False)

    try:
        src_loc = _resolve_read(locator_anchor_path)
        src_board = _resolve_read(board_anchor_path)
    except Exception as e:  # noqa: BLE001
        return ToolResult(text=f"[case12-step02-tool] failed to resolve input anchors: {e}", ok=False)

    ws = workspace.resolve()
    if not (ws / "debug").is_dir() and (ws / "workspace" / "debug").is_dir():
        ws = (ws / "workspace").resolve()
    dbg = ws / "debug"
    dbg.mkdir(parents=True, exist_ok=True)

    fixed_loc = dbg / "step02_locator_front_anchor.png"
    fixed_board = dbg / "step02_board_front_anchor.png"
    now = time.time()
    if src_loc.resolve() != fixed_loc.resolve():
        shutil.copyfile(src_loc, fixed_loc)
        os.utime(fixed_loc, (now, now))
    if src_board.resolve() != fixed_board.resolve():
        shutil.copyfile(src_board, fixed_board)
        os.utime(fixed_board, (now, now))

    try:
        graph_obj = run_build_step02_locator_graph(ws, write_viz_png=False)
        aligned_obj = run_align_locator_graph_to_board_ic_bbox(ws, write_viz_png=False)
    except Exception as e:  # noqa: BLE001
        return ToolResult(text=f"[case12-step02-tool] build/align failed: {e}", ok=False)

    out_json = dbg / "case12_step02_locator_graph.json"
    out_png = dbg / "case12_step02_locator_graph.png"
    aligned_json = dbg / "case12_board_points_aligned.json"
    overlay_png = dbg / "case12_board_approx_overlay_opencv.png"
    return ToolResult(
        text=(
            "case12 Step02 graph build+align completed.\n"
            f"workspace={ws}\n"
            f"locator_anchor={fixed_loc}\n"
            f"board_anchor={fixed_board}\n"
            f"graph_json={out_json}\n"
            f"aligned_json={aligned_json}\n"
            f"graph_nodes={len(graph_obj.get('references', [])) if isinstance(graph_obj, dict) else 'n/a'}\n"
            f"aligned_source={aligned_obj.get('source') if isinstance(aligned_obj, dict) else 'n/a'}"
        ),
        images=[],
    )


def _tool_emit_step08_from_case12_aligned(
    workspace: Path,
    aligned_json_path: str = "debug/case12_board_points_aligned.json",
    board_anchor_path: str = "debug/step02_board_front_anchor.png",
    out_step08_png_path: str = "debug/step08_final_tp.png",
    out_step08_json_path: str = "debug/step08_result.json",
    out_mapping_json_path: str = "debug/step03_mapping.json",
    mapping_method: str | None = None,
) -> ToolResult:
    """Generate Step08 outputs from case12 aligned target in one deterministic step."""
    try:
        aligned_p = _resolve_read(aligned_json_path)
        board_p = _resolve_read(board_anchor_path)
        aligned = json.loads(aligned_p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return ToolResult(text=f"[case12-step08-tool] failed to load inputs: {e}", ok=False)

    tgt = aligned.get("board_roi_target_px_approx")
    if not (isinstance(tgt, list) and len(tgt) == 2):
        return ToolResult(
            text="[case12-step08-tool] aligned JSON must include `board_roi_target_px_approx: [x,y]`.",
            ok=False,
        )
    try:
        tx = float(tgt[0])
        ty = float(tgt[1])
    except Exception as e:  # noqa: BLE001
        return ToolResult(text=f"[case12-step08-tool] invalid target point: {e}", ok=False)

    source = str(aligned.get("source", "")).strip()
    inferred_method = "case12_step02_opencv_ic_align"
    if source == "vlm_ic_correspondence_isotropic_align":
        inferred_method = "case12_step02_vlm_ic_align"
    elif source == "opencv_ic_bbox_isotropic_align":
        inferred_method = "case12_step02_opencv_ic_align"
    final_method = inferred_method
    if isinstance(mapping_method, str) and mapping_method.strip() in {
        "case12_step02_opencv_ic_align",
        "case12_step02_vlm_ic_align",
        "front_board_outline_holes",
        "back_board_outline_holes",
    }:
        final_method = mapping_method.strip()

    with Image.open(board_p) as im:
        # Match OpenCV registration coordinates for camera JPEGs carrying an
        # EXIF orientation tag. PNG output has no tag to fix the view later.
        base = ImageOps.exif_transpose(im).convert("RGB")
    w, h = base.size
    try:
        from case12_step02_graph import clamp_board_pixel

        x, y = clamp_board_pixel(tx, ty, w, h)
    except Exception:  # noqa: BLE001
        x = int(round(max(0.0, min(float(w - 1), tx))))
        y = int(round(max(0.0, min(float(h - 1), ty))))
    clamped = (abs(x - tx) > 0.5) or (abs(y - ty) > 0.5)
    r = 15
    draw = ImageDraw.Draw(base)
    draw.ellipse((x - r, y - r, x + r, y + r), outline="red", width=3)
    draw.line((x - r - 8, y, x + r + 8, y), fill="red", width=3)
    draw.line((x, y - r - 8, x, y + r + 8), fill="red", width=3)
    draw.text((min(x + r + 8, w - 120), max(0, y - r - 16)), "TP", fill="red")

    out_png = _resolve_write(workspace, out_step08_png_path)
    write_debug_image(out_png, base)

    out_json = _resolve_write(workspace, out_step08_json_path)
    selected_id = "TP_candidate"
    # Highest priority: keep existing selected_id if already set in step08_result.json.
    try:
        if out_json.exists():
            prev_o = json.loads(out_json.read_text(encoding="utf-8"))
            prev_sid = prev_o.get("selected_id")
            if isinstance(prev_sid, str) and prev_sid.strip():
                selected_id = prev_sid.strip()
    except Exception:
        pass
    # Next priority: infer TP id from Part0 signal file.
    try:
        sig_p = _resolve_read("debug/case10_signal_to_tp.json")
        sig_o = json.loads(sig_p.read_text(encoding="utf-8"))
        sid = sig_o.get("tp_id_or_ref") or sig_o.get("tp_id")
        if isinstance(sid, str) and sid.strip():
            selected_id = sid.strip()
    except Exception:
        pass
    out_json_obj = {
        "selected_id": selected_id,
        "marker_radius": int(r),
        "pixel": [int(x), int(y)],
    }
    out_json.write_text(json.dumps(out_json_obj, ensure_ascii=False, indent=2), encoding="utf-8")

    out_map = _resolve_write(workspace, out_mapping_json_path)
    map_obj = {
        "mapping_method": str(final_method),
        "tp_prior_board": [float(tx), float(ty)],
        "tp_board_rounded": [int(x), int(y)],
        "source": source or "case12_aligned_json",
    }
    out_map.write_text(json.dumps(map_obj, ensure_ascii=False, indent=2), encoding="utf-8")

    return ToolResult(
        text=(
            "Step08 outputs generated from case12 aligned target.\n"
            f"aligned_json={aligned_p}\n"
            f"board_anchor={board_p} ({w}x{h})\n"
            f"target_float=[{tx:.3f}, {ty:.3f}] -> pixel=[{x}, {y}]"
            + (" (clamped to board image bounds)" if clamped else "")
            + f"\n"
            f"mapping_method={final_method} (inferred from source={source or 'n/a'})\n"
            f"saved:\n- {out_png}\n- {out_json}\n- {out_map}"
        ),
        images=[],
    )


def _tool_prepare_back_board_landmark_candidates(
    workspace: Path,
    locator_path: str = "debug/case10_assembly_drawing_tp_marked.png",
    back_board_path: str = "INPUT_PATHS.back_board_photo",
) -> ToolResult:
    """Generate numbered CV proposals that the VLM must semantically review."""
    try:
        from .back_board_registration import prepare_landmark_review
        locator = _resolve_read(locator_path)
        board = _resolve_read(back_board_path)
        debug = _resolve_write(workspace, "debug/back_02_edge_hole_candidates.json").parent
        summary = prepare_landmark_review(locator, board, debug)
    except Exception as e:  # noqa: BLE001
        # Landmark matching is optional refinement.  Persist a valid empty
        # review input so the workflow can record matches=[] and advance to
        # outline-only registration instead of retrying this tool until the
        # agent reaches max_steps.
        debug = _resolve_write(workspace, "debug/back_02_edge_hole_candidates.json").parent
        debug.mkdir(parents=True, exist_ok=True)
        reason = f"{type(e).__name__}: {e}"
        candidate_json = debug / "back_02_edge_hole_candidates.json"
        candidate_sheet = debug / "back_02_vlm_edge_hole_candidate_sheet.png"
        candidate_json.write_text(
            json.dumps(
                {
                    "instruction": (
                        "Candidate generation failed. Record matches=[] and continue with "
                        "the safe outline-only registration fallback."
                    ),
                    "locator": {"accepted_by_cv": [], "rejected_by_cv": []},
                    "photo": {"accepted_by_cv": [], "rejected_by_cv": []},
                    "fallback_reason": reason,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        sheet = Image.new("RGB", (1280, 320), "white")
        draw = ImageDraw.Draw(sheet)
        draw.text((40, 45), "BACK-BOARD LANDMARK FALLBACK", fill=(180, 30, 30))
        draw.text((40, 105), "No reliable CV candidates were produced.", fill=(20, 20, 20))
        draw.text((40, 155), "Call record_back_landmark_review with matches=[] to continue.", fill=(20, 20, 20))
        draw.text((40, 215), reason[:170], fill=(90, 90, 90))
        sheet.save(candidate_sheet)
        return ToolResult(
            text=(
                "Back-board landmark candidate generation could not produce reliable candidates, "
                "so a safe empty fallback was created.\n"
                "Call record_back_landmark_review with matches=[] and explain that CV candidate "
                "generation failed; do not retry prepare_back_board_landmark_candidates.\n"
                f"fallback_reason={reason}\ncombined_sheet={candidate_sheet}"
            ),
            images=[str(candidate_sheet)],
        )
    return ToolResult(
        text=(
            "Back-board PCB-edge hole candidate sheets prepared for VLM semantic review.\n"
            "Inspect the combined full-board + enlarged-crop sheet, then call record_back_landmark_review. "
            "Use matches=[] when fewer than two reliable corresponding openings exist; registration will safely fall back.\n"
            f"locator_candidates={summary['locator_candidate_count']} "
            f"photo_candidates={summary['photo_candidate_count']}\n"
            f"combined_sheet={summary['candidate_sheet']}"
        ),
        images=[str(summary["candidate_sheet"])],
    )


def _tool_record_back_landmark_review(
    workspace: Path,
    matches: list[dict[str, Any]],
    overall_evidence: str,
    rejected_ids: list[str] | None = None,
    out_path: str = "debug/back_03_vlm_edge_hole_review.json",
) -> ToolResult:
    """Validate and persist VLM semantic landmark classifications/correspondences."""
    allowed = {"mounting_hole", "tooling_hole", "non_plated_hole", "fiducial", "board_cutout"}
    if not isinstance(matches, list) or len(matches) == 1:
        return ToolResult(text="[back-landmark-review] provide either zero matches (safe fallback) or at least two reliable pairs.", ok=False)
    if not str(overall_evidence or "").strip():
        return ToolResult(text="[back-landmark-review] overall visual evidence is required.", ok=False)
    try:
        candidate_path = _resolve_read("debug/back_02_edge_hole_candidates.json")
        candidate_data = json.loads(candidate_path.read_text(encoding="utf-8"))
        valid_locator_ids = {
            str(item.get("id")) for key in ("accepted_by_cv", "rejected_by_cv")
            for item in candidate_data.get("locator", {}).get(key, [])
        }
        valid_board_ids = {
            str(item.get("id")) for key in ("accepted_by_cv", "rejected_by_cv")
            for item in candidate_data.get("photo", {}).get(key, [])
        }
    except Exception as exc:  # noqa: BLE001
        return ToolResult(text=f"[back-landmark-review] cannot load candidate IDs: {exc}", ok=False)
    normalized: list[dict[str, Any]] = []
    used_locator: set[str] = set()
    used_board: set[str] = set()
    for index, match in enumerate(matches, 1):
        if not isinstance(match, dict):
            return ToolResult(text=f"[back-landmark-review] match #{index} must be an object.", ok=False)
        locator_id = str(match.get("locator_id", "")).strip()
        board_id = str(match.get("board_id", "")).strip()
        landmark_type = str(match.get("landmark_type", "")).strip().lower()
        evidence = str(match.get("evidence", "")).strip()
        try:
            confidence = float(match.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        if not locator_id or not board_id or locator_id in used_locator or board_id in used_board:
            return ToolResult(text=f"[back-landmark-review] match #{index} has missing or duplicate candidate IDs.", ok=False)
        if locator_id not in valid_locator_ids or board_id not in valid_board_ids:
            return ToolResult(
                text=(f"[back-landmark-review] match #{index} references IDs not drawn on the candidate sheet: "
                      f"{locator_id} -> {board_id}. Valid locator IDs={sorted(valid_locator_ids)}; "
                      f"valid photo IDs={sorted(valid_board_ids)}."),
                ok=False,
            )
        if landmark_type not in allowed:
            return ToolResult(text=f"[back-landmark-review] match #{index} has unsupported landmark_type={landmark_type!r}.", ok=False)
        if confidence < 0.75 or confidence > 1.0 or not evidence:
            return ToolResult(text=f"[back-landmark-review] match #{index} needs confidence >=0.75 and visual evidence.", ok=False)
        used_locator.add(locator_id)
        used_board.add(board_id)
        normalized.append({
            "locator_id": locator_id, "board_id": board_id,
            "landmark_type": landmark_type, "confidence": round(confidence, 3),
            "evidence": evidence,
        })
    payload = {
        "review_source": "vlm_visual_semantic_review",
        "overall_evidence": str(overall_evidence).strip(),
        "matches": normalized,
        "rejected_ids": [str(item) for item in (rejected_ids or [])],
        "policy": "Only PCB-edge mechanical openings are allowed. Solder pads, vias and TP pads are forbidden as global registration anchors.",
    }
    out = _resolve_write(workspace, out_path)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return ToolResult(text=f"Recorded {len(normalized)} VLM-reviewed back-board landmark pairs: {out}")


def _tool_register_back_board_from_outline_and_holes(
    workspace: Path,
    locator_path: str = "debug/case10_assembly_drawing_tp_marked.png",
    back_board_path: str = "INPUT_PATHS.back_board_photo",
    review_path: str = "debug/back_03_vlm_edge_hole_review.json",
    locator_roi_hint_norm: list[float] | None = None,
    back_board_roi_hint_norm: list[float] | None = None,
    out_json_path: str = "debug/back_board_registration.json",
    out_overlay_path: str = "debug/back_board_registration_overlay.png",
) -> ToolResult:
    """Map a green-marked locator to the selected board photo using outline holes."""
    try:
        from .back_board_registration import draw_overlay, register
        locator = _resolve_read(locator_path)
        board = _resolve_read(back_board_path)
        out_json = _resolve_write(workspace, out_json_path)
        out_overlay = _resolve_write(workspace, out_overlay_path)
        try:
            review = _resolve_read(review_path)
        except Exception:
            review = None
        result = register(
            locator,
            board,
            debug_dir=out_json.parent,
            review_path=review,
            locator_roi_hint_norm=locator_roi_hint_norm,
            board_roi_hint_norm=back_board_roi_hint_norm,
        )
        out_json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        draw_overlay(board, result, out_overlay)
    except Exception as e:  # noqa: BLE001
        used_semantic_hint = bool(locator_roi_hint_norm or back_board_roi_hint_norm)
        next_action = (
            "Semantic ROI refinement also failed. Do not retry registration; call finish with "
            "needs_user_help=true and report the outline quality failure."
            if used_semantic_hint else
            "View the marked locator and back-board photo, then retry exactly once with rough "
            "normalized [x1,y1,x2,y2] PCB ROIs. OpenCV will refine the edges inside those ROIs."
        )
        return ToolResult(text=f"[back-board-registration] failed: {e}\n{next_action}", ok=False)
    target = result["board_roi_target_px_approx"]
    return ToolResult(
        text=(
            "Back-board outline/hole registration completed.\n"
            f"locator={locator}\nboard={board}\n"
            f"target_px={target}\n"
            f"inlier_holes={result['inlier_hole_count']} mean_error_px={result['mean_hole_error_px']} "
            f"confidence={result['confidence']}\n"
            f"saved:\n- {out_json}\n- {out_overlay}"
        ),
        # The full-resolution overlay is retained for audit and UI display,
        # but attaching it to the next model turn can push the accumulated
        # multimodal request over the provider body-size limit.  The next step
        # is deterministic and reads the JSON directly, so no VLM attachment
        # is needed here.
        images=[],
    )


def _tool_record_board_side_decision(
    workspace: Path,
    side: str,
    evidence: str,
    confidence: float = 0.0,
    out_path: str = "debug/board_side_decision.json",
) -> ToolResult:
    """Persist a VLM visual decision about which physical side contains the TP."""
    normalized = str(side or "").strip().lower()
    aliases = {"front": "front", "top": "front", "正面": "front", "back": "back", "bottom": "back", "bot": "back", "背面": "back"}
    normalized = aliases.get(normalized, normalized)
    if normalized not in {"front", "back"}:
        return ToolResult(text="[board-side-decision] side must be front or back.", ok=False)
    if not str(evidence or "").strip():
        return ToolResult(text="[board-side-decision] visual/textual locator evidence is required.", ok=False)
    try:
        conf = max(0.0, min(1.0, float(confidence)))
    except (TypeError, ValueError):
        conf = 0.0
    available = {
        "front": bool(_tl_input_paths().get("front_board_photo")),
        "back": bool(_tl_input_paths().get("back_board_photo")),
    }
    payload = {
        "side": normalized,
        "camera_view": normalized,
        "evidence": str(evidence).strip(),
        "confidence": round(conf, 3),
        "corresponding_photo_available": bool(available[normalized]),
        "decision_source": "vlm_locator_visual_inspection",
    }
    out = _resolve_write(workspace, out_path)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if not available[normalized]:
        return ToolResult(
            text=(
                f"Board side decided as {normalized}, but INPUT_PATHS.{normalized}_board_photo is missing. "
                "The final localization must request the corresponding physical image."
            ),
            ok=False,
        )
    return ToolResult(
        text=f"Board side decided: {normalized}; confidence={conf:.3f}; saved={out}",
    )


def _tool_emit_step08_from_back_board_registration(
    workspace: Path,
    registration_json_path: str = "debug/back_board_registration.json",
    back_board_path: str = "INPUT_PATHS.back_board_photo",
    camera_view: str | None = None,
) -> ToolResult:
    """Produce final artifacts for outline/hole registration on either board side."""
    normalized_side = str(camera_view or "").strip().lower()
    aliases = {"top": "front", "bottom": "back", "bot": "back"}
    normalized_side = aliases.get(normalized_side, normalized_side)
    if normalized_side not in {"front", "back"}:
        normalized_side = (
            "front"
            if "front_board_photo" in str(back_board_path).lower()
            else "back"
        )
    mapping_method = f"{normalized_side}_board_outline_holes"
    result = _tool_emit_step08_from_case12_aligned(
        workspace=workspace,
        aligned_json_path=registration_json_path,
        board_anchor_path=back_board_path,
        out_step08_png_path="debug/step08_final_tp.png",
        out_step08_json_path="debug/step08_result.json",
        out_mapping_json_path="debug/step03_mapping.json",
        mapping_method=mapping_method,
    )
    if result.ok:
        try:
            out = _resolve_write(workspace, "debug/step08_result.json")
            payload = json.loads(out.read_text(encoding="utf-8"))
            payload["camera_view"] = normalized_side
            payload["mapping_method"] = mapping_method
            out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            return ToolResult(text=f"[back-board-step08] failed to tag final result: {e}", ok=False)
    return result


# --------------------------------------------------------------------------- #
# Image tools
# --------------------------------------------------------------------------- #

def _tool_image_info(path: str) -> ToolResult:
    p = _resolve_read(path)
    with Image.open(p) as im:
        w, h = im.size
        mode = im.mode
    return ToolResult(
        text=f"{p}: {w}x{h} px, mode={mode}, {p.stat().st_size} bytes"
    )


def _tool_view_image(path: str, note: str = "") -> ToolResult:
    """Attach an image so the VLM can *see* it in the next turn."""
    p = _resolve_read(path)
    if p.suffix.lower() == ".pdf":
        return ToolResult(
            text=(
                "[view-image-error] PDF is not a directly viewable image. "
                "First convert a PDF page to PNG via `pdf_page_to_image`, "
                "then call `view_image` on the generated PNG path."
            ),
            ok=False,
        )
    try:
        with Image.open(p) as im:
            im.verify()
    except Exception:
        return ToolResult(
            text=(f"[view-image-error] `{p}` is not a readable raster image. "
                  "Use `read_text_file` for JSON/text artifacts."),
            ok=False,
        )
    text = f"Attached image: {p}"
    if note:
        text += f"\nNote: {note}"
    if _is_stepb3_assembly_largest_ic_box_png(p):
        _stepb3_record_view_gate(_tl_workspace(), p)
        text += (
            "\n[stepb3-gate] Recorded StepB3 view of current "
            f"`{_STEPSB3_VIEW_GATE_REL.as_posix()}` (mtime matches this PNG)."
        )
    return ToolResult(text=text, images=[str(p)])


def _tool_pdf_page_to_image(
    workspace: Path,
    pdf_path: str,
    page: int = 1,
    out_path: str = "debug/pdf_page.png",
    dpi: int = 600,
    graphics_min_line_width: float | None = None,
    aa_level: int | None = None,
) -> ToolResult:
    """Convert one PDF page to PNG for downstream visual inspection tools.

    Optional PyMuPDF rendering tweaks (thin colored strokes often look faint vs PDF viewers):
    ``graphics_min_line_width`` (e.g. 0.35–0.75) and ``aa_level`` (0–8; lower = crisper
    thin lines). Only apply when PyMuPDF renders the page (not pdf2image fallback).
    """
    src = _resolve_read(pdf_path)
    if src.suffix.lower() != ".pdf":
        return ToolResult(
            text=f"[pdf-convert-error] input is not a PDF: {src}",
            ok=False,
        )
    try:
        page = int(page)
    except (TypeError, ValueError):
        return ToolResult(
            text=f"[pdf-convert-error] invalid page value: {page!r}",
            ok=False,
        )
    try:
        dpi = int(dpi)
    except (TypeError, ValueError):
        return ToolResult(
            text=f"[pdf-convert-error] invalid dpi value: {dpi!r}",
            ok=False,
        )
    if page < 1:
        return ToolResult(
            text="[pdf-convert-error] `page` must be >= 1 (1-based).",
            ok=False,
        )
    dpi = max(72, dpi)
    if graphics_min_line_width is not None:
        try:
            graphics_min_line_width = float(graphics_min_line_width)
        except (TypeError, ValueError):
            graphics_min_line_width = None
    if aa_level is not None:
        try:
            aa_level = int(aa_level)
        except (TypeError, ValueError):
            aa_level = None
    out = _resolve_write(workspace, out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    # Prefer PyMuPDF when available; fallback to pdf2image.
    try:
        import fitz  # type: ignore

        doc = fitz.open(str(src))
        if page > doc.page_count:
            return ToolResult(
                text=(
                    f"[pdf-convert-error] page {page} out of range; "
                    f"document has {doc.page_count} page(s)."
                ),
                ok=False,
            )
        zoom = dpi / 72.0
        pg = doc.load_page(page - 1)
        with _fitz_render_prefs(graphics_min_line_width, aa_level):
            pix = pg.get_pixmap(
                matrix=fitz.Matrix(zoom, zoom),
                alpha=False,
            )
        pix_img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        if not write_debug_image(out, pix_img, max_pixels=pdf_raster_max_pixels()):
            doc.close()
            return ToolResult(
                text=f"[pdf-convert-error] failed to write output image: {out}",
                ok=False,
            )
        doc.close()
        return ToolResult(
            text=(
                f"Converted PDF page {page} to image.\n"
                f"source={src}\noutput={out}\nsize={pix.width}x{pix.height}"
            ),
            images=[str(out)],
        )
    except Exception:
        pass

    try:
        from pdf2image import convert_from_path  # type: ignore

        pages = convert_from_path(
            str(src),
            dpi=dpi,
            first_page=int(page),
            last_page=int(page),
        )
        if not pages:
            return ToolResult(
                text=f"[pdf-convert-error] failed to render page {page}.",
                ok=False,
            )
        if not write_debug_image(out, pages[0], max_pixels=pdf_raster_max_pixels()):
            return ToolResult(
                text=f"[pdf-convert-error] failed to write output image: {out}",
                ok=False,
            )
        with Image.open(out) as im:
            w, h = im.size
        return ToolResult(
            text=(
                f"Converted PDF page {page} to image (pdf2image fallback).\n"
                f"source={src}\noutput={out}\nsize={w}x{h}"
            ),
            images=[str(out)],
        )
    except Exception as e:  # noqa: BLE001
        return ToolResult(
            text=(
                "[pdf-convert-error] no available PDF renderer. "
                f"Tried PyMuPDF/pdf2image and failed: {e}"
            ),
            ok=False,
        )


def _tool_pdf_draw_circle_then_rasterize(
        workspace: Path,
        pdf_path: str,
        page: int = 1,
        rect_pdf: list[Any] | None = None,
        center_pdf: list[Any] | None = None,
        radius_pt: float | None = None,
        min_radius_pt: float = 4.0,
        stroke_width_pt: float = 1.5,
        dpi: int = 600,
        out_path: str = "debug/pdf_page_marked.png",
        graphics_min_line_width: float | None = None,
        aa_level: int | None = None,
) -> ToolResult:
    """Draw a circle in PDF user space, then rasterize the page (PyMuPDF only).

    Coordinates match ``search_pdf_text`` hits / WPS text highlights: same page
    space as ``get_pixmap(matrix=...)``. Does **not** modify the PDF on disk.

    Optional ``graphics_min_line_width`` / ``aa_level`` (see ``pdf_page_to_image``)
    apply only to the rasterization step.

    When ``rect_pdf`` is given and ``radius_pt`` is omitted, the circle radius is
    ``0.9 * max(half_width, half_height)`` of that box (smaller than legacy 1.8× for a tighter TP ring).
    """
    src = _resolve_read(pdf_path)
    if src.suffix.lower() != ".pdf":
        return ToolResult(
            text=f"[pdf-draw-raster-error] input is not a PDF: {src}",
            ok=False,
        )
    try:
        page = int(page)
    except (TypeError, ValueError):
        return ToolResult(
            text=f"[pdf-draw-raster-error] invalid page value: {page!r}",
            ok=False,
        )
    if page < 1:
        return ToolResult(
            text="[pdf-draw-raster-error] `page` must be >= 1 (1-based).",
            ok=False,
        )
    try:
        dpi = int(dpi)
    except (TypeError, ValueError):
        dpi = 600
    dpi = max(72, dpi)
    try:
        min_radius_pt = float(min_radius_pt)
    except (TypeError, ValueError):
        min_radius_pt = 4.0
    try:
        stroke_width_pt = float(stroke_width_pt)
    except (TypeError, ValueError):
        stroke_width_pt = 1.5
    if graphics_min_line_width is not None:
        try:
            graphics_min_line_width = float(graphics_min_line_width)
        except (TypeError, ValueError):
            graphics_min_line_width = None
    if aa_level is not None:
        try:
            aa_level = int(aa_level)
        except (TypeError, ValueError):
            aa_level = None

    if radius_pt is not None:
        try:
            radius_pt = float(radius_pt)
        except (TypeError, ValueError):
            radius_pt = None

    cx: float | None = None
    cy: float | None = None
    r: float | None = None

    if rect_pdf is not None:
        if (not isinstance(rect_pdf, (list, tuple))) or len(rect_pdf) != 4:
            return ToolResult(
                text="[pdf-draw-raster-error] rect_pdf must be [x0,y0,x1,y1] in PDF points.",
                ok=False,
            )
        x0, y0, x1, y1 = (float(rect_pdf[0]), float(rect_pdf[1]),
                          float(rect_pdf[2]), float(rect_pdf[3]))
        cx = (x0 + x1) / 2.0
        cy = (y0 + y1) / 2.0
        half_w = abs(x1 - x0) / 2.0
        half_h = abs(y1 - y0) / 2.0
        # Tighter TP highlight: ~half the legacy circle (was max(half)*1.8).
        r_auto = max(half_w, half_h) * 0.9
        r = max(min_radius_pt, r_auto)
        if radius_pt is not None:
            r = max(float(radius_pt), min_radius_pt)
    elif center_pdf is not None:
        if (not isinstance(center_pdf, (list, tuple))) or len(center_pdf) != 2:
            return ToolResult(
                text="[pdf-draw-raster-error] center_pdf must be [cx, cy] in PDF points.",
                ok=False,
            )
        cx, cy = float(center_pdf[0]), float(center_pdf[1])
        r = float(radius_pt) if radius_pt is not None else max(min_radius_pt, 6.0)
        r = max(r, min_radius_pt)
    else:
        return ToolResult(
            text="[pdf-draw-raster-error] provide rect_pdf [x0,y0,x1,y1] or center_pdf [cx,cy] (+ radius_pt).",
            ok=False,
        )

    out = _resolve_write(workspace, out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    try:
        import fitz  # type: ignore
    except Exception as e:  # noqa: BLE001
        return ToolResult(
            text=f"[pdf-draw-raster-error] PyMuPDF (fitz) unavailable: {e}",
            ok=False,
        )

    doc = None
    try:
        doc = fitz.open(str(src))
        if page > doc.page_count:
            return ToolResult(
                text=(
                    f"[pdf-draw-raster-error] page {page} out of range; "
                    f"document has {doc.page_count} page(s)."
                ),
                ok=False,
            )
        pg = doc.load_page(page - 1)
        pg.draw_circle(
            (cx, cy),
            r,
            color=(0, 1, 0),
            width=stroke_width_pt,
            fill=None,
        )
        zoom = dpi / 72.0
        with _fitz_render_prefs(graphics_min_line_width, aa_level):
            pix = pg.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        pix_img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        if not write_debug_image(out, pix_img, max_pixels=pdf_raster_max_pixels()):
            return ToolResult(
                text=f"[pdf-draw-raster-error] failed to write output image: {out}",
                ok=False,
            )
        return ToolResult(
            text=(
                f"Drew green circle in PDF space center=({cx:.3f},{cy:.3f}) pt, r={r:.3f} pt, "
                f"then rendered page {page} @ {dpi} dpi.\n"
                f"source={src}\noutput={out}\nsize={pix.width}x{pix.height}"
            ),
            images=[str(out)],
        )
    except Exception as e:  # noqa: BLE001
        return ToolResult(
            text=f"[pdf-draw-raster-error] {e}",
            ok=False,
        )
    finally:
        if doc is not None:
            try:
                doc.close()
            except Exception:
                pass


def _tool_crop_image(workspace: Path, path: str, bbox: list[int],
                     out_path: str) -> ToolResult:
    if not (isinstance(bbox, (list, tuple)) and len(bbox) == 4):
        return ToolResult(
            text="[crop-error] bbox must be [left, top, right, bottom]",
            ok=False,
        )
    p = _resolve_read(path)
    out = _resolve_write(workspace, out_path)
    norm_out = str(out).replace("\\", "/").lower()
    if "step04_roi_crop" in norm_out:
        blocked = _require_before_board_step04_roi_crop(workspace)
        if blocked is not None:
            return blocked
    if "step03_board_roi" in norm_out:
        blocked = _require_step3b_vlm_or_locator_roi(workspace, early_board_roi=True)
        if blocked is not None:
            return blocked
    try:
        l, t, r, b = (int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3]))
    except (TypeError, ValueError):
        return ToolResult(
            text="[crop-error] bbox values must be integers",
            ok=False,
        )
    if r <= l or b <= t:
        return ToolResult(
            text="[crop-error] bbox must satisfy right>left and bottom>top",
            ok=False,
        )
    cw, ch = r - l, b - t

    if "step03_locator_roi" in norm_out:
        norm_src = str(p.resolve()).replace("\\", "/").lower()
        if "step01_locator_front_anchor" not in norm_src:
            return ToolResult(
                text=(
                    "[crop-error] `debug/step03_locator_roi.png` must be cropped from "
                    "`debug/step01_locator_front_anchor.png` (locator / 位号图), not the board photo. "
                    f"You used: {p}"
                ),
                ok=False,
            )
        if cw < 500 or ch < 500:
            return ToolResult(
                text=(
                    "[crop-error] Locator neighborhood ROI is too small: "
                    f"{cw}x{ch}px. Expected about **500×500 px** (or larger) centered on the green TP "
                    "(default `crop_green_tp_neighborhood_on_locator`; increase "
                    "`margin_px` / `min_side_px` for more context). "
                    "Re-`view_image` the locator, find the **green circle center**, "
                    "and expand the bbox symmetrically."
                ),
                ok=False,
            )

    with Image.open(p) as im:
        iw, ih = im.size
        if l < 0 or t < 0 or r > iw or b > ih:
            return ToolResult(
                text=(
                    f"[crop-error] bbox {bbox} is outside image bounds {iw}x{ih}. "
                    "Adjust coordinates."
                ),
                ok=False,
            )
        cropped = im.crop((l, t, r, b))
        if not write_debug_image(out, cropped):
            return ToolResult(
                text=f"[crop-error] failed to write output image: {out}",
                ok=False,
            )
    return ToolResult(
        text=f"Cropped {p} with bbox={bbox} → {out} ({cropped.size[0]}x{cropped.size[1]})",
        images=[str(out)],
    )


def _tool_crop_green_tp_neighborhood_on_locator(
    workspace: Path,
    locator_path: str = "debug/step01_locator_front_anchor.png",
    margin_px: int = 450,
    min_side_px: int = 500,
    out_path: str = "debug/step03_locator_roi.png",
) -> ToolResult:
    """Crop an axis-aligned ROI centered on the **green TP circle** on the locator PNG.

    Uses the same HSV green band as Step3 red/green tools, so the center matches the
    raster produced by ``pdf_draw_circle_then_rasterize``. Call this for Step3A instead
    of guessing ``crop_image`` bboxes by eye. Default crop is about **500×500** px;
    raise ``margin_px`` / ``min_side_px`` for a wider neighborhood.
    """
    try:
        import cv2
        import numpy as np
    except Exception as e:  # noqa: BLE001
        return ToolResult(text=f"[crop-green-roi] import error: {e}", ok=False)

    try:
        margin_px = int(margin_px)
        min_side_px = int(min_side_px)
    except (TypeError, ValueError):
        margin_px = 450
        min_side_px = 500
    margin_px = max(64, margin_px)
    min_side_px = max(500, min_side_px)

    try:
        p = _resolve_read(locator_path)
        img = cv2.imread(str(p))
        if img is None:
            pil = Image.open(p).convert("RGB")
            arr = np.array(pil)
            img = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
    except Exception as e:  # noqa: BLE001
        return ToolResult(text=f"[crop-green-roi] failed to load locator: {e}", ok=False)

    ih, iw = img.shape[:2]
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    gmask = cv2.inRange(hsv, np.array([40, 100, 100]), np.array([80, 255, 255]))
    contours, _ = cv2.findContours(gmask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return ToolResult(
            text=(
                "[crop-green-roi] green circle not found on locator (HSV). "
                "Confirm `debug/step01_locator_front_anchor.png` has the Step1 green mark."
            ),
            ok=False,
        )
    cnt = max(contours, key=cv2.contourArea)
    m = cv2.moments(cnt)
    if float(m["m00"]) <= 0.0:
        return ToolResult(text="[crop-green-roi] invalid green contour.", ok=False)
    gx = float(m["m10"] / m["m00"])
    gy = float(m["m01"] / m["m00"])

    half_w = max(margin_px, min_side_px // 2)
    half_h = max(margin_px, min_side_px // 2)
    l = int(np.floor(gx - half_w))
    t = int(np.floor(gy - half_h))
    r = int(np.ceil(gx + half_w))
    b = int(np.ceil(gy + half_h))

    # Clamp, then expand symmetrically if we lost size at edges.
    l = max(0, l)
    t = max(0, t)
    r = min(iw, r)
    b = min(ih, b)
    if r - l < min_side_px:
        need = min_side_px - (r - l)
        l2 = max(0, l - need // 2)
        r2 = min(iw, r + (need - (l - l2)))
        l, r = l2, r2
        if r - l < min_side_px:
            r = min(iw, l + min_side_px)
    if b - t < min_side_px:
        need = min_side_px - (b - t)
        t2 = max(0, t - need // 2)
        b2 = min(ih, b + (need - (t - t2)))
        t, b = t2, b2
        if b - t < min_side_px:
            b = min(ih, t + min_side_px)

    if r <= l or b <= t:
        return ToolResult(
            text=f"[crop-green-roi] degenerate bbox after clamp; image={iw}x{ih}, center≈({gx:.1f},{gy:.1f})",
            ok=False,
        )

    out = _resolve_write(workspace, out_path)
    crop = img[t:b, l:r]
    write_debug_image(out, crop)
    cw, ch = r - l, b - t
    return ToolResult(
        text=(
            f"Cropped locator neighbourhood around green TP centroid ({gx:.1f}, {gy:.1f}) px.\n"
            f"source={p}\n"
            f"bbox [left,top,right,bottom]=[{l},{t},{r},{b}] size={cw}x{ch}\n"
            f"saved={out}"
        ),
        images=[str(out)],
    )


def _tool_annotate_image(workspace: Path, path: str,
                         points: list[dict[str, Any]],
                         out_path: str,
                         radius: int = 4) -> ToolResult:
    """Draw markers on an image.

    Each entry may be:
      - Crosshair: {x, y, label?, color?, radius?}
      - Anchor rectangle (Step2): {bbox:[x1,y1,x2,y2], label?, color?, width?}
    """
    p = _resolve_read(path)
    out = _resolve_write(workspace, out_path)
    if _is_vlm_test_workflow_mode() and out.name.lower() == "case10_largest_ic_box.png":
        return ToolResult(
            text=(
                "[vlm_test-guard] Annotating/writing **`case10_largest_ic_box.png`** is forbidden "
                "when `workflow_mode=vlm_test` (no Part A red IC box on the physical photo). "
                "Use **`case10_board_landscape.png`** normalization only; **`step02_board_front_anchor.png`** "
                "must be a copy of **`case10_board_landscape.png`**."
            ),
            ok=False,
        )
    norm_phys_src = str(p.resolve()).replace("\\", "/").lower()
    if _is_vlm_test_workflow_mode() and _annotate_source_smells_physical_board_workflow(
        norm_phys_src
    ):
        for pt in points:
            bb = pt.get("bbox")
            if isinstance(bb, (list, tuple)) and len(bb) == 4:
                return ToolResult(
                    text=(
                        "[vlm_test-guard] `annotate_image` must **not** draw **`bbox`** rectangles "
                        "on **`case10_board_landscape.png`** / **`step02_board_front_anchor.png`** "
                        "(scripted IC localization). TP Step8 overlays use **`{x,y,color,radius,...}` "
                        "** dots only.**"
                    ),
                    ok=False,
                )

    norm_out = str(out).replace("\\", "/").lower()
    if "step04_locator_landmarks" in norm_out:
        blocked = _check_step3b_written(workspace)
        if blocked is not None:
            return blocked
        loc_roi_file = workspace / "debug" / "step03_locator_roi.png"
        if not loc_roi_file.is_file():
            return ToolResult(
                text=(
                    "[step4-landmarks] Save `debug/step03_locator_roi.png` (Step3A) "
                    "before `debug/step04_locator_landmarks.png`."
                ),
                ok=False,
            )
        norm_src = str(p.resolve()).replace("\\", "/").lower()
        if "step03_locator_roi" not in norm_src:
            return ToolResult(
                text=(
                    "[step4-landmarks] `path` must be **`debug/step03_locator_roi.png`** "
                    f"(Step4 works in locator ROI pixels only). You passed: {p}"
                ),
                ok=False,
            )
        bbox_specs = 0
        for pt in points:
            bb = pt.get("bbox")
            if isinstance(bb, (list, tuple)) and len(bb) == 4:
                bbox_specs += 1
        if bbox_specs < 1:
            return ToolResult(
                text=(
                    "[step4-landmarks] Provide at least one rectangle landmark: "
                    "`{bbox:[x1,y1,x2,y2], color:\"red\", width:4, label?:...}` in **locator ROI** "
                    "pixels (tight box around a **clear gray/black silkscreen part-outline** near the green TP)."
                ),
                ok=False,
            )
    if "step03_prior_on_board" in norm_out:
        blocked = _require_step3b_vlm_or_locator_roi(workspace, early_board_roi=False)
        if blocked is not None:
            return blocked
        blocked = _check_step4_locator_landmarks_present(workspace)
        if blocked is not None:
            return blocked
    # Load raster the same way OpenCV does everywhere else. PIL alone can apply EXIF
    # orientation so ``Image.open`` pixels disagree with ``cv2.imread`` (90° mismatch).
    try:
        import cv2
        bgr = cv2.imread(str(p))
        if bgr is not None:
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            img = Image.fromarray(rgb)
        else:
            with Image.open(p) as im:
                img = im.convert("RGB").copy()
    except Exception:  # noqa: BLE001
        with Image.open(p) as im:
            img = im.convert("RGB").copy()
    iw, ih = img.size
    draw = ImageDraw.Draw(img)
    step04_lm_out = "step04_locator_landmarks" in norm_out
    try:
        font = ImageFont.truetype("arial.ttf", size=max(14, radius))
    except Exception:  # noqa: BLE001
        font = ImageFont.load_default()
    drawn = 0
    rect_errors: list[str] = []
    for pt in points:
        bbox = pt.get("bbox")
        if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
            try:
                x1, y1, x2, y2 = (int(bbox[0]), int(bbox[1]),
                                  int(bbox[2]), int(bbox[3]))
            except (TypeError, ValueError):
                rect_errors.append(f"non-integer bbox {bbox!r}")
                continue
            if x2 <= x1 or y2 <= y1:
                rect_errors.append(f"invalid bbox order (need x2>x1, y2>y1): {bbox}")
                continue
            if x1 < 0 or y1 < 0 or x2 > iw or y2 > ih:
                rect_errors.append(
                    f"bbox {bbox} outside {iw}x{ih} image (use **ROI-only** coords: 0..W-1, 0..H-1)"
                )
                if step04_lm_out:
                    continue
            try:
                rw = int(pt.get("width", 4 if step04_lm_out else 3))
            except (TypeError, ValueError):
                rw = 4 if step04_lm_out else 3
            rw = max(1, min(20, rw))
            color = pt.get("color", "red")
            label = str(pt.get("label", ""))
            draw.rectangle([x1, y1, x2, y2], outline=color, width=rw)
            if label:
                ly = max(0, y1 - max(16, rw + 4))
                draw.text((x1 + 4, ly), label, fill=color, font=font)
            drawn += 1
            continue
        try:
            x, y = int(pt["x"]), int(pt["y"])
        except (KeyError, TypeError, ValueError):
            continue
        try:
            point_radius = int(pt.get("radius", radius))
        except (TypeError, ValueError):
            point_radius = int(radius)
        point_radius = max(1, min(200, point_radius))
        color = pt.get("color", "red")
        label = str(pt.get("label", ""))
        draw.ellipse((x - point_radius, y - point_radius, x + point_radius, y + point_radius),
                     outline=color, width=3)
        draw.line((x - point_radius - 6, y, x + point_radius + 6, y), fill=color, width=2)
        draw.line((x, y - point_radius - 6, x, y + point_radius + 6), fill=color, width=2)
        if label:
            draw.text((x + point_radius + 4, y - point_radius - 4), label, fill=color, font=font)
        drawn += 1
    if step04_lm_out and drawn == 0:
        detail = "; ".join(rect_errors) if rect_errors else "check bbox integers and x2>x1, y2>y1, inside image"
        return ToolResult(
            text=(
                f"[step4-landmarks] Drew 0 rectangles. {detail}. "
                "Use `[left, top, right, bottom]` in **step03_locator_roi** pixel space."
            ),
            ok=False,
        )
    if not write_debug_image(out, img):
        return ToolResult(
            text=f"[annotate-image] failed to write output image: {out}",
            ok=False,
        )
    if _is_stepb3_assembly_largest_ic_box_png(out):
        _stepb3_invalidate_view_gate(workspace)
    extra = f"\nNote: {'; '.join(rect_errors)}" if rect_errors else ""
    return ToolResult(
        text=f"Annotated {drawn} marker(s) from {len(points)} spec(s) on {p} → {out}{extra}",
        images=[str(out)],
    )


def _overlay_case10_vlm_approx_on_step57(
    img: Any,
    workspace: Path,
    roi_bbox: list[int],
    *,
    coords_are_roi: bool,
) -> None:
    """If ``case10_tp_dual_roi_direct_vlm.json`` exists, draw ``board_roi_target_px_approx`` on step57 (BGR)."""
    try:
        import cv2
    except Exception:  # noqa: BLE001
        return
    for base in (workspace, workspace / "workspace"):
        p = base / "debug" / "case10_tp_dual_roi_direct_vlm.json"
        if not p.is_file():
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            ap = d.get("board_roi_target_px_approx")
            if not isinstance(ap, list) or len(ap) != 2:
                return
            ax_f, ay_f = float(ap[0]), float(ap[1])
            if coords_are_roi:
                gx, gy = int(round(ax_f)), int(round(ay_f))
            else:
                gx = int(round(float(roi_bbox[0]) + ax_f))
                gy = int(round(float(roi_bbox[1]) + ay_f))
            h, w = img.shape[:2]
            gx = max(0, min(w - 1, gx))
            gy = max(0, min(h - 1, gy))
            color = (255, 0, 255)  # magenta — VLM approx on board ROI coords
            cv2.drawMarker(
                img,
                (gx, gy),
                color,
                markerType=cv2.MARKER_CROSS,
                markerSize=16,
                thickness=2,
            )
            cv2.circle(img, (gx, gy), 8, color, 1)
            cv2.putText(
                img,
                "VLM approx",
                (min(gx + 10, w - 120), max(gy - 10, 14)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.45,
                color,
                1,
                cv2.LINE_AA,
            )
        except Exception:
            pass
        return


def _tool_run_candidate_pipeline(workspace: Path,
                                 roi_path: str = "debug/step04_roi_crop.png",
                                 roi_bbox: list[int] | None = None,
                                 prior_board: list[float] | None = None,
                                 board_path: str | None = None) -> ToolResult:
    """Deterministic Step5/6/7 pipeline: detect, score, rank candidates."""
    blocked = _require_step3b_vlm_or_locator_roi(workspace, early_board_roi=False)
    if blocked is not None:
        return blocked
    if (workspace / "debug" / "step03_locator_roi.png").is_file():
        if _step03_mapping_obj(workspace) is None:
            return ToolResult(
                text=(
                    "[candidate-pipeline-gate] `debug/step03_mapping.json` must exist "
                    "before `run_candidate_pipeline` when Step3 locator ROI is in use."
                ),
                ok=False,
            )
    try:
        import cv2
        import numpy as np
        import math
    except Exception as e:  # noqa: BLE001
        return ToolResult(text=f"[candidate-pipeline] import error: {e}", ok=False)

    def _as_py(v: Any) -> Any:
        if hasattr(v, "item"):
            try:
                return v.item()
            except Exception:  # noqa: BLE001
                return v
        return v

    def _json_dump(path: Path, obj: Any) -> None:
        path.write_text(
            json.dumps(obj, ensure_ascii=False, indent=2, default=_as_py),
            encoding="utf-8",
        )

    def _load_img(path_like: str) -> Any:
        p = _resolve_read(path_like)
        img = cv2.imread(str(p))
        if img is not None:
            return img, p
        pil = Image.open(p).convert("RGB")
        arr = np.array(pil)
        return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR), p

    roi_img, roi_abs = _load_img(roi_path)
    rh, rw = roi_img.shape[:2]

    if board_path is None:
        board_path = _tl_input_paths().get("front_board_marked")
    board_img = None
    board_abs: Path | None = None
    if board_path:
        try:
            board_img, board_abs = _load_img(board_path)
        except Exception:
            board_img = None
            board_abs = None

    # Fallback prior from step03 mapping
    if prior_board is None:
        for mp in [workspace / "debug" / "step03_mapping.json",
                   workspace / "workspace" / "debug" / "step03_mapping.json"]:
            if mp.exists():
                try:
                    m = json.loads(mp.read_text(encoding="utf-8"))
                    pb = m.get("tp_prior_board")
                    if isinstance(pb, list) and len(pb) == 2:
                        prior_board = [float(pb[0]), float(pb[1])]
                        break
                except Exception:
                    pass

    # Infer ROI bbox if not provided
    if roi_bbox is None and prior_board is not None and board_img is not None:
        H, W = board_img.shape[:2]
        px, py = float(prior_board[0]), float(prior_board[1])
        x1 = int(round(px - rw / 2))
        y1 = int(round(py - rh / 2))
        x1 = max(0, min(x1, max(0, W - rw)))
        y1 = max(0, min(y1, max(0, H - rh)))
        roi_bbox = [x1, y1, x1 + rw, y1 + rh]
    elif roi_bbox is not None and len(roi_bbox) == 4:
        roi_bbox = [int(v) for v in roi_bbox]
    else:
        roi_bbox = [0, 0, rw, rh]

    # Prior in ROI coordinates for distance scoring
    prior_roi_x, prior_roi_y = rw / 2.0, rh / 2.0
    if prior_board is not None:
        prior_roi_x = float(prior_board[0]) - float(roi_bbox[0])
        prior_roi_y = float(prior_board[1]) - float(roi_bbox[1])

    gray = cv2.cvtColor(roi_img, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(roi_img, cv2.COLOR_BGR2HSV)
    diag = max(1.0, float((rw ** 2 + rh ** 2) ** 0.5))

    def _extract_from_contours(contours: Any, method: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for cnt in contours:
            area = float(cv2.contourArea(cnt))
            if area < 8 or area > 500:
                continue
            peri = float(cv2.arcLength(cnt, True))
            if peri <= 0:
                continue
            circ = float(4.0 * math.pi * area / (peri * peri))
            if circ < 0.25:
                continue
            m = cv2.moments(cnt)
            if m["m00"] == 0:
                continue
            cx = float(m["m10"] / m["m00"])
            cy = float(m["m01"] / m["m00"])
            if cx < 1 or cy < 1 or cx > rw - 2 or cy > rh - 2:
                continue
            out.append({
                "cx": cx,
                "cy": cy,
                "area": area,
                "circularity": circ,
                "detector": method,
            })
        return out

    raw: list[dict[str, Any]] = []
    # Detector A: adaptive threshold + contours
    th = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 11, 2
    )
    contours_a, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    raw.extend(_extract_from_contours(contours_a, "adaptive"))

    # Detector B: canny + contours
    edges = cv2.Canny(gray, 50, 150)
    contours_b, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    raw.extend(_extract_from_contours(contours_b, "canny"))

    # Detector C: bright-metal HSV + contours (strict)
    mask_metal = cv2.inRange(hsv, np.array([0, 0, 150]), np.array([180, 110, 255]))
    contours_c, _ = cv2.findContours(mask_metal, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    raw.extend(_extract_from_contours(contours_c, "hsv_metal"))

    # Detector D: bright-metal HSV (loose) for non-uniform color pads
    # Keep it morphology-free to avoid eroding weak ring edges.
    mask_metal_loose = cv2.inRange(hsv, np.array([0, 0, 120]), np.array([180, 170, 255]))
    contours_d, _ = cv2.findContours(mask_metal_loose, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    raw.extend(_extract_from_contours(contours_d, "hsv_metal_loose"))

    # Merge near-duplicate centers (<= 4 px)
    merged: list[dict[str, Any]] = []
    for r in raw:
        matched = None
        for m in merged:
            if ((m["cx"] - r["cx"]) ** 2 + (m["cy"] - r["cy"]) ** 2) ** 0.5 <= 4.0:
                matched = m
                break
        if matched is None:
            merged.append({
                "cx": r["cx"],
                "cy": r["cy"],
                "areas": [r["area"]],
                "circularities": [r["circularity"]],
                "detectors": [r["detector"]],
            })
        else:
            matched["cx"] = (matched["cx"] + r["cx"]) / 2.0
            matched["cy"] = (matched["cy"] + r["cy"]) / 2.0
            matched["areas"].append(r["area"])
            matched["circularities"].append(r["circularity"])
            if r["detector"] not in matched["detectors"]:
                matched["detectors"].append(r["detector"])

    # Candidate gates (not scoring): round-enough + metal-pad-like rim contrast (Sobel magnitude
    # on an annulus around the pad). Excludes blobs with weak mask/edge transitions.
    gx_sobel = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy_sobel = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    grad_mag_img = cv2.magnitude(gx_sobel, gy_sobel)

    def _rim_mean_gradient(cx_f: float, cy_f: float, area_f: float) -> float:
        if area_f <= 1.0:
            return 0.0
        r = max(1.5, float(math.sqrt(area_f / math.pi)))
        ri = max(1.0, r * 0.52)
        ro = min(float(max(rw, rh)) * 0.55, r * 1.48)
        if ro < ri + 1.0:
            ro = ri + 2.0
        Hg, Wg = grad_mag_img.shape[:2]
        y0 = int(max(0, math.floor(cy_f - ro - 2)))
        y1 = int(min(Hg, math.ceil(cy_f + ro + 2)))
        x0 = int(max(0, math.floor(cx_f - ro - 2)))
        x1 = int(min(Wg, math.ceil(cx_f + ro + 2)))
        if y1 <= y0 or x1 <= x0:
            return 0.0
        patch = grad_mag_img[y0:y1, x0:x1]
        yy, xx = np.mgrid[y0:y1, x0:x1].astype(np.float64)
        dist = np.sqrt((xx - cx_f) ** 2 + (yy - cy_f) ** 2)
        ring = (dist >= ri) & (dist <= ro)
        if not np.any(ring):
            return 0.0
        return float(np.mean(patch[ring]))

    _CIRC_MIN_CAND = 0.46
    _RIM_ABS_FLOOR = 4.5
    _RIM_REL_TO_MEDIAN = 0.40

    rim_eval: list[tuple[dict[str, Any], float, float, float]] = []
    for m in merged:
        area_a = float(sum(m["areas"]) / max(1, len(m["areas"])))
        circ_a = float(sum(m["circularities"]) / max(1, len(m["circularities"])))
        cx_a, cy_a = float(m["cx"]), float(m["cy"])
        rim_a = _rim_mean_gradient(cx_a, cy_a, area_a)
        rim_eval.append((m, area_a, circ_a, rim_a))

    merged_pass: list[dict[str, Any]] = []
    if len(rim_eval) >= 3:
        med_rim = float(np.median(np.array([t[3] for t in rim_eval], dtype=np.float64)))
        thr = max(_RIM_ABS_FLOOR, _RIM_REL_TO_MEDIAN * med_rim)
        for m, _area_a, circ_a, rim_a in rim_eval:
            if circ_a >= _CIRC_MIN_CAND and rim_a >= thr:
                merged_pass.append(m)
    elif len(rim_eval) >= 1:
        rims_1 = [t[3] for t in rim_eval]
        med_rim = float(np.median(np.array(rims_1, dtype=np.float64)))
        thr = max(_RIM_ABS_FLOOR * 0.72, _RIM_REL_TO_MEDIAN * med_rim)
        for m, _area_a, circ_a, rim_a in rim_eval:
            if circ_a >= _CIRC_MIN_CAND and rim_a >= thr:
                merged_pass.append(m)
    if len(merged_pass) == 0 and len(rim_eval) > 0:
        merged = [t[0] for t in rim_eval]
    else:
        merged = merged_pass

    candidates: list[dict[str, Any]] = []
    for i, m in enumerate(merged):
        cx = float(m["cx"])
        cy = float(m["cy"])
        area = float(sum(m["areas"]) / max(1, len(m["areas"])))
        circ = float(sum(m["circularities"]) / max(1, len(m["circularities"])))
        gx = float(roi_bbox[0] + cx)
        gy = float(roi_bbox[1] + cy)
        r_eq = float((area / math.pi) ** 0.5)
        r_vis = int(max(2, min(12, round(1.2 * r_eq))))
        dist = float(((cx - prior_roi_x) ** 2 + (cy - prior_roi_y) ** 2) ** 0.5)
        support = min(1.0, len(m["detectors"]) / 3.0)
        rim_mean = round(float(_rim_mean_gradient(cx, cy, area)), 4)
        score_local = (
            0.55 * max(0.0, 1.0 - dist / diag)
            + 0.35 * min(1.0, max(0.0, circ))
            + 0.10 * support
        )
        candidates.append({
            "id": int(i),
            "cx": round(cx, 2),
            "cy": round(cy, 2),
            "gx": round(gx, 2),
            "gy": round(gy, 2),
            "area": round(area, 2),
            "circularity": round(circ, 4),
            "detectors": m["detectors"],
            "r_vis": int(r_vis),
            "rim_grad_mean": rim_mean,
            "score_local_feature": round(float(score_local), 4),
        })

    # Step6 semantic score
    for c in candidates:
        area = float(c["area"])
        circ = float(c["circularity"])
        r_c = float(max(1.0, c.get("r_vis", 2)))
        near = 0
        for o in candidates:
            if o["id"] == c["id"]:
                continue
            d = ((float(o["cx"]) - float(c["cx"])) ** 2 + (float(o["cy"]) - float(c["cy"])) ** 2) ** 0.5
            # Adaptive neighborhood: candidate size aware + small margin.
            # This avoids a fixed pixel threshold across different pad scales.
            r_o = float(max(1.0, o.get("r_vis", 2)))
            near_thresh = max(8.0, min(36.0, r_c + r_o + 6.0))
            if d < near_thresh:
                near += 1
        area_term = max(0.0, 1.0 - abs(area - 25.0) / 35.0)
        circ_term = max(0.0, min(1.0, circ))
        iso_term = max(0.0, 1.0 - min(near, 6) / 6.0)
        sem = 0.45 * circ_term + 0.35 * area_term + 0.20 * iso_term
        c["score_semantic"] = round(float(sem), 4)
        c["semantic_terms"] = {
            "circularity": round(circ_term, 4),
            "area": round(area_term, 4),
            "isolation": round(iso_term, 4),
        }

    # Step7 geometry + final
    for c in candidates:
        d_board = ((float(c["gx"]) - float(prior_board[0] if prior_board else c["gx"])) ** 2
                   + (float(c["gy"]) - float(prior_board[1] if prior_board else c["gy"])) ** 2) ** 0.5
        geo = max(0.0, 1.0 - d_board / max(diag, 1.0))
        c["score_geometry"] = round(float(geo), 4)
        # Slightly increase prior-distance influence in final ranking.
        final = 0.40 * float(c["score_local_feature"]) + 0.30 * float(c["score_semantic"]) + 0.30 * geo
        c["final_score"] = round(float(final), 4)

    candidates.sort(key=lambda x: float(x["final_score"]), reverse=True)
    for idx, c in enumerate(candidates, start=1):
        c["rank"] = idx

    out_dir = _resolve_write(workspace, "debug")
    # Persist JSON artifacts
    _json_dump(out_dir / "step05_scores.json", [
        {
            "id": c["id"], "cx": c["cx"], "cy": c["cy"], "gx": c["gx"], "gy": c["gy"],
            "area": c["area"], "circularity": c["circularity"], "detectors": c["detectors"],
            "r_vis": c["r_vis"], "rim_grad_mean": c.get("rim_grad_mean"),
            "score_local_feature": c["score_local_feature"],
        }
        for c in candidates
    ])
    _json_dump(out_dir / "step06_scores.json", [
        {
            "id": c["id"], "r_vis": c["r_vis"], "score_semantic": c["score_semantic"],
            "semantic_terms": c["semantic_terms"],
        }
        for c in candidates
    ])
    _json_dump(out_dir / "step07_scores.json", [
        {
            "id": c["id"], "score_geometry": c["score_geometry"],
            "final_score": c["final_score"], "rank": c["rank"],
        }
        for c in candidates
    ])
    # Canonical candidate file for downstream Step8 checks.
    _json_dump(out_dir / "step05_candidates.json", candidates)

    # Debug images
    step05_img = roi_img.copy()
    for c in candidates:
        cx, cy, r = int(round(c["cx"])), int(round(c["cy"])), int(c["r_vis"])
        cv2.circle(step05_img, (cx, cy), r, (0, 255, 0), 1)
        cv2.putText(step05_img, f"{c['id']}:{c['score_local_feature']:.2f}", (cx + 3, cy - 3),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 255, 0), 1)
    step05_path = out_dir / "step05_local_feature_scores.png"
    write_debug_image(step05_path, step05_img)

    step06_img = roi_img.copy()
    for c in candidates:
        cx, cy = int(round(c["cx"])), int(round(c["cy"]))
        color = (0, 255, 255) if float(c["score_semantic"]) >= 0.5 else (0, 128, 255)
        cv2.circle(step06_img, (cx, cy), max(2, int(c["r_vis"])), color, 1)
        cv2.putText(step06_img, f"{c['id']}:{c['score_semantic']:.2f}", (cx + 3, cy - 3),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.35, color, 1)
    step06_path = out_dir / "step06_semantic_scores.png"
    write_debug_image(step06_path, step06_img)

    if board_img is not None:
        step57_img = board_img.copy()
        for c in candidates:
            gx, gy = int(round(c["gx"])), int(round(c["gy"]))
            cv2.circle(step57_img, (gx, gy), max(2, int(c["r_vis"])), (0, 0, 255), 1)
            cv2.putText(step57_img, f"#{c['rank']}", (gx + 4, gy - 4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 255), 1)
        _overlay_case10_vlm_approx_on_step57(
            step57_img, workspace, roi_bbox, coords_are_roi=False
        )
    else:
        step57_img = roi_img.copy()
        for c in candidates:
            cx, cy = int(round(c["cx"])), int(round(c["cy"]))
            cv2.circle(step57_img, (cx, cy), max(2, int(c["r_vis"])), (0, 0, 255), 1)
            cv2.putText(step57_img, f"#{c['rank']}", (cx + 4, cy - 4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 255), 1)
        _overlay_case10_vlm_approx_on_step57(
            step57_img, workspace, roi_bbox, coords_are_roi=True
        )
    step57_path = out_dir / "step57_candidates_scored.png"
    write_debug_image(step57_path, step57_img)

    top = candidates[0] if candidates else None
    top_text = "none"
    if top is not None:
        top_text = (
            f"id={top['id']} gx={top['gx']} gy={top['gy']} "
            f"r_vis={top['r_vis']} final_score={top['final_score']}"
        )
    return ToolResult(
        text=(
            f"Step5/6/7 pipeline completed on ROI {roi_abs}.\n"
            f"Candidates detected: {len(candidates)}\n"
            f"Top candidate: {top_text}\n"
            f"Saved:\n"
            f"- debug/step05_candidates.json\n"
            f"- debug/step05_scores.json\n"
            f"- debug/step06_scores.json\n"
            f"- debug/step07_scores.json\n"
            f"- debug/step05_local_feature_scores.png\n"
            f"- debug/step06_semantic_scores.png\n"
            f"- debug/step57_candidates_scored.png"
        ),
        images=[str(step05_path), str(step06_path), str(step57_path)],
    )


def _tool_run_step3_mapping(
    workspace: Path,
    locator_path: str | None = None,
    board_path: str | None = None,
    out_json_path: str = "debug/step03_mapping.json",
    out_image_path: str = "debug/step03_prior_on_board.png",
) -> ToolResult:
    """Deterministic Step3 mapping: red-box anchor + green TP center + scalar mapping."""
    try:
        import cv2
        import numpy as np
        import math
    except Exception as e:  # noqa: BLE001
        return ToolResult(text=f"[step3-mapping] import error: {e}", ok=False)

    def _load_img(path_like: str) -> tuple[Any, Path]:
        p = _resolve_read(path_like)
        img = cv2.imread(str(p))
        if img is not None:
            return img, p
        # Unicode/Windows fallback
        pil = Image.open(p).convert("RGB")
        arr = np.array(pil)
        return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR), p

    def _largest_box_from_red(img_bgr: Any) -> list[int]:
        hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
        mask1 = cv2.inRange(hsv, np.array([0, 100, 100]), np.array([10, 255, 255]))
        mask2 = cv2.inRange(hsv, np.array([170, 100, 100]), np.array([180, 255, 255]))
        mask = cv2.bitwise_or(mask1, mask2)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            raise ValueError("red anchor contour not found")
        cnt = max(contours, key=cv2.contourArea)
        x, y, w, h = cv2.boundingRect(cnt)
        return [int(x), int(y), int(x + w), int(y + h)]

    def _largest_center_from_green(img_bgr: Any) -> list[float]:
        hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, np.array([40, 100, 100]), np.array([80, 255, 255]))
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            raise ValueError("green TP contour not found")
        cnt = max(contours, key=cv2.contourArea)
        m = cv2.moments(cnt)
        if float(m["m00"]) == 0.0:
            raise ValueError("green TP contour has zero m00")
        gx = float(m["m10"] / m["m00"])
        gy = float(m["m01"] / m["m00"])
        return [gx, gy]

    try:
        if locator_path is None:
            locator_path = _tl_input_paths().get("front_locator_marked")
        if board_path is None:
            board_path = _tl_input_paths().get("front_board_marked")
        if not locator_path or not board_path:
            return ToolResult(
                text=(
                    "[step3-mapping] locator_path/board_path missing. "
                    "Provide them explicitly or ensure INPUT_PATHS has "
                    "`front_locator_marked` and `front_board_marked`."
                ),
                ok=False,
            )

        locator_img, locator_abs = _load_img(locator_path)
        board_img, board_abs = _load_img(board_path)

        locator_box = _largest_box_from_red(locator_img)
        board_box = _largest_box_from_red(board_img)
        tp_locator_center = _largest_center_from_green(locator_img)

        lx1, ly1, lx2, ly2 = [float(v) for v in locator_box]
        bx1, by1, bx2, by2 = [float(v) for v in board_box]
        gx, gy = float(tp_locator_center[0]), float(tp_locator_center[1])
        lw = lx2 - lx1
        lh = ly2 - ly1
        bw = bx2 - bx1
        bh = by2 - by1
        if lw <= 0 or lh <= 0 or bw <= 0 or bh <= 0:
            return ToolResult(
                text="[step3-mapping] invalid anchor box dimensions (x2>x1 and y2>y1 required).",
                ok=False,
            )

        # Scalar mapping (canonical): u/v may be outside [0,1].
        u = (gx - lx1) / lw
        v = (gy - ly1) / lh
        px = bx1 + u * bw
        py = by1 + v * bh

        if not all(math.isfinite(x) for x in [u, v, px, py]):
            return ToolResult(
                text="[step3-mapping] non-finite mapping values produced.",
                ok=False,
            )

        # Step3 self-check: recompute from u/v and compare.
        px_chk = bx1 + u * bw
        py_chk = by1 + v * bh
        err = float(math.hypot(px_chk - px, py_chk - py))

        mapping = {
            "locator_box": [round(x, 2) for x in [lx1, ly1, lx2, ly2]],
            "board_box": [round(x, 2) for x in [bx1, by1, bx2, by2]],
            "tp_locator_center": [round(gx, 2), round(gy, 2)],
            "u": float(u),
            "v": float(v),
            "tp_prior_board": [float(px), float(py)],
            "self_check": {
                "reprojected_from_uv": [float(px_chk), float(py_chk)],
                "reproject_error_px": round(err, 6),
            },
        }

        out_json = _resolve_write(workspace, out_json_path)
        out_json.write_text(
            json.dumps(mapping, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        out_img = _resolve_write(workspace, out_image_path)
        vis = board_img.copy()
        pxi, pyi = int(round(px)), int(round(py))
        cv2.circle(vis, (pxi, pyi), 10, (0, 0, 255), 2)
        cv2.line(vis, (pxi - 12, pyi), (pxi + 12, pyi), (0, 0, 255), 2)
        cv2.line(vis, (pxi, pyi - 12), (pxi, pyi + 12), (0, 0, 255), 2)
        cv2.putText(
            vis,
            f"prior ({pxi},{pyi})",
            (pxi + 8, pyi - 8),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (0, 0, 255),
            1,
        )
        write_debug_image(out_img, vis)

        return ToolResult(
            text=(
                f"Step3 mapping completed.\n"
                f"locator={locator_abs}\n"
                f"board={board_abs}\n"
                f"Locator box: {locator_box}\n"
                f"Board box: {board_box}\n"
                f"TP locator center: {[round(gx, 2), round(gy, 2)]}\n"
                f"u, v: {u:.6f}, {v:.6f}\n"
                f"TP prior board: {[round(px, 2), round(py, 2)]}\n"
                f"Saved:\n- {out_json}\n- {out_img}"
            ),
            images=[str(out_img)],
        )
    except Exception as e:  # noqa: BLE001
        return ToolResult(text=f"[step3-mapping] failed: {e}", ok=False)


def _tool_match_green_tp_roi_to_board(
    workspace: Path,
    locator_path: str | None = None,
    board_path: str | None = None,
    margin_px: int = 80,
    search_max_dim: int = 1200,
    min_match_score: float = 0.2,
    scale_steps: int = 26,
    out_template_path: str = "debug/step03_tp_roi_template.png",
    out_debug_path: str = "debug/step03_tp_roi_match_debug.png",
    out_json_path: str = "debug/step03_mapping.json",
    out_image_path: str = "debug/step03_prior_on_board.png",
) -> ToolResult:
    """Crop neighbourhood around green TP on locator, inpaint marker, multi-scale match on board.

    Writes ``step03_mapping.json`` with the same keys as ``run_step3_mapping`` so downstream
    steps and finish-validation work: boxes are the locator ROI and the matched patch on
    the board; u/v are relative coords of the green center inside the locator ROI.
    """
    try:
        import cv2
        import numpy as np
        import math
    except Exception as e:  # noqa: BLE001
        return ToolResult(text=f"[tp-roi-match] import error: {e}", ok=False)

    def _load_img(path_like: str) -> tuple[Any, Path]:
        p = _resolve_read(path_like)
        img = cv2.imread(str(p))
        if img is not None:
            return img, p
        pil = Image.open(p).convert("RGB")
        arr = np.array(pil)
        return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR), p

    try:
        if locator_path is None:
            locator_path = "debug/step01_locator_front_anchor.png"
        if board_path is None:
            board_path = _tl_input_paths().get("front_board_photo")
        if not board_path:
            return ToolResult(
                text=(
                    "[tp-roi-match] board_path missing. "
                    "Pass `board_path` or set INPUT_PATHS `front_board_photo`."
                ),
                ok=False,
            )

        loc_img, loc_abs = _load_img(locator_path)
        board_img, board_abs = _load_img(board_path)
        lh, lw = loc_img.shape[:2]
        bh, bw = board_img.shape[:2]

        hsv = cv2.cvtColor(loc_img, cv2.COLOR_BGR2HSV)
        gmask = cv2.inRange(hsv, np.array([40, 100, 100]), np.array([80, 255, 255]))
        contours, _ = cv2.findContours(gmask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return ToolResult(text="[tp-roi-match] green TP circle not found on locator.", ok=False)
        cnt = max(contours, key=cv2.contourArea)
        m = cv2.moments(cnt)
        if float(m["m00"]) <= 0.0:
            return ToolResult(text="[tp-roi-match] invalid green contour.", ok=False)
        gx = float(m["m10"] / m["m00"])
        gy = float(m["m01"] / m["m00"])
        x, y, wc, hc = cv2.boundingRect(cnt)

        mpx = max(8, int(margin_px))
        x1 = max(0, x - mpx)
        y1 = max(0, y - mpx)
        x2 = min(lw, x + wc + mpx)
        y2 = min(lh, y + hc + mpx)
        if x2 <= x1 + 4:
            x2 = min(lw, x1 + 32)
        if y2 <= y1 + 4:
            y2 = min(lh, y1 + 32)

        roi_bgr = loc_img[y1:y2, x1:x2].copy()
        mask_roi = gmask[y1:y2, x1:x2]
        mask_u8 = np.where(mask_roi > 0, np.uint8(255), np.uint8(0))
        roi_clean = cv2.inpaint(roi_bgr, mask_u8, 7, cv2.INPAINT_TELEA)
        tpl_gray = cv2.cvtColor(roi_clean, cv2.COLOR_BGR2GRAY)

        th0, tw0 = tpl_gray.shape[:2]

        sm = min(1.0, float(search_max_dim) / float(max(bh, bw)))
        if sm < 1.0:
            board_s = cv2.resize(
                board_img,
                (int(round(bw * sm)), int(round(bh * sm))),
                interpolation=cv2.INTER_AREA,
            )
        else:
            board_s = board_img
        sh, sw = board_s.shape[:2]
        board_g = cv2.cvtColor(board_s, cv2.COLOR_BGR2GRAY)

        scales = np.linspace(0.22, 2.85, int(scale_steps), dtype=np.float64)
        best_val = -2.0
        best_loc: tuple[int, int] = (0, 0)
        best_tw = 16
        best_th = 16
        best_sc = 1.0

        for sc in scales:
            tw = max(16, min(int(round(tw0 * sc)), sw - 2))
            th = max(16, min(int(round(th0 * sc)), sh - 2))
            if tw < 16 or th < 16 or tw >= sw or th >= sh:
                continue
            t = cv2.resize(tpl_gray, (tw, th), interpolation=cv2.INTER_AREA)
            if t.shape[0] > board_g.shape[0] or t.shape[1] > board_g.shape[1]:
                continue
            res = cv2.matchTemplate(board_g, t, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(res)
            if float(max_val) > best_val:
                best_val = float(max_val)
                best_loc = (int(max_loc[0]), int(max_loc[1]))
                best_tw, best_th = tw, th
                best_sc = float(sc)

        if best_val < float(min_match_score):
            return ToolResult(
                text=(
                    f"[tp-roi-match] best normalized score {best_val:.4f} "
                    f"below min_match_score={min_match_score}. "
                    "Try different lighting, margin_px, or use run_step3_mapping."
                ),
                ok=False,
            )

        sx = float(bw) / float(sw)
        sy = float(bh) / float(sh)
        TLx_f = int(round(best_loc[0] * sx))
        TLy_f = int(round(best_loc[1] * sy))
        Tw_f = max(8, int(round(best_tw * sx)))
        Th_f = max(8, int(round(best_th * sy)))
        bx2 = min(bw - 1, TLx_f + Tw_f)
        by2 = min(bh - 1, TLy_f + Th_f)
        lx1f, ly1f, lx2f, ly2f = float(x1), float(y1), float(x2), float(y2)
        bw_box = bx2 - TLx_f
        bh_box = by2 - TLy_f
        lw_box = lx2f - lx1f
        lh_box = ly2f - ly1f
        if lw_box <= 1e-6 or lh_box <= 1e-6 or bw_box <= 0 or bh_box <= 0:
            return ToolResult(text="[tp-roi-match] degenerate ROI or board patch.", ok=False)

        u = (gx - lx1f) / lw_box
        v = (gy - ly1f) / lh_box
        px = float(TLx_f) + u * float(bw_box)
        py = float(TLy_f) + v * float(bh_box)
        if not all(math.isfinite(t) for t in (u, v, px, py)):
            return ToolResult(text="[tp-roi-match] non-finite mapping.", ok=False)

        mapping = {
            "mapping_method": "green_roi_template_match",
            "match_score_normed": round(best_val, 6),
            "template_scale": best_sc,
            "locator_path": str(loc_abs),
            "board_path": str(board_abs),
            "locator_roi": [int(x1), int(y1), int(x2), int(y2)],
            "locator_box": [float(lx1f), float(ly1f), float(lx2f), float(ly2f)],
            "board_box": [float(TLx_f), float(TLy_f), float(bx2), float(by2)],
            "tp_locator_center": [round(gx, 2), round(gy, 2)],
            "u": float(u),
            "v": float(v),
            "tp_prior_board": [float(px), float(py)],
            "self_check": {
                "formula_px": [float(TLx_f + u * bw_box), float(TLy_f + v * bh_box)],
                "reproject_error_px": round(
                    math.hypot(
                        (TLx_f + u * bw_box) - px,
                        (TLy_f + v * bh_box) - py,
                    ),
                    6,
                ),
            },
        }

        def _imwrite_bgr(path: Path, img: Any) -> None:
            if write_debug_image(path, img):
                return
            raise OSError(f"write_debug_image failed for {path}")

        out_tpl = _resolve_write(workspace, out_template_path)
        _imwrite_bgr(out_tpl, roi_clean)

        out_json = _resolve_write(workspace, out_json_path)
        out_json.write_text(
            json.dumps(mapping, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        out_img = _resolve_write(workspace, out_image_path)
        vis = board_img.copy()
        pxi, pyi = int(round(px)), int(round(py))
        cv2.rectangle(vis, (TLx_f, TLy_f), (bx2, by2), (255, 0, 0), 2)
        cv2.circle(vis, (pxi, pyi), 10, (0, 0, 255), 2)
        cv2.line(vis, (pxi - 14, pyi), (pxi + 14, pyi), (0, 0, 255), 2)
        cv2.line(vis, (pxi, pyi - 14), (pxi, pyi + 14), (0, 0, 255), 2)
        cv2.putText(
            vis,
            f"prior ({pxi},{pyi})",
            (pxi + 10, pyi - 10),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (0, 0, 255),
            2,
        )
        _imwrite_bgr(out_img, vis)

        dbg = _resolve_write(workspace, out_debug_path)
        dbg_vis = board_img.copy()
        cv2.rectangle(dbg_vis, (TLx_f, TLy_f), (bx2, by2), (0, 255, 255), 2)
        cv2.putText(
            dbg_vis,
            f"match score={best_val:.3f} scale={best_sc:.3f}",
            (TLx_f, max(20, TLy_f - 8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (0, 255, 255),
            2,
        )
        _imwrite_bgr(dbg, dbg_vis)

        return ToolResult(
            text=(
                f"Green TP ROI template match completed.\n"
                f"locator={loc_abs}\n"
                f"board={board_abs}\n"
                f"Locator ROI (also locator_box): [{x1},{y1},{x2},{y2}]\n"
                f"Board matched patch: [{TLx_f},{TLy_f},{bx2},{by2}]\n"
                f"TP center on locator: {[round(gx, 2), round(gy, 2)]}\n"
                f"Best TM_CCOEFF_NORMED score: {best_val:.4f} (scale≈{best_sc:.3f})\n"
                f"TP prior on board: {[round(px, 2), round(py, 2)]}\n"
                f"u,v: {u:.5f}, {v:.5f}\n"
                f"Saved:\n- {out_tpl}\n- {dbg}\n- {out_json}\n- {out_img}"
            ),
            images=[str(out_tpl), str(dbg), str(out_img)],
        )
    except Exception as e:  # noqa: BLE001
        return ToolResult(text=f"[tp-roi-match] failed: {e}", ok=False)


# --------------------------------------------------------------------------- #
# Code execution tools
# --------------------------------------------------------------------------- #

def _tool_run_python(workspace: Path, code: str, timeout: int = 30) -> ToolResult:
    """Execute Python inside the current process, capturing stdout/stderr.

    Running in-process keeps PIL/cv2/numpy imports warm and lets the agent
    keep state between calls via `_PY_GLOBALS`. Cwd is switched to the
    workspace for the duration of the call so that relative paths in
    model-generated code match what `crop_image` / `save_text_file` etc.
    write. The `timeout` argument is accepted for future sandboxing but
    currently informational.
    """
    if re.search(r"cv2\s*\.\s*transform\s*\(", code):
        return ToolResult(
            text=(
                "[python-guard] Forbidden API detected: cv2.transform().\n"
                "Step3 mapping must use scalar coordinate formulas instead:\n"
                "u=(gx-lx1)/(lx2-lx1), v=(gy-ly1)/(ly2-ly1), "
                "px=bx1+u*(bx2-bx1), py=by1+v*(by2-by1)."
            ),
            ok=False,
        )

    if _is_vlm_test_workflow_mode():
        # Must not reference legacy OpenCV red-IC-board align (`..._bbox_vlm` is allowed).
        if re.search(r"\brun_align_locator_graph_to_board_ic_bbox\b", code):
            return ToolResult(
                text=(
                    "[vlm_test-guard] `run_align_locator_graph_to_board_ic_bbox` (OpenCV HSV IC box on board) "
                    "is forbidden when `workflow_mode=vlm_test`. "
                    "Save **`debug/case12_board_largest_ic_bbox_vlm.json`** then call "
                    "**`run_align_locator_graph_to_board_ic_bbox_vlm`** "
                    "(aligned JSON **`source`** = `vlm_ic_correspondence_isotropic_align`)."
                ),
                ok=False,
            )

    blocked_phys = _vlm_test_run_python_guard_physical_board_ic_geometry(code)
    if blocked_phys is not None:
        return blocked_phys

    buf_out, buf_err = io.StringIO(), io.StringIO()
    _PY_GLOBALS["WORKSPACE"] = workspace
    _PY_GLOBALS["PROJECT_ROOT"] = _tl_project_root()
    _PY_GLOBALS["INPUT_PATHS"] = dict(_tl_input_paths())
    _PY_GLOBALS["normalize_path"] = _normalize_runtime_path
    _PY_GLOBALS["resolve_read"] = lambda p: str(_resolve_read(_normalize_runtime_path(p)))
    prev_cwd = os.getcwd()
    prev_env_workspace = os.environ.get("WORKSPACE")
    prev_env_project_root = os.environ.get("PROJECT_ROOT")
    # Temporary monkey-patches to make model-generated code robust on Windows
    # (unicode paths, relative roots, workspace/ prefix confusion).
    orig_exists = os.path.exists
    orig_open = builtins.open
    pil_open = Image.open
    cv2_mod = _PY_GLOBALS.get("cv2")
    cv2_imread = getattr(cv2_mod, "imread", None) if cv2_mod is not None else None
    orig_cv2_imwrite = getattr(cv2_mod, "imwrite", None) if cv2_mod is not None else None

    def _guarded_cv2_imwrite(filename: Any, img: Any, *args: Any, **kwargs: Any):
        if orig_cv2_imwrite is None:
            return None
        bn = Path(str(filename)).name.lower()
        if bn == "case10_largest_ic_box.png":
            raise RuntimeError(
                "[vlm_test-guard] Cannot write `case10_largest_ic_box.png` "
                "(`workflow_mode=vlm_test` skips Part A board IC red box)."
            )
        return orig_cv2_imwrite(filename, img, *args, **kwargs)

    _vlm_cv2_imwrite_guard = False

    def _patched_exists(path_like: Any) -> bool:
        try:
            rp = _resolve_read(_normalize_runtime_path(path_like))
            return rp.exists()
        except Exception:
            return orig_exists(path_like)

    def _patched_open(file: Any, *args: Any, **kwargs: Any):
        if isinstance(file, (str, os.PathLike)):
            try:
                return orig_open(_resolve_read(_normalize_runtime_path(file)), *args, **kwargs)
            except Exception:
                return orig_open(_normalize_runtime_path(file), *args, **kwargs)
        return orig_open(file, *args, **kwargs)

    def _patched_pil_open(fp: Any, *args: Any, **kwargs: Any):
        if isinstance(fp, (str, os.PathLike)):
            try:
                return pil_open(_resolve_read(_normalize_runtime_path(fp)), *args, **kwargs)
            except Exception:
                return pil_open(_normalize_runtime_path(fp), *args, **kwargs)
        return pil_open(fp, *args, **kwargs)

    def _patched_cv2_imread(filename: Any, flags: int = 1):
        # Handle unicode/relative paths via np.fromfile + imdecode.
        if not isinstance(filename, (str, os.PathLike)):
            return cv2_imread(filename, flags) if cv2_imread is not None else None
        filename = _normalize_runtime_path(filename)
        resolved: Path | None = None
        try:
            resolved = _resolve_read(filename)
        except Exception:
            resolved = Path(filename)
        if cv2_mod is None:
            return None
        try:
            arr = _PY_GLOBALS["np"].fromfile(str(resolved), dtype=_PY_GLOBALS["np"].uint8)
            if arr.size > 0:
                img = cv2_mod.imdecode(arr, flags)
                if img is not None:
                    return img
        except Exception:
            pass
        if cv2_imread is not None:
            return cv2_imread(str(resolved), flags)
        return None

    try:
        os.chdir(str(workspace))
        os.environ["WORKSPACE"] = str(workspace)
        os.environ["PROJECT_ROOT"] = str(_tl_project_root())
        os.path.exists = _patched_exists
        builtins.open = _patched_open
        Image.open = _patched_pil_open
        if cv2_mod is not None and cv2_imread is not None:
            cv2_mod.imread = _patched_cv2_imread
            if orig_cv2_imwrite is not None and _is_vlm_test_workflow_mode():
                cv2_mod.imwrite = _guarded_cv2_imwrite  # type: ignore[assignment]
                _vlm_cv2_imwrite_guard = True
        with redirect_stdout(buf_out), redirect_stderr(buf_err):
            exec(compile(code, "<agent>", "exec"), _PY_GLOBALS)  # noqa: S102
        out = buf_out.getvalue()
        err = buf_err.getvalue()
        body = ""
        if out:
            body += "--- stdout ---\n" + truncate(out, 4000) + "\n"
        if err:
            body += "--- stderr ---\n" + truncate(err, 2000) + "\n"
        if not body:
            body = "(no output)"
        return ToolResult(text=body)
    except Exception as e:  # noqa: BLE001
        import traceback as tb_mod
        return ToolResult(
            text=f"[python-error] {type(e).__name__}: {e}\n"
                 + tb_mod.format_exc(limit=5)
                 + ("\n--- stdout ---\n" + buf_out.getvalue() if buf_out.getvalue() else ""),
            ok=False,
        )
    finally:
        if prev_env_workspace is None:
            os.environ.pop("WORKSPACE", None)
        else:
            os.environ["WORKSPACE"] = prev_env_workspace
        if prev_env_project_root is None:
            os.environ.pop("PROJECT_ROOT", None)
        else:
            os.environ["PROJECT_ROOT"] = prev_env_project_root
        os.path.exists = orig_exists
        builtins.open = orig_open
        Image.open = pil_open
        if cv2_mod is not None and cv2_imread is not None:
            cv2_mod.imread = cv2_imread
        if cv2_mod is not None and orig_cv2_imwrite is not None and _vlm_cv2_imwrite_guard:
            cv2_mod.imwrite = orig_cv2_imwrite
        try:
            os.chdir(prev_cwd)
        except OSError:
            pass


def _tool_run_shell(workspace: Path, command: str, timeout: int = 30) -> ToolResult:
    cmd = command.strip()
    # Normalize common Unix commands when running on Windows shells.
    if os.name == "nt":
        if cmd == "pwd":
            command = "cd"
        elif cmd.startswith("ls "):
            command = "dir " + cmd[3:]
        elif cmd == "ls":
            command = "dir"
    try:
        proc = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(workspace),
        )
    except subprocess.TimeoutExpired:
        return ToolResult(text=f"[shell-error] timeout after {timeout}s", ok=False)
    out = truncate(proc.stdout or "", 4000)
    err = truncate(proc.stderr or "", 2000)
    return ToolResult(
        text=f"exit={proc.returncode}\n--- stdout ---\n{out}\n--- stderr ---\n{err}",
        ok=(proc.returncode == 0),
    )


# --------------------------------------------------------------------------- #
# Finish
# --------------------------------------------------------------------------- #

def _tool_finish(answer: dict[str, Any] | str) -> ToolResult:
    """Submit the final answer and stop the loop."""
    if isinstance(answer, str):
        try:
            answer = json.loads(answer)
        except json.JSONDecodeError:
            answer = {"text": answer}
    return ToolResult(
        text=f"[final-answer]\n{json.dumps(answer, ensure_ascii=False, indent=2)}",
        is_final=True,
        final_data=answer,
    )


# --------------------------------------------------------------------------- #
# Python exec globals (shared across run_python calls)
# --------------------------------------------------------------------------- #

_PY_GLOBALS: dict[str, Any] = {
    "__name__": "__agent_python__",
    "__builtins__": __builtins__,
}

# Pre-import the most common libs so the VLM does not have to.
try:
    import numpy as _np  # noqa: F401
    import cv2 as _cv2   # noqa: F401
    _PY_GLOBALS["np"] = _np
    _PY_GLOBALS["cv2"] = _cv2
except Exception:  # noqa: BLE001
    pass
_PY_GLOBALS["Image"] = Image


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #

def build_default_registry(workspace: Path) -> ToolRegistry:
    """Build the default tool set bound to the given workspace directory."""
    reg = ToolRegistry()

    reg.register(Tool(
        name="list_files",
        description="List files in a directory (or describe a single file).",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory or file path."},
                "max_entries": {"type": "integer", "default": 200},
            },
            "required": ["path"],
        },
        fn=_tool_list_files,
    ))

    reg.register(Tool(
        name="read_text_file",
        description=(
            "Read a UTF-8 text file (schematic netlist, BOM, CSV, logs...). "
            "Supports line ranges to handle large files."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "start_line": {"type": "integer", "default": 1},
                "end_line": {"type": "integer"},
                "max_chars": {"type": "integer", "default": 6000},
            },
            "required": ["path"],
        },
        fn=_tool_read_text_file,
    ))

    reg.register(Tool(
        name="search_pdf_text",
        description=(
            "Search the PDF text layer; returns hit pages, snippets, and rect_pdf "
            "(bounding box in PDF points, origin top-left, y down). "
            "For queries matching TP+digits (e.g. TP1, TP105), substring false positives "
            "are filtered: TP1 does not match the TP1 inside TP105."
        ),
        parameters={
            "type": "object",
            "properties": {
                "pdf_path": {"type": "string"},
                "query": {"type": "string"},
                "case_sensitive": {"type": "boolean", "default": False},
                "max_results": {"type": "integer", "default": 20},
                "context_chars": {"type": "integer", "default": 80},
                "out_json_path": {
                    "type": "string",
                    "description": "Optional output JSON under workspace, e.g. debug/step01_pdf_search.json",
                },
                "page_filter": {
                    "type": "array",
                    "items": {"type": "integer", "minimum": 1},
                    "description": (
                        "Optional strict 1-based page allowlist. Assembly TP searches should use "
                        "the locator page matching target_board_side."
                    ),
                },
                "page_filter_reason": {
                    "type": "string",
                    "description": "Audit reason for applying page_filter.",
                },
            },
            "required": ["pdf_path", "query"],
        },
        fn=lambda pdf_path, query, case_sensitive=False, max_results=20,
            context_chars=80, out_json_path="", page_filter=None,
            page_filter_reason="": _tool_search_pdf_text(
                workspace=workspace,
                pdf_path=pdf_path,
                query=query,
                case_sensitive=case_sensitive,
                max_results=max_results,
                context_chars=context_chars,
                out_json_path=out_json_path,
                page_filter=page_filter,
                page_filter_reason=page_filter_reason,
            ),
    ))

    reg.register(Tool(
        name="save_text_file",
        description="Save text content to a file inside the workspace.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path under the workspace."},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
        fn=lambda path, content: _tool_save_text_file(workspace, path, content),
    ))

    reg.register(Tool(
        name="pdf_page_to_image",
        description=(
            "Render a single PDF page to PNG inside workspace. "
            "Use this before view_image when source is a multi-page PDF."
        ),
        parameters={
            "type": "object",
            "properties": {
                "pdf_path": {"type": "string"},
                "page": {"type": "integer", "default": 1},
                "out_path": {"type": "string", "default": "debug/pdf_page.png"},
                "dpi": {"type": "integer", "default": 600},
                "graphics_min_line_width": {
                    "type": "number",
                    "description": (
                        "Optional PyMuPDF minimum vector stroke width in points when rasterizing "
                        "(e.g. 0.5). Helps faint colored silkscreen vs gray/black strokes."
                    ),
                },
                "aa_level": {
                    "type": "integer",
                    "description": "Optional anti-alias level 0–8 (lower = sharper thin lines).",
                },
            },
            "required": ["pdf_path"],
        },
        fn=lambda pdf_path, page=1, out_path="debug/pdf_page.png", dpi=600,
            graphics_min_line_width=None, aa_level=None:
            _tool_pdf_page_to_image(
                workspace=workspace,
                pdf_path=pdf_path,
                page=page,
                out_path=out_path,
                dpi=dpi,
                graphics_min_line_width=graphics_min_line_width,
                aa_level=aa_level,
            ),
    ))

    reg.register(Tool(
        name="mark_tp_on_assembly_from_pdf_hit",
        description=(
            "Use `debug/case10_target_tp_pdf_search.json` hit rect to locate TP on "
            "`debug/case10_assembly_drawing.png`: detect circular pad in fixed ROI with OpenCV, "
            "then draw green circle on full image (`case10_assembly_drawing_tp_marked.png`; "
            "work ROI is not written to disk)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "search_json_path": {"type": "string", "default": "debug/case10_target_tp_pdf_search.json"},
                "assembly_png_path": {"type": "string", "default": "debug/case10_assembly_drawing.png"},
                "assembly_pdf_path": {
                    "type": "string",
                    "description": "Optional; defaults to `pdf_path` in search JSON.",
                },
                "hit_index": {"type": "integer", "default": 0},
                "roi_half": {"type": "integer", "default": 50},
                "out_work_roi_path": {"type": "string", "default": "debug/case10_target_tp_work_roi.png"},
                "out_marked_path": {"type": "string", "default": "debug/case10_assembly_drawing_tp_marked.png"},
            },
            "required": [],
        },
        fn=lambda search_json_path="debug/case10_target_tp_pdf_search.json",
            assembly_png_path="debug/case10_assembly_drawing.png",
            assembly_pdf_path="",
            hit_index=0,
            roi_half=50,
            out_work_roi_path="debug/case10_target_tp_work_roi.png",
            out_marked_path="debug/case10_assembly_drawing_tp_marked.png":
            _tool_mark_tp_on_assembly_from_pdf_hit(
                workspace=workspace,
                search_json_path=search_json_path,
                assembly_png_path=assembly_png_path,
                assembly_pdf_path=assembly_pdf_path,
                hit_index=hit_index,
                roi_half=roi_half,
                out_work_roi_path=out_work_roi_path,
                out_marked_path=out_marked_path,
            ),
    ))

    reg.register(Tool(
        name="detect_largest_ic_on_assembly_from_vlm_hint",
        description=(
            "PartB deterministic assembly IC detection using legacy successful baseline "
            "(THRESH_BINARY_INV=180, MORPH_RECT 3x3 dilate=1, min_area>=5000, aspect 0.5~1.5, "
            "rank by max bbox area). Writes case10_assembly_largest_ic_box.png + "
            "case10_assembly_largest_ic.json (debug/mask PNGs are not written or attached)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "assembly_path": {"type": "string", "default": "debug/case10_assembly_drawing_tp_marked.png"},
                "hints_json_path": {"type": "string", "default": "debug/case10_assembly_vlm_hints.json"},
                "work_margin_ratio": {"type": "number", "default": 0.2},
                "out_debug_path": {"type": "string", "default": "debug/case10_assembly_opencv_debug.png"},
                "out_box_path": {"type": "string", "default": "debug/case10_assembly_largest_ic_box.png"},
                "out_json_path": {"type": "string", "default": "debug/case10_assembly_largest_ic.json"},
            },
            "required": [],
        },
        fn=lambda assembly_path="debug/case10_assembly_drawing_tp_marked.png",
            hints_json_path="debug/case10_assembly_vlm_hints.json",
            work_margin_ratio=0.2,
            out_debug_path="debug/case10_assembly_opencv_debug.png",
            out_box_path="debug/case10_assembly_largest_ic_box.png",
            out_json_path="debug/case10_assembly_largest_ic.json":
            _tool_detect_largest_ic_on_assembly_from_vlm_hint(
                workspace=workspace,
                assembly_path=assembly_path,
                hints_json_path=hints_json_path,
                work_margin_ratio=work_margin_ratio,
                out_debug_path=out_debug_path,
                out_box_path=out_box_path,
                out_json_path=out_json_path,
            ),
    ))

    reg.register(Tool(
        name="case12_build_and_align_from_step02_anchors",
        description=(
            "PartD dedicated tool. Input locator + board anchor images "
            "(direct case10 largest-IC box images are supported), then run "
            "`case12_step02_graph` official functions to generate locator graph and aligned board points."
        ),
        parameters={
            "type": "object",
            "properties": {
                "locator_anchor_path": {"type": "string", "default": "debug/case10_assembly_largest_ic_box.png"},
                "board_anchor_path": {"type": "string", "default": "debug/case10_largest_ic_box.png"},
            },
            "required": [],
        },
        fn=lambda locator_anchor_path="debug/case10_assembly_largest_ic_box.png",
            board_anchor_path="debug/case10_largest_ic_box.png":
            _tool_case12_build_and_align_from_step02_anchors(
                workspace=workspace,
                locator_anchor_path=locator_anchor_path,
                board_anchor_path=board_anchor_path,
            ),
    ))

    reg.register(Tool(
        name="emit_step08_from_case12_aligned",
        description=(
            "PartD deterministic finalization. Read `case12_board_points_aligned.json` target point, "
            "draw final TP marker on `step02_board_front_anchor.png`, write `step08_final_tp.png` + "
            "`step08_result.json`, and set `step03_mapping.json.mapping_method` for finish contract."
        ),
        parameters={
            "type": "object",
            "properties": {
                "aligned_json_path": {"type": "string", "default": "debug/case12_board_points_aligned.json"},
                "board_anchor_path": {"type": "string", "default": "debug/step02_board_front_anchor.png"},
                "out_step08_png_path": {"type": "string", "default": "debug/step08_final_tp.png"},
                "out_step08_json_path": {"type": "string", "default": "debug/step08_result.json"},
                "out_mapping_json_path": {"type": "string", "default": "debug/step03_mapping.json"},
                "mapping_method": {
                    "type": "string",
                    "description": "Optional override; normally inferred from aligned source (opencv/vlm case12).",
                },
            },
            "required": [],
        },
        fn=lambda aligned_json_path="debug/case12_board_points_aligned.json",
            board_anchor_path="debug/step02_board_front_anchor.png",
            out_step08_png_path="debug/step08_final_tp.png",
            out_step08_json_path="debug/step08_result.json",
            out_mapping_json_path="debug/step03_mapping.json",
            mapping_method=None:
            _tool_emit_step08_from_case12_aligned(
                workspace=workspace,
                aligned_json_path=aligned_json_path,
                board_anchor_path=board_anchor_path,
                out_step08_png_path=out_step08_png_path,
                out_step08_json_path=out_step08_json_path,
                out_mapping_json_path=out_mapping_json_path,
                mapping_method=mapping_method,
            ),
    ))

    reg.register(Tool(
        name="prepare_back_board_landmark_candidates",
        description=(
            "After back_01 PCB rectangles, prepare numbered PCB-edge opening candidates and enlarged crops "
            "for VLM semantic matching. This tool does not register or map the TP."
        ),
        parameters={
            "type": "object",
            "properties": {
                "locator_path": {"type": "string", "default": "debug/case10_assembly_drawing_tp_marked.png"},
                "back_board_path": {"type": "string", "default": "INPUT_PATHS.back_board_photo"},
            },
        },
        fn=lambda locator_path="debug/case10_assembly_drawing_tp_marked.png", back_board_path="INPUT_PATHS.back_board_photo":
            _tool_prepare_back_board_landmark_candidates(workspace, locator_path, back_board_path),
    ))

    reg.register(Tool(
        name="record_back_landmark_review",
        description=(
            "After inspecting the PCB-edge candidate sheet, record zero pairs for safe rectangle fallback or at "
            "least two high-confidence mechanical-opening correspondences. Never select pads, vias or TP pads."
        ),
        parameters={
            "type": "object",
            "properties": {
                "matches": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "locator_id": {"type": "string"}, "board_id": {"type": "string"},
                            "landmark_type": {"type": "string", "enum": ["mounting_hole", "tooling_hole", "non_plated_hole", "fiducial", "board_cutout"]},
                            "confidence": {"type": "number", "minimum": 0.75, "maximum": 1.0},
                            "evidence": {"type": "string"},
                        },
                        "required": ["locator_id", "board_id", "landmark_type", "confidence", "evidence"],
                    },
                },
                "overall_evidence": {"type": "string"},
                "rejected_ids": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["matches", "overall_evidence"],
        },
        fn=lambda matches, overall_evidence, rejected_ids=None: _tool_record_back_landmark_review(
            workspace, matches, overall_evidence, rejected_ids
        ),
    ))

    reg.register(Tool(
        name="record_board_side_decision",
        description=(
            "After viewing the green-marked locator page, record whether that TP is on the front "
            "or back side. Base the decision on locator labels such as TOP/BOTTOM/FRONT/BACK, page "
            "title, mirrored silkscreen/component layout, and visible side-specific evidence."
        ),
        parameters={
            "type": "object",
            "properties": {
                "side": {"type": "string", "enum": ["front", "back"]},
                "evidence": {"type": "string"},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": ["side", "evidence"],
        },
        fn=lambda side, evidence, confidence=0.0: _tool_record_board_side_decision(
            workspace=workspace,
            side=side,
            evidence=evidence,
            confidence=confidence,
        ),
    ))

    reg.register(Tool(
        name="register_back_board_from_outline_and_holes",
        description=(
            "IC-free registration for either physical side: start from the PCB rectangle homography, optionally refine it with "
            "VLM-reviewed PCB-edge holes when strict error gates improve, otherwise automatically retain the "
            "original rectangle mapping. Automatic CV excludes page frames and border-colored photo backgrounds. "
            "If automatic detection fails, view both images and retry only once with rough normalized PCB ROIs; "
            "OpenCV, not the VLM coordinates, remains the final geometry."
        ),
        parameters={
            "type": "object",
            "properties": {
                "locator_path": {"type": "string", "default": "debug/case10_assembly_drawing_tp_marked.png"},
                "back_board_path": {"type": "string", "default": "INPUT_PATHS.back_board_photo"},
                "review_path": {"type": "string", "default": "debug/back_03_vlm_edge_hole_review.json"},
                "locator_roi_hint_norm": {
                    "type": "array", "items": {"type": "number", "minimum": 0, "maximum": 1},
                    "minItems": 4, "maxItems": 4,
                    "description": "Optional one-time VLM rough PCB ROI [x1,y1,x2,y2], normalized to locator image.",
                },
                "back_board_roi_hint_norm": {
                    "type": "array", "items": {"type": "number", "minimum": 0, "maximum": 1},
                    "minItems": 4, "maxItems": 4,
                    "description": "Optional one-time VLM rough PCB ROI [x1,y1,x2,y2], normalized to photo.",
                },
            },
        },
        fn=lambda locator_path="debug/case10_assembly_drawing_tp_marked.png",
            back_board_path="INPUT_PATHS.back_board_photo",
            review_path="debug/back_03_vlm_edge_hole_review.json",
            locator_roi_hint_norm=None,
            back_board_roi_hint_norm=None:
            _tool_register_back_board_from_outline_and_holes(
                workspace=workspace,
                locator_path=locator_path,
                back_board_path=back_board_path,
                review_path=review_path,
                locator_roi_hint_norm=locator_roi_hint_norm,
                back_board_roi_hint_norm=back_board_roi_hint_norm,
            ),
    ))

    reg.register(Tool(
        name="emit_step08_from_back_board_registration",
        description=(
            "Deterministic outline/hole finalization for either side. Read back_board_registration.json, draw the "
            "final TP marker on the selected physical photo, and write standard step08 and mapping JSON artifacts."
        ),
        parameters={
            "type": "object",
            "properties": {
                "registration_json_path": {"type": "string", "default": "debug/back_board_registration.json"},
                "back_board_path": {"type": "string", "default": "INPUT_PATHS.back_board_photo"},
                "camera_view": {"type": "string", "enum": ["front", "back"]},
            },
        },
        fn=lambda registration_json_path="debug/back_board_registration.json",
            back_board_path="INPUT_PATHS.back_board_photo",
            camera_view=None:
            _tool_emit_step08_from_back_board_registration(
                workspace=workspace,
                registration_json_path=registration_json_path,
                back_board_path=back_board_path,
                camera_view=camera_view,
            ),
    ))

    reg.register(Tool(
        name="detect_largest_ic_on_board_full",
        description=(
            "PartA deterministic board IC detection without VLM hints: auto-detect green PCB "
            "region on the full board photo, apply QFP-oriented OpenCV filters, and write "
            "`case10_board_landscape.png`, `case10_largest_ic_box.png`, and `case10_largest_ic.json`."
        ),
        parameters={
            "type": "object",
            "properties": {
                "board_path": {"type": "string", "default": "INPUT_PATHS.front_board_photo"},
                "out_debug_path": {"type": "string", "default": "debug/case10_opencv_debug.png"},
                "out_box_path": {"type": "string", "default": "debug/case10_largest_ic_box.png"},
                "out_json_path": {"type": "string", "default": "debug/case10_largest_ic.json"},
                "out_landscape_path": {"type": "string", "default": "debug/case10_board_landscape.png"},
            },
            "required": [],
        },
        fn=lambda board_path="INPUT_PATHS.front_board_photo",
            out_debug_path="debug/case10_opencv_debug.png",
            out_box_path="debug/case10_largest_ic_box.png",
            out_json_path="debug/case10_largest_ic.json",
            out_landscape_path="debug/case10_board_landscape.png":
            _tool_detect_largest_ic_on_board_full(
                workspace=workspace,
                board_path=board_path,
                out_debug_path=out_debug_path,
                out_box_path=out_box_path,
                out_json_path=out_json_path,
                out_landscape_path=out_landscape_path,
            ),
    ))

    reg.register(Tool(
        name="detect_largest_ic_on_board_from_vlm_hint",
        description=(
            "PartA deterministic board IC detection: read board image from "
            "`INPUT_PATHS.front_board_photo` "
            "+ board hints JSON (`case10_vlm_hints.json` preferred, `case10_board_vlm_hints.json` compatible), "
            "expand work ROI, run a "
            "stable OpenCV baseline, and write `case10_largest_ic_box.png` + "
            "`case10_largest_ic.json` (debug/mask PNGs are not written or attached)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "board_path": {"type": "string", "default": "INPUT_PATHS.front_board_photo"},
                "hints_json_path": {"type": "string", "default": "debug/case10_vlm_hints.json"},
                "work_margin_ratio": {"type": "number", "default": 0.2},
                "out_debug_path": {"type": "string", "default": "debug/case10_opencv_debug.png"},
                "out_box_path": {"type": "string", "default": "debug/case10_largest_ic_box.png"},
                "out_json_path": {"type": "string", "default": "debug/case10_largest_ic.json"},
            },
            "required": [],
        },
        fn=lambda board_path="INPUT_PATHS.front_board_photo",
            hints_json_path="debug/case10_vlm_hints.json",
            work_margin_ratio=0.2,
            out_debug_path="debug/case10_opencv_debug.png",
            out_box_path="debug/case10_largest_ic_box.png",
            out_json_path="debug/case10_largest_ic.json":
            _tool_detect_largest_ic_on_board_from_vlm_hint(
                workspace=workspace,
                board_path=board_path,
                hints_json_path=hints_json_path,
                work_margin_ratio=work_margin_ratio,
                out_debug_path=out_debug_path,
                out_box_path=out_box_path,
                out_json_path=out_json_path,
            ),
    ))

    reg.register(Tool(
        name="view_image",
        description=(
            "Attach an image file so you can SEE it in the next turn. "
            "Use this whenever you need to visually inspect a board photo, "
            "locator drawing or a zoomed crop. "
            "For Part B StepB3, calling this on `debug/case10_assembly_largest_ic_box.png` "
            "records `debug/case10_stepb3_viewed_largest_ic_box.json` (required by finish); "
            "re-run it after every `annotate_image` that overwrites that PNG."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "note": {"type": "string",
                         "description": "Why you want to look at this image."},
            },
            "required": ["path"],
        },
        fn=_tool_view_image,
    ))

    reg.register(Tool(
        name="crop_image",
        description=(
            "Crop an image to a bounding box and save it to the workspace. "
            "The cropped image is automatically attached for the next turn. "
            "bbox = [left, top, right, bottom] in pixels of the **input** image (the file in `path`). "
            "For Step3 `debug/step03_locator_roi.png`, `path` must be the **locator/silkscreen** image "
            "(e.g. `debug/step01_locator_front_anchor.png`), not the board photo."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "bbox": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "minItems": 4,
                    "maxItems": 4,
                },
                "out_path": {"type": "string",
                             "description": "Output path inside the workspace."},
            },
            "required": ["path", "bbox", "out_path"],
        },
        fn=lambda path, bbox, out_path: _tool_crop_image(workspace, path, bbox, out_path),
    ))

    reg.register(Tool(
        name="crop_green_tp_neighborhood_on_locator",
        description=(
            "Step3 helper: on `debug/step01_locator_front_anchor.png`, find the green TP circle via HSV "
            "(same as other Step3 tools), take its centroid, and crop an axis-aligned neighborhood "
            "around it (default about **500×500** px). Saves e.g. `debug/step03_locator_roi.png`. "
            "Increase `margin_px` / `min_side_px` for a larger context window. "
            "Use this instead of guessing `crop_image` bbox — the green mark comes from Step1 "
            "`pdf_draw_circle_then_rasterize`, but pixel coordinates are **not** sent to the model unless you "
            "read them from the image or call this tool."
        ),
        parameters={
            "type": "object",
            "properties": {
                "locator_path": {
                    "type": "string",
                    "default": "debug/step01_locator_front_anchor.png",
                },
                "margin_px": {
                    "type": "integer",
                    "default": 450,
                    "description": "Half-width/half-height margin from green centroid (before clamp).",
                },
                "min_side_px": {
                    "type": "integer",
                    "default": 500,
                    "description": "Minimum crop width and height after clamping.",
                },
                "out_path": {
                    "type": "string",
                    "default": "debug/step03_locator_roi.png",
                },
            },
            "required": [],
        },
        fn=lambda locator_path="debug/step01_locator_front_anchor.png", margin_px=450,
            min_side_px=500, out_path="debug/step03_locator_roi.png":
            _tool_crop_green_tp_neighborhood_on_locator(
                workspace,
                locator_path=locator_path,
                margin_px=margin_px,
                min_side_px=min_side_px,
                out_path=out_path,
            ),
    ))

    reg.register(Tool(
        name="annotate_image",
        description=(
            "Draw markers on an image and save the result. "
            "Each point is either a crosshair {x, y, label?, color?, radius?}, "
            "or a rectangle outline for Step2 red anchor / Step4 locator landmarks: "
            "{bbox:[x1,y1,x2,y2], color?:str (default red), width?, label?}. "
            "For VLM Path C Step4, use `path`=`debug/step03_locator_roi.png` and "
            "`out_path`=`debug/step04_locator_landmarks.png`: draw **red** `bbox` rectangles "
            "tightly around **clear gray/black silkscreen part boxes** (red refdes text inside) "
            "near the green TP — ROI pixel coords `[left,top,right,bottom]`, optional `width:4`."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "points": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "x": {"type": "integer"},
                            "y": {"type": "integer"},
                            "bbox": {
                                "type": "array",
                                "items": {"type": "integer"},
                                "minItems": 4,
                                "maxItems": 4,
                                "description": "Rectangle [left, top, right, bottom] in pixels.",
                            },
                            "label": {"type": "string"},
                            "color": {"type": "string"},
                            "radius": {"type": "integer"},
                            "width": {
                                "type": "integer",
                                "description": "Stroke width for bbox (default 3).",
                            },
                        },
                    },
                },
                "out_path": {"type": "string"},
                "radius": {"type": "integer", "default": 4},
            },
            "required": ["path", "points", "out_path"],
        },
        fn=lambda path, points, out_path, radius=4:
            _tool_annotate_image(workspace, path, points, out_path, radius),
    ))

    reg.register(Tool(
        name="run_step3_mapping",
        description=(
            "Deterministic Step3 local mapping: detect red anchor rectangles in locator+board, "
            "detect green TP center in locator, compute scalar u/v->px/py, and write "
            "debug/step03_mapping.json + debug/step03_prior_on_board.png. "
            "Requires Step2 PNGs on disk with real red strokes (see annotate_image bbox)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "locator_path": {
                    "type": "string",
                    "description": "Optional locator image path. Defaults to INPUT_PATHS.front_locator_marked.",
                },
                "board_path": {
                    "type": "string",
                    "description": "Optional board image path. Defaults to INPUT_PATHS.front_board_marked.",
                },
                "out_json_path": {
                    "type": "string",
                    "default": "debug/step03_mapping.json",
                },
                "out_image_path": {
                    "type": "string",
                    "default": "debug/step03_prior_on_board.png",
                },
            },
        },
        fn=lambda locator_path=None,
            board_path=None,
            out_json_path="debug/step03_mapping.json",
            out_image_path="debug/step03_prior_on_board.png": _tool_run_step3_mapping(
                workspace=workspace,
                locator_path=locator_path,
                board_path=board_path,
                out_json_path=out_json_path,
                out_image_path=out_image_path,
            ),
    ))

    reg.register(Tool(
        name="match_green_tp_roi_to_board",
        description=(
            "Step3 alternative: detect green TP on the locator PNG, crop a neighbourhood "
            "(inpaint green ink), multi-scale template-match on the raw board photo, "
            "and write debug/step03_mapping.json + step03_prior_on_board.png. "
            "Fills the same JSON keys as run_step3_mapping (locator_box=ROI, board_box=matched "
            "patch, u,v,tp_prior_board). Default locator is debug/step01_locator_front_anchor.png; "
            "board defaults to INPUT_PATHS.front_board_photo. Does not require red anchor boxes."
        ),
        parameters={
            "type": "object",
            "properties": {
                "locator_path": {
                    "type": "string",
                    "description": "Locator image with green TP circle (default step01 path).",
                },
                "board_path": {
                    "type": "string",
                    "description": "Full-resolution board photo (default INPUT_PATHS.front_board_photo).",
                },
                "margin_px": {
                    "type": "integer",
                    "default": 80,
                    "description": "Extra border around green bbox when building the template crop.",
                },
                "search_max_dim": {
                    "type": "integer",
                    "default": 1200,
                    "description": "Downscale board longer side to this for coarse search (speed).",
                },
                "min_match_score": {
                    "type": "number",
                    "default": 0.2,
                    "description": "Reject if best TM_CCOEFF_NORMED score is below this.",
                },
                "scale_steps": {
                    "type": "integer",
                    "default": 26,
                    "description": "Number of template scales between ~0.22 and ~2.85.",
                },
                "out_template_path": {
                    "type": "string",
                    "default": "debug/step03_tp_roi_template.png",
                },
                "out_debug_path": {
                    "type": "string",
                    "default": "debug/step03_tp_roi_match_debug.png",
                },
                "out_json_path": {
                    "type": "string",
                    "default": "debug/step03_mapping.json",
                },
                "out_image_path": {
                    "type": "string",
                    "default": "debug/step03_prior_on_board.png",
                },
            },
        },
        fn=lambda locator_path=None,
            board_path=None,
            margin_px=80,
            search_max_dim=1200,
            min_match_score=0.2,
            scale_steps=26,
            out_template_path="debug/step03_tp_roi_template.png",
            out_debug_path="debug/step03_tp_roi_match_debug.png",
            out_json_path="debug/step03_mapping.json",
            out_image_path="debug/step03_prior_on_board.png":
            _tool_match_green_tp_roi_to_board(
                workspace=workspace,
                locator_path=locator_path,
                board_path=board_path,
                margin_px=margin_px,
                search_max_dim=search_max_dim,
                min_match_score=min_match_score,
                scale_steps=scale_steps,
                out_template_path=out_template_path,
                out_debug_path=out_debug_path,
                out_json_path=out_json_path,
                out_image_path=out_image_path,
            ),
    ))

    reg.register(Tool(
        name="run_python",
        description=(
            "Execute a snippet of Python 3 in-process. Pre-imported: "
            "numpy as np, cv2, PIL.Image as Image. State is kept across calls. "
            "Use for template matching, colour filtering, SIFT/ORB alignment, "
            "coordinate transforms, etc."
        ),
        parameters={
            "type": "object",
            "properties": {
                "code": {"type": "string"},
                "timeout": {"type": "integer", "default": 30},
            },
            "required": ["code"],
        },
        fn=lambda code, timeout=30: _tool_run_python(workspace, code, timeout),
    ))

    reg.register(Tool(
        name="run_candidate_pipeline",
        description=(
            "Deterministic Step5/6/7 pipeline: detect candidates in ROI, "
            "score semantic + geometry terms, rank, and write debug artifacts."
        ),
        parameters={
            "type": "object",
            "properties": {
                "roi_path": {"type": "string", "default": "debug/step04_roi_crop.png"},
                "roi_bbox": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "minItems": 4,
                    "maxItems": 4,
                    "description": "Optional ROI bbox [x1,y1,x2,y2] in board image coordinates.",
                },
                "prior_board": {
                    "type": "array",
                    "items": {"type": "number"},
                    "minItems": 2,
                    "maxItems": 2,
                    "description": "Optional prior board point [px,py].",
                },
                "board_path": {
                    "type": "string",
                    "description": "Optional board image path. Defaults to INPUT_PATHS.front_board_marked.",
                },
            },
        },
        fn=lambda roi_path="debug/step04_roi_crop.png",
            roi_bbox=None,
            prior_board=None,
            board_path=None: _tool_run_candidate_pipeline(
                workspace=workspace,
                roi_path=roi_path,
                roi_bbox=roi_bbox,
                prior_board=prior_board,
                board_path=board_path,
            ),
    ))

    reg.register(Tool(
        name="run_shell",
        description=(
            "Run a shell command in the workspace directory. Short, non-interactive "
            "commands only."
        ),
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout": {"type": "integer", "default": 30},
            },
            "required": ["command"],
        },
        fn=lambda command, timeout=30: _tool_run_shell(workspace, command, timeout),
    ))

    reg.register(Tool(
        name="finish",
        description=(
            "Stop the run. Emit ONLY this tool call — zero assistant prose before it. "
            'answer must be minimal JSON, e.g. {"pixel": [x, y], "needs_user_help": false} '
            "(copy pixel from debug/step08_result.json). Forbidden: reasoning, confidence, "
            "tp_id, user_message, or any natural-language explanation outside the tool call."
        ),
        parameters={
            "type": "object",
            "properties": {
                "answer": {
                    "type": "object",
                    "description": (
                        "Minimal JSON only: pixel [x,y] and needs_user_help (default false). "
                        "No reasoning or extra fields unless needs_user_help=true."
                    ),
                },
            },
            "required": ["answer"],
        },
        fn=_tool_finish,
    ))

    return reg
