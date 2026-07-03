"""
Depth estimation from monocular camera images.

Provides a unified interface for relative and metric depth estimation,
with plane-fitting alignment using known table geometry.
"""

from __future__ import annotations

from pathlib import Path
import abc
from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np


@dataclass
class DepthMap:
    """Container for a depth-map result."""

    depth: np.ndarray  # H x W, meters (metric) or arbitrary units (relative)
    uncertainty: Optional[np.ndarray] = None  # H x W, same shape as depth
    residual: float = 0.0  # scalar alignment residual
    is_metric: bool = False  # True if depth values are in meters


# ---------------------------------------------------------------------------
# Base estimator
# ---------------------------------------------------------------------------

class BaseDepthEstimator(abc.ABC):
    """Abstract base class for all depth estimators."""

    @abc.abstractmethod
    def estimate(self, rgb_image: np.ndarray) -> DepthMap:
        """Run inference on *rgb_image* (H x W x 3, uint8 or float)."""
        ...

    def _metric_align(
        self,
        depth_map: np.ndarray,
        mask: np.ndarray,
        table_points: Optional[np.ndarray] = None,
    ) -> Tuple[np.ndarray, float, np.ndarray]:
        """Align a *relative* depth map to metric scale using a known table.

        Parameters
        ----------
        depth_map : H x W, relative depth values.
        mask : H x W, boolean — pixels belonging to the table.
        table_points : optional N x 3 world-space points on the table
                       (used only to extract ``known_table_z`` when supplied).

        Returns
        -------
        aligned_depth : H x W metric depth in metres.
        residual : scalar mean residual of the plane fit.
        uncertainty : H x W per-pixel uncertainty.
        """

        K = np.eye(3)  # default intrinsics; subclasses may override
        known_table_z = 0.0 if table_points is None else float(np.mean(table_points[:, 2]))

        aligner = PlaneFittingAligner()
        return aligner.align_depth_to_plane(depth_map, mask, known_table_z, K)


# ---------------------------------------------------------------------------
# Plane-fitting utility
# ---------------------------------------------------------------------------

class PlaneFittingAligner:
    """Standalone utility for plane-based metric alignment."""

    # ------------------------------------------------------------------
    @staticmethod
    def fit_plane(
        points_3d: np.ndarray,
    ) -> Tuple[np.ndarray, np.ndarray, float]:
        """Fit a plane to *points_3d* (N x 3) via SVD.

        Returns ``(normal, point_on_plane, mean_residual)`` where
        ``normal`` is unit-length and ``point_on_plane`` is the centroid.
        """
        if points_3d.ndim != 2 or points_3d.shape[1] != 3:
            raise ValueError("points_3d must be N x 3")
        if points_3d.shape[0] < 3:
            raise ValueError("Need at least 3 points to fit a plane")

        centroid = np.mean(points_3d, axis=0)
        centered = points_3d - centroid
        _, s, vh = np.linalg.svd(centered)
        normal = vh[-1]  # smallest singular vector
        normal /= np.linalg.norm(normal) + 1e-12

        residuals = np.abs(centered @ normal)
        mean_residual = float(np.mean(residuals))
        return normal, centroid, mean_residual

    # ------------------------------------------------------------------
    @staticmethod
    def align_depth_to_plane(
        rel_depth: np.ndarray,
        table_mask: np.ndarray,
        known_table_z: float,
        K: np.ndarray,
    ) -> Tuple[np.ndarray, float, np.ndarray]:
        """Scale/shift *rel_depth* so that table-mask pixels sit at *known_table_z*.

        The alignment fits a plane in the **relative-depth** space over the
        table region, then computes an affine transform (scale + shift) that
        maps the fitted plane to *known_table_z*.

        Parameters
        ----------
        rel_depth : H x W — raw relative depth from a model.
        table_mask : H x W — boolean mask of table pixels.
        known_table_z : scalar — the true Z of the table in world coords.
        K : 3 x 3 camera intrinsics (unused for the scalar alignment but kept
            for API symmetry with future extensions).

        Returns
        -------
        metric_depth : H x W depth in metres.
        residual : mean absolute residual of the plane fit on table pixels.
        uncertainty : H x W — |rel_depth - plane_prediction| on table pixels,
                      extrapolated (via nearest neighbour dilation) elsewhere.
        """
        if rel_depth.shape != table_mask.shape:
            raise ValueError("rel_depth and table_mask must have the same shape")

        H, W = rel_depth.shape
        ys, xs = np.where(table_mask)
        if ys.size == 0:
            # No table pixels — fall back to identity (no scaling).
            uncertainty = np.full_like(rel_depth, np.nan)
            return rel_depth.copy(), float("inf"), uncertainty

        rel_vals = rel_depth[ys, xs]

        # Back-project table pixels to 3-D rays (homogeneous).
        uv = np.stack([xs, ys], axis=1)  # N x 2
        ones = np.ones((uv.shape[0], 1))
        pix_hom = np.concatenate([uv, ones], axis=1)  # N x 3
        dirs = np.linalg.inv(K) @ pix_hom.T  # 3 x N
        dirs = dirs / (dirs[2:3] + 1e-12)  # normalise by z

        # We want a plane in *relative-depth* space: d_rel = a*x + b*y + c
        # Solve via least-squares over table pixels.
        A = np.stack([dirs[0], dirs[1], np.ones_like(xs)], axis=1)  # N x 3
        coeffs, *_ = np.linalg.lstsq(A, rel_vals, rcond=None)

        plane_pred = A @ coeffs
        residuals = np.abs(rel_vals - plane_pred)
        residual = float(np.mean(residuals))

        # Affine alignment: metric = scale * rel + shift
        # We want mean(plane_pred) -> known_table_z
        scale = known_table_z / (np.mean(plane_pred) + 1e-12)
        shift = known_table_z - scale * np.mean(plane_pred)

        metric_depth = scale * rel_depth + shift

        # Uncertainty: per-pixel |rel - plane| on table, NaN elsewhere initially.
        uncertainty = np.full((H, W), np.nan)
        uncertainty[ys, xs] = residuals

        # Extrapolate uncertainty to non-table pixels via nearest-table lookup.
        # Uses scipy.ndimage.distance_transform_edt with return_indices to find
        # the nearest table pixel for every non-table pixel — O(H*W) not O(N²).
        from scipy.ndimage import distance_transform_edt  # type: ignore

        nan_mask = np.isnan(uncertainty)
        if not np.all(nan_mask):
            # distance_transform_edt on the inverse mask gives indices of
            # nearest True (table) pixel for every False position.
            table_indicator = ~nan_mask  # True where table pixels exist
            dists, indices = distance_transform_edt(
                ~table_indicator, return_indices=True
            )
            # indices[0] = row indices, indices[1] = col indices of nearest table pixel
            nearest_rows = indices[0]
            nearest_cols = indices[1]
            uncertainty[nan_mask] = uncertainty[nearest_rows[nan_mask], nearest_cols[nan_mask]]

        return metric_depth, residual, uncertainty


# ---------------------------------------------------------------------------
# DepthAnything V2
# ---------------------------------------------------------------------------

class DepthAnythingV2Estimator(BaseDepthEstimator):
    """Wrapper around Depth-Anything-V2."""

    def __init__(self, config: dict, model_path: Optional[str] = None):
        self.config = config
        self.model_path = model_path
        self._model = None
        self._load_model()

    def _load_model(self):
        try:
            # Prefer the official Depth Anything V2 package if installed.
            try:
                from depth_anything_v2.dpt import DepthAnythingV2 as DAV2Model  # type: ignore
            except ImportError:
                from transformers import DPTForDepthEstimation as DAV2Model  # type: ignore

            encoder = self.config.get("encoder", "vitl")
            self._model = DAV2Model.from_pretrained(
                self.model_path or f"depth-anything/Depth-Anything-V2-{encoder}"
            )
        except Exception as exc:
            raise RuntimeError(
                "DepthAnythingV2 backend not available. Install with:\n"
                "  pip install depth-anything-v2\n"
                "or\n"
                "  pip install transformers torch"
            ) from exc

    def estimate(self, rgb_image: np.ndarray) -> DepthMap:
        if self._model is None:
            raise RuntimeError("Model not loaded")

        import torch  # type: ignore

        tensor = torch.from_numpy(rgb_image.astype(np.float32)).permute(2, 0, 1).unsqueeze(0)
        with torch.no_grad():
            output = self._model(tensor)

        # Handle both HuggingFace and native outputs.
        if hasattr(output, "predicted_depth"):
            rel = output.predicted_depth.squeeze().cpu().numpy()
        else:
            rel = output.squeeze().cpu().numpy()

        # Build a crude table mask assuming bottom-centre region.
        H, W = rel.shape
        mask = np.zeros((H, W), dtype=bool)
        mask[int(0.7 * H):, int(0.2 * W):int(0.8 * W)] = True

        aligned, residual, uncertainty = self._metric_align(rel, mask)
        return DepthMap(depth=aligned, uncertainty=uncertainty, residual=residual, is_metric=True)


# ---------------------------------------------------------------------------
# Metric3D
# ---------------------------------------------------------------------------

class Metric3DEstimator(BaseDepthEstimator):
    """Wrapper around Metric3D (outputs metric depth directly)."""

    def __init__(self, config: dict, model_path: Optional[str] = None):
        self.config = config
        self.model_path = model_path
        self._model = None
        self._load_model()

    def _load_model(self):
        try:
            try:
                from metric3d.models import Metric3D as M3DModel  # type: ignore
            except ImportError:
                from transformers import AutoModelForDepthEstimation as M3DModel  # type: ignore

            self._model = M3DModel.from_pretrained(
                self.model_path or "yuankai/Metric3D"
            )
        except Exception as exc:
            raise RuntimeError(
                "Metric3D backend not available. Install with:\n"
                "  pip install metric3d\n"
                "or\n"
                "  pip install transformers torch"
            ) from exc

    def estimate(self, rgb_image: np.ndarray) -> DepthMap:
        if self._model is None:
            raise RuntimeError("Model not loaded")

        import torch  # type: ignore

        tensor = torch.from_numpy(rgb_image.astype(np.float32)).permute(2, 0, 1).unsqueeze(0)
        with torch.no_grad():
            output = self._model(tensor)

        if hasattr(output, "predicted_depth"):
            metric = output.predicted_depth.squeeze().cpu().numpy()
        else:
            metric = output.squeeze().cpu().numpy()

        H, W = metric.shape
        mask = np.zeros((H, W), dtype=bool)
        mask[int(0.7 * H):, int(0.2 * W):int(0.8 * W)] = True

        aligned, residual, uncertainty = self._metric_align(metric, mask)
        return DepthMap(depth=aligned, uncertainty=uncertainty, residual=residual, is_metric=True)


# ---------------------------------------------------------------------------
# UniDepth
# ---------------------------------------------------------------------------

class UniDepthEstimator(BaseDepthEstimator):
    """Wrapper around UniDepth."""

    def __init__(self, config: dict, model_path: Optional[str] = None):
        self.config = config
        self.model_path = model_path
        self._model = None
        self._load_model()

    def _load_model(self):
        try:
            from unidepth.models import UniDepthV1 as UDModel  # type: ignore

            self._model = UDModel.from_pretrained(
                self.model_path or "EPFL-VILAB/UniDepth"
            )
        except Exception as exc:
            raise RuntimeError(
                "UniDepth backend not available. Install with:\n"
                "  pip install unidepth"
            ) from exc

    def estimate(self, rgb_image: np.ndarray) -> DepthMap:
        if self._model is None:
            raise RuntimeError("Model not loaded")

        import torch  # type: ignore

        tensor = torch.from_numpy(rgb_image.astype(np.float32)).permute(2, 0, 1).unsqueeze(0)
        with torch.no_grad():
            output = self._model(tensor)

        if hasattr(output, "predicted_depth"):
            rel = output.predicted_depth.squeeze().cpu().numpy()
        else:
            rel = output.squeeze().cpu().numpy()

        H, W = rel.shape
        mask = np.zeros((H, W), dtype=bool)
        mask[int(0.7 * H):, int(0.2 * W):int(0.8 * W)] = True

        aligned, residual, uncertainty = self._metric_align(rel, mask)
        return DepthMap(depth=aligned, uncertainty=uncertainty, residual=residual, is_metric=True)


# ---------------------------------------------------------------------------
# Depth Anything V2 (via transformers)
# ---------------------------------------------------------------------------

class DepthAnythingV2Estimator(BaseDepthEstimator):
    """Depth Anything V3 — state-of-the-art monocular depth estimation.

    Uses the Hugging Face transformers integration.  Outputs relative depth
    that is then aligned to metric scale via plane fitting on the table region.
    """

    _DEFAULT_MODEL = "depth-anything/Depth-Anything-V2-Small-hf"

    def __init__(self, config: dict, model_path: Optional[str] = None):
        self.config = config
        self.model_id = model_path or self._DEFAULT_MODEL
        self._model = None
        self._processor = None
        self._load_model()

    def _load_model(self):
        try:
            from transformers import DPTForDepthEstimation, DPTImageProcessor
            self._processor = DPTImageProcessor.from_pretrained(self.model_id)
            self._model = DPTForDepthEstimation.from_pretrained(self.model_id)
        except Exception as exc:
            raise RuntimeError(
                f"Depth Anything V2 backend not available.\n"
                f"Install with: pip install transformers accelerate\n"
                f"Model: {self.model_id}"
            ) from exc

    def estimate(self, rgb_image: np.ndarray) -> DepthMap:
        if self._model is None:
            raise RuntimeError("Model not loaded")

        import torch
        import cv2

        H_orig, W_orig = rgb_image.shape[:2]

        # Preprocess
        inputs = self._processor(images=rgb_image, return_tensors="pt")

        # Inference
        with torch.no_grad():
            outputs = self._model(**inputs)

        # predicted_depth is [1, H, W] — resize to original size
        predicted = outputs.predicted_depth.squeeze().cpu().numpy()
        predicted = cv2.resize(predicted, (W_orig, H_orig),
                               interpolation=cv2.INTER_LINEAR)

        # Relative depth alignment: table = 0, closer = positive
        # Table region: bottom-centre
        mask = np.zeros((H_orig, W_orig), dtype=bool)
        mask[int(0.65 * H_orig):, int(0.1 * W_orig):int(0.9 * W_orig)] = True

        # Use median table depth as reference
        if mask.sum() > 0:
            table_median = float(np.median(predicted[mask]))
            # Depth = table_median - predicted (closer objects have higher depth)
            aligned = (table_median - predicted).astype(np.float32)
            # Ensure non-negative
            aligned = np.maximum(aligned, 0)
            # Residual = std of table depth (lower = better)
            residual = float(np.std(predicted[mask]))
        else:
            aligned = np.zeros_like(predicted, dtype=np.float32)
            residual = float("inf")

        # Uncertainty: uniform for now (will be refined in Phase 2)
        uncertainty = np.full_like(predicted, residual, dtype=np.float32)

        return DepthMap(
            depth=aligned,
            uncertainty=uncertainty,
            residual=residual,
            is_metric=True,
        )


# ---------------------------------------------------------------------------
# Depth Anything 3 ONNX backend (no PyTorch required)
# ---------------------------------------------------------------------------

class DA3OnnxEstimator(BaseDepthEstimator):
    """Depth Anything 3 via ONNX Runtime — no PyTorch dependency.

    Uses pre-exported DA3-SMALL-504.onnx for monocular depth estimation.
    Output is relative depth, aligned to table plane for metric scale.

    Dependencies: onnxruntime, opencv-python, numpy (already in requirements).
    """

    _DEFAULT_ONNX_PATH = str(Path(__file__).parent.parent / "models" / "depth" / "da3_onnx" / "DA3-SMALL-504.onnx")

    def __init__(self, config: dict, onnx_path: Optional[str] = None, process_res: int = 504):
        self.config = config
        self.onnx_path = onnx_path or self._DEFAULT_ONNX_PATH
        self.process_res = process_res
        self._session = None
        self._load_model()

    def _load_model(self):
        try:
            import onnxruntime as ort
            self._session = ort.InferenceSession(
                self.onnx_path,
                providers=["CPUExecutionProvider"],
            )
        except Exception as exc:
            raise RuntimeError(
                f"ONNX Runtime not available.\n"
                f"Install with: pip install onnxruntime\n"
                f"Model: {self.onnx_path}"
            ) from exc

    @property
    def model_id(self) -> str:
        return f"DA3-SMALL-{self.process_res}"

    def estimate(self, rgb_image: np.ndarray) -> DepthMap:
        if self._session is None:
            raise RuntimeError("ONNX model not loaded")

        import cv2

        H_orig, W_orig = rgb_image.shape[:2]
        proc_res = self.process_res

        # Preprocess: resize to process_res x process_res, normalize to [0,1]
        # NOTE: DA3 ONNX expects [0,1] normalization, NOT ImageNet mean/std
        img_resized = cv2.resize(rgb_image, (proc_res, proc_res), interpolation=cv2.INTER_LINEAR)
        img_float = img_resized.astype(np.float32) / 255.0
        input_tensor = img_float.transpose(2, 0, 1)[np.newaxis, ...].astype(np.float32)

        # DEBUG: print input tensor stats
        print(f"[DA3 DEBUG] input_tensor.shape: {input_tensor.shape}")
        print(f"[DA3 DEBUG] input_tensor.min/max/mean: {input_tensor.min():.4f}/{input_tensor.max():.4f}/{input_tensor.mean():.4f}")

        # Inference
        outputs = self._session.run(None, {"image": input_tensor})
        raw_depth = outputs[0]  # (1, 1, H, W) or (1, H, W)

        # DEBUG: print output stats
        print(f"[DA3 DEBUG] outputs[0].shape: {raw_depth.shape}")
        print(f"[DA3 DEBUG] outputs[0].min/max/mean: {raw_depth.min():.4f}/{raw_depth.max():.4f}/{raw_depth.mean():.4f}")

        # Handle both 4D and 3D outputs (official DA3 ONNX写法)
        if raw_depth.ndim == 4:
            raw_depth = raw_depth[0, 0]  # (H, W)
        elif raw_depth.ndim == 3:
            raw_depth = raw_depth[0]  # (H, W)

        # Resize back to original size
        raw_depth_full = cv2.resize(raw_depth, (W_orig, H_orig), interpolation=cv2.INTER_LINEAR)

        # DEBUG: print raw depth stats
        print(f"[DA3 DEBUG] raw_depth_full.min/max/mean: {raw_depth_full.min():.4f}/{raw_depth_full.max():.4f}/{raw_depth_full.mean():.4f}")

        # Table region for reference
        table_mask = np.zeros((H_orig, W_orig), dtype=bool)
        table_mask[int(0.65 * H_orig):, int(0.1 * W_orig):int(0.9 * W_orig)] = True

        if table_mask.sum() > 0:
            table_median = float(np.median(raw_depth_full[table_mask]))
            # Keep raw relative depth (no truncation)
            # Sign will be determined later: positive could mean closer or farther
            relative_depth = (raw_depth_full - table_median).astype(np.float32)
            residual = float(np.std(raw_depth_full[table_mask]))
        else:
            relative_depth = np.zeros_like(raw_depth_full, dtype=np.float32)
            table_median = 0.0
            residual = float("inf")

        uncertainty = np.full_like(relative_depth, residual, dtype=np.float32)

        return DepthMap(
            depth=relative_depth,
            uncertainty=uncertainty,
            residual=residual,
            is_metric=False,  # relative depth, not metric
        ), raw_depth_full, table_median
