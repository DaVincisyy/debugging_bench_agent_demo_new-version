"""Small helpers shared by tools and the agent core."""

from __future__ import annotations

import base64
import mimetypes
import os
from io import BytesIO
from pathlib import Path


def encode_image_data_url(path: str | Path) -> str:
    """Return a `data:image/...;base64,...` URL for a local image file.

    Works with any OpenAI-compatible VLM: we never upload files, we just
    embed base64 bytes into the chat message, which is the most portable
    way to send images across providers.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Image not found: {p}")

    max_edge = int(os.getenv("VLM_INLINE_IMAGE_MAX_EDGE", "1800") or "0")
    jpeg_quality = int(os.getenv("VLM_INLINE_IMAGE_JPEG_QUALITY", "82") or "82")
    if max_edge > 0:
        try:
            from PIL import Image

            with Image.open(p) as im:
                if max(im.size) > max_edge:
                    im = im.convert("RGB")
                    im.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
                    buf = BytesIO()
                    im.save(buf, format="JPEG", quality=jpeg_quality, optimize=True)
                    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
                    return f"data:image/jpeg;base64,{b64}"
        except Exception:
            # Fall back to original bytes. Tool paths remain available, so a
            # provider-side rejection can still be debugged from the trace.
            pass

    mime, _ = mimetypes.guess_type(p.name)
    if mime is None:
        mime = "image/png"
    b64 = base64.b64encode(p.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def truncate(text: str, limit: int = 1200) -> str:
    """Truncate long strings for display/feed-back into the LLM."""
    if text is None:
        return ""
    if len(text) <= limit:
        return text
    keep = limit // 2 - 20
    return text[:keep] + f"\n... [truncated {len(text) - 2 * keep} chars] ...\n" + text[-keep:]
