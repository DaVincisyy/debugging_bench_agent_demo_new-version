"""
Motion planning around obstacles for KPIT debugging agent.

Implements:
- AStar2DPlanner: Coarse planner on 2.5D costmap
- RRTPlanner: RRT for coarse planning
- RRTConnectPlanner: Fine planner with dual-tree growth
- CollisionChecker: Capsule vs box collision detection
- LinkCapsule: Dataclass for arm link capsule approximation
- Executor: Path execution with re-perception and replanning
"""

import heapq
import math
import random
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class LinkCapsule:
    """Capsule approximation of an arm link."""

    name: str
    p1_mm: np.ndarray  # Endpoint 1 (3D) in mm
    p2_mm: np.ndarray  # Endpoint 2 (3D) in mm
    radius_mm: float   # Capsule radius in mm

    def closest_point_on_capsule_axis(self, point: np.ndarray) -> Tuple[np.ndarray, float]:
        """Find the closest point on the capsule axis segment to *point*.

        Returns ``(closest_point, distance)`` where ``distance`` is the
        Euclidean distance from *point* to that closest point.
        """
        axis = self.p2_mm - self.p1_mm
        axis_len_sq = np.dot(axis, axis)
        if axis_len_sq < 1e-12:
            return self.p1_mm.copy(), float(np.linalg.norm(point - self.p1_mm))

        t = np.dot(point - self.p1_mm, axis) / axis_len_sq
        t = np.clip(t, 0.0, 1.0)
        closest = self.p1_mm + t * axis
        dist = float(np.linalg.norm(point - closest))
        return closest, dist

    def intersects_box(self, box_min: np.ndarray, box_max: np.ndarray) -> bool:
        """Test whether this capsule intersects an axis-aligned box.

        Uses the standard approach: find the closest point on the box to
        the capsule axis segment, then check if the distance from that
        point to the capsule axis is less than the capsule radius.
        """
        # Clamp each endpoint to the box
        def _clamp_to_box(p, bmin, bmax):
            return np.clip(p, bmin, bmax)

        # Check if either endpoint is inside the box
        ep1_inside = np.all(self.p1_mm >= box_min) and np.all(self.p1_mm <= box_max)
        ep2_inside = np.all(self.p2_mm >= box_min) and np.all(self.p2_mm <= box_max)
        if ep1_inside or ep2_inside:
            return True

        # Find closest point on box to capsule axis
        closest_on_box = _clamp_to_box(
            self.closest_point_on_capsule_axis((box_min + box_max) / 2)[0],
            box_min, box_max,
        )

        # Find closest point on capsule axis to that box point
        _, dist = self.closest_point_on_capsule_axis(closest_on_box)
        return dist <= self.radius_mm


@dataclass
class ExecutionStep:
    """A single step recorded during path execution."""

    waypoint_index: int
    position_mm: np.ndarray
    action: str          # "move", "re-perceive", "replan"
    new_obstacles: List[Dict[str, Any]] = field(default_factory=list)
    replanned_path: Optional[List[np.ndarray]] = None


# ---------------------------------------------------------------------------
# ObstacleGridMap stub – callers supply their own; minimal interface here
# ---------------------------------------------------------------------------


class ObstacleGridMap:
    """Minimal obstacle grid map interface.

    Real implementations should provide richer functionality.  This stub
    exists so that the module type-checks without external dependencies.
    """

    def __init__(self, cell_size_mm: float = 50.0):
        self.cell_size_mm = cell_size_mm
        self._obstacles: Dict[str, Dict[str, np.ndarray]] = {}

    def add_obstacle(self, oid: str, vmin: np.ndarray, vmax: np.ndarray):
        self._obstacles[oid] = {"min": vmin, "max": vmax}

    def get_nearby_obstacles(self, point: np.ndarray, radius_mm: float) -> List[Tuple[str, np.ndarray, np.ndarray]]:
        result = []
        for oid, box in self._obstacles.items():
            bmin, bmax = box["min"], box["max"]
            centre = (bmin + bmax) / 2
            if np.linalg.norm(centre - point) < radius_mm + np.linalg.norm(bmax - bmin) / 2:
                result.append((oid, bmin, bmax))
        return result

    @property
    def all_obstacles(self) -> List[Tuple[str, np.ndarray, np.ndarray]]:
        return [(oid, b["min"], b["max"]) for oid, b in self._obstacles.items()]


# ===================================================================
# 1. AStar2DPlanner
# ===================================================================


class AStar2DPlanner:
    """A* planner on a 2-D grid derived from a 2.5-D costmap."""

    def __init__(self, costmap: np.ndarray, cell_size_mm: float):
        """
        Parameters
        ----------
        costmap : 2-D numpy array  (H x W)  – higher values = more costly
        cell_size_mm : physical size of one cell in mm
        """
        self.costmap = costmap
        self.cell_size_mm = cell_size_mm
        self.height, self.width = costmap.shape

    # -- helpers -------------------------------------------------------

    def _world_to_grid(self, xy_mm: Tuple[float, float]) -> Tuple[int, int]:
        x, y = xy_mm
        col = int(round(x / self.cell_size_mm))
        row = int(round(y / self.cell_size_mm))
        return row, col

    def _grid_to_world(self, row: int, col: int) -> Tuple[float, float]:
        x = col * self.cell_size_mm
        y = row * self.cell_size_mm
        return x, y

    def _in_bounds(self, r: int, c: int) -> bool:
        return 0 <= r < self.height and 0 <= c < self.width

    @staticmethod
    def _heuristic(a: Tuple[int, int], b: Tuple[int, int]) -> float:
        """Manhattan distance."""
        return abs(a[0] - b[0]) + abs(a[1] - b[1])

    # -- public API ----------------------------------------------------

    def plan(
        self,
        start_xy: Tuple[float, float],
        goal_xy: Tuple[float, float],
    ) -> Optional[List[Tuple[float, float]]]:
        """Return a path as list of (x_mm, y_mm) or None."""
        start = self._world_to_grid(start_xy)
        goal = self._world_to_grid(goal_xy)

        if not (self._in_bounds(*start) and self._in_bounds(*goal)):
            return None

        # A* open set (priority queue)
        # Each entry: (f_score, counter, node)
        counter = 0
        open_set: list = [(self._heuristic(start, goal), counter, start)]
        came_from: Dict[Tuple[int, int], Tuple[int, int]] = {}
        g_score: Dict[Tuple[int, int], float] = {start: 0.0}
        closed: set = set()

        # 8-connected neighbours
        neighbours = [(-1, -1), (-1, 0), (-1, 1),
                      (0, -1),           (0, 1),
                      (1, -1),  (1, 0),  (1, 1)]

        while open_set:
            _, _, current = heapq.heappop(open_set)

            if current == goal:
                # Reconstruct path
                path_grid: List[Tuple[int, int]] = [current]
                while current in came_from:
                    current = came_from[current]
                    path_grid.append(current)
                path_grid.reverse()
                return [self._grid_to_world(r, c) for r, c in path_grid]

            if current in closed:
                continue
            closed.add(current)

            cr, cc = current
            for dr, dc in neighbours:
                nr, nc = cr + dr, cc + dc
                if not self._in_bounds(nr, nc):
                    continue
                neighbour = (nr, nc)
                if neighbour in closed:
                    continue

                # Cost = Euclidean step cost + obstacle cost from costmap
                step_cost = math.sqrt(dr * dr + dc * dc)
                obs_cost = float(self.costmap[nr, nc])
                tentative_g = g_score[current] + step_cost + obs_cost

                if tentative_g < g_score.get(neighbour, float("inf")):
                    came_from[neighbour] = current
                    g_score[neighbour] = tentative_g
                    f = tentative_g + self._heuristic(neighbour, goal)
                    counter += 1
                    heapq.heappush(open_set, (f, counter, neighbour))

        return None  # No path found


# ===================================================================
# 2. RRTPlanner
# ===================================================================


class RRTPlanner:
    """Rapidly-exploring Random Tree for coarse planning in 2-D."""

    def __init__(
        self,
        workspace_bounds: Tuple[Tuple[float, float], Tuple[float, float]],
        obstacles: Optional[List[Tuple[np.ndarray, np.ndarray]]] = None,
    ):
        """
        Parameters
        ----------
        workspace_bounds : ((xmin, xmax), (ymin, ymax))
        obstacles : list of (box_min_2d, box_max_2d) tuples
        """
        self.bounds = workspace_bounds
        self.obstacles = obstacles if obstacles is not None else []

    # -- helpers -------------------------------------------------------

    @staticmethod
    def _sample_random(bounds: Tuple[Tuple[float, float], Tuple[float, float]]) -> np.ndarray:
        (xmin, xmax), (ymin, ymax) = bounds
        return np.array([
            random.uniform(xmin, xmax),
            random.uniform(ymin, ymax),
        ])

    @staticmethod
    def _nearest(nodes: List[np.ndarray], point: np.ndarray) -> int:
        dists = [np.linalg.norm(n - point) for n in nodes]
        return int(np.argmin(dists))

    @staticmethod
    def _steer(
        frm: np.ndarray, to: np.ndarray, max_step: float
    ) -> np.ndarray:
        vec = to - frm
        dist = float(np.linalg.norm(vec))
        if dist <= max_step:
            return to.copy()
        return frm + vec / dist * max_step

    def _collision_free(self, a: np.ndarray, b: np.ndarray) -> bool:
        """Check that the line segment a-b does not intersect any obstacle."""
        # Sample points along the segment
        steps = max(1, int(np.linalg.norm(b - a) / 5.0))
        for i in range(steps + 1):
            t = i / steps
            pt = a + t * (b - a)
            for omin, omax in self.obstacles:
                if np.all(pt >= omin) and np.all(pt <= omax):
                    return False
        return True

    @staticmethod
    def _smooth_path(
        path: List[np.ndarray], collision_check: Callable[[np.ndarray, np.ndarray], bool]
    ) -> List[np.ndarray]:
        """Shortcut smoothing – try to skip intermediate waypoints."""
        if len(path) < 3:
            return path

        smoothed = [path[0]]
        i = 0
        while i < len(path) - 1:
            j = len(path) - 1
            while j > i + 1:
                if collision_check(path[i], path[j]):
                    break
                j -= 1
            i = j
            smoothed.append(path[i])
        return smoothed

    # -- public API ----------------------------------------------------

    def plan(
        self,
        start_xy: Tuple[float, float],
        goal_xy: Tuple[float, float],
        max_iter: int = 1000,
        goal_bias: float = 0.1,
        step_size: float = 50.0,
    ) -> Optional[List[Tuple[float, float]]]:
        start = np.array(start_xy, dtype=float)
        goal = np.array(goal_xy, dtype=float)

        nodes: List[np.ndarray] = [start]
        parent: Dict[int, int] = {}
        goal_node: Optional[int] = None

        for _ in range(max_iter):
            # Sample
            if random.random() < goal_bias:
                rand_pt = goal
            else:
                rand_pt = self._sample_random(self.bounds)

            nearest_idx = self._nearest(nodes, rand_pt)
            new_pt = self._steer(nodes[nearest_idx], rand_pt, step_size)

            if not self._collision_free(nodes[nearest_idx], new_pt):
                continue

            new_idx = len(nodes)
            nodes.append(new_pt)
            parent[new_idx] = nearest_idx

            if np.linalg.norm(new_pt - goal) < step_size:
                # Connect to goal
                if self._collision_free(new_pt, goal):
                    goal_idx = len(nodes)
                    nodes.append(goal)
                    parent[goal_idx] = new_idx
                    goal_node = goal_idx
                    break

        if goal_node is None:
            return None

        # Reconstruct
        path: List[np.ndarray] = []
        idx: Optional[int] = goal_node
        while idx is not None:
            path.append(nodes[idx])
            idx = parent.get(idx, None)  # type: ignore[assignment]
        path.reverse()

        smoothed = self._smooth_path(path, self._collision_free)
        return [(float(p[0]), float(p[1])) for p in smoothed]


# ===================================================================
# 3. RRTConnectPlanner
# ===================================================================


class RRTConnectPlanner:
    """Dual-tree RRT-Connect for joint-space planning."""

    def __init__(
        self,
        joint_limits: List[Tuple[float, float]],
        collision_checker: Callable[[np.ndarray], bool],
    ):
        """
        Parameters
        ----------
        joint_limits : list of (low, high) per joint
        collision_checker : callable(joint_angles) -> True if collision-free
        """
        self.joint_limits = joint_limits
        self.collision_checker = collision_checker
        self.dim = len(joint_limits)

    # -- helpers -------------------------------------------------------

    def _sample_config(self) -> np.ndarray:
        return np.array([
            random.uniform(lo, hi) for lo, hi in self.joint_limits
        ])

    def _nearest(self, tree_nodes: List[np.ndarray], config: np.ndarray) -> int:
        dists = [np.linalg.norm(n - config) for n in tree_nodes]
        return int(np.argmin(dists))

    def _steer(
        self, frm: np.ndarray, to: np.ndarray, step_size: float
    ) -> np.ndarray:
        vec = to - frm
        dist = float(np.linalg.norm(vec))
        if dist <= step_size:
            return to.copy()
        return frm + vec / dist * step_size

    def _extend_towards(
        self,
        nodes: List[np.ndarray],
        parent: Dict[int, int],
        target: np.ndarray,
        step_size: float,
    ) -> int:
        """Extend tree by one step towards *target*.  Return new node index or -1."""
        nearest_idx = self._nearest(nodes, target)
        new_cfg = self._steer(nodes[nearest_idx], target, step_size)

        if not self.collision_checker(new_cfg):
            return -1

        new_idx = len(nodes)
        nodes.append(new_cfg)
        parent[new_idx] = nearest_idx
        return new_idx

    @staticmethod
    def _shortcut_smooth(
        path: List[np.ndarray],
        checker: Callable[[np.ndarray], bool],
    ) -> List[np.ndarray]:
        """Aggressive shortcut smoothing."""
        if len(path) < 3:
            return path

        changed = True
        while changed:
            changed = False
            i = 0
            while i < len(path) - 2:
                j = len(path) - 1
                while j > i + 1:
                    # Check straight line between i and j
                    seg_len = np.linalg.norm(path[j] - path[i])
                    steps = max(1, int(seg_len / 0.02))
                    ok = True
                    for s in range(steps + 1):
                        t = s / steps
                        cfg = path[i] + t * (path[j] - path[i])
                        if not checker(cfg):
                            ok = False
                            break
                    if ok:
                        path = path[: i + 1] + path[j:]
                        changed = True
                        break
                    j -= 1
                i += 1
        return path

    # -- public API ----------------------------------------------------

    def plan(
        self,
        start_joints: np.ndarray,
        goal_joints: np.ndarray,
        max_iter: int = 2000,
        step_size: float = 0.05,
    ) -> Optional[List[np.ndarray]]:
        if not self.collision_checker(start_joints) or not self.collision_checker(goal_joints):
            return None

        # Trees
        tree_a_nodes: List[np.ndarray] = [start_joints.copy()]
        tree_a_parent: Dict[int, int] = {}
        tree_b_nodes: List[np.ndarray] = [goal_joints.copy()]
        tree_b_parent: Dict[int, int] = {}

        connect_a_to_b: Optional[Tuple[int, int]] = None  # (idx_in_a, idx_in_b)

        for iteration in range(max_iter):
            # Extend tree A
            rand_cfg = self._sample_config()
            new_a = self._extend_towards(tree_a_nodes, tree_a_parent, rand_cfg, step_size)
            if new_a == -1:
                continue

            # Try to connect tree B to the new node in A
            new_b = self._extend_towards(tree_b_nodes, tree_b_parent, tree_a_nodes[new_a], step_size)
            if new_b != -1:
                # Check if trees are connected
                if np.linalg.norm(tree_b_nodes[new_b] - tree_a_nodes[new_a]) < step_size:
                    connect_a_to_b = (new_a, new_b)
                    break

            # Swap trees so we always extend the smaller one (optional heuristic)
            if len(tree_a_nodes) > len(tree_b_nodes):
                tree_a_nodes, tree_b_nodes = tree_b_nodes, tree_a_nodes
                tree_a_parent, tree_b_parent = tree_b_parent, tree_a_parent

        if connect_a_to_b is None:
            return None

        idx_a, idx_b = connect_a_to_b

        # Reconstruct path from start to connection point
        path: List[np.ndarray] = []
        idx: int = idx_a
        while True:
            path.append(tree_a_nodes[idx])
            if idx == 0:
                break
            idx = tree_a_parent[idx]
        path.reverse()

        # Reconstruct from goal to connection point
        path_b: List[np.ndarray] = []
        idx = idx_b
        while True:
            path_b.append(tree_b_nodes[idx])
            if idx == 0:
                break
            idx = tree_b_parent[idx]

        path.extend(path_b)

        # Smooth
        smoothed = self._shortcut_smooth(path, self.collision_checker)
        return smoothed


# ===================================================================
# 4. CollisionChecker
# ===================================================================


class CollisionChecker:
    """Detect collisions between arm-link capsules and obstacle boxes."""

    def __init__(
        self,
        arm_links: List[LinkCapsule],
        obstacle_map: ObstacleGridMap,
    ):
        self.arm_links = arm_links
        self.obstacle_map = obstacle_map

    def check_collision(
        self, joint_angles_or_pose: Any
    ) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """
        Parameters
        ----------
        joint_angles_or_pose : joint configuration or pose understood by
                               the forward-kinematics embedded in each
                               ``LinkCapsule``.  For simplicity this demo
                               assumes the capsules already carry their
                               **world-frame** endpoints at call time.

        Returns
        -------
        (is_collision, contact_info_dict_or_None)
        """
        for link in self.arm_links:
            nearby = self.obstacle_map.get_nearby_obstacles(
                (link.p1_mm + link.p2_mm) / 2,
                link.radius_mm + 200.0,  # search radius with margin
            )
            for oid, bmin, bmax in nearby:
                if link.intersects_box(bmin, bmax):
                    # Compute penetration depth approximation
                    centre = (bmin + bmax) / 2
                    closest, dist = link.closest_point_on_capsule_axis(centre)
                    penetration = link.radius_mm - dist
                    if penetration < 0:
                        penetration = 0.0
                    return True, {
                        "link_name": link.name,
                        "obstacle_id": oid,
                        "penetration_depth": penetration,
                    }
        return False, None


# ===================================================================
# 5. Executor
# ===================================================================


class Executor:
    """Execute a path while periodically re-perceiving and replanning."""

    def __init__(
        self,
        config: Dict[str, Any],
        collision_checker: CollisionChecker,
        perception_pipeline: Any,
    ):
        self.config = config
        self.collision_checker = collision_checker
        self.perception_pipeline = perception_pipeline
        self.step_size_mm: float = config.get("step_size_mm", 10.0)
        self.re_perception_interval_mm: float = config.get(
            "re_perception_interval_mm", 100.0
        )

    def execute_path(
        self,
        path: List[np.ndarray],
        re_perception_interval_mm: Optional[float] = None,
    ) -> List[ExecutionStep]:
        if re_perception_interval_mm is not None:
            self.re_perception_interval_mm = re_perception_interval_mm

        steps: List[ExecutionStep] = []
        accumulated_dist = 0.0
        current_pos = path[0].copy()

        for wp_idx in range(1, len(path)):
            target = path[wp_idx]
            direction = target - current_pos
            remaining = float(np.linalg.norm(direction))

            while remaining > 1e-6:
                step_vec = direction / remaining * min(self.step_size_mm, remaining)
                next_pos = current_pos + step_vec
                accumulated_dist += float(np.linalg.norm(step_vec))

                steps.append(ExecutionStep(
                    waypoint_index=wp_idx,
                    position_mm=next_pos.copy(),
                    action="move",
                ))

                # Re-perception trigger
                if accumulated_dist >= self.re_perception_interval_mm:
                    accumulated_dist = 0.0
                    new_obstacles = self.perception_pipeline.detect_new_obstacles()
                    if new_obstacles:
                        # Replan from current position to goal
                        replanned = self.perception_pipeline.replan(
                            start=current_pos, goal=path[-1]
                        )
                        if replanned:
                            steps.append(ExecutionStep(
                                waypoint_index=wp_idx,
                                position_mm=current_pos.copy(),
                                action="replan",
                                new_obstacles=new_obstacles,
                                replanned_path=replanned,
                            ))
                            # Continue along the new path (truncated for simplicity)
                            path = replanned
                            wp_idx = 0  # restart outer loop on new path
                            break

                    steps.append(ExecutionStep(
                        waypoint_index=wp_idx,
                        position_mm=current_pos.copy(),
                        action="re-perceive",
                        new_obstacles=new_obstacles,
                    ))

                current_pos = next_pos
                direction = target - current_pos
                remaining = float(np.linalg.norm(direction))

            if wp_idx == 0:
                break  # replan took over

        return steps
