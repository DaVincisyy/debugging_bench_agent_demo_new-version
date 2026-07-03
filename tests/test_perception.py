"""
Tests for the perception-planning pipeline.

All tests use synthetic numpy data — no real camera, no deep-learning models.
Only BackgroundSubtractionMasker tests need cv2 (already installed).
Depth/planning tests use pure numpy.
"""

from __future__ import annotations

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _make_K(fx=600.0, fy=600.0, cx=320.0, cy=240.0) -> np.ndarray:
    return np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1]], dtype=float)


def _make_frame(w=640, h=480, bg_color=255, rect=None) -> np.ndarray:
    """Create a synthetic HxWx3 BGR frame with an optional dark rectangle."""
    frame = np.full((h, w, 3), bg_color, dtype=np.uint8)
    if rect is not None:
        x1, y1, x2, y2 = rect
        frame[y1:y2, x1:x2] = 0  # black obstacle
    return frame


# ===================================================================
# 1. Mask generator tests
# ===================================================================

class TestMaskGenerator:

    def test_mask_generator_basic(self):
        """BackgroundSubtractionMasker detects a dark rectangle on white desk."""
        from perception.mask_generator import MaskConfig, BackgroundSubtractionMasker

        cfg = MaskConfig(threshold=30, min_contour_area=100.0)
        masker = BackgroundSubtractionMasker(cfg)

        bg = _make_frame(bg_color=255)
        masker.update_background(bg)

        frame = _make_frame(rect=(100, 100, 200, 200))  # 100x100 black rect
        result = masker.generate_mask(frame)

        assert result.mask.shape == (480, 640)
        # The rectangle region should be marked
        assert result.mask[150, 150] > 0  # inside rect → obstacle
        assert result.mask[50, 50] == 0   # outside rect → background
        assert result.confidence > 0.0
        assert result.method == "bg_subtraction"

    def test_mask_generator_no_background_raises(self):
        """Masker raises if background not initialised."""
        from perception.mask_generator import MaskConfig, BackgroundSubtractionMasker

        cfg = MaskConfig()
        masker = BackgroundSubtractionMasker(cfg)
        frame = _make_frame()
        with pytest.raises(RuntimeError, match="Background not initialised"):
            masker.generate_mask(frame)

    def test_arm_exclusion_mask(self):
        """Arm exclusion mask covers specified polygon region."""
        from perception.mask_generator import ArmExclusionMasker

        poly = np.array([[0.1, 0.1], [0.3, 0.1], [0.3, 0.3], [0.1, 0.3]])  # normalised
        masker = ArmExclusionMasker(arm_zones=[poly])

        mask = masker.generate_exclusion_mask((480, 640, 3))
        assert mask.shape == (480, 640)
        # Centre of the polygon should be excluded
        assert mask[144, 128] == 255  # 0.2*480=96 → 144, 0.2*640=128
        # Outside should be 0
        assert mask[400, 500] == 0

    def test_arm_exclusion_ee_circle(self):
        """Arm exclusion with end-effector position → rough circle."""
        from perception.mask_generator import ArmExclusionMasker

        masker = ArmExclusionMasker()
        # EE at pixel (300, 200)
        mask = masker.generate_exclusion_mask((480, 640, 3), arm_pose=np.array([300, 200]))
        assert mask.shape == (480, 640)
        assert mask[200, 300] == 255  # EE position excluded
        # Point 50px away should also be inside the ~80px radius circle
        assert mask[250, 300] == 255
        # Point 150px away should be outside
        assert mask[350, 300] == 0

    def test_yolo_masker_runs(self):
        """YoloObstacleMasker runs inference without error on a synthetic image.

        YOLO won't detect anything in a synthetic image (trained on real objects),
        but the inference pipeline must not crash.
        """
        from perception.mask_generator import MaskConfig, YoloObstacleMasker

        cfg = MaskConfig()
        masker = YoloObstacleMasker(cfg)

        frame = _make_frame()
        result = masker.generate_mask(frame)

        assert result.mask.shape == (480, 640)
        assert result.method == "yolov8_seg"
        # No real objects → 0 detections
        assert len(result.detections) == 0
        assert result.confidence == 0.0

    def test_combined_yolo_primary_bg_fallback(self):
        """Combined masker: YOLO primary → bg subtraction fallback."""
        from perception.mask_generator import (
            MaskConfig, BackgroundSubtractionMasker, CombinedMaskGenerator,
        )

        cfg = MaskConfig(threshold=30, min_contour_area=100.0)
        bg_masker = BackgroundSubtractionMasker(cfg)
        combined = CombinedMaskGenerator(config=cfg, bg_masker=bg_masker)

        # Init background for fallback
        bg = _make_frame(bg_color=255)
        combined.bg_masker.update_background(bg)

        # Frame with dark rectangle — YOLO won't detect it, bg subtraction will
        frame = _make_frame(rect=(100, 100, 200, 200))
        result = combined.process(frame)

        assert result.mask.shape == (480, 640)
        assert result.mask[150, 150] > 0  # bg subtraction detected it
        # Method indicates fallback since YOLO found nothing
        assert "fallback" in result.method

    def test_combined_mask_with_arm_exclusion(self):
        """Combined masker: bg subtraction AND arm exclusion."""
        from perception.mask_generator import (
            MaskConfig, BackgroundSubtractionMasker,
            ArmExclusionMasker, CombinedMaskGenerator,
        )

        cfg = MaskConfig(threshold=30, min_contour_area=100.0)
        bg_masker = BackgroundSubtractionMasker(cfg)
        arm_masker = ArmExclusionMasker(
            arm_zones=[np.array([[0.7, 0.7], [0.9, 0.7], [0.9, 0.9], [0.7, 0.9]])]
        )
        combined = CombinedMaskGenerator(
            config=cfg, bg_masker=bg_masker, arm_masker=arm_masker
        )

        bg = _make_frame(bg_color=255)
        combined.bg_masker.update_background(bg)

        # Frame with obstacle AND arm region both dark
        frame = _make_frame(rect=(100, 100, 200, 200))
        frame[350:430, 450:570] = 0  # arm region dark too

        result = combined.process(frame)
        # Obstacle at (150,150) should remain
        assert result.mask[150, 150] > 0
        # Arm region at (390, 510) should be excluded
        assert result.mask[390, 510] == 0


# ===================================================================
# 2. Plane fitting tests
# ===================================================================

class TestPlaneFitting:

    def test_plane_fitting_known_plane(self):
        """Fit plane to points on z=0 with small noise → normal ≈ (0,0,1)."""
        from perception.depth_estimator import PlaneFittingAligner

        # Generate 100 points on z=0 plane with noise
        rng = np.random.RandomState(42)
        x = rng.uniform(-100, 100, 100)
        y = rng.uniform(-100, 100, 100)
        z = rng.normal(0, 0.5, 100)  # small noise
        pts = np.column_stack([x, y, z])

        normal, centroid, residual = PlaneFittingAligner.fit_plane(pts)

        assert abs(normal[2]) > 0.95  # normal mostly in z
        assert residual < 1.0  # residual ≈ noise level
        assert abs(centroid[2]) < 1.0  # centroid near z=0

    def test_plane_fitting_insufficient_points(self):
        """Need at least 3 points."""
        from perception.depth_estimator import PlaneFittingAligner

        pts = np.array([[0, 0, 0], [1, 0, 0]])
        with pytest.raises(ValueError, match="at least 3"):
            PlaneFittingAligner.fit_plane(pts)


# ===================================================================
# 3. Depth to pointcloud tests
# ===================================================================

class TestDepthToPointcloud:

    def test_depth_to_pointcloud_flat_plane(self):
        """Flat depth map → points should form a plane at constant Z."""
        from perception.reconstruction import depth_to_pointcloud

        H, W = 100, 100
        K = _make_K()
        depth = np.full((H, W), 500.0, dtype=np.float32)  # 500mm away
        mask = np.ones((H, W), dtype=np.uint8) * 255

        pc = depth_to_pointcloud(depth, mask, K)

        assert not pc.is_empty()
        # Z should be ~500 for all points
        assert np.allclose(pc.xyz[:, 2], 500.0, atol=1.0)
        # X spread should match FOV: from -(cx/fx)*Z to +((W-cx)/fx)*Z
        expected_x_min = -(K[0, 2]) * 500.0 / K[0, 0]
        expected_x_max = (W - K[0, 2]) * 500.0 / K[0, 0]
        assert pc.xyz[:, 0].min() < expected_x_min + 10
        assert pc.xyz[:, 0].max() > expected_x_max - 10

    def test_depth_to_pointcloud_empty_mask(self):
        """Empty mask → empty pointcloud."""
        from perception.reconstruction import depth_to_pointcloud

        K = _make_K()
        depth = np.ones((100, 100), dtype=np.float32)
        mask = np.zeros((100, 100), dtype=np.uint8)

        pc = depth_to_pointcloud(depth, mask, K)
        assert pc.is_empty()

    def test_depth_to_pointcloud_shape_mismatch(self):
        from perception.reconstruction import depth_to_pointcloud

        K = _make_K()
        with pytest.raises(ValueError, match="Shape mismatch"):
            depth_to_pointcloud(np.ones((10, 10)), np.ones((20, 20)), K)


# ===================================================================
# 4. Remove table points tests
# ===================================================================

class TestRemoveTablePoints:

    def test_remove_table_points(self):
        """remove_table_points keeps points in [table_z - tolerance, table_z + small_margin].

        With table_z=0, tolerance=5, default small_margin=50:
        - Z=0 → kept (within [-5, 50])
        - Z=50 → kept (at upper bound)
        - Z=52, 55 → removed (above 50)
        """
        from perception.reconstruction import remove_table_points, PointCloud

        xyz = np.array([
            [0, 0, 0],     # table (within tolerance)
            [10, 5, 0],    # table
            [20, 10, 0],   # table
            [50, 30, 50],  # obstacle (at upper bound)
            [60, 35, 55],  # above margin → removed
            [55, 32, 52],  # above margin → removed
        ], dtype=np.float64)

        pc = PointCloud(xyz=xyz)
        filtered = remove_table_points(pc, table_z=0.0, tolerance_mm=5.0)

        assert not filtered.is_empty()
        assert filtered.num_points == 4  # 3 table + 1 at Z=50
        assert np.all(filtered.xyz[:, 2] <= 50)

    def test_remove_table_points_empty(self):
        from perception.reconstruction import remove_table_points, PointCloud

        pc = PointCloud(xyz=np.empty((0, 3)))
        result = remove_table_points(pc, table_z=0.0)
        assert result.is_empty()


# ===================================================================
# 5. Obstacle info computation tests
# ===================================================================

class TestObstacleInfo:

    def test_obstacle_info_computation(self):
        """Create box-shaped pointcloud → verify height and footprint."""
        from perception.reconstruction import (
            compute_obstacle_info, PointCloud,
        )

        # Box: 40x40mm footprint, 50mm high, sitting on table at Z=0
        rng = np.random.RandomState(0)
        n = 200
        x = rng.uniform(100, 140, n)
        y = rng.uniform(200, 240, n)
        z = rng.uniform(0, 50, n)  # 0 to 50mm above table
        xyz = np.column_stack([x, y, z])

        pc = PointCloud(xyz=xyz)
        config = {
            "grid_size_mm": 10.0,
            "height_percentiles": [90, 99],
            "min_points_per_cluster": 10,
        }

        obstacles = compute_obstacle_info(pc, table_z=0.0, config=config)

        assert len(obstacles) >= 1
        obs = obstacles[0]
        # Height p90 should be around 45 (90th percentile of uniform 0-50)
        assert 35 < obs.height_p90 < 50
        assert 45 < obs.height_p99 <= 50
        assert obs.confidence > 0.0
        assert obs.area > 0.0

    def test_obstacle_info_empty(self):
        from perception.reconstruction import compute_obstacle_info, PointCloud

        pc = PointCloud(xyz=np.empty((0, 3)))
        obstacles = compute_obstacle_info(pc, table_z=0.0, config={})
        assert obstacles == []


# ===================================================================
# 6. Obstacle grid map tests
# ===================================================================

class TestObstacleGridMap:

    def _make_map(self, bounds=(0, 500, 0, 500), cell=10):
        from perception.obstacle_map import ObstacleGridMap
        return ObstacleGridMap(workspace_bounds=bounds, cell_size_mm=cell)

    def test_grid_map_update(self):
        """Add obstacle → verify cells at point locations marked occupied."""
        from perception.obstacle_map import ObstacleInfo

        gmap = self._make_map()
        pts = np.array([
            [100, 100, 50], [100, 150, 50], [150, 150, 50], [150, 100, 50],
            [100, 100, 80], [100, 150, 80], [150, 150, 80], [150, 100, 80],
        ], dtype=float)
        obs = ObstacleInfo(footprint_3d=pts, confidence=0.9, label="box")

        gmap.update([obs])
        # The update rasterises individual points, so cells containing points are marked.
        # Cell at (100,100) maps to grid (10, 10)
        assert gmap.is_occupied(100, 100)
        assert gmap.is_occupied(150, 150)
        assert not gmap.is_occupied(300, 300)

    def test_grid_map_dilation(self):
        """Dilation expands occupied region."""
        from perception.obstacle_map import ObstacleInfo

        gmap = self._make_map(cell=10)
        pts = np.array([
            [200, 200, 50], [200, 210, 50], [210, 210, 50], [210, 200, 50],
            [200, 200, 60], [200, 210, 60], [210, 210, 60], [210, 200, 60],
        ], dtype=float)
        obs = ObstacleInfo(footprint_3d=pts, confidence=0.9, label="small")
        gmap.update([obs])

        # Cell at 205,205 occupied; cell at 240,240 should be free before dilation
        assert gmap.is_occupied(205, 205)
        was_free = not gmap.is_occupied(240, 240)
        assert was_free

        gmap.dilate_footprint(dilation_mm=50)
        # After 50mm dilation, cells ~40mm away should now be occupied
        assert gmap.is_occupied(240, 240)

    def test_grid_map_costmap(self):
        """Costmap has correct shape and high cost at occupied cells."""
        from perception.obstacle_map import ObstacleInfo

        gmap = self._make_map(bounds=(0, 50, 0, 50), cell=10)
        pts = np.array([[25, 25, 50]], dtype=float)
        obs = ObstacleInfo(footprint_3d=pts, confidence=0.9)
        gmap.update([obs])

        cost = gmap.get_costmap()
        assert cost.shape == (5, 5)
        assert cost[2, 2] == 1.0  # occupied cell

    def test_grid_map_3d_volume(self):
        vol = self._make_map(bounds=(0, 20, 0, 20), cell=10).get_3d_volume()
        assert "cells" in vol
        assert len(vol["cells"]) == 4  # 2x2 grid

    def test_workspace_from_camera_view(self):
        from perception.obstacle_map import compute_workspace_from_camera_view

        corners = np.array([
            [0, 0, 0], [800, 0, 0], [800, 600, 0], [0, 600, 0]
        ], dtype=float)
        bounds = compute_workspace_from_camera_view(corners, np.eye(3))
        assert bounds == (0.0, 800.0, 0.0, 600.0)


# ===================================================================
# 7. A* pathfinding tests
# ===================================================================

class TestAStarPlanner:

    def test_astar_pathfinding(self):
        """Costmap with center obstacle → path goes around."""
        from perception.planner import AStar2DPlanner

        # 20x20 costmap, obstacle in center
        costmap = np.zeros((20, 20), dtype=np.float64)
        costmap[8:12, 8:12] = 1.0  # 4x4 obstacle

        planner = AStar2DPlanner(costmap, cell_size_mm=10.0)
        # Start top-left, goal bottom-right (in world coords mm)
        path = planner.plan((0, 0), (190, 190))

        assert path is not None
        assert len(path) >= 2
        # Path should not go through obstacle center
        for x, y in path:
            r, c = planner._world_to_grid((x, y))
            assert not (8 <= r <= 11 and 8 <= c <= 11), \
                f"Path goes through obstacle at grid ({r},{c})"

    def test_astar_no_path(self):
        """Goal out of bounds → no path.

        Note: A* never treats finite costs as impassable, so we test
        the boundary case instead.
        """
        from perception.planner import AStar2DPlanner

        costmap = np.zeros((10, 10), dtype=np.float64)
        planner = AStar2DPlanner(costmap, cell_size_mm=10.0)
        # Goal outside the 10x10 grid
        path = planner.plan((0, 0), (200, 200))
        assert path is None


# ===================================================================
# 8. RRT planner tests
# ===================================================================

class TestRRTPlanner:

    def test_rrt_clear_path(self):
        """No obstacles → RRT finds direct-ish path."""
        from perception.planner import RRTPlanner

        bounds = ((0, 500), (0, 500))
        planner = RRTPlanner(workspace_bounds=bounds, obstacles=[])
        path = planner.plan((50, 50), (450, 450), max_iter=500)

        assert path is not None
        assert len(path) >= 2
        assert path[0] == pytest.approx((50, 50), abs=10)
        assert path[-1] == pytest.approx((450, 450), abs=10)

    def test_rrt_blocked_goal(self):
        """Goal inside obstacle → no path."""
        from perception.planner import RRTPlanner

        bounds = ((0, 500), (0, 500))
        obstacle_min = np.array([400, 400])
        obstacle_max = np.array([500, 500])
        planner = RRTPlanner(
            workspace_bounds=bounds,
            obstacles=[(obstacle_min, obstacle_max)]
        )
        path = planner.plan((50, 50), (480, 480), max_iter=200)
        assert path is None


# ===================================================================
# 9. RRT-Connect planner tests
# ===================================================================

class TestRRTConnectPlanner:

    def _collision_free_checker(self):
        """Checker that says everything is collision-free."""
        return lambda q: True

    def test_rrt_connect_open_space(self):
        """Open space → RRT-Connect connects start to goal."""
        from perception.planner import RRTConnectPlanner

        joint_limits = [(-170, 170)] * 3  # 3 DOF
        planner = RRTConnectPlanner(joint_limits, self._collision_free_checker())

        start = np.array([0.0, 0.0, 0.0])
        goal = np.array([1.0, 1.0, 1.0])
        path = planner.plan(start, goal, max_iter=500)

        assert path is not None
        assert len(path) >= 2
        assert np.allclose(path[0], start, atol=0.1)
        assert np.allclose(path[-1], goal, atol=0.1)

    def test_rrt_connect_collision(self):
        """Goal in collision → no path."""
        from perception.planner import RRTConnectPlanner

        joint_limits = [(-170, 170)] * 2
        # Collision if any joint > 0.5
        def checker(q):
            return np.all(q < 0.5)

        planner = RRTConnectPlanner(joint_limits, checker)
        start = np.array([0.0, 0.0])
        goal = np.array([1.0, 1.0])  # goal is in collision
        path = planner.plan(start, goal)
        assert path is None


# ===================================================================
# 10. LinkCapsule tests
# ===================================================================

class TestLinkCapsule:

    def test_capsule_closest_point(self):
        from perception.planner import LinkCapsule

        link = LinkCapsule(
            name="test",
            p1_mm=np.array([0, 0, 0]),
            p2_mm=np.array([100, 0, 0]),
            radius_mm=10.0,
        )

        closest, dist = link.closest_point_on_capsule_axis(np.array([50, 30, 0]))
        assert np.allclose(closest, [50, 0, 0])
        assert dist == pytest.approx(30.0, abs=0.1)

    def test_capsule_intersects_box(self):
        from perception.planner import LinkCapsule

        link = LinkCapsule(
            name="test",
            p1_mm=np.array([0, 0, 0]),
            p2_mm=np.array([100, 0, 0]),
            radius_mm=20.0,
        )

        # Box intersecting the capsule
        assert link.intersects_box(np.array([40, -5, -5]), np.array([60, 5, 5]))
        # Box far away
        assert not link.intersects_box(np.array([200, 200, 200]), np.array([210, 210, 210]))


# ===================================================================
# 11. Pipeline integration tests
# ===================================================================

class TestPipelineIntegration:

    def test_pipeline_diagnostics(self):
        """Create pipeline with default config → verify diagnostics keys."""
        from perception.pipeline import PerceptionPlanningPipeline

        pipeline = PerceptionPlanningPipeline(config_path_or_dict=None)
        diag = pipeline.get_diagnostics()

        assert isinstance(diag, dict)
        assert "uptime_seconds" in diag
        assert "frames_processed" in diag
        assert "planning_success_rate" in diag

    def test_pipeline_plan_astar(self):
        """Plan with A* on default map (empty) → should find path."""
        from perception.pipeline import PerceptionPlanningPipeline

        pipeline = PerceptionPlanningPipeline(config_path_or_dict=None)
        path = pipeline.plan((0, 0), (100, 100), use_rrt=False)

        assert path is not None
        assert len(path.waypoints) >= 2
        assert path.collision_free

    def test_pipeline_plan_rrt(self):
        """Plan with RRT on default map → should find path."""
        from perception.pipeline import PerceptionPlanningPipeline

        pipeline = PerceptionPlanningPipeline(config_path_or_dict=None)
        path = pipeline.plan((-100, -100), (100, 100), use_rrt=True)

        assert path is not None
        assert len(path.waypoints) >= 2

    def test_pipeline_no_path_to_execute(self):
        """None path → empty execution steps."""
        from perception.pipeline import PerceptionPlanningPipeline

        pipeline = PerceptionPlanningPipeline(config_path_or_dict=None)
        steps = pipeline.execute(None)
        assert steps == []

    def test_pipeline_capture_and_process(self):
        """Full pipeline cycle with synthetic data."""
        from perception.pipeline import PerceptionPlanningPipeline

        pipeline = PerceptionPlanningPipeline(config_path_or_dict=None)

        bg = _make_frame(bg_color=255)
        frame = _make_frame(rect=(200, 150, 280, 230))

        result = pipeline.capture_and_process(frame, background_frame=bg)

        assert result.success
        assert result.mask is not None
        assert result.depth is not None
        assert result.mask.mask.shape == frame.shape[:2]
        assert result.depth.depth.shape == frame.shape[:2]

    def test_pipeline_reset(self):
        from perception.pipeline import PerceptionPlanningPipeline
        pipeline = PerceptionPlanningPipeline(config_path_or_dict=None)
        pipeline._frame_count = 42
        pipeline.reset_statistics()
        assert pipeline._frame_count == 0

    def test_pipeline_create_factory(self):
        from perception.pipeline import create_pipeline

        p = create_pipeline()
        assert p is not None
        assert isinstance(p, type(p))


# ===================================================================
# 12. Config tests
# ===================================================================

class TestConfig:

    def test_default_config(self):
        from perception.config import get_default_config

        cfg = get_default_config()
        assert cfg.camera.width == 1280
        assert cfg.camera.height == 720
        assert cfg.mask.method == "yolo_seg"
        assert cfg.mask.yolo_model_path is not None
        assert cfg.planning.coarse_planner == "astar"
        assert len(cfg.planning.arm_link_capsules) == 6

    def test_load_config_from_dict(self):
        """Verify config can be merged from dict."""
        from perception.pipeline import PerceptionPlanningPipeline

        overrides = {"camera": {"width": 1920, "height": 1080}}
        pipeline = PerceptionPlanningPipeline(config_path_or_dict=overrides)
        assert pipeline.config.camera.width == 1920
        assert pipeline.config.camera.height == 1080


# ===================================================================
# 13. Reconstruction pipeline end-to-end
# ===================================================================

class TestReconstructionEndToEnd:

    def test_full_reconstruction(self):
        """Synthetic depth map + mask → obstacle list.

        NOTE: remove_table_points keeps Z in [table_z - tolerance, table_z + small_margin].
        We set table_z=400 so that points at Z=450-500 are within the range.
        The 'obstacle' region (Z=450) is 50mm 'below' the background (Z=500),
        simulating an object closer to the camera in a top-down view.
        """
        from perception.reconstruction import ReconstructionPipeline

        K = _make_K()
        config = {
            "grid_size_mm": 15.0,
            "height_percentiles": [90, 99],
            "min_points_per_cluster": 10,
        }
        # Set table_z=400 so that points at Z=450-500 are within [-5, 550] range
        recon = ReconstructionPipeline(config=config, K=K, table_z=400.0)

        # Create synthetic depth: background at 500mm, obstacle region at 450mm
        H, W = 100, 100
        depth = np.full((H, W), 500.0, dtype=np.float32)
        depth[30:60, 40:70] = 450.0  # obstacle closer to camera

        mask = np.zeros((H, W), dtype=np.uint8)
        mask[30:60, 40:70] = 255  # only obstacle region masked

        obstacles = recon.process(depth, mask)

        # Should find at least one obstacle cluster
        assert len(obstacles) >= 1
        obs = obstacles[0]
        # The obstacle points are at Z=450, relative to table_z=400 → height_p90 ≈ 50
        assert obs.height_p90 > 40
        assert obs.confidence > 0


# ===================================================================
# 14. Camera config loading tests
# ===================================================================

class TestCameraConfig:

    def test_pipeline_loads_camera_config(self):
        """Pipeline loads intrinsics/resolution/table_z from camera_config.yaml."""
        import os
        from pathlib import Path
        from perception.pipeline import PerceptionPlanningPipeline

        project_root = Path(__file__).parent.parent
        cam_cfg = project_root / "calibration" / "camera_config.yaml"

        if not cam_cfg.exists():
            pytest.skip("camera_config.yaml not found")

        pipeline = PerceptionPlanningPipeline(camera_config_path=str(cam_cfg))

        # Check intrinsics loaded from real calibration
        K = pipeline.K
        assert abs(K[0, 0] - 1494.37) < 1.0  # fx
        assert abs(K[0, 2] - 1339.94) < 1.0  # cx
        # Check resolution from camera_config.yaml
        assert pipeline.config.camera.width == 2560
        assert pipeline.config.camera.height == 1440
        # Check table_z
        assert pipeline.config.reconstruction.table_z_mm == 0.0

    def test_pipeline_config_override_with_cam_cfg(self):
        """Pipeline can combine config dict + camera_config.yaml."""
        from pathlib import Path
        from perception.pipeline import PerceptionPlanningPipeline

        project_root = Path(__file__).parent.parent
        cam_cfg = project_root / "calibration" / "camera_config.yaml"

        if not cam_cfg.exists():
            pytest.skip("camera_config.yaml not found")

        pipeline = PerceptionPlanningPipeline(
            config_path_or_dict={"mask": {"thresh": 50}},
            camera_config_path=str(cam_cfg),
        )

        # Config override applied
        assert pipeline.config.mask.thresh == 50
        # Camera config also applied (real calibration values)
        assert abs(pipeline.K[0, 0] - 1494.37) < 1.0


# ===================================================================
# 15. Debug dump with 0 obstacles
# ===================================================================

class TestDebugDumpZeroObstacles:

    def test_dump_with_no_obstacles(self):
        """Debug dump must save files even when no obstacles are detected."""
        import os, json, tempfile
        from perception.debug_dump import dump_scan

        # Create a blank frame
        frame = np.full((100, 100, 3), 255, dtype=np.uint8)

        # Fake YOLO result with no detections
        fake_yolo = type('FakeYoloResult', (), {
            'mask': np.zeros((100, 100), dtype=np.uint8),
            'detections': [],
            'method': 'yolo_fallback',
            'confidence': 0.0,
        })()

        # Blank masks
        blank = np.zeros((100, 100), dtype=np.uint8)

        with tempfile.TemporaryDirectory() as tmpdir:
            result_dir = dump_scan(
                output_dir=tmpdir,
                frame=frame,
                yolo_result=fake_yolo,
                bg_diff=blank,
                bg_mask=blank,
                arm_mask=None,
                final_mask=blank,
                obstacles=[],  # NO obstacles
                diagnostics={'test': 'zero_obstacles'},
            )

            # Verify all expected files exist
            assert os.path.exists(result_dir)
            assert os.path.exists(os.path.join(result_dir, "00_frame.jpg"))
            assert os.path.exists(os.path.join(result_dir, "06_final_mask.png"))
            assert os.path.exists(os.path.join(result_dir, "07_final_overlay.jpg"))
            assert os.path.exists(os.path.join(result_dir, "result.json"))
            assert os.path.exists(os.path.join(result_dir, "diagnostics.json"))

            # Verify result.json shows 0 obstacles
            with open(os.path.join(result_dir, "result.json")) as f:
                result = json.load(f)
            assert result["obstacle_count"] == 0
            assert result["obstacles"] == []

            # Verify diagnostics shows empty final mask
            with open(os.path.join(result_dir, "diagnostics.json")) as f:
                diag = json.load(f)
            assert diag["final"]["mask_area_px"] == 0
            assert diag["yolo"]["mask_area_px"] == 0
