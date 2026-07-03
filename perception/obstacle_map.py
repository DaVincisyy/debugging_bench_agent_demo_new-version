"""
2.5D Obstacle Grid Map module.

Builds and maintains a 2.5D obstacle grid map for robot workspace planning.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np


@dataclass
class ObstacleInfo:
    """Information about a single obstacle."""

    footprint_3d: np.ndarray  # (N, 3) array of (x, y, z) points in mm
    confidence: float = 1.0  # detection confidence [0, 1]
    label: str = "unknown"

    @property
    def min_z(self) -> float:
        return float(np.min(self.footprint_3d[:, 2]))

    @property
    def max_z(self) -> float:
        return float(np.max(self.footprint_3d[:, 2]))


@dataclass
class GridCell:
    """A single cell in the obstacle grid map."""

    max_h: float = -float("inf")
    min_h: float = float("inf")
    occupied: bool = False
    uncertainty: float = 0.0


class ObstacleGridMap:
    """
    2.5D obstacle grid map.

    The map discretizes a rectangular workspace into a regular grid. Each cell
    stores height bounds (min_h / max_h), occupancy status, and an uncertainty
    value derived from obstacle detection confidence.
    """

    def __init__(
        self,
        workspace_bounds: Tuple[float, float, float, float],
        cell_size_mm: float,
        config: Optional[Dict[str, Any]] = None,
    ):
        """
        Parameters
        ----------
        workspace_bounds : (x_min, x_max, y_min, y_max) in mm
        cell_size_mm : side length of each square cell in mm
        config : optional configuration dict
        """
        self.x_min, self.x_max, self.y_min, self.y_max = workspace_bounds
        self.cell_size_mm = cell_size_mm
        self.config = config or {}

        # Number of cells in each dimension (at least 1)
        self.nx = max(1, int(math.ceil((self.x_max - self.x_min) / self.cell_size_mm)))
        self.ny = max(1, int(math.ceil((self.y_max - self.y_min) / self.cell_size_mm)))

        # Initialize empty grid
        self.grid: np.ndarray = np.empty((self.ny, self.nx), dtype=object)
        for iy in range(self.ny):
            for ix in range(self.nx):
                self.grid[iy, ix] = GridCell()

    # ------------------------------------------------------------------ #
    #  Coordinate helpers                                                  #
    # ------------------------------------------------------------------ #

    def _world_to_grid(self, x: float, y: float) -> Tuple[int, int]:
        """Convert world coordinates to grid indices (ix, iy)."""
        ix = int((x - self.x_min) / self.cell_size_mm)
        iy = int((y - self.y_min) / self.cell_size_mm)
        return ix, iy

    def _grid_to_world_center(self, ix: int, iy: int) -> Tuple[float, float]:
        """Return the world center of a grid cell."""
        cx = self.x_min + (ix + 0.5) * self.cell_size_mm
        cy = self.y_min + (iy + 0.5) * self.cell_size_mm
        return cx, cy

    def _in_bounds(self, ix: int, iy: int) -> bool:
        return 0 <= ix < self.nx and 0 <= iy < self.ny

    # ------------------------------------------------------------------ #
    #  Update                                                             #
    # ------------------------------------------------------------------ #

    def update(self, obstacles: List[ObstacleInfo]) -> None:
        """
        Integrate a list of obstacles into the grid.

        For each obstacle we rasterise its footprint onto the grid, updating
        height bounds and occupancy.
        """
        for obs in obstacles:
            pts = obs.footprint_3d
            if pts.ndim != 2 or pts.shape[1] != 3:
                continue

            uncertainty = 1.0 - obs.confidence

            for pt in pts:
                x, y, z = float(pt[0]), float(pt[1]), float(pt[2])
                ix, iy = self._world_to_grid(x, y)
                if not self._in_bounds(ix, iy):
                    continue

                cell: GridCell = self.grid[iy, ix]
                cell.occupied = True
                cell.max_h = max(cell.max_h, z)
                cell.min_h = min(cell.min_h, z)
                # Blend uncertainty (higher when repeated observations disagree)
                cell.uncertainty = max(cell.uncertainty, uncertainty)

    # ------------------------------------------------------------------ #
    #  Morphological dilation on footprint                                 #
    # ------------------------------------------------------------------ #

    def dilate_footprint(self, dilation_mm: float) -> None:
        """
        Binary dilation of occupied cells by *dilation_mm*.

        Cells reached by dilation inherit the maximum height from the original
        occupied neighbourhood and receive increased uncertainty.
        """
        # Build binary mask
        mask: np.ndarray = np.zeros((self.ny, self.nx), dtype=bool)
        for iy in range(self.ny):
            for ix in range(self.nx):
                if self.grid[iy, ix].occupied:
                    mask[iy, ix] = True

        # Kernel radius in cells
        r = max(1, int(math.ceil(dilation_mm / self.cell_size_mm)))
        kernel = np.ones((2 * r + 1, 2 * r + 1), dtype=np.uint8)

        dilated_mask = cv2_dilate_binary(mask, kernel)

        # Propagate heights & mark new cells
        for iy in range(self.ny):
            for ix in range(self.nx):
                if not self._in_bounds(ix, iy):
                    continue
                if dilated_mask[iy, ix] and not self.grid[iy, ix].occupied:
                    # Find max_h in neighbourhood from original occupied cells
                    nh_max = -float("inf")
                    for dy in range(-r, r + 1):
                        for dx in range(-r, r + 1):
                            niy, nix = iy + dy, ix + dx
                            if self._in_bounds(nix, niy) and mask[niy, nix]:
                                nh_max = max(nh_max, self.grid[niy][nix].max_h)
                    if nh_max > -float("inf"):
                        self.grid[iy, ix].max_h = nh_max
                        self.grid[iy, ix].occupied = True
                        self.grid[iy, ix].uncertainty = min(
                            1.0, self.grid[iy, ix].uncertainty + 0.3
                        )

    # ------------------------------------------------------------------ #
    #  Height margin                                                      #
    # ------------------------------------------------------------------ #

    def dilate_height(self, margin_mm: float) -> None:
        """Add *margin_mm* to ``max_h`` of every occupied cell."""
        for iy in range(self.ny):
            for ix in range(self.nx):
                cell = self.grid[iy, ix]
                if cell.occupied:
                    cell.max_h += margin_mm

    # ------------------------------------------------------------------ #
    #  Uncertainty-aware safety                                           #
    # ------------------------------------------------------------------ #

    def apply_uncertainty_safety(self, factor: float) -> None:
        """
        For cells with high uncertainty, inflate the effective height margin.

        The dilation already applied is scaled by *factor* where uncertainty
        is high (>= 0.5). This is a conservative safety measure.
        """
        threshold = 0.5
        for iy in range(self.ny):
            for ix in range(self.nx):
                cell = self.grid[iy, ix]
                if cell.occupied and cell.uncertainty >= threshold:
                    extra = cell.max_h * (factor - 1.0) * cell.uncertainty
                    cell.max_h += extra

    # ------------------------------------------------------------------ #
    #  Occupancy query                                                    #
    # ------------------------------------------------------------------ #

    def is_occupied(self, x: float, y: float, z: Optional[float] = None) -> bool:
        """
        Check whether the point falls inside an occupied cell.

        If *z* is provided we also verify that z is below the cell's upper
        bound (with a small safety buffer).
        """
        ix, iy = self._world_to_grid(x, y)
        if not self._in_bounds(ix, iy):
            return False
        cell = self.grid[iy, ix]
        if not cell.occupied:
            return False
        if z is not None:
            safety = 2.0  # mm
            if z > cell.max_h + safety:
                return False
        return True

    # ------------------------------------------------------------------ #
    #  Costmap                                                            #
    # ------------------------------------------------------------------ #

    def get_costmap(self) -> np.ndarray:
        """
        Return a 2D cost array.

        Occupied cells get a high cost (1.0). Free cells near obstacles get a
        gradient cost decaying with distance, enabling smoother path planning.
        Empty far-away cells are 0.
        """
        cost = np.zeros((self.ny, self.nx), dtype=np.float64)

        # Mark occupied
        occ_mask = np.zeros((self.ny, self.nx), dtype=bool)
        for iy in range(self.ny):
            for ix in range(self.nx):
                if self.grid[iy, ix].occupied:
                    occ_mask[iy, ix] = True
                    cost[iy, ix] = 1.0

        # Distance transform for gradient
        if np.any(occ_mask):
            dist = distance_transform_edt(~occ_mask) * self.cell_size_mm
            # Gradient: decay over 50 mm
            decay_dist = 50.0
            gradient = np.clip(1.0 - dist / decay_dist, 0.0, 1.0)
            # Only apply gradient to previously free cells
            cost = np.where(occ_mask, 1.0, gradient)

        return cost

    # ------------------------------------------------------------------ #
    #  3-D volume representation                                          #
    # ------------------------------------------------------------------ #

    def get_3d_volume(self) -> Dict[str, Any]:
        """
        Return a compact representation suitable for 3-D collision checking.

        Returns
        -------
        dict with keys:
            cells : list of (x_center, y_center, z_min, z_max, occupied)
        """
        cells: List[Tuple[float, float, float, float, bool]] = []
        for iy in range(self.ny):
            for ix in range(self.nx):
                cell = self.grid[iy, ix]
                cx, cy = self._grid_to_world_center(ix, iy)
                z_min = cell.min_h if cell.occupied else -float("inf")
                z_max = cell.max_h if cell.occupied else float("inf")
                cells.append((cx, cy, z_min, z_max, cell.occupied))
        return {"cells": cells}

    # ------------------------------------------------------------------ #
    #  Persistence                                                        #
    # ------------------------------------------------------------------ #

    def save(self, path: str) -> None:
        """Serialize grid to *path* (.npz)."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)

        # Flatten grid metadata into arrays
        max_h = np.empty((self.ny, self.nx), dtype=np.float64)
        min_h = np.empty((self.ny, self.nx), dtype=np.float64)
        occupied = np.empty((self.ny, self.nx), dtype=bool)
        uncertainty = np.empty((self.ny, self.nx), dtype=np.float64)

        for iy in range(self.ny):
            for ix in range(self.nx):
                c = self.grid[iy, ix]
                max_h[iy, ix] = c.max_h if c.max_h != -float("inf") else -1e9
                min_h[iy, ix] = c.min_h if c.min_h != float("inf") else 1e9
                occupied[iy, ix] = c.occupied
                uncertainty[iy, ix] = c.uncertainty

        meta = {
            "x_min": self.x_min,
            "x_max": self.x_max,
            "y_min": self.y_min,
            "y_max": self.y_max,
            "cell_size_mm": self.cell_size_mm,
        }

        np.savez_compressed(
            p,
            max_h=max_h,
            min_h=min_h,
            occupied=occupied,
            uncertainty=uncertainty,
            meta=json.dumps(meta),
        )

    @classmethod
    def load(cls, path: str) -> "ObstacleGridMap":
        """Load a grid from *path* (.npz) and return an instance."""
        data = np.load(path, allow_pickle=True)
        meta = json.loads(str(data["meta"]))

        m = cls(
            workspace_bounds=(
                meta["x_min"],
                meta["x_max"],
                meta["y_min"],
                meta["y_max"],
            ),
            cell_size_mm=meta["cell_size_mm"],
        )

        max_h = data["max_h"]
        min_h = data["min_h"]
        occupied = data["occupied"]
        uncertainty = data["uncertainty"]

        for iy in range(m.ny):
            for ix in range(m.nx):
                c = GridCell()
                c.max_h = float(max_h[iy, ix])
                c.min_h = float(min_h[iy, ix])
                c.occupied = bool(occupied[iy, ix])
                c.uncertainty = float(uncertainty[iy, ix])
                m.grid[iy, ix] = c

        return m


# ====================================================================== #
#  WorkspaceBounds helper                                                 #
# ====================================================================== #


def compute_workspace_from_camera_view(
    table_corners_3d: np.ndarray,
    K: np.ndarray,
) -> Tuple[float, float, float, float]:
    """
    Compute 2-D workspace bounds from four table corners.

    Parameters
    ----------
    table_corners_3d : (4, 3) array of (X, Y, Z) in camera/world frame (mm)
    K : 3x3 intrinsic matrix (unused for pure geometric bounds but kept for
        API compatibility with projection-based workflows)

    Returns
    -------
    (x_min, x_max, y_min, y_max) enclosing all corners (mm)
    """
    if table_corners_3d.shape != (4, 3):
        raise ValueError("table_corners_3d must be (4, 3)")

    xs = table_corners_3d[:, 0]
    ys = table_corners_3d[:, 1]

    x_min, x_max = float(np.min(xs)), float(np.max(xs))
    y_min, y_max = float(np.min(ys)), float(np.max(ys))

    return x_min, x_max, y_min, y_max


# ---------------------------------------------------------------------- #
#  Small helpers – avoid hard dependency on scipy / cv2                  #
# ---------------------------------------------------------------------- #


def cv2_dilate_binary(mask: np.ndarray, kernel: np.ndarray) -> np.ndarray:
    """Minimal binary dilation using numpy only."""
    from scipy.ndimage import binary_dilation

    return binary_dilation(mask, structure=kernel).astype(np.uint8)


def distance_transform_edt(binary: np.ndarray) -> np.ndarray:
    """Approximate Euclidean distance transform via scipy."""
    from scipy.ndimage import distance_transform_edt as _edt

    return _edt(binary)


if __name__ == "__main__":
    # Quick smoke test
    bounds = (0.0, 500.0, 0.0, 500.0)
    gmap = ObstacleGridMap(workspace_bounds=bounds, cell_size_mm=10.0)

    # Fake obstacle: a box sitting on the table
    pts = np.array(
        [
            [100, 100, 50],
            [100, 150, 50],
            [150, 150, 50],
            [150, 100, 50],
            [100, 100, 80],
            [100, 150, 80],
            [150, 150, 80],
            [150, 100, 80],
        ],
        dtype=float,
    )
    obs = ObstacleInfo(footprint_3d=pts, confidence=0.9, label="box")
    gmap.update([obs])
    gmap.dilate_footprint(dilation_mm=15.0)
    gmap.dilate_height(margin_mm=5.0)

    print(f"Occupied at (120, 120)? {gmap.is_occupied(120, 120)}")
    print(f"Occupied at (300, 300)? {gmap.is_occupied(300, 300)}")

    cost = gmap.get_costmap()
    print(f"Costmap shape: {cost.shape}")

    vol = gmap.get_3d_volume()
    print(f"Volume cells count: {len(vol['cells'])}")

    tmp = "/tmp/test_grid.npz"
    gmap.save(tmp)
    loaded = ObstacleGridMap.load(tmp)
    print(f"Loaded grid matches: {np.array_equal(loaded.get_costmap(), cost)}")

    ws = compute_workspace_from_camera_view(
        np.array([[0, 0, 0], [500, 0, 0], [500, 500, 0], [0, 500, 0]], dtype=float),
        np.eye(3),
    )
    print(f"Workspace bounds: {ws}")
