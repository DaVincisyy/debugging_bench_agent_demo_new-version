"""Small helpers shared by tools and the agent core."""

from __future__ import annotations

import base64
import mimetypes
import os
from pathlib import Path
from typing import Any

from PIL import Image


def encode_image_data_url(
    path: str | Path,
    *,
    max_pixels: int | None = None,
    jpeg_quality: int | None = None,
) -> str:
    """Return a ``data:image/...;base64,...`` URL for a local image file.

    Lynn / workflow pattern: images are attached only when a step calls
    ``view_image`` (or a tool returns ``ToolResult.images``). Files are sent
    as-is (no downscale, no JPEG re-encode); PNG stays PNG, JPEG stays JPEG.
    ``max_pixels`` / ``jpeg_quality`` are accepted for call-site compatibility
    but intentionally ignored.
    """
    _ = max_pixels, jpeg_quality
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Image not found: {p}")
    mime, _ = mimetypes.guess_type(p.name)
    if mime is None or not mime.startswith("image/"):
        raise ValueError(f"Not an image file: {p} (MIME={mime or 'unknown'})")
    try:
        with Image.open(p) as im:
            im.verify()
    except Exception as exc:
        raise ValueError(f"Unreadable image file: {p}") from exc
    b64 = base64.b64encode(p.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _debug_max_pixels() -> int:
    val = os.environ.get("VLM_DEBUG_IMAGE_MAX_PIXELS")
    if val is None or not str(val).strip():
        val = os.environ.get("VLM_AGENT_IMAGE_MAX_PIXELS", "2000000")
    return int(val)


def _debug_jpeg_quality() -> int:
    val = os.environ.get("VLM_DEBUG_JPEG_QUALITY")
    if val is None or not str(val).strip():
        val = os.environ.get("VLM_AGENT_JPEG_QUALITY", "85")
    return int(val)


def pdf_raster_max_pixels() -> int:
    """Pixel budget for faint PDF/locator line art used by CV registration."""
    return int(os.environ.get("VLM_PDF_RASTER_MAX_PIXELS", "12000000"))


def _pil_from_array(img: Any) -> Image.Image:
    """Convert an OpenCV BGR (or grayscale) ndarray to PIL RGB/L."""
    import cv2  # type: ignore
    import numpy as np  # type: ignore

    arr = np.asarray(img)
    if arr.ndim == 2:
        return Image.fromarray(arr)
    if arr.ndim == 3 and arr.shape[2] == 3:
        rgb = cv2.cvtColor(arr, cv2.COLOR_BGR2RGB)
        return Image.fromarray(rgb)
    if arr.ndim == 3 and arr.shape[2] == 4:
        rgba = cv2.cvtColor(arr, cv2.COLOR_BGRA2RGBA)
        return Image.fromarray(rgba)
    raise TypeError(f"Unsupported ndarray shape for debug image: {getattr(arr, 'shape', arr)}")


def _downscale_pil(im: Image.Image, max_pixels: int) -> Image.Image:
    w, h = im.size
    if max_pixels <= 0 or w * h <= max_pixels:
        return im
    ratio = (max_pixels / float(w * h)) ** 0.5
    return im.resize((max(1, int(w * ratio)), max(1, int(h * ratio))), Image.LANCZOS)


def write_debug_image(
    path: str | Path,
    img: Any,
    *,
    max_pixels: int | None = None,
) -> bool:
    """Write a workspace debug artifact with optional downscale and compression.

    Accepts OpenCV BGR ndarrays or PIL Images. PNG paths stay PNG (compress_level=9);
    JPEG paths use configured quality. Parent directories are created as needed.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    if isinstance(img, Image.Image):
        pil = img
    else:
        try:
            pil = _pil_from_array(img)
        except Exception:
            return False

    pil = _downscale_pil(
        pil,
        _debug_max_pixels() if max_pixels is None else int(max_pixels),
    )
    suffix = p.suffix.lower()
    jpeg_quality = _debug_jpeg_quality()

    try:
        if suffix in (".jpg", ".jpeg"):
            if pil.mode in ("RGBA", "P", "LA"):
                pil = pil.convert("RGB")
            pil.save(p, format="JPEG", quality=jpeg_quality, optimize=True)
        elif suffix == ".png":
            pil.save(p, format="PNG", compress_level=9, optimize=True)
        else:
            pil.save(p, compress_level=9, optimize=True)
        return True
    except Exception:
        return False


def truncate(text: str, limit: int = 1200) -> str:
    """Truncate long strings for display/feed-back into the LLM."""
    if text is None:
        return ""
    if len(text) <= limit:
        return text
    keep = limit // 2 - 20
    return text[:keep] + f"\n... [truncated {len(text) - 2 * keep} chars] ...\n" + text[-keep:]
