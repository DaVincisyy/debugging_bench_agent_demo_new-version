"""Deterministic RTO6 mean-voltage display capture bridge.

Reads one JSON request from stdin and writes one JSON response to stdout.
The only supported action changes measurement slot 1's function to MEAN while
preserving its current source, reads the result, and downloads the display.
"""

from __future__ import annotations

import json
import math
import socket
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


class ScpiSession:
    def __init__(self, host: str, port: int, timeout_sec: float):
        self.host = host
        self.port = port
        self.timeout_sec = timeout_sec
        self.socket: socket.socket | None = None
        self.buffer = b""

    def connect(self) -> None:
        self.socket = socket.create_connection(
            (self.host, self.port),
            timeout=self.timeout_sec,
        )
        self.socket.settimeout(self.timeout_sec)

    def close(self) -> None:
        if self.socket is not None:
            self.socket.close()
            self.socket = None

    def write(self, command: str) -> None:
        if self.socket is None:
            raise RuntimeError("SCPI session is not connected")
        self.socket.sendall((command + "\n").encode("ascii"))

    def query(self, command: str) -> str:
        self.write(command)
        while b"\n" not in self.buffer:
            if self.socket is None:
                raise RuntimeError("SCPI session is not connected")
            chunk = self.socket.recv(8192)
            if not chunk:
                raise ConnectionError("RTO6 closed the SCPI connection")
            self.buffer += chunk
        line, self.buffer = self.buffer.split(b"\n", 1)
        return line.decode("utf-8", errors="replace").strip()


def download_screenshot(host: str, output_path: Path, timeout_sec: float) -> int:
    base = f"http://{host}/instrumentctrl/html/php"
    trigger_url = f"{base}/makescreenshot.php?metaDataOn=false"
    with urllib.request.urlopen(trigger_url, timeout=timeout_sec) as response:
        remote_name = response.read().decode("utf-8", errors="replace").strip()
    if not remote_name.lower().endswith((".jpg", ".jpeg", ".png")):
        raise RuntimeError(f"RTO6 screenshot trigger returned unexpected value: {remote_name}")

    download_url = (
        f"{base}/makescreenshotdownload.php"
        f"?filename={urllib.parse.quote(remote_name)}&usage=display"
    )
    with urllib.request.urlopen(download_url, timeout=max(timeout_sec, 30.0)) as response:
        image_bytes = response.read()
    if len(image_bytes) < 1024:
        raise RuntimeError(f"RTO6 screenshot response is too small: {len(image_bytes)} bytes")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(image_bytes)
    return len(image_bytes)


def capture_current_display(payload: dict[str, Any]) -> dict[str, Any]:
    config = payload.get("config") or {}
    host = str(config.get("host") or "192.168.2.3")
    port = int(config.get("port") or 5025)
    measurement_slot = int(config.get("measurementSlot") or 1)
    desired_measurement = str(config.get("desiredMeasurement") or "MEAN").upper()
    if measurement_slot != 1:
        raise ValueError("This workflow currently reads measurement slot 1")
    if desired_measurement != "MEAN":
        raise ValueError("This workflow is locked to the MEAN measurement function")
    timeout_sec = float(config.get("timeoutMs") or 10000) / 1000.0
    screenshot_path = Path(payload["screenshotPath"]).resolve()

    started = time.time()
    session = ScpiSession(host, port, timeout_sec)
    try:
        session.connect()
        identity = session.query("*IDN?")
        source = session.query(f"MEASurement{measurement_slot}:SOURce?")
        session.write(f"MEASurement{measurement_slot}:MAIN {desired_measurement}")
        measurement = session.query(f"MEASurement{measurement_slot}:MAIN?")
        if measurement.upper() != desired_measurement:
            raise RuntimeError(
                f"RTO6 rejected {desired_measurement}; measurement slot "
                f"{measurement_slot} is still {measurement}"
            )
        value = None
        for _ in range(5):
            candidate = float(session.query(f"MEASurement{measurement_slot}:RESult?"))
            if math.isfinite(candidate) and abs(candidate) < 1e36:
                value = candidate
                break
            time.sleep(1.0)
        if value is None:
            raise RuntimeError(
                f"RTO6 measurement slot {measurement_slot} did not return a valid finite result"
            )
    finally:
        session.close()

    # The tested RTO6 Web UI screenshot endpoint requires the SCPI socket to
    # be released before it renders and downloads the current instrument view.
    time.sleep(2.0)
    screenshot_bytes = download_screenshot(host, screenshot_path, timeout_sec)
    return {
        "ok": True,
        "instrument": identity,
        "host": host,
        "port": port,
        "measurementSlot": measurement_slot,
        "measurement": measurement,
        "source": source,
        "value": value,
        "unit": "V",
        "screenshotPath": str(screenshot_path),
        "screenshotBytes": screenshot_bytes,
        "capturedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "durationMs": round((time.time() - started) * 1000),
    }


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        action = payload.get("action")
        if action != "capture_current_display":
            raise ValueError(f"Unsupported RTO6 action: {action}")
        print(json.dumps(capture_current_display(payload), ensure_ascii=False))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
