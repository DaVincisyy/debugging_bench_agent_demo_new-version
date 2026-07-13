"""Planner: one VLM call over the user instruction + schematic to decide TPs.

Inputs:
  - user_question: e.g. "测输入电压是否正常"
  - schematic_image: schematic diagram (原理图). For PDF inputs, every page is
    rasterized to compressed JPEG and sent to the planner VLM (default
    ``qwen3.6-plus`` on the Coding endpoint). The 位号图 is NOT used here —
    it is handed to the downstream localization steps together with the TPs.

Output:
  PlannerResult(target_points=["TP1", "TP6"])
"""

from __future__ import annotations

import json
import mimetypes
import re
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import os

from PIL import Image

from .config import Config
from .llm_client import LLMClient


PLANNER_PDF_DPI = int(os.environ.get("PLANNER_PDF_DPI", "150"))

# Per-page pixel budget. Pages larger than this are downscaled. Kept modest
# so a multi-page schematic stays well within request-size / latency limits.
PLANNER_MAX_PIXELS = int(os.environ.get("PLANNER_MAX_PIXELS", str(2_000_000)))

# JPEG quality for the compressed schematic pages sent to the planner.
PLANNER_JPEG_QUALITY = int(os.environ.get("PLANNER_JPEG_QUALITY", "85"))
PLANNER_MODEL = os.environ.get("PLANNER_MODEL") or os.environ.get("VLM_MODEL", "qwen3.6-plus")


PLANNER_SYSTEM_PROMPT = """\
You are a test-point planner for a PCBA test system.
Your ONLY job: read the schematic document and find physical TP reference designators.

CRITICAL RULE — voltage/electrical measurements ALWAYS need 2 points:
- One for the signal being measured (positive probe)
- One for GND/return reference (reference probe)
Even if only one net is named in the request, you MUST find:
  (a) the physical TP on that signal net, AND
  (b) the nearest physical TP connected to GND/return.

How to find TPs:
1. Read the schematic document carefully.
2. Find TP labels like "TP1", "TP12", "TP415" next to test pads.
3. For the signal: trace the net mentioned in the request → find the TP on it.
4. For GND: look for a TP near a GND symbol or ground plane.
5. Use ONLY TPxxx format designators, NOT net names (GND, VIN, KL30, etc.).

Output PURE JSON — no markdown, no backticks, no extra text:
{"target_points": ["TP1", "TP6"]}

Return 1 point only for single-point localization tasks (e.g. "find TP12").
Return 2 points for ALL measurement tasks (voltage, resistance, continuity, etc.).
"""


PLANNER_USER_TEMPLATE = """\
User measurement request:
{user_question}

IMPORTANT: This request is about checking/measuring an electrical quantity.
You MUST find TWO physical TP reference designators (TPxxx format):
1. The TP on the signal net being measured
2. A GND/return reference TP

Read the schematic document NOW. Find TP labels. Output ONLY the JSON."""


@dataclass
class PlannerResult:
    target_points: list[str]


def _pdf_page_cache_dir(pdf_path: Path, dpi: int) -> Path:
    """Writable per-PDF cache dir for rasterized pages.

    Rendered pages are written here (NOT next to the source PDF, whose
    directory may be read-only). Keyed by absolute path + mtime + dpi so a
    changed PDF re-renders instead of serving stale pages.
    """
    import hashlib
    import tempfile

    try:
        mtime = int(pdf_path.stat().st_mtime)
    except OSError:
        mtime = 0
    key = hashlib.sha1(f"{pdf_path.resolve()}|{mtime}|{dpi}".encode()).hexdigest()[:16]
    d = Path(tempfile.gettempdir()) / "planner_pdf_pages" / key
    d.mkdir(parents=True, exist_ok=True)
    return d


def _render_pdf_all_pages(pdf_path: Path, dpi: int = PLANNER_PDF_DPI) -> list[str]:
    """Rasterize EVERY page of a PDF to PNG, in document order.

    Returns the ordered list of page PNG paths. Prefers PyMuPDF (fitz) with a
    ``dpi/72`` zoom matrix (matches the tool-loop rasterizer); falls back to
    pdf2image. PDFs are never sent to the VLM directly — qwen3.6-plus only
    accepts raster images via ``image_url``.
    """
    cache = _pdf_page_cache_dir(pdf_path, dpi)
    out_paths: list[str] = []
    try:
        import fitz  # PyMuPDF

        doc = fitz.open(str(pdf_path))
        zoom = dpi / 72.0
        for i in range(doc.page_count):
            out = cache / f"page{i + 1:02d}.png"
            if not out.exists():
                page = doc.load_page(i)
                pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
                pix.save(str(out))
            out_paths.append(str(out))
        doc.close()
        return out_paths
    except Exception:
        out_paths = []

    try:
        from pdf2image import convert_from_path

        images = convert_from_path(str(pdf_path), dpi=dpi)
        for i, im in enumerate(images):
            out = cache / f"page{i + 1:02d}.png"
            im.save(str(out), "PNG")
            out_paths.append(str(out))
    except Exception:
        pass
    return out_paths


def _encode_image_clamped(image_path: str, max_pixels: int = PLANNER_MAX_PIXELS) -> str:
    """Base64-encode a raster image, downscaling only if it exceeds the VLM's
    per-image pixel budget. No fixed small cap — schematic detail is kept."""
    import base64
    import io

    im = Image.open(image_path)
    w, h = im.size
    if max_pixels <= 0:
        mime, _ = mimetypes.guess_type(Path(image_path).name)
        if mime is None:
            mime = "image/png"
        b64 = base64.b64encode(Path(image_path).read_bytes()).decode("ascii")
        return f"data:{mime};base64,{b64}"
    if w * h > max_pixels:
        ratio = (max_pixels / float(w * h)) ** 0.5
        im = im.resize((max(1, int(w * ratio)), max(1, int(h * ratio))), Image.LANCZOS)
    if im.mode in ("RGBA", "P", "LA"):
        im = im.convert("RGB")
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=PLANNER_JPEG_QUALITY)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"


def run_planner(
    user_question: str,
    schematic_image_path: str | None = None,
    cfg: Config | None = None,
    event_sink=None,
) -> PlannerResult:
    """One VLM call: user instruction + schematic diagram → target TPs.

    Only the schematic (原理图) is sent to the planner; the 位号图 (assembly /
    bit-locator drawing) is NOT used here — it is consumed by the downstream
    steps that localize each returned test point on the board.
    """
    import logging
    _log = logging.getLogger(__name__)

    def _emit(event_type: str, payload: dict) -> None:
        if event_sink is None:
            return
        try:
            event_sink(event_type, payload)
        except Exception:
            pass

    if cfg is None:
        from dotenv import load_dotenv
        load_dotenv(dotenv_path=".env", override=False)
        import os as _os
        cfg = Config(
            base_url=(_os.environ.get("VLM_BASE_URL", "")),
            api_key=(_os.environ.get("VLM_API_KEY", "")),
            model=(_os.environ.get("VLM_MODEL", "")),
        )

    # Planner is one-shot — raster images + no thinking.
    planner_cfg = deepcopy(cfg)
    planner_cfg.model = PLANNER_MODEL
    planner_cfg.http_timeout_sec = 600
    planner_cfg.http_max_retries = 0
    planner_cfg.connect_retries = 0
    planner_cfg.enable_thinking = False
    planner_cfg.reasoning_effort = None
    planner_cfg.thinking_mode = False

    client = LLMClient(planner_cfg)
    user_text = PLANNER_USER_TEMPLATE.format(user_question=user_question)
    parts: list[dict[str, Any]] = [client.text_part(user_text)]
    attachments: list[str] = []
    planner_messages: list[dict[str, Any]] = [client.system_message(PLANNER_SYSTEM_PROMPT)]

    for label, raw_path in [
        ("schematic_diagram", schematic_image_path),
    ]:
        if not raw_path:
            continue
        _log.info("Planner: checking %s -> %s", label, raw_path)
        src = Path(raw_path)
        if not src.exists():
            _log.warning("Planner: %s not found: %s", label, raw_path)
            continue

        ext = src.suffix.lower()
        if ext == ".pdf":
            _emit("planner.progress", {
                "phase": "rasterizing",
                "message": f"正在栅格化原理图 PDF（{PLANNER_PDF_DPI} DPI）...",
            })
            page_paths = _render_pdf_all_pages(src)
            if not page_paths:
                _log.warning("Planner: failed to rasterize PDF %s", src)
                parts.append(client.text_part(
                    f"\n\n[note: could not rasterize {label} PDF {src}]"
                ))
                continue
        elif ext in {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"}:
            page_paths = [str(src)]
        else:
            _log.warning("Planner: unsupported %s format: %s", label, src)
            continue

        total = len(page_paths)
        for idx, page_path in enumerate(page_paths, start=1):
            try:
                data_url = _encode_image_clamped(page_path)
            except Exception as exc:
                _log.exception("Planner: failed to encode %s page %d", label, idx)
                parts.append(client.text_part(
                    f"\n\n[note: could not load {label} page {idx} from {page_path}: {exc}]"
                ))
                continue
            if total > 1:
                parts.append(client.text_part(f"[{label} — page {idx}/{total}]"))
            parts.append(client.image_part(data_url))
            attachments.append(f"{label} p{idx}/{total}: {page_path}")
            _log.info("Planner: encoded %s page %d/%d -> %d chars",
                      label, idx, total, len(data_url))

    if not attachments:
        _log.warning("Planner: no schematic attached, returning empty")
        _emit("planner.progress", {"phase": "no_images", "message": "未找到可用的原理图"})
        return PlannerResult(target_points=[])

    if parts:
        planner_messages.append(client.user_message(parts))
    _emit("planner.progress", {"phase": "encoding", "message": f"Planner 已准备 {len(attachments)} 份原理图输入...", "images": attachments})
    _log.info("Planner: calling %s with %d prepared inputs", planner_cfg.model, len(attachments))
    _emit("planner.progress", {"phase": "calling_vlm", "message": f"正在调用 {planner_cfg.model} 分析原理图..."})
    try:
        reply = client.chat(
            messages=planner_messages,
            tools_schema=None,
        )
        _log.info("Planner: VLM API returned")
    except Exception as exc:
        _log.exception("Planner: VLM API call failed")
        _emit("planner.progress", {"phase": "vlm_failed", "message": f"VLM 调用失败: {exc}"})
        return PlannerResult(target_points=[])

    _emit("planner.progress", {"phase": "parsing", "message": "正在解析 VLM 返回的目标测点..."})
    raw = (reply.content or "").strip()
    _log.info("Planner: VLM raw response: %s", raw[:300])
    points = _parse_planner_response(raw)
    _log.info("Planner: parsed points=%s", points)
    _emit("planner.progress", {"phase": "done", "message": f"解析完成，发现 {len(points)} 个目标点: {points}", "target_points": points})
    return PlannerResult(target_points=points)


def _parse_planner_response(raw: str) -> list[str]:
    try:
        obj = json.loads(raw)
        return _extract_tp_list(obj)
    except json.JSONDecodeError:
        pass
    json_pattern = re.compile(r"\{[^{}]*\"target_points\"[^{}]*\}", re.DOTALL)
    for match in json_pattern.finditer(raw):
        try:
            obj = json.loads(match.group())
            return _extract_tp_list(obj)
        except json.JSONDecodeError:
            continue
    tp_pattern = re.compile(r"\bTP\d+\b", re.IGNORECASE)
    found = list(dict.fromkeys(m.group(0).upper() for m in tp_pattern.finditer(raw)))
    return found[:2]


def _extract_tp_list(obj: dict[str, Any]) -> list[str]:
    points = obj.get("target_points", [])
    if isinstance(points, list):
        return [str(p).strip() for p in points if str(p).strip()][:2]
    return []
