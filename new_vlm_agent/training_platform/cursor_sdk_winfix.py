"""Windows workaround for cursor-sdk bridge discovery (WinError 10038)."""

from __future__ import annotations

import codecs
import os
import time
from typing import Any, Mapping


def apply_windows_bridge_patch() -> bool:
    """Patch cursor_sdk._bridge._read_discovery on Windows. Idempotent."""
    if os.name != "nt":
        return False

    import cursor_sdk._bridge as bridge_mod

    if getattr(bridge_mod._read_discovery, "_winfix_applied", False):
        return True

    from cursor_sdk.errors import CursorSDKError

    parse_discovery_line = bridge_mod.parse_discovery_line

    def _read_discovery_win(process: Any, timeout: float) -> Mapping[str, Any]:
        if process.stderr is None:
            raise CursorSDKError("Bridge process stderr is unavailable")
        stderr_fd = process.stderr.fileno()
        was_blocking = os.get_blocking(stderr_fd)
        os.set_blocking(stderr_fd, False)
        try:
            decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            deadline = time.monotonic() + timeout
            stderr_lines: list[str] = []
            pending = ""

            def drain_available() -> Mapping[str, Any] | None:
                nonlocal pending
                while True:
                    try:
                        chunk = os.read(stderr_fd, 8192)
                    except BlockingIOError:
                        return None
                    if not chunk:
                        final_text = decoder.decode(b"", final=True)
                        if final_text:
                            pending += final_text
                        if pending:
                            line = pending
                            pending = ""
                            stderr_lines.append(line)
                            return parse_discovery_line(line)
                        return None
                    pending += decoder.decode(chunk)
                    while "\n" in pending:
                        line, pending = pending.split("\n", 1)
                        line += "\n"
                        stderr_lines.append(line)
                        discovery = parse_discovery_line(line)
                        if discovery is not None:
                            return discovery

            while time.monotonic() < deadline:
                discovery = drain_available()
                if discovery is not None:
                    return discovery
                exit_code = process.poll()
                if exit_code is not None:
                    discovery = drain_available()
                    if discovery is not None:
                        return discovery
                    raise CursorSDKError(
                        f"Bridge exited before discovery with status {exit_code}: "
                        + "".join(stderr_lines)
                        + pending
                    )
                time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
            raise CursorSDKError("Timed out waiting for bridge discovery")
        finally:
            os.set_blocking(stderr_fd, was_blocking)

    _read_discovery_win._winfix_applied = True  # type: ignore[attr-defined]
    bridge_mod._read_discovery = _read_discovery_win
    return True
