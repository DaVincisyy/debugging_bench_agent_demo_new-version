"""case_010 Part D path A: locator-side reference graph → VLM multi-point approx → QC overlay → snap.

Primary flow
------------
0. ``run_build_locator_refs_from_workspace`` — after ``debug/step04_locator_roi_crop.png``,
   writes ``debug/case10_dual_roi_locator_refs.json`` + ``debug/step04_locator_roi_refs.png``
   (OpenCV boxes/circles + tp→ref edges + **pairwise** ref–ref / tp–ref distances &
   vectors in ``pairwise_roi``, so the anchor set is a rigid graph for VLM→board matching).

1. VLM reads ``step04_locator_roi_refs.png``, ``case10_dual_roi_locator_refs.json``,
   ``step04_roi_crop.png`` → ``case10_tp_dual_roi_direct_vlm.json`` including
   ``board_roi_target_px_approx`` and ``board_roi_reference_approx`` (same ``ref_id`` as JSON).

2. ``run_write_approx_overlay_from_workspace`` — ``debug/step04_dual_roi_approx_only.png``
   (board ROI + tp approx + optional ref crosses/lines; no OpenCV pad candidates).

3. Agent ``view_image`` that PNG, checks consistency, may revise approx **at most once**.

4. ``run_dual_roi_finalize_from_workspace`` — default **no CV snap**: center =
   VLM ``board_roi_target_px_approx``; radius = ``tp_green_radius_px`` × scale,
   where **scale** is median(ref–ref board / ref–ref locator), else median(tp–ref
   board / tp–ref locator). Optional legacy **snap** when ``geom_refine.mode`` is
   ``snap_nearest_in_search_radius``.

Fallback (optional): ``enumerate`` / ``apply_pick`` when snap fails or is ambiguous.

Expected cwd: workspace root (``WORKSPACE`` env or ``--workspace``).
"""

from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path
from typing import Any

import cv2
import numpy as np

CAND_JSON = "case10_tp_dual_roi_direct_candidates.json"
CAND_PNG = "step04_dual_roi_direct_candidates.png"
VLM_JSON = "case10_tp_dual_roi_direct_vlm.json"
PICK_JSON = "case10_tp_dual_roi_vlm_pick.json"
REFINED_JSON = "case10_tp_dual_roi_direct_refined.json"
ROI_PNG = "step04_roi_crop.png"
LOCATOR_ROI_PNG = "step04_locator_roi_crop.png"
LOCATOR_REFS_JSON = "case10_dual_roi_locator_refs.json"
LOCATOR_REFS_PNG = "step04_locator_roi_refs.png"
APPROX_ONLY_PNG = "step04_dual_roi_approx_only.png"
SNAP_PNG = "step04_dual_roi_direct_snap.png"

# BGR palette for reference markers on board overlay (target stays magenta).
_REF_OVERLAY_COLORS_BGR: list[tuple[int, int, int]] = [
    (0, 255, 255),  # yellow
    (0, 165, 255),  # orange
    (255, 128, 0),  # azure / blue-orange
    (203, 192, 255),  # pink
    (147, 20, 255),  # violet
]


def _circularity(area: float, peri: float) -> float:
    if peri <= 1e-6:
        return 0.0
    return float(4.0 * math.pi * area / (peri * peri))


def _merge_dupes(
    items: list[tuple[float, float, float, float, float]],
    merge_dup_px: float,
) -> list[tuple[float, float, float, float, float]]:
    if not items:
        return []
    items = sorted(items, key=lambda t: -t[4])
    kept: list[tuple[float, float, float, float, float]] = []
    for x, y, r, circ, area in items:
        dup = False
        for i, (kx, ky, _kr, _kc, ka) in enumerate(kept):
            if math.hypot(x - kx, y - ky) <= merge_dup_px:
                if area > ka:
                    kept[i] = (x, y, r, circ, area)
                dup = True
                break
        if not dup:
            kept.append((x, y, r, circ, area))
    return kept


def _hough_circle_candidates(
    gray: np.ndarray,
    *,
    min_r: int = 3,
    max_r: int = 45,
    min_dist: int = 8,
) -> list[tuple[float, float, float, float, float]]:
    blurred = cv2.medianBlur(gray, 5)
    circles = cv2.HoughCircles(
        blurred,
        cv2.HOUGH_GRADIENT,
        dp=1.5,
        minDist=float(min_dist),
        param1=80,
        param2=int(max(22, min_dist)),
        minRadius=min_r,
        maxRadius=max_r,
    )
    out: list[tuple[float, float, float, float, float]] = []
    if circles is None:
        return out
    for c in circles[0]:
        cx, cy, r = float(c[0]), float(c[1]), float(c[2])
        area = math.pi * r * r
        peri = 2 * math.pi * max(r, 1e-3)
        circ = _circularity(area, peri)
        out.append((cx, cy, r, circ, area))
    return out


def _detect_candidates(
    roi_bgr: np.ndarray,
    *,
    min_circularity: float,
    min_area: int,
    max_area_frac: float,
    blur_ksize: int = 5,
) -> list[tuple[float, float, float, float, float]]:
    h, w = roi_bgr.shape[:2]
    max_area = max(min_area + 1, int(h * w * max_area_frac))
    gray = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2GRAY)
    k = max(3, blur_ksize | 1)
    blurred = cv2.GaussianBlur(gray, (k, k), 0)
    _, th = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    th = cv2.dilate(th, np.ones((3, 3), np.uint8), iterations=1)
    contours, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out: list[tuple[float, float, float, float, float]] = []
    for c in contours:
        area = float(cv2.contourArea(c))
        if area < min_area or area > max_area:
            continue
        peri = float(cv2.arcLength(c, True))
        circ = _circularity(area, peri)
        if circ < min_circularity:
            continue
        (cx, cy), r = cv2.minEnclosingCircle(c)
        out.append((float(cx), float(cy), float(r), circ, area))
    return out


def _merged_circle_detections(
    roi: np.ndarray, geom: dict[str, Any]
) -> list[tuple[float, float, float, float, float]]:
    min_circ = float(geom.get("min_circularity", 0.55))
    merge_dup_px = float(geom.get("merge_dup_px", 12))
    raw = _detect_candidates(
        roi,
        min_circularity=min_circ,
        min_area=int(geom.get("min_contour_area", 40)),
        max_area_frac=float(geom.get("max_contour_area_frac", 0.12)),
    )
    if not raw:
        raw = _detect_candidates(
            roi,
            min_circularity=max(0.35, min_circ * 0.75),
            min_area=max(15, int(int(geom.get("min_contour_area", 40)) * 0.45)),
            max_area_frac=min(0.35, float(geom.get("max_contour_area_frac", 0.12)) * 2.5),
        )
    if not raw:
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        raw = _hough_circle_candidates(
            gray,
            min_r=int(geom.get("hough_min_radius", 4)),
            max_r=int(geom.get("hough_max_radius", 22)),
            min_dist=int(geom.get("hough_min_dist", 14)),
        )
    merged = _merge_dupes(raw, merge_dup_px)
    merged.sort(key=lambda t: (round(t[1]), round(t[0])))
    return merged


def _detect_green_tp_center(bgr: np.ndarray) -> tuple[float, float, float] | None:
    """Largest (0,255,0)-like region in locator ROI → circle center + radius."""
    b, g, r = cv2.split(bgr)
    mask = ((g > 130) & (r < 130) & (b < 130)).astype(np.uint8) * 255
    if int(mask.sum()) < 80:
        mask = ((g > 110) & (r < 150) & (b < 150) & (g > r + 35) & (g > b + 35)).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    best = max(contours, key=cv2.contourArea)
    if cv2.contourArea(best) < 30.0:
        return None
    (cx, cy), rad = cv2.minEnclosingCircle(best)
    return float(cx), float(cy), float(rad)


def _polar_from_tp(
    tp_xy: tuple[float, float], pt_xy: tuple[float, float]
) -> tuple[float, float]:
    dx = pt_xy[0] - tp_xy[0]
    dy = pt_xy[1] - tp_xy[1]
    dist = float(math.hypot(dx, dy))
    # atan2 in image coords (+x right, +y down); angle radians CCW from +x if y were up;
    # here dy positive is downward — still useful as a stable relative bearing.
    ang_deg = float(math.degrees(math.atan2(dy, dx)))
    return dist, ang_deg


def _node_sort_key(nid: str) -> tuple[int, int]:
    if nid == "tp":
        return (0, 0)
    if nid.startswith("ref_"):
        try:
            return (1, int(nid.split("_", 1)[1]))
        except ValueError:
            return (1, 9999)
    return (2, 0)


def build_pairwise_roi(
    centers: dict[str, tuple[float, float]],
) -> list[dict[str, Any]]:
    """Undirected complete graph on ``tp`` + refs: each pair once, ordered by ``_node_sort_key``.

    Fields per edge (from ``a`` toward ``b``, with ``a`` lex-before ``b``): Euclidean
    ``dist_px``, ``dx_a_to_b``, ``dy_a_to_b``, ``angle_deg_atan2_dy_dx``. Useful so that on
    the **board** ROI, once any two anchor points are fixed, the rest are geometrically
    overdetermined (same topology as locator side).
    """
    if len(centers) < 2:
        return []
    ids = sorted(centers.keys(), key=lambda n: (_node_sort_key(n), n))
    out: list[dict[str, Any]] = []
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a, b = ids[i], ids[j]
            ax, ay = centers[a]
            bx, by = centers[b]
            dx = float(bx - ax)
            dy = float(by - ay)
            dist = float(math.hypot(dx, dy))
            ang_deg = float(math.degrees(math.atan2(dy, dx)))
            out.append(
                {
                    "a": a,
                    "b": b,
                    "dist_px": round(dist, 3),
                    "dx_a_to_b": round(dx, 3),
                    "dy_a_to_b": round(dy, 3),
                    "angle_deg_atan2_dy_dx": round(ang_deg, 3),
                }
            )
    return out


def _rect_like_references(
    bgr: np.ndarray,
    tp_xy: tuple[float, float],
    tp_r: float,
    *,
    exclude_mask: np.ndarray | None,
) -> list[dict[str, Any]]:
    h, w = bgr.shape[:2]
    roi_area = float(h * w)
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    _, th = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    th = cv2.morphologyEx(th, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8), iterations=1)
    contours, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out: list[dict[str, Any]] = []
    for c in contours:
        x, y, cw, ch = cv2.boundingRect(c)
        area = float(cw * ch)
        if area < 350 or area > 0.42 * roi_area:
            continue
        if ch <= 2 or cw <= 2:
            continue
        ar = cw / float(ch)
        if ar < 0.07 or ar > 14.0:
            continue
        cx = x + cw / 2.0
        cy = y + ch / 2.0
        dist = math.hypot(cx - tp_xy[0], cy - tp_xy[1])
        if dist < max(10.0, tp_r * 0.85):
            continue
        if exclude_mask is not None and 0 <= int(cy) < h and 0 <= int(cx) < w:
            if exclude_mask[int(cy), int(cx)] > 0:
                continue
        out.append(
            {
                "kind": "ic_rect",
                "center": (cx, cy),
                "bbox": (int(x), int(y), int(x + cw), int(y + ch)),
                "dist": dist,
            }
        )
    out.sort(key=lambda z: z["dist"])
    return out


def _pad_circle_references(
    bgr: np.ndarray,
    tp_xy: tuple[float, float],
    tp_r: float,
) -> list[dict[str, Any]]:
    geom_relaxed = {
        "min_circularity": 0.45,
        "merge_dup_px": 14,
        "min_contour_area": 25,
        "max_contour_area_frac": 0.14,
    }
    merged = _merged_circle_detections(bgr, geom_relaxed)
    h, w = bgr.shape[:2]
    out: list[dict[str, Any]] = []
    for cx, cy, r, _circ, _a in merged:
        dist = math.hypot(cx - tp_xy[0], cy - tp_xy[1])
        if dist < max(8.0, tp_r + 6.0):
            continue
        if dist > max(w, h) * 0.75:
            continue
        side = max(r * 2.2, 14.0)
        x0 = int(round(cx - side))
        y0 = int(round(cy - side))
        x1 = int(round(cx + side))
        y1 = int(round(cy + side))
        out.append(
            {
                "kind": "pad_circle",
                "center": (float(cx), float(cy)),
                "bbox": (x0, y0, x1, y1),
                "dist": dist,
            }
        )
    out.sort(key=lambda z: z["dist"])
    return out


def _pick_diverse_refs(
    pool: list[dict[str, Any]],
    tp_xy: tuple[float, float],
    k: int = 3,
    *,
    min_angle_sep_deg: float = 20.0,
) -> list[dict[str, Any]]:
    if not pool:
        return []
    chosen: list[dict[str, Any]] = []
    angles: list[float] = []
    for cand in sorted(pool, key=lambda z: z["dist"]):
        cx, cy = cand["center"]
        _, ang = _polar_from_tp(tp_xy, (cx, cy))
        ok = True
        for a0 in angles:
            da = abs(((ang - a0) + 180.0) % 360.0 - 180.0)
            if da < min_angle_sep_deg:
                ok = False
                break
        if ok or len(chosen) == 0:
            chosen.append(cand)
            angles.append(ang)
        if len(chosen) >= k:
            break
    if len(chosen) < k:
        for cand in pool:
            if cand in chosen:
                continue
            chosen.append(cand)
            if len(chosen) >= k:
                break
    return chosen[:k]


def run_build_locator_refs_from_workspace(
    workspace: os.PathLike[str] | str | None = None,
    *,
    n_refs: int = 3,
) -> dict[str, Any]:
    """After ``step04_locator_roi_crop.png``: OpenCV picks ~3 refs, edges, metrics → JSON + annotated PNG."""
    ws = Path(workspace or os.environ.get("WORKSPACE", ".")).resolve()
    dbg = ws / "debug"
    crop_path = dbg / LOCATOR_ROI_PNG
    out_json = dbg / LOCATOR_REFS_JSON
    out_png = dbg / LOCATOR_REFS_PNG

    if not crop_path.is_file():
        raise FileNotFoundError(f"Missing {crop_path}")
    bgr = cv2.imread(str(crop_path))
    if bgr is None:
        raise FileNotFoundError(f"Cannot read {crop_path}")

    h, w = bgr.shape[:2]
    tp = _detect_green_tp_center(bgr)
    if tp is None:
        payload: dict[str, Any] = {
            "schema_version": 2,
            "tp_center_roi": None,
            "tp_green_radius_px": None,
            "references": [],
            "graph_edges": [],
            "pairwise_roi": [],
            "notes": "green_tp_not_detected_in_roi",
        }
        out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        cv2.imwrite(str(out_png), bgr.copy())
        print(f"Wrote {out_json} (no green TP)")
        return payload

    tp_xy = (tp[0], tp[1])
    tp_r = float(tp[2])

    gh, gw = bgr.shape[:2]
    green_mask = np.zeros((gh, gw), dtype=np.uint8)
    cv2.circle(
        green_mask,
        (int(round(tp_xy[0])), int(round(tp_xy[1]))),
        int(max(6, round(tp_r + 4))),
        255,
        -1,
    )

    rects = _rect_like_references(bgr, tp_xy, tp_r, exclude_mask=green_mask)
    pads = _pad_circle_references(bgr, tp_xy, tp_r)
    pool: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    for cand in rects + pads:
        key = (int(round(cand["center"][0]) // 4), int(round(cand["center"][1]) // 4))
        if key in seen:
            continue
        seen.add(key)
        pool.append(cand)

    picked = _pick_diverse_refs(pool, tp_xy, k=max(1, min(int(n_refs), 5)))

    references: list[dict[str, Any]] = []
    graph_edges: list[dict[str, Any]] = []
    for i, p in enumerate(picked, start=1):
        rid = f"ref_{i}"
        cx, cy = p["center"]
        bx = p["bbox"]
        dist, ang = _polar_from_tp(tp_xy, (cx, cy))
        references.append(
            {
                "ref_id": rid,
                "kind": p["kind"],
                "center_roi": [round(cx, 2), round(cy, 2)],
                "bbox_roi": [bx[0], bx[1], bx[2], bx[3]],
                "dist_to_tp_px": round(dist, 3),
                "angle_deg_atan2_dy_dx": round(ang, 3),
            }
        )
        graph_edges.append(
            {
                "from": "tp",
                "to": rid,
                "dist_px": round(dist, 3),
                "angle_deg_atan2_dy_dx": round(ang, 3),
            }
        )

    centers: dict[str, tuple[float, float]] = {"tp": tp_xy}
    for ref in references:
        cx, cy = ref["center_roi"]
        centers[ref["ref_id"]] = (float(cx), float(cy))
    pairwise_roi = build_pairwise_roi(centers)

    payload = {
        "schema_version": 2,
        "tp_center_roi": [round(tp_xy[0], 3), round(tp_xy[1], 3)],
        "tp_green_radius_px": round(tp_r, 3),
        "references": references,
        "graph_edges": graph_edges,
        "pairwise_roi": pairwise_roi,
    }
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    vis = bgr.copy()
    cv2.circle(
        vis,
        (int(round(tp_xy[0])), int(round(tp_xy[1]))),
        max(3, int(round(tp_r))),
        (0, 255, 0),
        2,
    )
    cv2.drawMarker(
        vis,
        (int(round(tp_xy[0])), int(round(tp_xy[1]))),
        (0, 255, 0),
        markerType=cv2.MARKER_CROSS,
        markerSize=16,
        thickness=2,
    )
    cv2.putText(
        vis,
        "tp",
        (int(round(tp_xy[0])) + 6, int(round(tp_xy[1])) - 6),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.45,
        (0, 255, 0),
        1,
        cv2.LINE_AA,
    )
    pal = _REF_OVERLAY_COLORS_BGR
    for i, ref in enumerate(references):
        col = pal[i % len(pal)]
        x0, y0, x1, y1 = ref["bbox_roi"]
        cx, cy = int(round(ref["center_roi"][0])), int(round(ref["center_roi"][1]))
        if ref["kind"] == "ic_rect":
            cv2.rectangle(vis, (x0, y0), (x1, y1), col, 2)
        else:
            cv2.circle(vis, (cx, cy), 6, col, 2)
        tx, ty = int(round(tp_xy[0])), int(round(tp_xy[1]))
        cv2.line(vis, (tx, ty), (cx, cy), col, 1, cv2.LINE_AA)
        lbl = ref["ref_id"]
        cv2.putText(
            vis,
            lbl,
            (cx + 6, cy - 6),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            col,
            1,
            cv2.LINE_AA,
        )

    gray = (160, 160, 160)
    for edge in pairwise_roi:
        a_id, b_id = edge["a"], edge["b"]
        if a_id == "tp" or b_id == "tp":
            continue
        ca = centers.get(a_id)
        cb = centers.get(b_id)
        if ca is None or cb is None:
            continue
        pa = (int(round(ca[0])), int(round(ca[1])))
        pb = (int(round(cb[0])), int(round(cb[1])))
        cv2.line(vis, pa, pb, gray, 1, cv2.LINE_AA)

    cv2.imwrite(str(out_png), vis)
    print(f"Wrote {out_json} tp={payload['tp_center_roi']} n_refs={len(references)}")
    print(f"Wrote {out_png}")
    return payload


def run_write_approx_overlay_from_workspace(
    workspace: os.PathLike[str] | str | None = None,
    *,
    draw_search_radius: bool = False,
) -> Path:
    """Board ROI + VLM approx: target TP (magenta) + optional ``board_roi_reference_approx`` anchors.

    Does **not** draw OpenCV pad-detection candidates — only coordinates from
    ``case10_tp_dual_roi_direct_vlm.json``. Thin lines mirror locator-side ``tp→ref_*`` edges.
    """
    ws = Path(workspace or os.environ.get("WORKSPACE", ".")).resolve()
    dbg = ws / "debug"
    roi_path = dbg / ROI_PNG
    vlm_path = dbg / VLM_JSON
    out_png = dbg / APPROX_ONLY_PNG

    if not roi_path.is_file():
        raise FileNotFoundError(f"Missing {roi_path}")
    if not vlm_path.is_file():
        raise FileNotFoundError(f"Missing {vlm_path}")

    roi = cv2.imread(str(roi_path))
    if roi is None:
        raise FileNotFoundError(f"Cannot read ROI image {roi_path}")

    data = json.loads(vlm_path.read_text(encoding="utf-8"))
    approx = data.get("board_roi_target_px_approx")
    if not isinstance(approx, list) or len(approx) != 2:
        raise ValueError("board_roi_target_px_approx must be [cx, cy] in ROI pixels")

    geom = data.get("geom_refine") or {}
    search_r = float(geom.get("search_radius_px", 28))

    h, w = roi.shape[:2]
    ax = max(0.0, min(float(w - 1), float(approx[0])))
    ay = max(0.0, min(float(h - 1), float(approx[1])))
    iax, iay = int(round(ax)), int(round(ay))

    refs_raw = data.get("board_roi_reference_approx")
    ref_pts: list[tuple[str, float, float, tuple[int, int, int]]] = []
    if isinstance(refs_raw, list):
        for i, item in enumerate(refs_raw):
            if not isinstance(item, dict):
                continue
            rid = str(item.get("ref_id") or f"ref_{i + 1}")
            xy = item.get("center_px") or item.get("xy")
            if not isinstance(xy, list) or len(xy) != 2:
                continue
            rx = max(0.0, min(float(w - 1), float(xy[0])))
            ry = max(0.0, min(float(h - 1), float(xy[1])))
            col = _REF_OVERLAY_COLORS_BGR[i % len(_REF_OVERLAY_COLORS_BGR)]
            ref_pts.append((rid, rx, ry, col))

    vis = roi.copy()
    tp_col = (255, 0, 255)
    cv2.drawMarker(
        vis,
        (iax, iay),
        tp_col,
        markerType=cv2.MARKER_CROSS,
        markerSize=22,
        thickness=2,
    )
    cv2.putText(
        vis,
        "tp approx",
        (min(iax + 14, w - 180), max(iay - 14, 20)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.5,
        tp_col,
        1,
        cv2.LINE_AA,
    )
    for rid, rx, ry, col in ref_pts:
        ix, iy = int(round(rx)), int(round(ry))
        cv2.line(vis, (iax, iay), (ix, iy), col, 1, cv2.LINE_AA)
        cv2.drawMarker(
            vis,
            (ix, iy),
            col,
            markerType=cv2.MARKER_CROSS,
            markerSize=18,
            thickness=2,
        )
        cv2.putText(
            vis,
            rid,
            (min(ix + 10, w - 60), max(iy - 8, 14)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            col,
            1,
            cv2.LINE_AA,
        )

    if len(ref_pts) >= 2:
        gray_line = (160, 160, 160)
        for i in range(len(ref_pts)):
            for j in range(i + 1, len(ref_pts)):
                _a, ax, ay, _ca = ref_pts[i]
                _b, bx, by, _cb = ref_pts[j]
                cv2.line(
                    vis,
                    (int(round(ax)), int(round(ay))),
                    (int(round(bx)), int(round(by))),
                    gray_line,
                    1,
                    cv2.LINE_AA,
                )

    if draw_search_radius and search_r > 1:
        cv2.circle(vis, (iax, iay), int(round(search_r)), (255, 128, 255), 1)

    cv2.imwrite(str(out_png), vis)
    print(f"Wrote {out_png} tp_approx=({iax},{iay}) ref_marks={len(ref_pts)}")
    return out_png


def _board_ref_centers(
    board_refs_raw: Any,
) -> dict[str, tuple[float, float]]:
    out: dict[str, tuple[float, float]] = {}
    if not isinstance(board_refs_raw, list):
        return out
    for item in board_refs_raw:
        if not isinstance(item, dict):
            continue
        rid = str(item.get("ref_id") or "")
        if not rid or not rid.startswith("ref_"):
            continue
        xy = item.get("center_px") or item.get("xy")
        if not isinstance(xy, list) or len(xy) != 2:
            continue
        out[rid] = (float(xy[0]), float(xy[1]))
    return out


def compute_locator_to_board_scale(
    locator_obj: dict[str, Any],
    board_ref_centers: dict[str, tuple[float, float]],
    approx_xy: tuple[float, float],
    locator_tp_xy: tuple[float, float],
) -> tuple[float | None, str]:
    """Median scale board/locator from ref–ref edges; else tp–ref distances."""
    ratios: list[float] = []
    pairwise = locator_obj.get("pairwise_roi")
    if isinstance(pairwise, list):
        for edge in pairwise:
            if not isinstance(edge, dict):
                continue
            a, b = str(edge.get("a")), str(edge.get("b"))
            if a == "tp" or b == "tp":
                continue
            d_loc = float(edge.get("dist_px") or 0.0)
            if d_loc <= 1e-3:
                continue
            ca = board_ref_centers.get(a)
            cb = board_ref_centers.get(b)
            if ca is None or cb is None:
                continue
            d_board = math.hypot(ca[0] - cb[0], ca[1] - cb[1])
            ratios.append(d_board / d_loc)

    if ratios:
        ratios.sort()
        mid = ratios[len(ratios) // 2]
        return mid, "ref_ref_median"

    for e in locator_obj.get("graph_edges") or []:
        if not isinstance(e, dict) or e.get("from") != "tp":
            continue
        rid = str(e.get("to") or "")
        if not rid.startswith("ref_"):
            continue
        d_loc = float(e.get("dist_px") or 0.0)
        if d_loc <= 1e-3:
            continue
        cb = board_ref_centers.get(rid)
        if cb is None:
            continue
        d_board = math.hypot(approx_xy[0] - cb[0], approx_xy[1] - cb[1])
        ratios.append(d_board / d_loc)

    if ratios:
        ratios.sort()
        return ratios[len(ratios) // 2], "tp_ref_median"

    return None, "no_scale"


def run_scaled_radius_from_workspace(
    workspace: os.PathLike[str] | str | None = None,
) -> dict[str, Any]:
    """Final TP = VLM approx center; radius = locator ``tp_green_radius_px`` × structure scale."""
    ws = Path(workspace or os.environ.get("WORKSPACE", ".")).resolve()
    dbg = ws / "debug"
    roi_path = dbg / ROI_PNG
    vlm_path = dbg / VLM_JSON
    loc_path = dbg / LOCATOR_REFS_JSON
    out_json = dbg / REFINED_JSON
    out_png = dbg / SNAP_PNG

    if not roi_path.is_file():
        raise FileNotFoundError(f"Missing {roi_path}")
    if not vlm_path.is_file():
        raise FileNotFoundError(f"Missing {vlm_path}")
    if not loc_path.is_file():
        raise FileNotFoundError(f"Missing {loc_path}")

    roi = cv2.imread(str(roi_path))
    if roi is None:
        raise FileNotFoundError(f"Cannot read ROI image {roi_path}")

    data = json.loads(vlm_path.read_text(encoding="utf-8"))
    approx = data.get("board_roi_target_px_approx")
    if not isinstance(approx, list) or len(approx) != 2:
        payload = {"qc_direct_path": "FAIL", "reason": "invalid board_roi_target_px_approx"}
        out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return payload

    loc_data = json.loads(loc_path.read_text(encoding="utf-8"))
    tp_center = loc_data.get("tp_center_roi")
    if not isinstance(tp_center, list) or len(tp_center) != 2:
        payload = {"qc_direct_path": "FAIL", "reason": "locator_refs missing tp_center_roi"}
        out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return payload

    r_loc = float(loc_data.get("tp_green_radius_px") or 8.0)
    if r_loc < 2.0:
        r_loc = 8.0

    ax = float(approx[0])
    ay = float(approx[1])
    h, w = roi.shape[:2]
    ax = max(0.0, min(float(w - 1), ax))
    ay = max(0.0, min(float(h - 1), ay))

    board_refs = _board_ref_centers(data.get("board_roi_reference_approx"))
    ltp = (float(tp_center[0]), float(tp_center[1]))
    scale, scale_mode = compute_locator_to_board_scale(loc_data, board_refs, (ax, ay), ltp)
    if scale is None or scale <= 0.0:
        payload = {
            "qc_direct_path": "FAIL",
            "reason": "cannot derive scale (need board_roi_reference_approx matching locator refs)",
            "approx_used": [ax, ay],
        }
        out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(payload, ensure_ascii=False))
        return payload

    r_vis = max(2, int(round(r_loc * scale)))
    r_vis = min(r_vis, max(3, min(h, w) // 3))

    payload: dict[str, Any] = {
        "cx_roi": int(round(ax)),
        "cy_roi": int(round(ay)),
        "r_vis": r_vis,
        "snapped_from_approx": False,
        "selection_method": "vlm_approx_scaled_radius",
        "scale_board_over_locator": round(scale, 5),
        "scale_mode": scale_mode,
        "tp_green_radius_locator_px": round(r_loc, 3),
        "dist_snap_px": 0.0,
        "qc_direct_path": "PASS",
    }
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    vis = roi.copy()
    col = (255, 0, 255)
    iax, iay = int(round(ax)), int(round(ay))
    cv2.drawMarker(vis, (iax, iay), col, markerType=cv2.MARKER_CROSS, markerSize=18, thickness=2)
    cv2.circle(vis, (iax, iay), r_vis, (0, 255, 0), 2)
    cv2.putText(
        vis,
        f"r={r_vis} scale",
        (min(iax + 8, w - 120), max(iay - 12, 16)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.45,
        (0, 255, 0),
        1,
        cv2.LINE_AA,
    )
    cv2.imwrite(str(out_png), vis)

    print(
        f"Wrote {out_json} scaled_r -> ({payload['cx_roi']},{payload['cy_roi']}) "
        f"r_vis={r_vis} scale={scale:.4f} ({scale_mode})"
    )
    print(f"Wrote {out_png}")
    return payload


def run_dual_roi_finalize_from_workspace(
    workspace: os.PathLike[str] | str | None = None,
) -> dict[str, Any]:
    """Default: VLM approx + scaled radius; set ``geom_refine.mode`` to
    ``snap_nearest_in_search_radius`` for legacy OpenCV snap."""
    ws = Path(workspace or os.environ.get("WORKSPACE", ".")).resolve()
    dbg = ws / "debug"
    vlm_path = dbg / VLM_JSON
    if not vlm_path.is_file():
        raise FileNotFoundError(f"Missing {vlm_path}")
    vlm_data = json.loads(vlm_path.read_text(encoding="utf-8"))
    mode = (vlm_data.get("geom_refine") or {}).get("mode")
    if mode == "snap_nearest_in_search_radius":
        return _run_snap_nearest_only(workspace)
    return run_scaled_radius_from_workspace(workspace)


def run_snap_nearest_from_workspace(workspace: os.PathLike[str] | str | None = None) -> dict[str, Any]:
    """Alias for agents/CLI: same as :func:`run_dual_roi_finalize_from_workspace`."""
    return run_dual_roi_finalize_from_workspace(workspace)


def _run_snap_nearest_only(workspace: os.PathLike[str] | str | None = None) -> dict[str, Any]:
    """Pick circle with min Euclidean distance to approx within search_radius; tie-break higher circ."""
    ws = Path(workspace or os.environ.get("WORKSPACE", ".")).resolve()
    dbg = ws / "debug"
    roi_path = dbg / ROI_PNG
    vlm_path = dbg / VLM_JSON
    out_json = dbg / REFINED_JSON
    out_snap = dbg / SNAP_PNG

    if not roi_path.is_file():
        raise FileNotFoundError(f"Missing {roi_path}")
    if not vlm_path.is_file():
        raise FileNotFoundError(f"Missing {vlm_path}")

    roi = cv2.imread(str(roi_path))
    if roi is None:
        raise FileNotFoundError(f"Cannot read ROI image {roi_path}")

    data = json.loads(vlm_path.read_text(encoding="utf-8"))
    approx = data.get("board_roi_target_px_approx")
    if not isinstance(approx, list) or len(approx) != 2:
        payload = {"qc_direct_path": "FAIL", "reason": "invalid board_roi_target_px_approx"}
        out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return payload

    geom = data.get("geom_refine") or {}
    search_r = float(geom.get("search_radius_px", 28))
    min_circ = float(geom.get("min_circularity", 0.55))

    ax = float(approx[0])
    ay = float(approx[1])
    merged = _merged_circle_detections(roi, geom)

    in_band: list[tuple[float, float, float, float, float, float]] = []
    for cx, cy, r, circ, _a in merged:
        dist = math.hypot(cx - ax, cy - ay)
        if dist <= search_r and circ >= min_circ * 0.999:
            in_band.append((dist, -circ, cx, cy, r, circ))

    print(f"snap: candidates_total={len(merged)} within_radius={len(in_band)} search_r={search_r}")

    if not in_band:
        payload = {
            "qc_direct_path": "FAIL",
            "reason": f"no circle within search_radius_px={search_r} matching min_circularity",
            "approx_used": [ax, ay],
            "candidates_total": len(merged),
        }
        out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(payload, ensure_ascii=False))
        return payload

    in_band.sort()
    _, _neg_circ, cx, cy, r, circ = in_band[0]
    dist = math.hypot(cx - ax, cy - ay)
    r_vis = max(2, int(round(r)))

    payload = {
        "cx_roi": int(round(cx)),
        "cy_roi": int(round(cy)),
        "r_vis": r_vis,
        "snapped_from_approx": True,
        "selection_method": "snap_nearest_in_search_radius",
        "dist_snap_px": dist,
        "circularity": round(circ, 4),
        "search_radius_px": search_r,
        "qc_direct_path": "PASS",
    }
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    vis = roi.copy()
    col = (255, 0, 255)
    iax, iay = int(round(ax)), int(round(ay))
    cv2.drawMarker(vis, (iax, iay), col, markerType=cv2.MARKER_CROSS, markerSize=18, thickness=2)
    wx, wy = int(round(cx)), int(round(cy))
    cv2.circle(vis, (wx, wy), r_vis, (0, 255, 0), 2)
    cv2.line(vis, (iax, iay), (wx, wy), (0, 255, 200), 1)
    cv2.putText(
        vis,
        "snap",
        (wx + 6, wy - 6),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.45,
        (0, 255, 0),
        1,
        cv2.LINE_AA,
    )
    cv2.imwrite(str(out_snap), vis)

    print(f"Wrote {out_json} snap -> ({payload['cx_roi']},{payload['cy_roi']}) dist={dist:.2f} qc=PASS")
    print(f"Wrote {out_snap}")
    return payload


def run_enumerate_from_workspace(workspace: os.PathLike[str] | str | None = None) -> dict[str, Any]:
    """Fallback: numbered candidates PNG + JSON (optional)."""
    ws = Path(workspace or os.environ.get("WORKSPACE", ".")).resolve()
    dbg = ws / "debug"
    roi_path = dbg / ROI_PNG
    vlm_path = dbg / VLM_JSON
    out_json = dbg / CAND_JSON
    out_png = dbg / CAND_PNG

    if not roi_path.is_file():
        raise FileNotFoundError(f"Missing {roi_path}")
    if not vlm_path.is_file():
        raise FileNotFoundError(f"Missing {vlm_path}")

    roi = cv2.imread(str(roi_path))
    if roi is None:
        raise FileNotFoundError(f"Cannot read ROI image {roi_path}")

    data = json.loads(vlm_path.read_text(encoding="utf-8"))
    approx = data.get("board_roi_target_px_approx")
    if not isinstance(approx, list) or len(approx) != 2:
        raise ValueError("board_roi_target_px_approx must be [cx, cy] in ROI pixels")

    geom = data.get("geom_refine") or {}
    search_r = float(geom.get("search_radius_px", 28))

    merged = _merged_circle_detections(roi, geom)

    ax, ay = float(approx[0]), float(approx[1])
    h, w = roi.shape[:2]
    ax = max(0.0, min(float(w - 1), ax))
    ay = max(0.0, min(float(h - 1), ay))

    cands_out: list[dict[str, Any]] = []
    for i, (cx, cy, r, circ, _area) in enumerate(merged, start=1):
        r_vis = max(2.0, float(r))
        dist = math.hypot(cx - ax, cy - ay)
        cands_out.append(
            {
                "id": i,
                "cx_roi": int(round(cx)),
                "cy_roi": int(round(cy)),
                "r_vis": int(round(r_vis)),
                "circularity": round(circ, 4),
                "dist_to_approx_px": round(dist, 2),
            }
        )

    payload: dict[str, Any] = {
        "roi_wh": [w, h],
        "board_roi_target_px_approx": [ax, ay],
        "geom_refine_echo": geom,
        "search_radius_px": search_r,
        "candidates": cands_out,
        "candidates_within_search_radius": [
            c for c in cands_out if float(c["dist_to_approx_px"]) <= search_r
        ],
    }
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    vis = roi.copy()
    for c in cands_out:
        cx, cy = int(c["cx_roi"]), int(c["cy_roi"])
        rr = int(c["r_vis"])
        cv2.circle(vis, (cx, cy), rr, (0, 180, 255), 2)
        label = str(c["id"])
        cv2.putText(
            vis,
            label,
            (cx + max(rr // 2, 6), cy - rr - 4),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (0, 180, 255),
            2,
            cv2.LINE_AA,
        )

    col = (255, 0, 255)
    iax, iay = int(round(ax)), int(round(ay))
    cv2.drawMarker(vis, (iax, iay), col, markerType=cv2.MARKER_CROSS, markerSize=18, thickness=2)
    cv2.putText(
        vis,
        "VLM approx",
        (min(iax + 12, w - 130), max(iay - 12, 18)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.45,
        col,
        1,
        cv2.LINE_AA,
    )
    if search_r > 1:
        cv2.circle(vis, (iax, iay), int(round(search_r)), (255, 0, 255), 1)

    cv2.imwrite(str(out_png), vis)

    print(f"Wrote {out_json} ({len(cands_out)} candidates)")
    print(f"Wrote {out_png}")
    print(f"candidates_within_search_radius: {len(payload['candidates_within_search_radius'])}")
    return payload


def run_apply_pick_from_workspace(workspace: os.PathLike[str] | str | None = None) -> dict[str, Any]:
    ws = Path(workspace or os.environ.get("WORKSPACE", ".")).resolve()
    dbg = ws / "debug"
    cand_path = dbg / CAND_JSON
    pick_path = dbg / PICK_JSON
    out_path = dbg / REFINED_JSON

    if not cand_path.is_file():
        raise FileNotFoundError(f"Run enumerate first: missing {cand_path}")
    if not pick_path.is_file():
        raise FileNotFoundError(
            f"Missing {pick_path} — VLM must write winner_candidate_id after viewing candidate image."
        )

    cdata = json.loads(cand_path.read_text(encoding="utf-8"))
    pick = json.loads(pick_path.read_text(encoding="utf-8"))
    winner_id = pick.get("winner_candidate_id")
    if winner_id is None:
        raise ValueError("case10_tp_dual_roi_vlm_pick.json must include winner_candidate_id")

    wid = int(winner_id)
    cands = cdata.get("candidates") or []
    chosen = next((c for c in cands if int(c.get("id", -1)) == wid), None)
    if chosen is None:
        payload = {
            "qc_direct_path": "FAIL",
            "reason": f"winner_candidate_id {wid} not in candidates (count={len(cands)})",
        }
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(payload, ensure_ascii=False))
        return payload

    approx = cdata.get("board_roi_target_px_approx") or [0, 0]
    ax, ay = float(approx[0]), float(approx[1])
    cx = float(chosen["cx_roi"])
    cy = float(chosen["cy_roi"])
    dist = math.hypot(cx - ax, cy - ay)

    confirm = pick.get("confirm_zh") or ""
    payload = {
        "cx_roi": int(chosen["cx_roi"]),
        "cy_roi": int(chosen["cy_roi"]),
        "r_vis": int(chosen["r_vis"]),
        "winner_candidate_id": wid,
        "selection_method": "vlm_pick_on_candidates_image",
        "dist_to_approx_px": round(dist, 3),
        "vlm_confirm_zh": confirm,
        "qc_direct_path": "PASS",
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {out_path} winner_id={wid} qc=PASS")
    return payload


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(
        description="case10 dual-ROI path A: approx overlay / snap / optional enumerate+picking",
        epilog="Example: python case10_dual_roi_refine.py --workspace /path/to/workspace approx-overlay",
    )
    p.add_argument("--workspace", type=Path, default=None, help="workspace root (default: WORKSPACE env or cwd)")
    p.add_argument(
        "--draw-search-radius",
        action="store_true",
        help="on approx-overlay, also draw faint search_radius circle from geom_refine",
    )
    sp = p.add_subparsers(dest="cmd", required=True)
    sp.add_parser("approx-overlay", help="clean ROI + VLM approx only (for view_image)")
    sp.add_parser(
        "build-locator-refs",
        help="after step04_locator_roi_crop: OpenCV refs + JSON + step04_locator_roi_refs.png",
    )
    sp.add_parser(
        "snap-nearest",
        help="finalize direct ROI: default VLM approx + scaled radius; set geom_refine.mode=snap_nearest_in_search_radius for CV snap",
    )
    sp.add_parser("enumerate", help="fallback: detect candidates + numbered PNG")
    sp.add_parser("apply-pick", help="fallback: read VLM pick JSON -> refined JSON")
    args = p.parse_args()
    root = args.workspace
    if args.cmd == "approx-overlay":
        run_write_approx_overlay_from_workspace(root, draw_search_radius=args.draw_search_radius)
    elif args.cmd == "build-locator-refs":
        run_build_locator_refs_from_workspace(root)
    elif args.cmd == "snap-nearest":
        run_snap_nearest_from_workspace(root)
    elif args.cmd == "enumerate":
        run_enumerate_from_workspace(root)
    else:
        run_apply_pick_from_workspace(root)
    sys.exit(0)
