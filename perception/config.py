"""Configuration module for the perception-planning pipeline."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

try:
    import yaml  # type: ignore[import-not-found]
except ImportError:
    yaml = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Dataclass definitions
# ---------------------------------------------------------------------------


@dataclass
class CameraConfig:
    """Camera intrinsic / extrinsic configuration."""

    width: int = 1280
    height: int = 720
    fps: float = 30.0
    # 3x3 camera intrinsic matrix (row-major flat list)
    intrinsics_k: List[float] = field(
        default_factory=lambda: [
            900.0, 0.0, 640.0,
            0.0, 900.0, 360.0,
            0.0, 0.0, 1.0,
        ]
    )
    # Distortion coefficients (k1, k2, p1, p2, k3, ...)
    dist_coeffs: List[float] = field(default_factory=list)
    frame_id: str = "camera_link"

    @property
    def resolution(self) -> Tuple[int, int]:
        return (self.width, self.height)


@dataclass
class MaskConfig:
    """Foreground / obstacle segmentation configuration."""

    method: str = "yolo_seg"  # yolo_seg | background_subtraction | sam
    # YOLOv8-seg parameters
    yolo_model_path: Optional[str] = None  # None = auto-detect bundled yolov8n-seg.pt
    yolo_conf_thresh: float = 0.10  # 降低阈值以检测更多物体（瓶子等）
    yolo_classes: Optional[List[str]] = None  # None = all 80 COCO classes
    # Background-subtraction parameters
    blur_k: int = 5
    thresh: int = 25
    min_area: int = 500
    # Arm exclusion zones in pixel coordinates (list of polygons, each polygon is a list of [x,y])
    arm_exclusion_zones: List[List[Tuple[int, int]]] = field(default_factory=list)
    # Optional model paths when using SAM
    sam_model_path: Optional[str] = None


@dataclass
class DepthConfig:
    """Depth-estimation backend and post-processing configuration."""

    backend: str = "depth_anything_v2"  # depth_anything_v2 | metric3d | unidepth | stereo
    # Method to align relative depth to metric scale
    metric_alignment_method: str = "plane_fit"  # plane_fit | known_distance
    # RANSAC inlier threshold for plane fitting (mm)
    plane_fit_inlier_threshold: float = 5.0
    # Gaussian smoothing sigma applied to depth map (pixels); 0 disables smoothing
    depth_smooth_sigma: float = 1.0


@dataclass
class ReconstructionConfig:
    """Point-cloud reconstruction & filtering configuration."""

    # Z-height of the table surface in the world frame (mm)
    table_z_mm: float = 0.0
    # Tolerance for removing points near the table plane (mm)
    remove_table_tolerance_mm: float = 5.0
    # Minimum projected area of an obstacle to be considered valid (mm^2)
    min_obstacle_area_mm2: float = 100.0
    # Height percentiles used for vertical bounding (p_low, p_high)
    height_percentiles: Tuple[float, float] = (5.0, 90.0)
    # Minimum number of points required to accept an obstacle cluster
    min_points_count: int = 50


@dataclass
class MapConfig:
    """Occupancy / cost-map configuration."""

    cell_size_mm: float = 10.0
    footprint_dilation_mm: float = 20.0
    height_safety_margin_mm: float = 10.0
    uncertainty_dilation_factor: float = 2.0


@dataclass
class PlanningConfig:
    """Motion-planning configuration."""

    coarse_planner: str = "astar"  # astar | rrt
    fine_planner: str = "rrt_connect"
    grid_resolution_mm: float = 20.0
    # Each capsule is defined by two endpoints (p1, p2) in the link frame and a radius (all in mm)
    arm_link_capsules: List[Dict[str, Any]] = field(default_factory=list)
    # Joint limits as list of (min_deg, max_deg) per joint; empty means no constraint
    joint_limits: List[Tuple[float, float]] = field(default_factory=list)
    step_size_mm: float = 5.0
    re_perception_every_mm: float = 10.0


# ---------------------------------------------------------------------------
# Top-level config
# ---------------------------------------------------------------------------


@dataclass
class PerceptionPlanningConfig:
    """Root configuration container."""

    camera: CameraConfig = field(default_factory=CameraConfig)
    mask: MaskConfig = field(default_factory=MaskConfig)
    depth: DepthConfig = field(default_factory=DepthConfig)
    reconstruction: ReconstructionConfig = field(default_factory=ReconstructionConfig)
    map: MapConfig = field(default_factory=MapConfig)
    planning: PlanningConfig = field(default_factory=PlanningConfig)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _default_arm_capsules() -> List[Dict[str, Any]]:
    """Sensible default arm-link capsules for a ~6-DOF desktop manipulator.

    Values are illustrative placeholders — tune to your real kinematics.
    """
    return [
        {"name": "link1", "p1": [0, 0, 0], "p2": [0, 0, 120], "radius": 35},
        {"name": "link2", "p1": [0, 0, 120], "p2": [200, 0, 120], "radius": 30},
        {"name": "link3", "p1": [200, 0, 120], "p2": [380, 0, 120], "radius": 28},
        {"name": "link4", "p1": [380, 0, 120], "p2": [500, 0, 120], "radius": 25},
        {"name": "link5", "p1": [500, 0, 120], "p2": [600, 0, 120], "radius": 22},
        {"name": "link6", "p1": [600, 0, 120], "p2": [680, 0, 120], "radius": 20},
    ]


def get_default_config() -> PerceptionPlanningConfig:
    """Return a configuration with reasonable defaults for a 6-DOF arm on a desktop workspace (~800x600 mm)."""

    cfg = PerceptionPlanningConfig()

    # Camera tuned for a top-down RGB-D sensor at ~1 m height covering ~800x600 mm
    cfg.camera.width = 1280
    cfg.camera.height = 720
    cfg.camera.fps = 30.0
    cfg.camera.intrinsics_k = [
        900.0, 0.0, 640.0,
        0.0, 900.0, 360.0,
        0.0, 0.0, 1.0,
    ]
    cfg.camera.dist_coeffs = [0.0, 0.0, 0.0, 0.0, 0.0]
    cfg.camera.frame_id = "camera_link"

    # Mask – YOLOv8-seg primary, background subtraction fallback
    from pathlib import Path
    _yolo_path = Path(__file__).parent / "yolov8n-seg.pt"
    cfg.mask.method = "yolo_seg"
    cfg.mask.yolo_model_path = str(_yolo_path) if _yolo_path.exists() else None
    cfg.mask.yolo_conf_thresh = 0.10  # 降低阈值以检测更多物体
    cfg.mask.yolo_classes = None  # all 80 COCO classes
    cfg.mask.blur_k = 5
    cfg.mask.thresh = 25
    cfg.mask.min_area = 500
    cfg.mask.arm_exclusion_zones = []
    cfg.mask.sam_model_path = None

    # Depth
    cfg.depth.backend = "depth_anything_v2"
    cfg.depth.metric_alignment_method = "plane_fit"
    cfg.depth.plane_fit_inlier_threshold = 5.0
    cfg.depth.depth_smooth_sigma = 1.0

    # Reconstruction
    cfg.reconstruction.table_z_mm = 0.0
    cfg.reconstruction.remove_table_tolerance_mm = 5.0
    cfg.reconstruction.min_obstacle_area_mm2 = 100.0
    cfg.reconstruction.height_percentiles = (5.0, 90.0)
    cfg.reconstruction.min_points_count = 50

    # Map
    cfg.map.cell_size_mm = 10.0
    cfg.map.footprint_dilation_mm = 20.0
    cfg.map.height_safety_margin_mm = 10.0
    cfg.map.uncertainty_dilation_factor = 2.0

    # Planning
    cfg.planning.coarse_planner = "astar"
    cfg.planning.fine_planner = "rrt_connect"
    cfg.planning.grid_resolution_mm = 20.0
    cfg.planning.arm_link_capsules = _default_arm_capsules()
    cfg.planning.joint_limits = [(-170, 170), (-90, 90), (-120, 120), (-170, 170), (-120, 120), (-170, 170)]
    cfg.planning.step_size_mm = 5.0
    cfg.planning.re_perception_every_mm = 10.0

    return cfg


def load_config(yaml_path: str) -> PerceptionPlanningConfig:
    """Load configuration from a YAML file.

    The YAML structure mirrors the dataclass hierarchy.  Missing keys fall
    back to :func:`get_default_config` values.
    """
    if yaml is None:
        raise ImportError(
            "PyYAML is required to load config files. Install it with `pip install pyyaml`."
        )

    path = os.path.expanduser(yaml_path)
    with open(path, "r", encoding="utf-8") as fh:
        raw: Dict[str, Any] = yaml.safe_load(fh) or {}

    cfg = get_default_config()

    # --- Camera -----------------------------------------------------------
    if "camera" in raw:
        c = raw["camera"]
        for attr in ("width", "height", "fps", "frame_id"):
            if attr in c:
                setattr(cfg.camera, attr, c[attr])
        if "intrinsics_k" in c:
            cfg.camera.intrinsics_k = list(c["intrinsics_k"])
        if "dist_coeffs" in c:
            cfg.camera.dist_coeffs = list(c["dist_coeffs"])

    # --- Mask -------------------------------------------------------------
    if "mask" in raw:
        m = raw["mask"]
        for attr in ("method", "blur_k", "thresh", "min_area", "sam_model_path",
                     "yolo_model_path", "yolo_conf_thresh"):
            if attr in m:
                setattr(cfg.mask, attr, m[attr])
        if "yolo_classes" in m:
            cfg.mask.yolo_classes = list(m["yolo_classes"])
        if "arm_exclusion_zones" in m:
            cfg.mask.arm_exclusion_zones = [
                [tuple(pt) for pt in zone] for zone in m["arm_exclusion_zones"]
            ]

    # --- Depth ------------------------------------------------------------
    if "depth" in raw:
        d = raw["depth"]
        for attr in ("backend", "metric_alignment_method", "plane_fit_inlier_threshold", "depth_smooth_sigma"):
            if attr in d:
                setattr(cfg.depth, attr, d[attr])

    # --- Reconstruction ---------------------------------------------------
    if "reconstruction" in raw:
        r = raw["reconstruction"]
        for attr in ("table_z_mm", "remove_table_tolerance_mm", "min_obstacle_area_mm2",
                     "min_points_count"):
            if attr in r:
                setattr(cfg.reconstruction, attr, r[attr])
        if "height_percentiles" in r:
            cfg.reconstruction.height_percentiles = tuple(r["height_percentiles"])

    # --- Map --------------------------------------------------------------
    if "map" in raw:
        mp = raw["map"]
        for attr in ("cell_size_mm", "footprint_dilation_mm", "height_safety_margin_mm",
                     "uncertainty_dilation_factor"):
            if attr in mp:
                setattr(cfg.map, attr, mp[attr])

    # --- Planning ---------------------------------------------------------
    if "planning" in raw:
        p = raw["planning"]
        for attr in ("coarse_planner", "fine_planner", "grid_resolution_mm", "step_size_mm",
                     "re_perception_every_mm"):
            if attr in p:
                setattr(cfg.planning, attr, p[attr])
        if "arm_link_capsules" in p:
            cfg.planning.arm_link_capsules = list(p["arm_link_capsules"])
        if "joint_limits" in p:
            cfg.planning.joint_limits = [tuple(jl) for jl in p["joint_limits"]]

    return cfg


# ---------------------------------------------------------------------------
# Convenience
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    import json

    default_cfg = get_default_config()
    print(json.dumps({
        "camera": vars(default_cfg.camera),
        "mask": vars(default_cfg.mask),
        "depth": vars(default_cfg.depth),
        "reconstruction": vars(default_cfg.reconstruction),
        "map": vars(default_cfg.map),
        "planning": {
            **vars(default_cfg.planning),
            "arm_link_capsules": default_cfg.planning.arm_link_capsules,
            "joint_limits": default_cfg.planning.joint_limits,
        },
    }, indent=2))
