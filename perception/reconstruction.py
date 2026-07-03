"""
Reconstruction module for 2.5D/3D obstacle geometry from depth + mask.

Provides functions to convert depth maps to point clouds, filter table points,
and compute obstacle information via grid-based clustering.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np


@dataclass
class PointCloud:
    """Simple point cloud container."""

    xyz: np.ndarray  # (N, 3) array of float64
    attributes: dict = field(default_factory=dict)

    @property
    def num_points(self) -> int:
        return self.xyz.shape[0]

    def is_empty(self) -> bool:
        return self.num_points == 0


@dataclass
class ObstacleInfo:
    """Information about a single detected obstacle."""

    cluster_id: int
    points: np.ndarray  # (M, 3) points belonging to this obstacle
    footprint: np.ndarray  # (K, 2) convex hull or bounding box corners in XY
    height_p90: float  # 90th percentile Z relative to table_z
    height_p99: float  # 99th percentile Z relative to table_z
    base_z: float  # min Z absolute
    top_z: float  # max Z absolute
    confidence: float  # confidence score [0, 1]
    centroid: np.ndarray = field(default_factory=lambda: np.zeros(2))  # (2,) XY centroid
    area: float = 0.0  # footprint area in mm^2


def depth_to_pointcloud(
    depth_map: np.ndarray,
    mask: np.ndarray,
    K: np.ndarray,
) -> PointCloud:
    """Convert metric depth + mask to a 3D point cloud.

    Parameters
    ----------
    depth_map : np.ndarray
        Metric depth image of shape (H, W).
    mask : np.ndarray
        Binary obstacle mask of shape (H, W). Non-zero pixels are obstacles.
    K : np.ndarray
        Camera intrinsic matrix (3x3):
            [[fx,  0, cx],
             [ 0, fy, cy],
             [ 0,  0,  1]]

    Returns
    -------
    PointCloud with xyz points in camera coordinates.
    """
    if depth_map.shape != mask.shape:
        raise ValueError(f"Shape mismatch: depth {depth_map.shape} vs mask {mask.shape}")

    H, W = depth_map.shape
    fx = K[0, 0]
    fy = K[1, 1]
    cx = K[0, 2]
    cy = K[1, 2]

    # Create pixel coordinate grids
    u, v = np.meshgrid(np.arange(W), np.arange(H))

    # Only keep masked pixels
    valid = mask.astype(bool)
    u_valid = u[valid]
    v_valid = v[valid]
    d_valid = depth_map[valid]

    # Filter out zero/negative depths
    positive = d_valid > 0
    u_valid = u_valid[positive]
    v_valid = v_valid[positive]
    d_valid = d_valid[positive]

    if d_valid.size == 0:
        return PointCloud(xyz=np.empty((0, 3), dtype=np.float64))

    # Back-project to 3D
    X = (u_valid - cx) * d_valid / fx
    Y = (v_valid - cy) * d_valid / fy
    Z = d_valid.copy()

    xyz = np.column_stack([X, Y, Z])

    pc = PointCloud(xyz=xyz.astype(np.float64))
    pc.attributes["pixel_u"] = u_valid
    pc.attributes["pixel_v"] = v_valid
    pc.attributes["depth"] = d_valid

    return pc


def remove_table_points(
    pointcloud: PointCloud,
    table_z: float,
    tolerance_mm: float = 5.0,
    small_margin_mm: float = 50.0,
) -> PointCloud:
    """Filter out points that lie on or below the table surface.

    Keeps only points whose Z coordinate is above ``table_z - tolerance``
    and below ``table_z + small_margin``.

    Parameters
    ----------
    pointcloud : PointCloud
        Input point cloud.
    table_z : float
        Z coordinate of the table surface (in same units as point cloud).
    tolerance_mm : float
        Lower tolerance below table_z (points closer than this are removed).
    small_margin_mm : float
        Upper margin above table_z (points higher than table_z + margin are removed).

    Returns
    -------
    PointCloud containing only points above the table.
    """
    if pointcloud.is_empty():
        return PointCloud(xyz=np.empty((0, 3), dtype=np.float64))

    z = pointcloud.xyz[:, 2]
    lower_bound = table_z - tolerance_mm
    upper_bound = table_z + small_margin_mm

    above_table = (z >= lower_bound) & (z <= upper_bound)

    filtered_xyz = pointcloud.xyz[above_table]

    result = PointCloud(xyz=filtered_xyz)
    # Copy relevant attributes that survive filtering
    for key, val in pointcloud.attributes.items():
        if isinstance(val, np.ndarray) and val.shape[0] == pointcloud.num_points:
            result.attributes[key] = val[above_table]

    return result


# ---------------------------------------------------------------------------
# Grid-based clustering (no sklearn dependency)
# ---------------------------------------------------------------------------


def _grid_cluster(
    xyz: np.ndarray,
    grid_size_mm: float,
) -> np.ndarray:
    """Assign each point to a cluster via grid-based connected components.

    Algorithm
    ---------
    1. Discretise XY into grid cells of size ``grid_size_mm``.
    2. Mark occupied cells.
    3. BFS flood-fill over the 8-connected neighbourhood to find connected
       components of occupied cells.
    4. Map cell-level cluster ids back to individual points.

    Parameters
    ----------
    xyz : np.ndarray
        (N, 3) point array.
    grid_size_mm : float
        Side length of each grid cell.

    Returns
    -------
    labels : np.ndarray
        (N,) integer cluster labels, starting from 0.
    """
    if xyz.shape[0] == 0:
        return np.empty((0,), dtype=np.intp)

    xy = xyz[:, :2]

    # Compute grid indices
    min_xy = xy.min(axis=0)
    grid_u = ((xy[:, 0] - min_xy[0]) / grid_size_mm).astype(np.intp)
    grid_v = ((xy[:, 1] - min_xy[1]) / grid_size_mm).astype(np.intp)

    # Determine grid dimensions
    max_u = grid_u.max()
    max_v = grid_v.max()
    gu_count = max_u + 1
    gv_count = max_v + 1

    # Build a set of occupied cells
    occupied = np.zeros((gu_count, gv_count), dtype=bool)
    occupied[grid_u, grid_v] = True

    # BFS to label connected components (8-connectivity)
    labels_grid = np.full((gu_count, gv_count), -1, dtype=np.intp)
    cluster_id = 0
    directions = [(-1, -1), (-1, 0), (-1, 1),
                  (0, -1),           (0, 1),
                  (1, -1),  (1, 0),  (1, 1)]

    for start_u in range(gu_count):
        for start_v in range(gv_count):
            if not occupied[start_u, start_v] or labels_grid[start_u, start_v] >= 0:
                continue
            # BFS from this cell
            queue = deque()
            queue.append((start_u, start_v))
            labels_grid[start_u, start_v] = cluster_id
            while queue:
                cu, cv = queue.popleft()
                for du, dv in directions:
                    nu, nv = cu + du, cv + dv
                    if 0 <= nu < gu_count and 0 <= nv < gv_count:
                        if occupied[nu, nv] and labels_grid[nu, nv] < 0:
                            labels_grid[nu, nv] = cluster_id
                            queue.append((nu, nv))
            cluster_id += 1

    # Map back to points
    point_labels = labels_grid[grid_u, grid_v]
    return point_labels


def _convex_hull_2d(points: np.ndarray) -> np.ndarray:
    """Compute 2D convex hull using Graham scan / monotone chain.

    Parameters
    ----------
    points : np.ndarray
        (N, 2) XY points.

    Returns
    -------
    hull : np.ndarray
        Vertices of the convex hull in CCW order, shape (K, 2).
    """
    pts = points
    if pts.shape[0] <= 1:
        return pts.copy()

    # Sort by x then y
    idx = np.lexsort((pts[:, 1], pts[:, 0]))
    pts = pts[idx]

    # Cross product of vectors OA and OB
    def cross(O, A, B):
        return (A[0] - O[0]) * (B[1] - O[1]) - (A[1] - O[1]) * (B[0] - O[0])

    # Build lower hull
    lower = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)

    # Build upper hull
    upper = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)

    # Concatenation, last point of each half is omitted because it's repeated
    hull = np.array(lower[:-1] + upper[:-1], dtype=np.float64)
    return hull


def _polygon_area(hull: np.ndarray) -> float:
    """Shoelace formula for polygon area.

    Parameters
    ----------
    hull : np.ndarray
        (K, 2) vertices of a closed polygon (first vertex NOT repeated at end).

    Returns
    -------
    area : float
    """
    if hull.shape[0] < 3:
        return 0.0
    x = hull[:, 0]
    y = hull[:, 1]
    return float(abs(np.dot(x, np.roll(y, 1)) - np.dot(y, np.roll(x, 1)))) / 2.0


def compute_obstacle_info(
    pointcloud: PointCloud,
    table_z: float,
    config: dict,
) -> List[ObstacleInfo]:
    """Cluster points and compute per-obstacle statistics.

    Parameters
    ----------
    pointcloud : PointCloud
        Points already filtered to be above the table.
    table_z : float
        Table surface Z used as reference for relative heights.
    config : dict
        Configuration dictionary. Expected keys:
        - ``grid_size_mm`` (float): grid cell size for clustering.
        - ``height_percentiles`` (list[float]): percentiles to compute;
          typically ``[90, 99]``. The first two map to height_p90 / height_p99.
        - ``min_points_per_cluster`` (int, optional): discard clusters smaller
          than this (default 10).
        - ``depth_uncertainty_m_per_m`` (float, optional): relative depth noise
          used in confidence computation (default 0.01).

    Returns
    -------
    obstacles : list[ObstacleInfo]
    """
    if pointcloud.is_empty():
        return []

    grid_size_mm = float(config.get("grid_size_mm", 20.0))
    height_percentiles = config.get("height_percentiles", [90, 99])
    min_pts = int(config.get("min_points_per_cluster", 10))
    depth_rel_noise = float(config.get("depth_uncertainty_m_per_m", 0.01))

    xyz = pointcloud.xyz
    labels = _grid_cluster(xyz, grid_size_mm)

    unique_labels = np.unique(labels)
    obstacles: List[ObstacleInfo] = []

    for cid in unique_labels:
        mask_c = labels == cid
        pts = xyz[mask_c]
        if pts.shape[0] < min_pts:
            continue

        z_vals = pts[:, 2]
        z_relative = z_vals - table_z

        # Height percentiles
        p90 = float(np.percentile(z_relative, height_percentiles[0]))
        p99 = float(np.percentile(z_relative, height_percentiles[1]))

        base_z = float(z_vals.min())
        top_z = float(z_vals.max())

        # Footprint (convex hull of XY)
        xy_pts = pts[:, :2]
        hull = _convex_hull_2d(xy_pts)
        area = _polygon_area(hull)

        # Centroid
        centroid = xy_pts.mean(axis=0)

        # Confidence: based on number of points, spread, and depth uncertainty
        n_pts = pts.shape[0]
        mean_depth = float(z_vals.mean())
        # Simple heuristic: more points and tighter depth distribution => higher confidence
        depth_spread = float(z_relative.std()) + 1e-6
        point_quality = min(n_pts / 500.0, 1.0)  # saturate at 500 pts
        depth_quality = 1.0 / (1.0 + depth_rel_noise * mean_depth / depth_spread)
        density = n_pts / (area + 1e-6)
        density_quality = min(density * 100.0, 1.0)  # heuristic scaling
        confidence = float(
            0.4 * point_quality + 0.3 * depth_quality + 0.3 * density_quality
        )
        confidence = min(confidence, 1.0)

        obs = ObstacleInfo(
            cluster_id=int(cid),
            points=pts,
            footprint=hull,
            height_p90=p90,
            height_p99=p99,
            base_z=base_z,
            top_z=top_z,
            confidence=confidence,
            centroid=centroid,
            area=area,
        )
        obstacles.append(obs)

    # Sort by confidence descending
    obstacles.sort(key=lambda o: o.confidence, reverse=True)
    # Re-index cluster ids after sorting
    for i, obs in enumerate(obstacles):
        obs.cluster_id = i

    return obstacles


# ---------------------------------------------------------------------------
# ReconstructionPipeline
# ---------------------------------------------------------------------------


class ReconstructionPipeline:
    """End-to-end pipeline: depth+mask -> obstacle list."""

    def __init__(self, config: dict, K: np.ndarray, table_z: float):
        """
        Parameters
        ----------
        config : dict
            Passed through to :func:`compute_obstacle_info`.
        K : np.ndarray
            3x3 camera intrinsic matrix.
        table_z : float
            Estimated table-plane Z in camera coordinates.
        """
        self.config = config
        self.K = K
        self.table_z = table_z

    def process(self, depth_map: np.ndarray, obstacle_mask: np.ndarray) -> List[ObstacleInfo]:
        """Run the full reconstruction pipeline.

        Parameters
        ----------
        depth_map : np.ndarray
            Metric depth (H, W).
        obstacle_mask : np.ndarray
            Binary mask (H, W); non-zero indicates obstacle pixels.

        Returns
        -------
        obstacles : list[ObstacleInfo]
        """
        # Step 1: depth -> point cloud
        pc = depth_to_pointcloud(depth_map, obstacle_mask, self.K)
        if pc.is_empty():
            return []

        # Step 2: remove table points
        pc_above = remove_table_points(pc, self.table_z)
        if pc_above.is_empty():
            return []

        # Step 3: cluster & compute stats
        obstacles = compute_obstacle_info(pc_above, self.table_z, self.config)
        return obstacles
