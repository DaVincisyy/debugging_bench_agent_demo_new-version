"""case_010: map TP415 green circle from assembly locator to board photo using IC red-box alignment.

Uses a homography from the four corners of the assembly IC rectangle to the board IC rectangle
(same corner order). Transforms the green test-point circle center and scales radius.

Expected layout under *workspace* directory (agent `run_python` cwd is workspace):
  - debug/case10_assembly_largest_ic_box.png  (green TP circle + red IC box)
  - debug/case10_largest_ic_box.png            (red IC box on board)
  - debug/case10_assembly_largest_ic.json     {"bbox": [l,t,r,b], ...}
  - debug/case10_largest_ic.json               {"bbox": [l,t,r,b], ...}

Writes:
  - debug/board_tp_marked.png
  - debug/case10_tp_board_mapping.json (homography + mapped circle, for debugging)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import cv2
import numpy as np


def _rect_corners(l: int, t: int, r: int, b: int) -> np.ndarray:
    return np.array(
        [[l, t], [r, t], [r, b], [l, b]],
        dtype=np.float32,
    )


def _load_bbox_json(path: Path) -> list[int]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    bb = data.get("bbox")
    if not isinstance(bb, (list, tuple)) or len(bb) != 4:
        raise ValueError(f"{path}: missing or invalid bbox")
    return [int(x) for x in bb]


def _detect_red_bbox(img_bgr: np.ndarray) -> tuple[int, int, int, int]:
    b, g, r = cv2.split(img_bgr)
    mask = (r > 200) & (g < 90) & (b < 90)
    mask_u8 = (mask.astype(np.uint8) * 255)
    mask_u8 = cv2.morphologyEx(mask_u8, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8), iterations=1)
    cnts, _ = cv2.findContours(mask_u8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        raise RuntimeError("case10 board_tp_marked: no red mask for IC bbox fallback")
    best = max(cnts, key=cv2.contourArea)
    x, y, w, h = cv2.boundingRect(best)
    return x, y, x + w, y + h


def _detect_green_circle(img_bgr: np.ndarray) -> tuple[float, float, float]:
    b, g, r = cv2.split(img_bgr)
    mask = (g > 180) & (r < 120) & (b < 120)
    mask_u8 = (mask.astype(np.uint8) * 255)
    mask_u8 = cv2.morphologyEx(mask_u8, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8), iterations=2)
    cnts, _ = cv2.findContours(mask_u8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        raise RuntimeError("case10 board_tp_marked: no green TP mask on locator image")
    # Prefer nearly circular, sufficiently large blob
    scored: list[tuple[float, tuple[float, float, float]]] = []
    for c in cnts:
        a = float(cv2.contourArea(c))
        if a < 30.0:
            continue
        peri = cv2.arcLength(c, True)
        if peri <= 1e-6:
            continue
        circ = 4.0 * np.pi * a / (peri * peri)
        (cx, cy), rad = cv2.minEnclosingCircle(c)
        scored.append((circ * a, (float(cx), float(cy), float(rad))))
    if not scored:
        raise RuntimeError("case10 board_tp_marked: green contours too small or degenerate")
    scored.sort(key=lambda x: x[0], reverse=True)
    cx, cy, rad = scored[0][1]
    return cx, cy, rad


def run_from_workspace(ws: Path | None = None) -> dict[str, Any]:
    ws = Path(ws) if ws is not None else Path.cwd()
    debug = ws / "debug"
    loc_img = debug / "case10_assembly_largest_ic_box.png"
    board_img = debug / "case10_largest_ic_box.png"
    asm_json = debug / "case10_assembly_largest_ic.json"
    brd_json = debug / "case10_largest_ic.json"
    out_img = debug / "board_tp_marked.png"
    out_meta = debug / "case10_tp_board_mapping.json"

    if not loc_img.is_file():
        raise FileNotFoundError(loc_img)
    if not board_img.is_file():
        raise FileNotFoundError(board_img)

    img_a = cv2.imread(str(loc_img))
    img_b = cv2.imread(str(board_img))
    if img_a is None or img_b is None:
        raise RuntimeError("case10 board_tp_marked: failed to read input PNGs")

    if asm_json.is_file():
        al, at, ar, ab = _load_bbox_json(asm_json)
    else:
        print("[board_tp_marked] warning: missing case10_assembly_largest_ic.json; detecting red bbox on locator", file=sys.stderr)
        al, at, ar, ab = _detect_red_bbox(img_a)

    if brd_json.is_file():
        bl, bt, br, bb = _load_bbox_json(brd_json)
    else:
        print("[board_tp_marked] warning: missing case10_largest_ic.json; detecting red bbox on board", file=sys.stderr)
        bl, bt, br, bb = _detect_red_bbox(img_b)

    if ar <= al or ab <= at or br <= bl or bb <= bt:
        raise ValueError("invalid assembly or board IC bbox (need l<t r>b with r>l, b>t)")

    src = _rect_corners(al, at, ar, ab)
    dst = _rect_corners(bl, bt, br, bb)
    h_mat, status = cv2.findHomography(src, dst)
    if h_mat is None:
        raise RuntimeError("case10 board_tp_marked: homography failed")

    gx, gy, gr = _detect_green_circle(img_a)
    gvec = np.array([gx, gy, 1.0], dtype=np.float64)
    mapped = h_mat @ gvec
    if abs(mapped[2]) < 1e-9:
        raise RuntimeError("case10 board_tp_marked: singular homogeneous w")
    mx = float(mapped[0] / mapped[2])
    my = float(mapped[1] / mapped[2])

    # Scale radius: RMS of edge lengths assembly -> board
    aw, ah = ar - al, ab - at
    bw, bh = br - bl, bb - bt
    scale = np.sqrt((bw / max(aw, 1)) * (bh / max(ah, 1)))
    mr = float(max(3.0, gr * scale))

    out = img_b.copy()
    mi = int(round(mx))
    mj = int(round(my))
    ri = int(round(mr))
    cv2.circle(out, (mi, mj), ri, (0, 255, 0), thickness=3, lineType=cv2.LINE_AA)
    cv2.imwrite(str(out_img), out)

    meta = {
        "assembly_ic_bbox": [al, at, ar, ab],
        "board_ic_bbox": [bl, bt, br, bb],
        "assembly_tp_circle_xyr": [gx, gy, gr],
        "board_tp_circle_xyr_mapped": [mx, my, mr],
        "homography_row_major": h_mat.tolist(),
        "inliers": int(np.asarray(status).sum()) if status is not None else 4,
        "out_image": str(out_img.as_posix()),
    }
    out_meta.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[board_tp_marked] wrote {out_img}")
    print(f"[board_tp_marked] mapped TP center ({mx:.1f}, {my:.1f}) r≈{mr:.1f}")
    return meta


def main() -> None:
    repo = Path(__file__).resolve().parent
    ws = repo / "workspace"
    run_from_workspace(ws)


if __name__ == "__main__":
    main()
