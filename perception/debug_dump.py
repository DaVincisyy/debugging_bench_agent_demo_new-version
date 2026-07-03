"""
Debug dump utilities for perception pipeline.

Saves intermediate results from each scan into a timestamped directory:
    workspace/perception_debug/scan_YYYYMMDD_HHMMSS/

Files saved:
    00_frame.jpg          - raw camera frame
    01_yolo_overlay.jpg   - YOLO bounding boxes + labels
    02_yolo_mask.png      - YOLO segmentation mask
    03_bg_diff.png        - background difference (grayscale)
    04_bg_mask.png        - background subtraction binary mask
    05_arm_mask.png       - arm exclusion mask
    06_final_mask.png     - final combined mask
    07_final_overlay.jpg  - raw frame + red mask overlay
    08_components.jpg     - connected components with labels
    result.json           - obstacle list returned by scan
    diagnostics.json      - detailed diagnostics
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import cv2
import numpy as np


def _imwrite(filepath, img):
    """_imwrite that handles Chinese/Unicode paths on Windows."""
    # Use numpy + cv2.imencode to avoid Windows path encoding issues
    _, buf = cv2.imencode(
        os.path.splitext(filepath)[1],
        img,
        [int(cv2.IMWRITE_JPEG_QUALITY), 90] if filepath.endswith('.jpg') else []
    )
    with open(filepath, 'wb') as f:
        f.write(buf.tobytes())


# ---------------------------------------------------------------------------
# Core dump function
# ---------------------------------------------------------------------------

def dump_scan(
    output_dir: str,
    frame: np.ndarray,
    yolo_result: Optional[Any] = None,
    bg_diff: Optional[np.ndarray] = None,
    bg_mask: Optional[np.ndarray] = None,
    arm_mask: Optional[np.ndarray] = None,
    candidate_union: Optional[np.ndarray] = None,
    final_mask: Optional[np.ndarray] = None,
    depth_colormap: Optional[np.ndarray] = None,
    depth_overlay: Optional[np.ndarray] = None,
    depth_stats_img: Optional[np.ndarray] = None,
    obstacles: Optional[List[Dict[str, Any]]] = None,
    diagnostics: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Save a complete scan debug dump.

    Parameters
    ----------
    output_dir : base directory for all scan dumps
    frame : raw camera frame (HxWx3 uint8)
    yolo_result : ObstacleMask from YoloObstacleMasker (or None)
    bg_diff : background difference grayscale (HxW)
    bg_mask : background subtraction binary mask (HxW uint8)
    arm_mask : arm exclusion mask (HxW uint8)
    final_mask : final combined mask (HxW uint8)
    obstacles : list of obstacle dicts returned by scan
    diagnostics : optional pre-computed diagnostics dict

    Returns
    -------
    Path to the created scan directory.
    """
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    scan_dir = os.path.join(output_dir, f"scan_{ts}")
    os.makedirs(scan_dir, exist_ok=True)

    H, W = frame.shape[:2]

    # ------------------------------------------------------------------
    # 00_frame.jpg
    # ------------------------------------------------------------------
    _imwrite(os.path.join(scan_dir, "00_frame.jpg"), frame)

    # ------------------------------------------------------------------
    # 01_yolo_overlay.jpg — YOLO boxes + labels (real YOLO only)
    # ------------------------------------------------------------------
    yolo_overlay = frame.copy()
    yolo_mask_area = 0
    yolo_detections = []

    # Only draw YOLO overlay if we have real YOLO detections
    if yolo_result is not None and yolo_result.detections:
        yolo_mask = yolo_result.mask
        yolo_mask_area = int((yolo_mask > 0).sum())

        # Draw segmentation mask
        mask_color = np.zeros_like(frame)
        mask_color[yolo_mask > 0] = [0, 255, 0]  # green for YOLO
        yolo_overlay = cv2.addWeighted(yolo_overlay, 0.8, mask_color, 0.2, 0)

        # Draw bounding boxes from detections
        from ultralytics import YOLO
        # Get class names
        class_names = {}
        try:
            model_path = getattr(yolo_result, '_model_path', None)
            if model_path:
                class_names = YOLO(model_path).names
        except Exception:
            pass

        for cls_id, conf, bbox in yolo_result.detections:
            if len(bbox) == 4:
                x1, y1, x2, y2 = [int(v) for v in bbox]
                name = class_names.get(cls_id, f"class_{cls_id}")
                cv2.rectangle(yolo_overlay, (x1, y1), (x2, y2), (255, 255, 0), 2)
                label = f"{name} {conf:.2f}"
                cv2.putText(yolo_overlay, label, (x1, y1 - 5),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 0), 1)
                yolo_detections.append({
                    "class_id": cls_id,
                    "class": name,
                    "confidence": round(conf, 4),
                    "bbox_xyxy": [x1, y1, x2, y2],
                    "mask_area_px": yolo_mask_area,
                })
    # If no YOLO detections, keep original frame as overlay

    _imwrite(os.path.join(scan_dir, "01_yolo_overlay.jpg"), yolo_overlay)

    # ------------------------------------------------------------------
    # 02_yolo_mask.png — YOLO segmentation mask (not fallback)
    # ------------------------------------------------------------------
    # Use the yolo_mask from debug info, not yolo_result.mask (which may contain fallback)
    yolo_only_mask = None
    if yolo_result is not None:
        # Check if this is a real YOLO result or fallback
        if hasattr(yolo_result, 'detections') and yolo_result.detections:
            yolo_only_mask = yolo_result.mask
        else:
            # Fallback case - save empty mask
            yolo_only_mask = np.zeros((H, W), dtype=np.uint8)

    if yolo_only_mask is not None and yolo_only_mask.sum() > 0:
        _imwrite(os.path.join(scan_dir, "02_yolo_mask.png"), yolo_only_mask)
    else:
        _save_blank_mask(scan_dir, "02_yolo_mask.png", H, W)

    # ------------------------------------------------------------------
    # 03_bg_diff.png — background difference grayscale
    # ------------------------------------------------------------------
    if bg_diff is not None:
        diff_normalized = cv2.normalize(bg_diff, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
        _imwrite(os.path.join(scan_dir, "03_bg_diff.png"), diff_normalized)
    else:
        _save_blank_mask(scan_dir, "03_bg_diff.png", H, W)

    # ------------------------------------------------------------------
    # 04_bg_mask.png — background subtraction binary
    # ------------------------------------------------------------------
    if bg_mask is not None:
        _imwrite(os.path.join(scan_dir, "04_bg_mask.png"), bg_mask)
    else:
        _save_blank_mask(scan_dir, "04_bg_mask.png", H, W)

    # ------------------------------------------------------------------
    # 05_arm_mask.png — arm exclusion
    # ------------------------------------------------------------------
    if arm_mask is not None:
        _imwrite(os.path.join(scan_dir, "05_arm_mask.png"), arm_mask)
    else:
        _save_blank_mask(scan_dir, "05_arm_mask.png", H, W)

    # ------------------------------------------------------------------
    # 06_candidate_union.png — YOLO | BG before arm exclusion
    # ------------------------------------------------------------------
    if candidate_union is not None:
        _imwrite(os.path.join(scan_dir, "06_candidate_union.png"), candidate_union)
    else:
        _save_blank_mask(scan_dir, "06_candidate_union.png", H, W)

    # ------------------------------------------------------------------
    # 07_final_mask.png — final combined mask (after arm exclusion)
    # ------------------------------------------------------------------
    if final_mask is not None:
        _imwrite(os.path.join(scan_dir, "07_final_mask.png"), final_mask)
    else:
        _save_blank_mask(scan_dir, "07_final_mask.png", H, W)

    # ------------------------------------------------------------------
    # 08_final_overlay.jpg — raw frame + red mask overlay
    # ------------------------------------------------------------------
    final_overlay = frame.copy()
    if final_mask is not None:
        overlay_red = np.zeros_like(frame)
        overlay_red[final_mask > 0] = [0, 0, 255]  # red in BGR
        final_overlay = cv2.addWeighted(final_overlay, 0.7, overlay_red, 0.3, 0)
    _imwrite(os.path.join(scan_dir, "08_final_overlay.jpg"), final_overlay)

    # ------------------------------------------------------------------
    # 09_depth_colormap.jpg — Depth Anything 3 depth visualization
    # ------------------------------------------------------------------
    if depth_colormap is not None:
        _imwrite(os.path.join(scan_dir, "09_depth_colormap.jpg"), depth_colormap)
    else:
        _save_blank_mask(scan_dir, "09_depth_colormap.jpg", H, W, color=True)

    # ------------------------------------------------------------------
    # 10_depth_overlay.jpg — original frame + depth semi-transparent
    # ------------------------------------------------------------------
    if depth_overlay is not None:
        _imwrite(os.path.join(scan_dir, "10_depth_overlay.jpg"), depth_overlay)
    else:
        _imwrite(os.path.join(scan_dir, "10_depth_overlay.jpg"), frame)

    # ------------------------------------------------------------------
    # 11_obstacle_depth_stats.jpg — connected components with depth stats
    # ------------------------------------------------------------------
    if depth_stats_img is not None:
        _imwrite(os.path.join(scan_dir, "11_obstacle_depth_stats.jpg"), depth_stats_img)
    else:
        _imwrite(os.path.join(scan_dir, "11_obstacle_depth_stats.jpg"), frame)

    # ------------------------------------------------------------------
    # 12_components.jpg — connected components with labels
    # ------------------------------------------------------------------
    components_img = frame.copy()
    final_areas = []
    rejected_components = []

    if final_mask is not None and final_mask.sum() > 0:
        num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(
            final_mask, connectivity=8
        )

        # Component 0 is background, skip
        min_area = 500  # same as min_contour_area in config

        for i in range(1, num_labels):
            area = int(stats[i, cv2.CC_STAT_AREA])
            cx, cy = int(centroids[i, 0]), int(centroids[i, 1])
            x, y, w, h = stats[i, cv2.CC_STAT_LEFT], stats[i, cv2.CC_STAT_TOP], \
                         stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT]

            if area >= min_area:
                # Accepted component — green contour
                component_mask = (labels == i).astype(np.uint8) * 255
                contours, _ = cv2.findContours(component_mask, cv2.RETR_EXTERNAL,
                                               cv2.CHAIN_APPROX_SIMPLE)
                if contours:
                    cv2.drawContours(components_img, contours, -1, (0, 255, 0), 2)
                    cv2.circle(components_img, (cx, cy), 5, (0, 255, 0), -1)
                    cv2.putText(components_img, f"#{i} {area}px",
                                (cx - 30, cy - 10), cv2.FONT_HERSHEY_SIMPLEX,
                                0.35, (0, 255, 0), 1)
                    final_areas.append(area)
            else:
                # Rejected component — red X
                cv2.putText(components_img, f"x{area}",
                            (cx - 15, cy), cv2.FONT_HERSHEY_SIMPLEX,
                            0.3, (0, 0, 255), 1)
                rejected_components.append({
                    "component_id": i,
                    "reason": "area_too_small",
                    "area_px": area,
                    "centroid": [cx, cy],
                })

    _imwrite(os.path.join(scan_dir, "08_components.jpg"), components_img)

    # ------------------------------------------------------------------
    # result.json
    # ------------------------------------------------------------------
    result_data = {
        "timestamp": datetime.now().isoformat(),
        "scan_dir": scan_dir,
        "obstacle_count": len(obstacles) if obstacles else 0,
        "obstacles": obstacles or [],
    }
    with open(os.path.join(scan_dir, "result.json"), "w", encoding="utf-8") as f:
        json.dump(result_data, f, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------------
    # diagnostics.json
    # ------------------------------------------------------------------
    if diagnostics is None:
        diagnostics = {}

    diagnostics["timestamp"] = datetime.now().isoformat()
    diagnostics["frame_shape"] = list(frame.shape)
    diagnostics["scan_dir"] = scan_dir

    # YOLO diagnostics
    if "yolo" not in diagnostics:
        diagnostics["yolo"] = {}
    diagnostics["yolo"]["enabled"] = yolo_result is not None
    if yolo_result is not None:
        diagnostics["yolo"]["detections"] = yolo_detections
        diagnostics["yolo"]["mask_area_px"] = yolo_mask_area
        diagnostics["yolo"]["method"] = getattr(yolo_result, 'method', 'unknown')

    # Background diagnostics
    if "background" not in diagnostics:
        diagnostics["background"] = {}
    diagnostics["background"]["available"] = bg_mask is not None
    diagnostics["background"]["used"] = bg_diff is not None
    if bg_diff is not None:
        diagnostics["background"]["raw_diff_mean"] = round(float(bg_diff.mean()), 2)
        diagnostics["background"]["mask_area_px"] = int((bg_mask > 0).sum()) if bg_mask is not None else 0

    # Arm exclusion diagnostics
    if "arm_exclusion" not in diagnostics:
        diagnostics["arm_exclusion"] = {}
    diagnostics["arm_exclusion"]["mask_area_px"] = int((arm_mask > 0).sum()) if arm_mask is not None else 0

    # Final mask diagnostics
    if "final" not in diagnostics:
        diagnostics["final"] = {}
    diagnostics["final"]["mask_area_px"] = int((final_mask > 0).sum()) if final_mask is not None else 0
    diagnostics["final"]["connected_components"] = len(final_areas) + len(rejected_components)
    diagnostics["final"]["accepted_components"] = len(final_areas)
    diagnostics["final"]["rejected_components"] = rejected_components

    with open(os.path.join(scan_dir, "diagnostics.json"), "w", encoding="utf-8") as f:
        json.dump(diagnostics, f, indent=2, ensure_ascii=False)

    return scan_dir


def _save_blank_mask(scan_dir: str, filename: str, h: int, w: int, color: bool = False) -> None:
    """Save a blank (all-zero) mask image."""
    if color:
        blank = np.zeros((h, w, 3), dtype=np.uint8)
    else:
        blank = np.zeros((h, w), dtype=np.uint8)
    _imwrite(os.path.join(scan_dir, filename), blank)
