"""case_012: full-frame Step2 anchor images → rigid graph + **IC correspondence + one *s,t* map** to board.

**Locator** (``step02_locator_front_anchor``): unchanged — green TP + red largest-IC box; OpenCV builds
``case12_step02_locator_graph.json``.

**Clean board** (no red box on photo): VLM sees **both** Step2 PNGs plus the graph, matches the boxed
largest IC to the real chip, and saves ``case12_board_largest_ic_bbox_vlm.json`` with
**``ref_ic_center_board_px``** (required). **Scale** *s* comes from **either**
``isotropic_scale_locator_to_board`` (float) **or** ``board_largest_ic_bbox_xyxy`` paired with the
locator red ``bbox_roi``. **Translation** *t* is **always** chosen so
``s * ref_ic_loc + t = ref_ic_center_board``; then **all** anchors use ``p_board = s * p_loc + t``.
No per-reference VLM point placement.

**Legacy** board photo with red IC box: ``run_align_locator_graph_to_board_ic_bbox`` (OpenCV HSV), corner-mean *t*.

This module is **case12-specific**; default agent / STANDARD_WORKFLOW unchanged.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from case10_dual_roi_refine import (
    build_pairwise_roi,
    _detect_green_tp_center,
    _pad_circle_references,
    _pick_diverse_refs,
    _polar_from_tp,
    _rect_like_references,
)

LOCATOR_STEP02 = "step02_locator_front_anchor.png"
BOARD_STEP02 = "step02_board_front_anchor.png"


def clamp_board_pixel(x: float, y: float, width: int, height: int) -> tuple[int, int]:
    """Clamp a board-frame point to image pixel bounds (origin top-left)."""
    w = max(1, int(width))
    h = max(1, int(height))
    cx = int(round(max(0.0, min(float(w - 1), float(x)))))
    cy = int(round(max(0.0, min(float(h - 1), float(y)))))
    return cx, cy


def _board_step02_size(ws: Path) -> tuple[int, int] | None:
    board_path = ws / "debug" / BOARD_STEP02
    if not board_path.is_file():
        return None
    bgr = cv2.imread(str(board_path))
    if bgr is None:
        return None
    h, w = bgr.shape[:2]
    return int(w), int(h)
OUT_JSON = "case12_step02_locator_graph.json"
OUT_PNG = "case12_step02_locator_graph.png"
VLM_BOARD_JSON = "case12_board_points_vlm.json"
VLM_REFINE_INPUT_JSON = "case12_board_points_vlm_refine.json"
ALIGNED_JSON = "case12_board_points_aligned.json"
REFINED_JSON = "case12_board_points_refined.json"
BOARD_OVERLAY_PNG = "case12_board_approx_overlay.png"
BOARD_OVERLAY_OPENCV_PNG = "case12_board_approx_overlay_opencv.png"
# VLM correspondence JSON (same filename): ref_ic_center_board_px + scale; optional board bbox for *s* or QC.
VLM_BOARD_IC_BBOX_JSON = "case12_board_largest_ic_bbox_vlm.json"


def _largest_red_box_bbox(bgr: np.ndarray) -> tuple[int, int, int, int] | None:
    """Largest merged HSV-red region (Step2-style anchor box). Returns [x1,y1,x2,y2]."""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    m1 = cv2.inRange(hsv, np.array([0, 100, 100]), np.array([10, 255, 255]))
    m2 = cv2.inRange(hsv, np.array([170, 100, 100]), np.array([180, 255, 255]))
    mask = cv2.bitwise_or(m1, m2)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    best = max(contours, key=cv2.contourArea)
    if cv2.contourArea(best) < 200.0:
        return None
    x, y, cw, ch = cv2.boundingRect(best)
    return int(x), int(y), int(x + cw), int(y + ch)


def _ic_exclude_mask(shape: tuple[int, int], bbox: tuple[int, int, int, int], pad: int = 8) -> np.ndarray:
    h, w = shape[:2]
    mask = np.zeros((h, w), dtype=np.uint8)
    x1, y1, x2, y2 = bbox
    x1 = max(0, x1 - pad)
    y1 = max(0, y1 - pad)
    x2 = min(w - 1, x2 + pad)
    y2 = min(h - 1, y2 + pad)
    cv2.rectangle(mask, (x1, y1), (x2, y2), 255, -1)
    return mask


def _fit_isotropic_scale_translate_ic_boxes(
    bbox_loc: tuple[int, int, int, int],
    bbox_board: tuple[int, int, int, int],
) -> tuple[float, float, float, dict[str, Any]]:
    """One uniform scale *s* and translation *t* from axis-aligned IC boxes (TL,TR,BR,BL).

    ``p_board = s * p_loc + t`` minimizes mean corner error when *s* is fixed to
    mean(width ratio, height ratio). **No rotation** → internal shape identical to locator.
    """
    lx1, ly1, lx2, ly2 = bbox_loc
    bx1, by1, bx2, by2 = bbox_board
    w_l = float(lx2 - lx1)
    h_l = float(ly2 - ly1)
    w_b = float(bx2 - bx1)
    h_b = float(by2 - by1)
    if w_l < 1.0 or h_l < 1.0:
        raise ValueError("locator IC bbox width/height too small")
    s_w = w_b / w_l
    s_h = h_b / h_l
    s = 0.5 * (s_w + s_h)
    corners_l = np.array(
        [[lx1, ly1], [lx2, ly1], [lx2, ly2], [lx1, ly2]],
        dtype=np.float64,
    )
    corners_b = np.array(
        [[bx1, by1], [bx2, by1], [bx2, by2], [bx1, by2]],
        dtype=np.float64,
    )
    t_vec = (corners_b - s * corners_l).mean(axis=0)
    tx, ty = float(t_vec[0]), float(t_vec[1])
    pred = s * corners_l + t_vec
    res = np.linalg.norm(pred - corners_b, axis=1)
    meta = {
        "scale_width": round(s_w, 6),
        "scale_height": round(s_h, 6),
        "scale_used_isotropic": round(s, 6),
        "aspect_loc": round(w_l / max(h_l, 1e-6), 5),
        "aspect_board": round(w_b / max(h_b, 1e-6), 5),
        "translation_xy": [round(tx, 3), round(ty, 3)],
        "ic_corner_residuals_px": [round(float(x), 3) for x in res],
        "ic_corner_residual_max_px": round(float(res.max()), 3),
    }
    return s, tx, ty, meta


def _ic_box_corner_residuals_px(
    bbox_loc: tuple[int, int, int, int],
    bbox_board: tuple[int, int, int, int],
    s: float,
    tx: float,
    ty: float,
) -> tuple[list[float], float]:
    lx1, ly1, lx2, ly2 = bbox_loc
    bx1, by1, bx2, by2 = bbox_board
    corners_l = np.array(
        [[lx1, ly1], [lx2, ly1], [lx2, ly2], [lx1, ly2]],
        dtype=np.float64,
    )
    corners_b = np.array(
        [[bx1, by1], [bx2, by1], [bx2, by2], [bx1, by2]],
        dtype=np.float64,
    )
    pred = s * corners_l + np.array([tx, ty])
    res = np.linalg.norm(pred - corners_b, axis=1)
    return [round(float(x), 3) for x in res], float(res.max())


def _compute_st_from_vlm_ic_correspondence(
    obj: dict[str, Any],
    image_wh: tuple[int, int],
    bbox_loc: tuple[int, int, int, int],
    ref_ic_loc: tuple[float, float],
) -> tuple[float, float, float, dict[str, Any], tuple[int, int, int, int] | None]:
    """VLM: ref_ic center on board + (*s* from explicit float or from board bbox vs locator bbox)."""
    w, h = image_wh
    center_raw = obj.get("ref_ic_center_board_px")
    if not isinstance(center_raw, list) or len(center_raw) != 2:
        raise ValueError(
            "VLM JSON must include ref_ic_center_board_px: [x,y] in step02_board_front_anchor pixels "
            "(visual match for the largest IC · center on the real photo)"
        )
    cx_b = float(center_raw[0])
    cy_b = float(center_raw[1])
    cx_b = max(0.0, min(cx_b, float(w - 1)))
    cy_b = max(0.0, min(cy_b, float(h - 1)))
    ref_ic_board = (cx_b, cy_b)

    s_ex = obj.get("isotropic_scale_locator_to_board")
    if s_ex is None:
        s_ex = obj.get("s_locator_to_board")

    bbox_board: tuple[int, int, int, int] | None = None
    raw_bb = obj.get("board_largest_ic_bbox_xyxy") or obj.get("bbox_board_ic_xyxy")
    if isinstance(raw_bb, list) and len(raw_bb) == 4:
        bbox_board = _parse_vlm_board_largest_ic_bbox(obj, (w, h))

    if s_ex is not None:
        s = float(s_ex)
        if not (1e-6 < s < 1e3):
            raise ValueError("isotropic_scale_locator_to_board must be a positive plausible float")
        tx = ref_ic_board[0] - s * ref_ic_loc[0]
        ty = ref_ic_board[1] - s * ref_ic_loc[1]
        meta: dict[str, Any] = {
            "align_mode": "vlm_ref_ic_center_explicit_s",
            "scale_used_isotropic": round(s, 6),
            "translation_xy": [round(tx, 3), round(ty, 3)],
            "ref_ic_center_board_px": [round(cx_b, 3), round(cy_b, 3)],
        }
        if bbox_board is not None:
            res_list, res_max = _ic_box_corner_residuals_px(bbox_loc, bbox_board, s, tx, ty)
            meta["ic_corner_residuals_px"] = res_list
            meta["ic_corner_residual_max_px"] = round(res_max, 3)
        else:
            meta["ic_corner_residuals_px"] = None
            meta["ic_corner_residual_max_px"] = None
        return s, tx, ty, meta, bbox_board

    if bbox_board is not None:
        s, _tx_old, _ty_old, corner_meta = _fit_isotropic_scale_translate_ic_boxes(bbox_loc, bbox_board)
        tx = ref_ic_board[0] - s * ref_ic_loc[0]
        ty = ref_ic_board[1] - s * ref_ic_loc[1]
        res_list, res_max = _ic_box_corner_residuals_px(bbox_loc, bbox_board, s, tx, ty)
        meta = {
            "align_mode": "vlm_ref_ic_center_bbox_derived_s",
            "scale_width": corner_meta["scale_width"],
            "scale_height": corner_meta["scale_height"],
            "scale_used_isotropic": corner_meta["scale_used_isotropic"],
            "aspect_loc": corner_meta["aspect_loc"],
            "aspect_board": corner_meta["aspect_board"],
            "translation_xy": [round(tx, 3), round(ty, 3)],
            "ref_ic_center_board_px": [round(cx_b, 3), round(cy_b, 3)],
            "ic_corner_residuals_px": res_list,
            "ic_corner_residual_max_px": round(res_max, 3),
        }
        return s, tx, ty, meta, bbox_board

    raise ValueError(
        "VLM JSON needs isotropic_scale_locator_to_board (float) OR board_largest_ic_bbox_xyxy "
        "to fix global scale; ref_ic_center_board_px is always required"
    )


def _write_aligned_board_points_impl(
    ws: Path,
    *,
    payload: dict[str, Any],
    bbox_loc: tuple[int, int, int, int],
    bbox_board: tuple[int, int, int, int] | None,
    s: float,
    tx: float,
    ty: float,
    ic_meta: dict[str, Any],
    source: str,
    extra_fields: dict[str, Any] | None = None,
    write_viz_png: bool = False,
) -> dict[str, Any]:
    dbg = ws / "debug"
    out_json = dbg / ALIGNED_JSON
    anchors_loc = _locator_anchor_points_from_graph(payload)

    board_refs: list[dict[str, Any]] = []
    ids_sorted = sorted(
        anchors_loc.keys(),
        key=lambda k: (0 if k == "ref_ic" else 1, k),
    )
    for rid in ids_sorted:
        if rid == "tp":
            continue
        bx, by = _map_locator_to_board_pt(anchors_loc[rid], s, tx, ty)
        board_refs.append(
            {
                "ref_id": rid,
                "center_px": [round(bx, 2), round(by, 2)],
            }
        )

    tp_b = _map_locator_to_board_pt(anchors_loc["tp"], s, tx, ty)
    board_wh = _board_step02_size(ws)
    tp_raw = [int(round(tp_b[0])), int(round(tp_b[1]))]
    tp_out = list(tp_raw)
    refs_out: list[dict[str, Any]] = []
    for r in board_refs:
        rx = float(r["center_px"][0])
        ry = float(r["center_px"][1])
        if board_wh is not None:
            cx, cy = clamp_board_pixel(rx, ry, board_wh[0], board_wh[1])
            refs_out.append({"ref_id": r["ref_id"], "center_px": [cx, cy]})
        else:
            refs_out.append(
                {
                    "ref_id": r["ref_id"],
                    "center_px": [int(round(rx)), int(round(ry))],
                }
            )
    if board_wh is not None:
        tp_out = list(clamp_board_pixel(tp_b[0], tp_b[1], board_wh[0], board_wh[1]))
    out_payload: dict[str, Any] = {
        "schema_version": 1,
        "coordinate_frame": BOARD_STEP02,
        "source": source,
        "board_roi_target_px_approx": tp_out,
        "board_roi_reference_approx": refs_out,
        "transform": {
            "kind": "isotropic_scale_translate_xy",
            "s": round(s, 6),
            "tx": round(tx, 4),
            "ty": round(ty, 4),
            **ic_meta,
        },
        "bbox_locator_ic": list(bbox_loc),
        "bbox_board_ic_detected": list(bbox_board) if bbox_board is not None else None,
    }
    if board_wh is not None and tp_out != tp_raw:
        out_payload["board_roi_target_px_unclamped"] = tp_raw
        out_payload["board_image_wh"] = [board_wh[0], board_wh[1]]
    if extra_fields:
        out_payload.update(extra_fields)
    out_json.write_text(json.dumps(out_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    res_max = ic_meta.get("ic_corner_residual_max_px")
    res_s = f" ic_corner_max_err={res_max}px" if res_max is not None else ""
    print(f"Wrote {out_json} source={source} s={s:.5f} t=({tx:.1f},{ty:.1f}){res_s}")
    if write_viz_png:
        run_write_board_approx_overlay(
            ws,
            points_json=out_json,
            out_png=dbg / BOARD_OVERLAY_OPENCV_PNG,
        )
    return out_payload


def _write_aligned_board_points_from_ic_bboxes(
    ws: Path,
    *,
    payload: dict[str, Any],
    bbox_loc: tuple[int, int, int, int],
    bbox_board: tuple[int, int, int, int],
    source: str,
    extra_fields: dict[str, Any] | None = None,
    write_viz_png: bool = False,
) -> dict[str, Any]:
    """OpenCV board red-box path: *s,t* from matching IC rectangles (corner-mean translation)."""
    s, tx, ty, ic_meta = _fit_isotropic_scale_translate_ic_boxes(bbox_loc, bbox_board)
    return _write_aligned_board_points_impl(
        ws,
        payload=payload,
        bbox_loc=bbox_loc,
        bbox_board=bbox_board,
        s=s,
        tx=tx,
        ty=ty,
        ic_meta=ic_meta,
        source=source,
        extra_fields=extra_fields,
        write_viz_png=write_viz_png,
    )


def _locator_anchor_points_from_graph(payload: dict[str, Any]) -> dict[str, tuple[float, float]]:
    if not payload.get("tp_center") or not payload.get("largest_ic"):
        raise ValueError("locator graph missing tp_center or largest_ic")
    out: dict[str, tuple[float, float]] = {
        "tp": (float(payload["tp_center"][0]), float(payload["tp_center"][1])),
        "ref_ic": (
            float(payload["largest_ic"]["center_roi"][0]),
            float(payload["largest_ic"]["center_roi"][1]),
        ),
    }
    for r in payload.get("references") or []:
        if not isinstance(r, dict):
            continue
        rid = str(r.get("ref_id") or "")
        cr = r.get("center_roi")
        if rid and isinstance(cr, list) and len(cr) == 2:
            out[rid] = (float(cr[0]), float(cr[1]))
    return out


def _map_locator_to_board_pt(
    p: tuple[float, float], s: float, tx: float, ty: float
) -> tuple[float, float]:
    return (s * p[0] + tx, s * p[1] + ty)


def _parse_vlm_board_largest_ic_bbox(
    obj: dict[str, Any],
    image_wh: tuple[int, int],
    *,
    min_side_px: float = 20.0,
) -> tuple[int, int, int, int]:
    """``board_largest_ic_bbox_xyxy``: [x1,y1,x2,y2] axis-aligned, same frame as board step02."""
    w, h = image_wh
    raw = obj.get("board_largest_ic_bbox_xyxy")
    if raw is None:
        raw = obj.get("bbox_board_ic_xyxy")
    if not isinstance(raw, list) or len(raw) != 4:
        raise ValueError(
            "VLM JSON must set board_largest_ic_bbox_xyxy: [x1,y1,x2,y2] "
            "(integers, step02_board_front_anchor pixels)"
        )
    x1, y1, x2, y2 = (int(round(float(raw[0]))), int(round(float(raw[1]))), int(round(float(raw[2]))), int(round(float(raw[3]))))
    if x2 < x1:
        x1, x2 = x2, x1
    if y2 < y1:
        y1, y2 = y2, y1
    x1 = max(0, min(x1, w - 1))
    x2 = max(0, min(x2, w - 1))
    y1 = max(0, min(y1, h - 1))
    y2 = max(0, min(y2, h - 1))
    if x2 <= x1 or y2 <= y1:
        raise ValueError("VLM IC bbox empty after clipping to image")
    if (x2 - x1) < min_side_px or (y2 - y1) < min_side_px:
        raise ValueError(f"VLM IC bbox too small: w={x2 - x1} h={y2 - y1}")
    return x1, y1, x2, y2


def run_align_locator_graph_to_board_ic_bbox(
    workspace: os.PathLike[str] | str | None = None,
    *,
    write_viz_png: bool = False,
) -> dict[str, Any]:
    """**Legacy boxed board**: align using **red HSV IC box** on ``step02_board_front_anchor``."""
    if os.environ.get("VLM_AGENT_WORKFLOW_MODE", "").strip() == "vlm_test":
        raise RuntimeError(
            "run_align_locator_graph_to_board_ic_bbox is forbidden when "
            "VLM_AGENT_WORKFLOW_MODE=vlm_test; use "
            "run_align_locator_graph_to_board_ic_bbox_vlm + "
            "debug/case12_board_largest_ic_bbox_vlm.json instead."
        )
    ws = Path(workspace or os.environ.get("WORKSPACE", ".")).resolve()
    dbg = ws / "debug"
    graph_path = dbg / OUT_JSON
    board_path = dbg / BOARD_STEP02
    out_json = dbg / ALIGNED_JSON

    if not graph_path.is_file():
        raise FileNotFoundError(f"Missing {graph_path}; run build-graph first")
    if not board_path.is_file():
        raise FileNotFoundError(f"Missing {board_path}")

    payload = json.loads(graph_path.read_text(encoding="utf-8"))
    bbox_loc_t = payload.get("largest_ic", {}).get("bbox_roi")
    if not isinstance(bbox_loc_t, list) or len(bbox_loc_t) != 4:
        raise ValueError("case12_step02_locator_graph.json missing largest_ic.bbox_roi")

    bbox_loc = int(bbox_loc_t[0]), int(bbox_loc_t[1]), int(bbox_loc_t[2]), int(bbox_loc_t[3])
    board_bgr = cv2.imread(str(board_path))
    if board_bgr is None:
        raise FileNotFoundError(f"Cannot read {board_path}")
    bbox_board = _largest_red_box_bbox(board_bgr)
    if bbox_board is None:
        if out_json.is_file():
            out_json.unlink()
        opencv_ov = dbg / BOARD_OVERLAY_OPENCV_PNG
        if opencv_ov.is_file():
            opencv_ov.unlink()
        err = {
            "ok": False,
            "reason": "board red IC box not detected",
            "coordinate_frame": BOARD_STEP02,
        }
        print(json.dumps(err, ensure_ascii=False))
        return err

    return _write_aligned_board_points_from_ic_bboxes(
        ws,
        payload=payload,
        bbox_loc=bbox_loc,
        bbox_board=bbox_board,
        source="opencv_ic_bbox_isotropic_align",
        write_viz_png=write_viz_png,
    )


def run_align_locator_graph_to_board_ic_bbox_vlm(
    workspace: os.PathLike[str] | str | None = None,
    *,
    vlm_bbox_json: os.PathLike[str] | str | None = None,
) -> dict[str, Any]:
    """**Clean board**: VLM gives ``ref_ic_center_board_px`` + scale; one *s,t* maps **all** anchors.

    Scale *s*: ``isotropic_scale_locator_to_board`` **or** derived from ``board_largest_ic_bbox_xyxy``
    vs locator ``bbox_roi``. Translation *t*: ``ref_ic_board - s * ref_ic_loc``.
    """
    ws = Path(workspace or os.environ.get("WORKSPACE", ".")).resolve()
    dbg = ws / "debug"
    graph_path = dbg / OUT_JSON
    board_path = dbg / BOARD_STEP02
    vlm_path = Path(vlm_bbox_json) if vlm_bbox_json is not None else dbg / VLM_BOARD_IC_BBOX_JSON

    if not graph_path.is_file():
        raise FileNotFoundError(f"Missing {graph_path}; run build-graph first")
    if not board_path.is_file():
        raise FileNotFoundError(f"Missing {board_path}")
    if not vlm_path.is_file():
        raise FileNotFoundError(
            f"Missing {vlm_path}; VLM must save IC correspondence JSON ({VLM_BOARD_IC_BBOX_JSON}) first"
        )

    payload = json.loads(graph_path.read_text(encoding="utf-8"))
    bbox_loc_t = payload.get("largest_ic", {}).get("bbox_roi")
    if not isinstance(bbox_loc_t, list) or len(bbox_loc_t) != 4:
        raise ValueError("case12_step02_locator_graph.json missing largest_ic.bbox_roi")

    bbox_loc = int(bbox_loc_t[0]), int(bbox_loc_t[1]), int(bbox_loc_t[2]), int(bbox_loc_t[3])
    board_bgr = cv2.imread(str(board_path))
    if board_bgr is None:
        raise FileNotFoundError(f"Cannot read {board_path}")
    ih, iw = board_bgr.shape[:2]
    vlm_obj = json.loads(vlm_path.read_text(encoding="utf-8"))
    anchors_loc = _locator_anchor_points_from_graph(payload)
    ref_ic_loc = anchors_loc["ref_ic"]
    s, tx, ty, ic_meta, bbox_board = _compute_st_from_vlm_ic_correspondence(
        vlm_obj, (iw, ih), bbox_loc, ref_ic_loc
    )

    return _write_aligned_board_points_impl(
        ws,
        payload=payload,
        bbox_loc=bbox_loc,
        bbox_board=bbox_board,
        s=s,
        tx=tx,
        ty=ty,
        ic_meta=ic_meta,
        source="vlm_ic_correspondence_isotropic_align",
        extra_fields={
            "board_ic_align_source": "vlm",
            "vlm_ic_correspondence_file": vlm_path.name,
        },
    )


def _board_anchor_points_from_board_json(data: dict[str, Any]) -> dict[str, tuple[float, float]]:
    """tp + each ref_id → center in board step02 pixels."""
    out: dict[str, tuple[float, float]] = {}
    tp = data.get("board_roi_target_px_approx") or data.get("board_tp_px_approx")
    if not isinstance(tp, list) or len(tp) != 2:
        raise ValueError("board json missing board_roi_target_px_approx / board_tp_px_approx [x,y]")
    out["tp"] = (float(tp[0]), float(tp[1]))
    board_refs = data.get("board_roi_reference_approx")
    if not isinstance(board_refs, list):
        raise ValueError("board json missing board_roi_reference_approx list")
    seen: set[str] = set()
    for item in board_refs:
        if not isinstance(item, dict):
            continue
        rid = str(item.get("ref_id") or "")
        xy = item.get("center_px")
        if not rid or not isinstance(xy, list) or len(xy) != 2:
            continue
        if rid in seen:
            raise ValueError(f"duplicate ref_id in board json: {rid}")
        seen.add(rid)
        out[rid] = (float(xy[0]), float(xy[1]))
    if "ref_ic" not in out:
        raise ValueError("board json must include ref_ic in board_roi_reference_approx")
    return out


def validate_refinement_against_base(
    base_pts: dict[str, tuple[float, float]],
    refined_pts: dict[str, tuple[float, float]],
    *,
    max_delta_px: float = 40.0,
    max_relative_pairwise_dist_change: float | None = 0.12,
) -> dict[str, Any]:
    """Check small nudges only; optional edge-length ratio band (structure ~ preserved)."""
    if set(base_pts) != set(refined_pts):
        raise ValueError(
            f"refinement ref set mismatch: base={sorted(base_pts)} refined={sorted(refined_pts)}"
        )
    per: dict[str, float] = {}
    max_d = 0.0
    for k in base_pts:
        bx, by = base_pts[k]
        rx, ry = refined_pts[k]
        d = float(math.hypot(rx - bx, ry - by))
        per[k] = round(d, 4)
        max_d = max(max_d, d)
    if max_d > max_delta_px + 1e-9:
        raise ValueError(
            f"refinement exceeds max_delta_px={max_delta_px}: max={max_d:.3f} per_point={per}"
        )
    stats: dict[str, Any] = {
        "per_point_delta_px": per,
        "max_delta_px": round(max_d, 4),
    }
    if max_relative_pairwise_dist_change is None:
        return stats
    ids = list(base_pts.keys())
    worst_pair: tuple[str, str, float, float, float] | None = None
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a, b = ids[i], ids[j]
            db = math.hypot(
                base_pts[a][0] - base_pts[b][0],
                base_pts[a][1] - base_pts[b][1],
            )
            dr = math.hypot(
                refined_pts[a][0] - refined_pts[b][0],
                refined_pts[a][1] - refined_pts[b][1],
            )
            if db < 2.0:
                continue
            rel = abs(dr / db - 1.0)
            if worst_pair is None or rel > worst_pair[4]:
                worst_pair = (a, b, db, dr, rel)
            if rel > max_relative_pairwise_dist_change + 1e-9:
                raise ValueError(
                    f"pair ({a},{b}) relative dist change {rel:.4f} > "
                    f"{max_relative_pairwise_dist_change} (db={db:.2f} dr={dr:.2f})"
                )
    if worst_pair is not None:
        stats["worst_pairwise_rel_dist_change"] = round(worst_pair[4], 5)
        stats["worst_pairwise"] = {
            "a": worst_pair[0],
            "b": worst_pair[1],
            "dist_base_px": round(worst_pair[2], 3),
            "dist_refined_px": round(worst_pair[3], 3),
        }
    return stats


def run_apply_vlm_refinement(
    workspace: os.PathLike[str] | str | None = None,
    *,
    refine_json: os.PathLike[str] | str | None = None,
    max_delta_px: float = 40.0,
    max_relative_pairwise_dist_change: float | None = 0.12,
) -> dict[str, Any]:
    """Read VLM micro-adjustments, validate vs OpenCV-aligned points, write refined board JSON."""
    ws = Path(workspace or os.environ.get("WORKSPACE", ".")).resolve()
    dbg = ws / "debug"
    base_path = dbg / ALIGNED_JSON
    src_path = Path(refine_json) if refine_json is not None else dbg / VLM_REFINE_INPUT_JSON
    out_path = dbg / REFINED_JSON

    if not base_path.is_file():
        raise FileNotFoundError(
            f"Missing {base_path}; run run_align_locator_graph_to_board_ic_bbox_vlm "
            f"(or legacy run_align_locator_graph_to_board_ic_bbox) first"
        )
    if not src_path.is_file():
        raise FileNotFoundError(
            f"Missing {src_path}; save VLM refinement JSON as {VLM_REFINE_INPUT_JSON} "
            f"or pass refine_json="
        )

    base_obj = json.loads(base_path.read_text(encoding="utf-8"))
    refine_obj = json.loads(src_path.read_text(encoding="utf-8"))
    base_pts = _board_anchor_points_from_board_json(base_obj)
    refined_pts = _board_anchor_points_from_board_json(refine_obj)
    rstats = validate_refinement_against_base(
        base_pts,
        refined_pts,
        max_delta_px=max_delta_px,
        max_relative_pairwise_dist_change=max_relative_pairwise_dist_change,
    )

    board_refs_out: list[dict[str, Any]] = []
    for rid in sorted(
        refined_pts.keys(),
        key=lambda k: (0 if k == "ref_ic" else 1, k),
    ):
        if rid == "tp":
            continue
        x, y = refined_pts[rid]
        board_refs_out.append(
            {"ref_id": rid, "center_px": [int(round(x)), int(round(y))]}
        )
    tp_x, tp_y = refined_pts["tp"]
    merged: dict[str, Any] = {
        "schema_version": 1,
        "coordinate_frame": BOARD_STEP02,
        "source": "vlm_refine_after_opencv_align",
        "board_roi_target_px_approx": [int(round(tp_x)), int(round(tp_y))],
        "board_roi_reference_approx": board_refs_out,
        "refinement": {
            "base_file": ALIGNED_JSON,
            "vlm_input_file": str(src_path.name),
            "max_delta_px_limit": max_delta_px,
            "max_relative_pairwise_dist_change_limit": max_relative_pairwise_dist_change,
            **rstats,
        },
    }
    # Carry forward mapping metadata when helpful for downstream debug
    if isinstance(base_obj.get("transform"), dict):
        merged["opencv_align_transform"] = base_obj["transform"]
    out_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {out_path} max_delta={rstats['max_delta_px']}px (limit {max_delta_px})")
    return merged


def _resolve_green_tp_for_locator_build(
    ws: Path, bgr: np.ndarray
) -> tuple[float, float, float] | None:
    """Detect green TP on ``step02_locator`` frame; try same-size assembly fallbacks if needed."""
    tp = _detect_green_tp_center(bgr)
    if tp is not None:
        return tp
    h, w = bgr.shape[:2]
    dbg = ws / "debug"
    for name in (
        "case10_assembly_largest_ic_box.png",
        "case10_assembly_drawing_tp_marked.png",
        "case10_assembly_drawing.png",
    ):
        alt_p = dbg / name
        if not alt_p.is_file():
            continue
        alt_bgr = cv2.imread(str(alt_p))
        if alt_bgr is None or alt_bgr.shape[0] != h or alt_bgr.shape[1] != w:
            continue
        tp = _detect_green_tp_center(alt_bgr)
        if tp is not None:
            print(
                f"[case12-graph] green TP detected via fallback {name} "
                f"(same {w}x{h} as step02_locator_front_anchor)"
            )
            return tp
    return None


def run_build_step02_locator_graph(
    workspace: os.PathLike[str] | str | None = None,
    *,
    n_local_refs: int = 3,
    write_viz_png: bool = False,
) -> dict[str, Any]:
    ws = Path(workspace or os.environ.get("WORKSPACE", ".")).resolve()
    dbg = ws / "debug"
    loc_path = dbg / LOCATOR_STEP02
    out_json = dbg / OUT_JSON
    out_png = dbg / OUT_PNG

    if not loc_path.is_file():
        raise FileNotFoundError(f"Missing {loc_path}")
    bgr = cv2.imread(str(loc_path))
    if bgr is None:
        raise FileNotFoundError(f"Cannot read {loc_path}")

    tp = _resolve_green_tp_for_locator_build(ws, bgr)

    ic_bbox = _largest_red_box_bbox(bgr)
    if ic_bbox is None:
        payload = {
            "schema_version": 3,
            "frame": "step02_locator_front_anchor",
            "notes": "largest_red_anchor_box_not_detected",
            "tp_center": None,
            "tp_green_radius_px": None,
            "largest_ic": None,
            "references": [],
            "graph_edges": [],
            "pairwise_roi": [],
        }
        out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        if write_viz_png:
            cv2.imwrite(str(out_png), bgr.copy())
        print(f"Wrote {out_json} (no red IC box)")
        return payload

    x1, y1, x2, y2 = ic_bbox
    ic_cx = 0.5 * (x1 + x2)
    ic_cy = 0.5 * (y1 + y2)

    if tp is None:
        payload = {
            "schema_version": 3,
            "frame": "step02_locator_front_anchor",
            "notes": "green_tp_not_detected",
            "tp_center": None,
            "tp_green_radius_px": None,
            "largest_ic": {
                "ref_id": "ref_ic",
                "kind": "step02_red_anchor_box",
                "center_roi": [round(ic_cx, 2), round(ic_cy, 2)],
                "bbox_roi": [x1, y1, x2, y2],
            },
            "references": [],
            "graph_edges": [],
            "pairwise_roi": [],
        }
        out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        if write_viz_png:
            cv2.imwrite(str(out_png), bgr.copy())
        print(f"Wrote {out_json} (no green TP)")
        return payload

    tp_xy = (float(tp[0]), float(tp[1]))
    tp_r = float(tp[2])

    h, w = bgr.shape[:2]
    exclude_green = np.zeros((h, w), dtype=np.uint8)
    cv2.circle(
        exclude_green,
        (int(round(tp_xy[0])), int(round(tp_xy[1]))),
        int(max(6, round(tp_r + 4))),
        255,
        -1,
    )
    exclude_ic = _ic_exclude_mask(bgr.shape, ic_bbox, pad=12)
    exclude = cv2.bitwise_or(exclude_green, exclude_ic)

    rects = _rect_like_references(bgr, tp_xy, tp_r, exclude_mask=exclude)
    pads = _pad_circle_references(bgr, tp_xy, tp_r)
    pool: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    for cand in rects + pads:
        cx, cy = cand["center"]
        if exclude[int(np.clip(cy, 0, h - 1)), int(np.clip(cx, 0, w - 1))] > 0:
            continue
        key = (int(round(cx) // 4), int(round(cy) // 4))
        if key in seen:
            continue
        seen.add(key)
        pool.append(cand)

    k = max(1, min(int(n_local_refs), 5))
    picked = _pick_diverse_refs(pool, tp_xy, k=k)

    largest_ic_entry = {
        "ref_id": "ref_ic",
        "kind": "step02_red_anchor_box",
        "center_roi": [round(ic_cx, 2), round(ic_cy, 2)],
        "bbox_roi": [x1, y1, x2, y2],
        "dist_to_tp_px": round(float(math.hypot(ic_cx - tp_xy[0], ic_cy - tp_xy[1])), 3),
    }
    d_ic, ang_ic = _polar_from_tp(tp_xy, (ic_cx, ic_cy))
    largest_ic_entry["angle_deg_atan2_dy_dx"] = round(ang_ic, 3)

    references: list[dict[str, Any]] = []
    graph_edges: list[dict[str, Any]] = [
        {
            "from": "tp",
            "to": "ref_ic",
            "dist_px": round(d_ic, 3),
            "angle_deg_atan2_dy_dx": round(ang_ic, 3),
        }
    ]

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

    centers: dict[str, tuple[float, float]] = {
        "tp": tp_xy,
        "ref_ic": (float(ic_cx), float(ic_cy)),
    }
    for ref in references:
        cx, cy = ref["center_roi"]
        centers[ref["ref_id"]] = (float(cx), float(cy))

    pairwise_roi = build_pairwise_roi(centers)

    payload: dict[str, Any] = {
        "schema_version": 3,
        "frame": "step02_locator_front_anchor",
        "image_hint": str(loc_path.name),
        "tp_center": [round(tp_xy[0], 3), round(tp_xy[1], 3)],
        "tp_green_radius_px": round(tp_r, 3),
        "largest_ic": largest_ic_entry,
        "references": references,
        "graph_edges": graph_edges,
        "pairwise_roi": pairwise_roi,
    }
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # --- draw overlay (palette aligned with case10_dual_roi_refine) ---
    from case10_dual_roi_refine import _REF_OVERLAY_COLORS_BGR

    vis = bgr.copy()
    cv2.rectangle(vis, (x1, y1), (x2, y2), (0, 0, 255), 2)
    ic_pt = (int(round(ic_cx)), int(round(ic_cy)))
    cv2.drawMarker(
        vis,
        ic_pt,
        (0, 0, 255),
        markerType=cv2.MARKER_CROSS,
        markerSize=20,
        thickness=2,
    )
    cv2.putText(
        vis,
        "ref_ic",
        (ic_pt[0] + 8, ic_pt[1] - 8),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.5,
        (0, 0, 255),
        1,
        cv2.LINE_AA,
    )

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

    tx, ty = int(round(tp_xy[0])), int(round(tp_xy[1]))
    cv2.line(vis, (tx, ty), ic_pt, (200, 200, 255), 1, cv2.LINE_AA)

    pal = _REF_OVERLAY_COLORS_BGR
    for i, ref in enumerate(references):
        col = pal[i % len(pal)]
        rx0, ry0, rx1, ry1 = ref["bbox_roi"]
        cx, cy = int(round(ref["center_roi"][0])), int(round(ref["center_roi"][1]))
        if ref["kind"] == "ic_rect":
            cv2.rectangle(vis, (rx0, ry0), (rx1, ry1), col, 2)
        else:
            cv2.circle(vis, (cx, cy), 6, col, 2)
        cv2.line(vis, (tx, ty), (cx, cy), col, 1, cv2.LINE_AA)
        cv2.putText(
            vis,
            ref["ref_id"],
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

    if write_viz_png:
        cv2.imwrite(str(out_png), vis)
        print(f"Wrote {out_png}")
    print(
        f"Wrote {out_json} tp={payload['tp_center']} ref_ic=({ic_cx:.1f},{ic_cy:.1f}) "
        f"n_local_refs={len(references)}"
    )
    return payload


def run_write_board_approx_overlay(
    workspace: os.PathLike[str] | str | None = None,
    *,
    points_json: Path | None = None,
    out_png: os.PathLike[str] | str | None = None,
) -> Path:
    """Draw board approx points on ``step02_board_front_anchor.png`` (refined > aligned > vlm).

    Use ``out_png=…/case12_board_approx_overlay_opencv.png`` with ``points_json=aligned`` for the
    OpenCV-only debug image (written from ``run_align_…``; not overwritten by refine).
    """
    ws = Path(workspace or os.environ.get("WORKSPACE", ".")).resolve()
    dbg = ws / "debug"
    board_path = dbg / BOARD_STEP02
    if points_json is not None:
        pts_path = Path(points_json)
    else:
        refined = dbg / REFINED_JSON
        aligned = dbg / ALIGNED_JSON
        if refined.is_file():
            pts_path = refined
        elif aligned.is_file():
            pts_path = aligned
        else:
            pts_path = dbg / VLM_BOARD_JSON
    if out_png is None:
        out_path = dbg / BOARD_OVERLAY_PNG
    else:
        op = Path(out_png)
        out_path = op if op.is_absolute() else dbg / op

    if not board_path.is_file():
        raise FileNotFoundError(f"Missing {board_path}")
    if not pts_path.is_file():
        raise FileNotFoundError(
            f"Missing {pts_path}; run align-board (and optional apply-vlm-refine). WORKSPACE={ws}"
        )

    bgr = cv2.imread(str(board_path))
    if bgr is None:
        raise FileNotFoundError(f"Cannot read {board_path}")

    data = json.loads(pts_path.read_text(encoding="utf-8"))
    vis = bgr.copy()
    h, w = vis.shape[:2]

    def _pt(val: Any) -> tuple[int, int] | None:
        if not isinstance(val, list) or len(val) != 2:
            return None
        x, y = float(val[0]), float(val[1])
        return int(round(max(0, min(w - 1, x)))), int(round(max(0, min(h - 1, y))))

    board_refs = data.get("board_roi_reference_approx")
    if not isinstance(board_refs, list):
        board_refs = []

    from case10_dual_roi_refine import _REF_OVERLAY_COLORS_BGR

    pal = _REF_OVERLAY_COLORS_BGR
    ref_pts: dict[str, tuple[int, int]] = {}
    for i, item in enumerate(board_refs):
        if not isinstance(item, dict):
            continue
        rid = str(item.get("ref_id") or "")
        xy = item.get("center_px")
        p = _pt(xy)
        if not rid or p is None:
            continue
        ref_pts[rid] = p
        col = (0, 0, 255) if rid == "ref_ic" else pal[(max(0, i - 1)) % len(pal)]
        cv2.drawMarker(vis, p, col, markerType=cv2.MARKER_CROSS, markerSize=18, thickness=2)
        cv2.putText(
            vis,
            rid,
            (min(p[0] + 8, w - 80), max(p[1] - 8, 16)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            col,
            1,
            cv2.LINE_AA,
        )

    tp_ap = data.get("board_roi_target_px_approx") or data.get("board_tp_px_approx")
    tp_p = _pt(tp_ap)
    if tp_p is not None:
        cv2.drawMarker(
            vis,
            tp_p,
            (255, 0, 255),
            markerType=cv2.MARKER_CROSS,
            markerSize=22,
            thickness=2,
        )
        cv2.putText(
            vis,
            "tp_approx",
            (min(tp_p[0] + 8, w - 100), max(tp_p[1] - 12, 18)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (255, 0, 255),
            1,
            cv2.LINE_AA,
        )

    if tp_p is not None:
        ic_pt = ref_pts.get("ref_ic")
        if ic_pt is not None:
            cv2.line(vis, tp_p, ic_pt, (200, 200, 255), 1, cv2.LINE_AA)
        for rid, p in ref_pts.items():
            if rid == "ref_ic":
                continue
            cv2.line(vis, tp_p, p, (180, 220, 180), 1, cv2.LINE_AA)

    rids = sorted(ref_pts.keys(), key=lambda s: (s == "ref_ic", s))
    for ia in range(len(rids)):
        for ib in range(ia + 1, len(rids)):
            a, b = rids[ia], rids[ib]
            if a == "tp" or b == "tp":
                continue
            pa, pb = ref_pts[a], ref_pts[b]
            cv2.line(vis, pa, pb, (140, 140, 140), 1, cv2.LINE_AA)

    cv2.imwrite(str(out_path), vis)
    print(f"Wrote {out_path}")
    return out_path


def main() -> int:
    p = argparse.ArgumentParser(description="case12 Step2 full-frame anchor graph helpers")
    p.add_argument("--workspace", type=Path, default=None)
    sub = p.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("build-graph", help="locator step02 → case12_step02_locator_graph.json + PNG")
    g.add_argument("--n-local-refs", type=int, default=3)
    ab = sub.add_parser(
        "align-board",
        help="IC alignment → aligned JSON + opencv overlay (--mode vlm-bbox default, or opencv-red legacy)",
    )
    ab.add_argument(
        "--mode",
        choices=("vlm-bbox", "opencv-red"),
        default="vlm-bbox",
        help="vlm-bbox: VLM JSON largest-IC bbox on clean board; opencv-red: HSV red box on board",
    )
    ab.add_argument(
        "--vlm-bbox-json",
        type=Path,
        default=None,
        help=f"override path to VLM bbox json (default: debug/{VLM_BOARD_IC_BBOX_JSON})",
    )
    bo = sub.add_parser(
        "board-overlay",
        help="refined / aligned / vlm board json → overlay on board step02",
    )
    bo.add_argument(
        "--points-json",
        type=Path,
        default=None,
        help="override points file (default: refined > aligned > legacy vlm)",
    )
    bo.add_argument(
        "--out-png",
        type=Path,
        default=None,
        help="output PNG path (default: debug/case12_board_approx_overlay.png)",
    )
    ar = sub.add_parser(
        "apply-vlm-refine",
        help=f"validate {VLM_REFINE_INPUT_JSON} vs aligned → {REFINED_JSON}",
    )
    ar.add_argument("--refine-json", type=Path, default=None)
    ar.add_argument("--max-delta-px", type=float, default=40.0)
    ar.add_argument(
        "--max-rel-dist",
        type=float,
        default=0.12,
        help="max |d'/d - 1| between pairs; set negative to disable",
    )
    args = p.parse_args()
    root = args.workspace or Path(os.environ.get("WORKSPACE", "."))
    if args.cmd == "build-graph":
        run_build_step02_locator_graph(
            root, n_local_refs=args.n_local_refs, write_viz_png=True
        )
    elif args.cmd == "align-board":
        if args.mode == "opencv-red":
            run_align_locator_graph_to_board_ic_bbox(root, write_viz_png=True)
        else:
            run_align_locator_graph_to_board_ic_bbox_vlm(
                root,
                vlm_bbox_json=args.vlm_bbox_json,
            )
    elif args.cmd == "apply-vlm-refine":
        rel = args.max_rel_dist
        run_apply_vlm_refinement(
            root,
            refine_json=args.refine_json,
            max_delta_px=args.max_delta_px,
            max_relative_pairwise_dist_change=None if rel < 0 else rel,
        )
    else:
        run_write_board_approx_overlay(root, points_json=args.points_json, out_png=args.out_png)
    return 0


if __name__ == "__main__":
    sys.exit(main())
