"""Planner: one VLM call with inline images to decide target TPs.

Inputs:
  - user_question: e.g. "测输入电压是否正常"
  - schematic_image: PNG/JPG of the schematic diagram
  - assembly_image: PNG/JPG of the 位号图

Output:
  PlannerResult(target_points=["TP1", "TP6"])
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

from .config import Config
from .llm_client import LLMClient
from .utils import encode_image_data_url


# Max dimension for planner inline images (pixels). Resize larger images
# so base64 data URLs stay well under API request-size limits.
PLANNER_IMAGE_MAX_DIM = 1024


PLANNER_SYSTEM_PROMPT = """\
You are a test-point planner for a PCBA test system.
Your ONLY job: look at the schematic image and find physical TP reference designators.

CRITICAL RULE — voltage/electrical measurements ALWAYS need 2 points:
- One for the signal being measured (positive probe)
- One for GND/return reference (reference probe)
Even if only one net is named in the request, you MUST find:
  (a) the physical TP on that signal net, AND
  (b) the nearest physical TP connected to GND/return.

How to find TPs:
1. Look at the schematic image.
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

Look at the schematic image NOW. Find TP labels. Output ONLY the JSON."""


@dataclass
class PlannerResult:
    target_points: list[str]


def _ensure_image(path_str: str) -> str | None:
    """Return the path if it's already an image; render first page if it's a PDF."""
    p = Path(path_str)
    if not p.exists():
        return None
    ext = p.suffix.lower()
    if ext in {".png", ".jpg", ".jpeg", ".bmp", ".webp"}:
        return str(p)
    if ext == ".pdf":
        return _render_pdf_first_page(p)
    return None


def _render_pdf_first_page(pdf_path: Path) -> str | None:
    """Render page 0 of a PDF to a temp PNG. Returns the PNG path."""
    out = pdf_path.with_suffix(".page0.png")
    if out.exists():
        return str(out)
    try:
        import fitz  # PyMuPDF
    except ImportError:
        try:
            from pdf2image import convert_from_path
            images = convert_from_path(str(pdf_path), first_page=1, last_page=1)
            if images:
                images[0].save(str(out), "PNG")
                return str(out)
        except Exception:
            pass
        return None
    try:
        doc = fitz.open(str(pdf_path))
        page = doc[0]
        mat = fitz.Matrix(2.0, 2.0)
        pix = page.get_pixmap(matrix=mat)
        pix.save(str(out))
        doc.close()
        return str(out)
    except Exception:
        return None


def _encode_resized(image_path: str) -> str:
    """Resize large images to PLANNER_IMAGE_MAX_DIM before base64 encoding."""
    im = Image.open(image_path)
    w, h = im.size
    if max(w, h) > PLANNER_IMAGE_MAX_DIM:
        ratio = PLANNER_IMAGE_MAX_DIM / max(w, h)
        im = im.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
    # Save resized version to bytes, then encode
    import io
    buf = io.BytesIO()
    im.save(buf, format="JPEG" if im.mode != "RGBA" else "PNG")
    buf.seek(0)
    import base64
    mime = "image/jpeg" if im.mode != "RGBA" else "image/png"
    b64 = base64.b64encode(buf.read()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def run_planner(
    user_question: str,
    schematic_image_path: str | None = None,
    assembly_image_path: str | None = None,
    cfg: Config | None = None,
    event_sink=None,
) -> PlannerResult:
    """One VLM call with inline schematic/assembly images to find target TPs."""
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

    # Planner is one-shot — override long service timeouts
    cfg.http_timeout_sec = 600
    cfg.http_max_retries = 0
    cfg.connect_retries = 0
    cfg.enable_thinking = False
    cfg.reasoning_effort = None

    client = LLMClient(cfg)
    user_text = PLANNER_USER_TEMPLATE.format(user_question=user_question)
    parts: list[dict[str, Any]] = [client.text_part(user_text)]
    images_attached: list[str] = []

    for label, raw_path in [
        ("schematic_diagram", schematic_image_path),
        ("assembly_drawing", assembly_image_path),
    ]:
        if not raw_path:
            continue
        _log.info("Planner: checking %s -> %s", label, raw_path)
        img_path = _ensure_image(raw_path)
        if not img_path:
            _log.warning("Planner: %s not found or not renderable", label)
            continue
        try:
            _log.info("Planner: encoding %s (%s)", label, img_path)
            data_url = _encode_resized(img_path)
            _log.info("Planner: encoded %s -> %d chars", label, len(data_url))
            parts.append(client.image_part(data_url))
            images_attached.append(f"{label}: {img_path}")
        except Exception as exc:
            _log.exception("Planner: failed to encode %s", label)
            parts.append(client.text_part(
                f"\n\n[note: could not load {label} image from {img_path}: {exc}]"
            ))

    if not images_attached:
        _log.warning("Planner: no images attached, returning empty")
        _emit("planner.progress", {"phase": "no_images", "message": "未找到可用的原理图/位号图"})
        return PlannerResult(target_points=[])

    _emit("planner.progress", {"phase": "encoding", "message": f"正在编码 {len(images_attached)} 张图片...", "images": images_attached})
    _log.info("Planner: calling VLM API with %d images", len(images_attached))
    _emit("planner.progress", {"phase": "calling_vlm", "message": "正在调用 VLM 分析图片..."})
    try:
        reply = client.chat(
            messages=[
                client.system_message(PLANNER_SYSTEM_PROMPT),
                client.user_message(parts),
            ],
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
