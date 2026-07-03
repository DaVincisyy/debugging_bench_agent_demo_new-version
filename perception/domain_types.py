"""
Data types for the perception -> planning pipeline.

All types are pure data containers using dataclasses with numpy typing.
No mock or runtime logic — just schema definitions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict, Any

import numpy as np


@dataclass
class CameraFrame:
    """Raw camera frame with calibration metadata."""
    image: np.ndarray                     # HxWxC (BGR or RGB)
    timestamp: float                      # Unix epoch seconds
    cam_id: str                           # e.g. "realsense_d435"
    intrinsics_K: np.ndarray              # 3x3 camera matrix
    dist_coeffs: np.ndarray               # distortion coefficients vector


@dataclass
class ObstacleMask:
    """Binary segmentation mask for obstacles."""
    mask: np.ndarray                      # HxW uint8, 0=background, 1=obstacle
    mask_type: str                        # "background_subtraction" | "sam" | "yolo_seg" | "arm_exclusion"
    confidence: float | np.ndarray = 1.0  # scalar [0,1] or per-pixel HxW float array


@dataclass
class DepthMap:
    """2.5D depth information aligned to camera frame."""
    depth: np.ndarray                     # HxW float32, depth in mm
    is_metric: bool                       # True if values are real-world mm
    alignment_residual: float             # residual from plane-fit alignment (mm)
    uncertainty: np.ndarray = field(default_factory=lambda: np.array([]))  # HxW float32, per-pixel std dev


@dataclass
class PointCloud:
    """Unstructured 3D point cloud derived from depth + mask."""
    xyz: np.ndarray                       # Nx3 float64, world-frame coordinates
    colors: Optional[np.ndarray] = None   # Nx3 uint8 RGB, optional
    mask_indices: np.ndarray = field(default_factory=lambda: np.array([], dtype=np.int64))  # indices into source pixel grid
    source_frame_ref: str = ""            # reference frame name, e.g. "base_link"


@dataclass
class ObstacleInfo:
    """Structured obstacle description consumed by the planner."""
    footprint_xy: List[Tuple[float, float]]  # list of (x_mm, y_mm) contour points on table plane
    height_p90: float                       # 90th-percentile height above table (mm)
    height_p99: float                       # 99th-percentile height above table (mm)
    base_z: float                           # z-coordinate of obstacle base (mm, world frame)
    top_z: float                            # z-coordinate of obstacle top (mm, world frame)
    confidence: float                       # [0, 1] detection confidence
    obstacle_id: int                        # unique identifier across frames


@dataclass
class ObstacleMap:
    """Grid-based 2.5D occupancy / height map for collision checking."""
    cell_size_mm: float
    grid: np.ndarray                        # HxW structured array with fields:
                                            #   max_height (float), min_height (float),
                                            #   has_obstacle (bool), uncertainty (float)
    footprint_dilation_mm: float            # how much footprints were dilated before rasterising
    height_safety_margin_mm: float          # added to max_height during planning


@dataclass
class PlanPath:
    """A candidate path produced by the motion planner."""
    waypoints: List[Dict[str, Any]]         # each entry: joint angles {joint_name: angle} or cartesian {x,y,z,r,p,y}
    collision_free: bool
    cost: float


@dataclass
class ExecutionStep:
    """Result of executing one waypoint on the robot."""
    target_pose: Dict[str, float]           # commanded pose
    actual_pose: Dict[str, float]           # measured pose after execution
    success: bool                           # whether the step succeeded
    re_perception_triggered: bool           # whether perception was re-triggered mid-execution
