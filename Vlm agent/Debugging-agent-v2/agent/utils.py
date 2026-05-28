"""Small helpers shared by tools and the agent core."""

from __future__ import annotations

import base64
import mimetypes
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
