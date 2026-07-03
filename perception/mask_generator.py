"""Obstacle mask generation from camera frames.

Primary method: **YOLOv8-seg** — zero-shot instance segmentation of
80 COCO classes with pixel-level masks.

Optional fallback: background subtraction (for environments where YOLO
cannot run or a static camera setup is preferred).

Architecture:
    YoloObstacleMasker  →  ArmExclusionMasker  →  CombinedMaskGenerator
    (primary)                (AND NOT arm)            (final output)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import cv2
import numpy as np


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class MaskConfig:
    """Configuration for mask generation."""

    # YOLO settings
    yolo_model_path: str = ""              # path to yolov8n-seg.pt (empty = auto-detect)
    yolo_conf_thresh: float = 0.25         # confidence threshold for detection
    yolo_classes: Optional[List[str]] = None  # if set, only mask these classes; None = all 80
    # Background subtraction fallback
    bg_update_rate: float = 0.01
    blur_kernel_size: int = 5
    threshold: int = 30
    min_contour_area: float = 500.0
    morph_open_kernel: int = 3
    morph_close_kernel: int = 7
    contrast_low: float = 20.0
    contrast_high: float = 100.0
    # Arm exclusion
    arm_exclusion_zones: Optional[List[np.ndarray]] = None


@dataclass
class ObstacleMask:
    """Result of a mask-generation step.

    Attributes
        mask:  uint8 array, same HxW as input, values in {0, 255}
        confidence:  float in [0, 1] scalar or per-pixel HxW
        method:  human-readable label
        detections:  list of (class_id, confidence, bbox_xyxy) for debugging
    """
    mask: np.ndarray
    confidence: float = 1.0
    method: str = ""
    detections: List[tuple] = field(default_factory=list)


# ===================================================================
# 1. YoloObstacleMasker  — primary method
# ===================================================================

class YoloObstacleMasker:
    """Generate obstacle masks via **YOLOv8-seg** instance segmentation.

    Runs zero-shot inference on every frame.  By default it masks all
    80 COCO classes; set ``classes`` in config to filter (e.g. only
    detect bottles, cups, tools on a workbench).

    The YOLO model is loaded lazily on first call so that importing this
    module is cheap when YOLO isn't needed.
    """

    _DEFAULT_MODEL_PATH = str(Path(__file__).parent / "yolov8n-seg.pt")

    def __init__(self, config: MaskConfig) -> None:
        self.config = config
        self._model = None

    # -- lazy model loading ---------------------------------------------------

    def _get_model(self):
        if self._model is None:
            from ultralytics import YOLO
            model_path = self.config.yolo_model_path or self._DEFAULT_MODEL_PATH
            if not Path(model_path).exists():
                raise FileNotFoundError(
                    f"YOLO weights not found at {model_path}.\n"
                    "Download with: yolo download model=yolov8n-seg.pt\n"
                    "Or set yolo_model_path in MaskConfig."
                )
            self._model = YOLO(model_path)
        return self._model

    # -- public API -----------------------------------------------------------

    def generate_mask(self, frame: np.ndarray) -> ObstacleMask:
        """Run YOLOv8-seg inference and combine all detected masks.

        Parameters
        ----------
        frame : HxWx3 BGR/RGB image.

        Returns
        -------
        ObstacleMask with binary mask (255 = obstacle pixel).
        """
        model = self._get_model()
        results = model(frame, conf=self.config.yolo_conf_thresh, verbose=False)

        H, W = frame.shape[:2]
        combined = np.zeros((H, W), dtype=np.uint8)
        detections: List[tuple] = []
        max_conf = 0.0

        for r in results:
            if r.masks is None:
                continue

            masks_data = r.masks.data  # (N, H, W) tensor or numpy
            boxes = r.boxes  # includes class ids and confidences

            if hasattr(masks_data, "cpu"):
                masks_arr = masks_data.cpu().numpy()
            else:
                masks_arr = np.array(masks_data)

            for i, seg in enumerate(masks_arr):
                cls_id = int(boxes.cls[i]) if boxes is not None and boxes.cls is not None else -1
                conf = float(boxes.conf[i]) if boxes is not None and boxes.conf is not None else 1.0

                # Class filter
                if self.config.yolo_classes is not None:
                    from ultralytics import YOLO
                    model_names = model.names
                    cls_name = model_names.get(cls_id, str(cls_id))
                    if cls_name not in self.config.yolo_classes:
                        continue

                binary_seg = (seg > 0.5).astype(np.uint8) * 255

                # Resize mask to match original frame size if needed
                if binary_seg.shape != (H, W):
                    binary_seg = cv2.resize(
                        binary_seg,
                        (W, H),  # cv2.resize takes (width, height)
                        interpolation=cv2.INTER_NEAREST,
                    )

                combined = cv2.bitwise_or(combined, binary_seg)
                max_conf = max(max_conf, conf)

                bbox = boxes.xyxy[i].cpu().numpy() if boxes is not None else []
                detections.append((cls_id, conf, bbox.tolist() if len(bbox) else []))

        confidence = max_conf if detections else 0.0
        return ObstacleMask(
            mask=combined,
            confidence=confidence,
            method="yolov8_seg",
            detections=detections,
        )

    def get_class_names(self) -> dict[int, str]:
        """Return the COCO class name mapping from the loaded model."""
        return self._get_model().names


# ===================================================================
# 2. BackgroundSubtractionMasker  — fallback
# ===================================================================

class BackgroundSubtractionMasker:
    """Generate obstacle masks via classical background subtraction.

    Uses a running-average background model.  Suitable for static-camera
    setups where the background is relatively stable.
    """

    def __init__(self, config: MaskConfig) -> None:
        self.config = config
        self._background: Optional[np.ndarray] = None

    def update_background(self, frame: np.ndarray) -> None:
        """Update the reference background model."""
        gray = self._to_gray(frame)
        if self._background is None:
            self._background = gray.astype(np.float32)
        else:
            cv2.accumulateWeighted(gray, self._background, self.config.bg_update_rate)

    def generate_mask(self, frame: np.ndarray) -> ObstacleMask:
        if self._background is None:
            raise RuntimeError("Background not initialised – call update_background() first.")

        gray = self._to_gray(frame)
        bg = self._background.astype(np.uint8)

        diff = cv2.absdiff(gray, bg)
        _, binary = cv2.threshold(diff, self.config.threshold, 255, cv2.THRESH_BINARY)

        open_k = self.config.morph_open_kernel
        close_k = self.config.morph_close_kernel
        kernel_open = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (open_k, open_k))
        kernel_close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (close_k, close_k))
        cleaned = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel_open)
        cleaned = cv2.morphologyEx(cleaned, cv2.MORPH_CLOSE, kernel_close)

        k = self.config.blur_kernel_size
        if k > 1:
            cleaned = cv2.GaussianBlur(cleaned, (k | 1, k | 1), 0)

        contours, _ = cv2.findContours(cleaned, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        filtered = np.zeros_like(cleaned)
        max_diff_val = 0
        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area >= self.config.min_contour_area:
                cv2.drawContours(filtered, [cnt], -1, 255, -1)
                region_mean = cv2.mean(diff, mask=(filtered > 0).astype(np.uint8))[0]
                max_diff_val = max(max_diff_val, region_mean)

        confidence = self._compute_confidence(max_diff_val)
        return ObstacleMask(mask=filtered, confidence=confidence, method="bg_subtraction")

    @staticmethod
    def _to_gray(frame: np.ndarray) -> np.ndarray:
        if frame.ndim == 2:
            return frame
        return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    def _compute_confidence(self, contrast: float) -> float:
        lo, hi = self.config.contrast_low, self.config.contrast_high
        if contrast <= lo:
            return 0.0
        if contrast >= hi:
            return 1.0
        return (contrast - lo) / (hi - lo)


# ===================================================================
# 3. ArmExclusionMasker
# ===================================================================

class ArmExclusionMasker:
    """Produce a binary mask marking pixels occupied by the robot arm."""

    def __init__(self, arm_zones: Optional[List[np.ndarray]] = None) -> None:
        self.arm_zones: List[np.ndarray] = arm_zones if arm_zones is not None else []

    def generate_exclusion_mask(
        self,
        frame_shape,
        arm_pose: Optional[np.ndarray] = None,
    ) -> np.ndarray:
        """Return uint8 mask where arm pixels = 255."""
        h, w = frame_shape[0], frame_shape[1]
        mask = np.zeros((h, w), dtype=np.uint8)

        if arm_pose is not None:
            self._draw_dynamic_silhouette(mask, arm_pose)
        else:
            for poly in self.arm_zones:
                pts = self._normalise(poly, h, w)
                cv2.fillPoly(mask, [pts], 255)

        return mask

    @staticmethod
    def _normalise(poly: np.ndarray, h: int, w: int) -> np.ndarray:
        if poly.max() <= 1.0:
            pts = poly.copy()
            pts[:, 0] *= w
            pts[:, 1] *= h
            return pts.astype(np.int32)
        return poly.astype(np.int32)

    def _draw_dynamic_silhouette(self, mask: np.ndarray, arm_pose: np.ndarray) -> None:
        """Draw a rough exclusion circle around the arm end-effector.

        *arm_pose* is treated as a 2-D pixel position (x, y) of the
        end-effector.  A circle of radius ~80 px is drawn — large enough
        to cover the arm body and tool.

        This is intentionally simple: no forward-kinematics, no kinematic
        chain.  Just a rough bounding circle around where the arm currently
        is.  Projects that need precision can replace this with their own
        FK-based silhouette renderer.
        """
        h, w = mask.shape[:2]
        pt = arm_pose.flatten()

        if pt.size >= 2:
            cx, cy = int(pt[0]), int(pt[1])
            radius = 80  # px — covers arm body + gripper
        elif pt.size == 1:
            # Single scalar — assume image centre region
            cx, cy = w // 2, int(h * 0.6)
            radius = 80
        else:
            return

        cv2.circle(mask, (cx, cy), radius, 255, -1)


# ===================================================================
# 4. CombinedMaskGenerator  — YOLO primary + arm exclusion
# ===================================================================

class CombinedMaskGenerator:
    """Composite masker: YOLOv8-seg (primary) + arm exclusion + bg-sub fallback.

    Flow:
        1. Try YOLOv8-seg → obstacle mask
        2. Apply arm exclusion (AND NOT arm_mask)
        3. If YOLO fails / no detections, fall back to background subtraction
    """

    def __init__(
        self,
        config: MaskConfig,
        arm_masker: Optional[ArmExclusionMasker] = None,
        bg_masker: Optional[BackgroundSubtractionMasker] = None,
    ) -> None:
        self.config = config
        self.yolo_masker = YoloObstacleMasker(config)
        self.arm_masker = arm_masker
        self.bg_masker = bg_masker  # optional fallback

    # -- public API -----------------------------------------------------------

    def process(self, frame: np.ndarray, arm_pose: Optional[np.ndarray] = None) -> ObstacleMask:
        """Run the full mask pipeline: YOLO → arm exclusion → fallback."""
        result, _ = self.process_with_debug(frame, arm_pose)
        return result

    def process_with_debug(
        self, frame: np.ndarray, arm_pose: Optional[np.ndarray] = None
    ) -> tuple[ObstacleMask, Dict[str, Any]]:
        """Run the full mask pipeline and return intermediate results for debugging.

        Returns
        -------
        (ObstacleMask, dict) where dict contains:
            yolo_mask : YOLO segmentation mask (or None)
            yolo_detections : list of (cls_id, conf, bbox)
            bg_diff : background difference grayscale (or None)
            bg_mask : background subtraction binary mask (or None)
            arm_mask : arm exclusion mask (or None)
        """
        H, W = frame.shape[:2]
        debug: Dict[str, Any] = {
            "yolo_mask": None,
            "yolo_detections": [],
            "bg_diff": None,
            "bg_mask": None,
            "arm_mask": None,
            "candidate_union": None,
        }

        # Initialize masks
        yolo_mask = np.zeros((H, W), dtype=np.uint8)
        bg_mask = np.zeros((H, W), dtype=np.uint8)
        arm_mask = np.zeros((H, W), dtype=np.uint8)
        bg_diff = np.zeros((H, W), dtype=np.uint8)

        # 1. YOLOv8-seg (always runs)
        yolo_status = "no_detection"
        yolo_error = None
        yolo_detections = []
        try:
            yolo_result = self.yolo_masker.generate_mask(frame)
            if yolo_result.mask.sum() > 0:
                yolo_mask = yolo_result.mask.copy()
                yolo_detections = list(yolo_result.detections)
                yolo_status = "detected"
            debug["yolo_mask"] = yolo_mask.copy()
            debug["yolo_detections"] = yolo_detections
            debug["yolo_raw_mask_area_px"] = int(yolo_mask.sum() // 255)
        except Exception as e:
            yolo_error = f"{type(e).__name__}: {e}"
            debug["yolo_error"] = yolo_error
            yolo_status = "error"

        # 2. Background subtraction (always runs)
        bg_computed = False
        if self.bg_masker is not None:
            try:
                bg_result = self.bg_masker.generate_mask(frame)
                bg_mask = bg_result.mask.copy()
                # Get diff image for debugging
                gray = self.bg_masker._to_gray(frame)
                bg = self.bg_masker._background.astype(np.uint8)
                bg_diff = cv2.absdiff(gray, bg)
                bg_computed = True
            except RuntimeError:
                pass  # background not initialised

        debug["bg_diff"] = bg_diff if bg_computed else None
        debug["bg_mask"] = bg_mask.copy()
        debug["background_computed"] = bg_computed
        debug["background_mask_area_px"] = int(bg_mask.sum() // 255)

        # 3. Arm exclusion (always generates)
        arm_zones_count = 0
        if self.arm_masker is not None:
            arm_mask = self.arm_masker.generate_exclusion_mask(frame.shape, arm_pose)
            arm_zones_count = len(self.arm_masker.arm_zones)

        # Use learned arm_mask if available (from learn_background)
        learned_arm_mask = getattr(self, '_learned_arm_mask', None)
        if learned_arm_mask is not None:
            if arm_mask is not None:
                arm_mask = cv2.bitwise_or(arm_mask, learned_arm_mask)
            else:
                arm_mask = learned_arm_mask.copy()
            arm_zones_count = -1  # -1 indicates learned mask
        debug["arm_mask"] = arm_mask.copy()
        debug["arm_exclusion_enabled"] = self.arm_masker is not None
        debug["arm_exclusion_zones_count"] = arm_zones_count
        debug["arm_mask_area_px"] = int(arm_mask.sum() // 255)

        # 4. Fusion: candidate = (YOLO | BG) & ~arm_mask
        candidate_mask = cv2.bitwise_or(yolo_mask, bg_mask)
        candidate_mask = cv2.bitwise_and(candidate_mask, cv2.bitwise_not(arm_mask))
        debug["candidate_union"] = candidate_mask.copy()

        # Record component sources
        debug["yolo_only_area_px"] = int(cv2.bitwise_and(yolo_mask, cv2.bitwise_not(bg_mask)).sum() // 255)
        debug["bg_only_area_px"] = int(cv2.bitwise_and(bg_mask, cv2.bitwise_not(yolo_mask)).sum() // 255)
        debug["yolo_and_bg_area_px"] = int(cv2.bitwise_and(yolo_mask, bg_mask).sum() // 255)
        debug["removed_by_arm_px"] = int(cv2.bitwise_and(candidate_mask, arm_mask).sum() // 255)

        # Final mask
        final_mask = candidate_mask
        debug["final_mask_area_px"] = int(final_mask.sum() // 255)
        debug["yolo_status"] = yolo_status

        return ObstacleMask(
            mask=final_mask,
            confidence=max(yolo_result.confidence if 'yolo_result' in locals() else 0.0,
                          float(bg_computed)),
            method="yolo_and_bg" if yolo_status == "detected" and bg_computed else
                   "yolo_only" if yolo_status == "detected" else
                   "bg_only" if bg_computed else "empty",
            detections=yolo_detections,
        ), debug

    def _run_fallback(self, frame: np.ndarray) -> tuple[np.ndarray, Optional[np.ndarray], Optional[np.ndarray]]:
        """Run background subtraction fallback if available.

        Returns
        -------
        (mask, diff_grayscale, binary_mask)
        """
        if self.bg_masker is not None:
            try:
                result = self.bg_masker.generate_mask(frame)
                # Also get the diff image for debugging
                gray = self.bg_masker._to_gray(frame)
                bg = self.bg_masker._background.astype(np.uint8)
                diff = cv2.absdiff(gray, bg)
                return result.mask, diff, result.mask
            except RuntimeError:
                pass  # background not initialised
        H, W = frame.shape[:2]
        return np.zeros((H, W), dtype=np.uint8), None, None
