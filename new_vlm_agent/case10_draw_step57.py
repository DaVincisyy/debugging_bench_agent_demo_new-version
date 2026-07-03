"""case_010 Part D: redraw debug/step57_candidates_scored.png on the board ROI crop.

Draws all candidates from step05_candidates.json (winner in green, others red) and overlays
``board_roi_target_px_approx`` from case10_tp_dual_roi_direct_vlm.json (magenta cross, label
``VLM approx``). Path A: approx is QC'd on ``step04_dual_roi_approx_only.png``; refined center
/radius default from ``case10_dual_roi_refine.run_snap_nearest_from_workspace`` (VLM approx +
scaled ``r_vis`` from locator graph; optional CV snap via ``geom_refine.mode``) or D4.5C pick fallback.

Expected cwd / workspace layout (agent usually cwd=workspace):
  - debug/step04_roi_crop.png
  - debug/step05_candidates.json
  - debug/case10_tp_dual_roi_direct_vlm.json (optional but typical for case10)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import cv2


def write_step57_debug(workspace: Path | None = None) -> Path:
    ws = (workspace or Path(os.environ.get("WORKSPACE", "."))).resolve()
    dbg = ws / "debug"
    roi_path = dbg / "step04_roi_crop.png"
    step05_path = dbg / "step05_candidates.json"
    vlm_path = dbg / "case10_tp_dual_roi_direct_vlm.json"
    out_path = dbg / "step57_candidates_scored.png"

    img = cv2.imread(str(roi_path))
    if img is None:
        raise FileNotFoundError(f"Cannot read ROI: {roi_path}")

    if step05_path.is_file():
        data = json.loads(step05_path.read_text(encoding="utf-8"))
        cands = data.get("candidates") or []
        win_id = data.get("winner_id")
        for c in cands:
            cx = int(round(float(c.get("cx", 0))))
            cy = int(round(float(c.get("cy", 0))))
            r = max(2, int(round(float(c.get("r_vis", 8)))))
            cid = str(c.get("id", ""))
            is_win = win_id is not None and cid == str(win_id)
            color = (0, 200, 0) if is_win else (0, 0, 255)
            thick = 2 if is_win else 1
            cv2.circle(img, (cx, cy), r, color, thick)
            cv2.putText(
                img,
                str(cid),
                (cx + 4, cy - 4),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.4,
                color,
                1,
            )

    if vlm_path.is_file():
        try:
            d = json.loads(vlm_path.read_text(encoding="utf-8"))
            ap = d.get("board_roi_target_px_approx")
            if isinstance(ap, list) and len(ap) == 2:
                gx = int(round(float(ap[0])))
                gy = int(round(float(ap[1])))
                h, w = img.shape[:2]
                gx = max(0, min(w - 1, gx))
                gy = max(0, min(h - 1, gy))
                col = (255, 0, 255)
                cv2.drawMarker(
                    img,
                    (gx, gy),
                    col,
                    markerType=cv2.MARKER_CROSS,
                    markerSize=16,
                    thickness=2,
                )
                cv2.circle(img, (gx, gy), 8, col, 1)
                cv2.putText(
                    img,
                    "VLM approx",
                    (min(gx + 10, w - 120), max(gy - 10, 14)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.45,
                    col,
                    1,
                    cv2.LINE_AA,
                )
                refs = d.get("board_roi_reference_approx")
                ref_colors = [
                    (0, 255, 255),
                    (0, 165, 255),
                    (255, 128, 0),
                    (203, 192, 255),
                    (147, 20, 255),
                ]
                if isinstance(refs, list):
                    for i, item in enumerate(refs):
                        if not isinstance(item, dict):
                            continue
                        xy = item.get("center_px") or item.get("xy")
                        if not isinstance(xy, list) or len(xy) != 2:
                            continue
                        rcx = int(round(float(xy[0])))
                        rcy = int(round(float(xy[1])))
                        rcx = max(0, min(w - 1, rcx))
                        rcy = max(0, min(h - 1, rcy))
                        rcol = ref_colors[i % len(ref_colors)]
                        cv2.line(img, (gx, gy), (rcx, rcy), rcol, 1, cv2.LINE_AA)
                        cv2.drawMarker(
                            img,
                            (rcx, rcy),
                            rcol,
                            markerType=cv2.MARKER_CROSS,
                            markerSize=12,
                            thickness=2,
                        )
                        rid = str(item.get("ref_id") or f"ref_{i + 1}")
                        cv2.putText(
                            img,
                            rid,
                            (min(rcx + 6, w - 50), max(rcy - 6, 12)),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.4,
                            rcol,
                            1,
                            cv2.LINE_AA,
                        )
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

    cv2.imwrite(str(out_path), img)
    return out_path


def run_from_workspace(workspace: Path | None = None) -> Path:
    return write_step57_debug(workspace)


if __name__ == "__main__":
    root = Path(__file__).resolve().parent
    out = write_step57_debug(root / "workspace" if (root / "workspace").is_dir() else root)
    print(out)
    sys.exit(0)
