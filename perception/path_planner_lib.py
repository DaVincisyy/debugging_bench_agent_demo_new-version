"""
基于 python-motion-planning 库的避障路径规划器
"""
import math
from python_motion_planning import Grid, RRTStar, AStar
import numpy as np


class LibPathPlanner:
    def __init__(self, robot_radius_mm=35, safety_margin_mm=15,
                 grid_res=10, algorithm='rrtstar'):
        self.robot_r = robot_radius_mm + safety_margin_mm
        self.grid_res = grid_res
        self.algorithm = algorithm

    def plan(self, start_xyz, goal_xyz, obstacles, hover_z=-170):
        sx, sy, sz = start_xyz
        gx, gy, gz = goal_xyz

        if not obstacles:
            wp = []
            if abs(sz - hover_z) > 2: wp.append((sx, sy, hover_z))
            wp.append((gx, gy, hover_z))
            if abs(gz - hover_z) > 2: wp.append((gx, gy, gz))
            return wp

        # 构建占据栅格
        margin = 80
        x_vals = [sx, gx] + [o["x"] for o in obstacles]
        y_vals = [sy, gy] + [o["y"] for o in obstacles]
        x_min, x_max = min(x_vals)-margin, max(x_vals)+margin
        y_min, y_max = min(y_vals)-margin, max(y_vals)+margin

        w = int((x_max - x_min) / self.grid_res) + 1
        h = int((y_max - y_min) / self.grid_res) + 1

        # 创建障碍物栅格 (width, height)
        grid_data = np.zeros((w, h), dtype=np.int8)
        for o in obstacles:
            cx_cell = int((o["x"] - x_min) / self.grid_res)
            cy_cell = int((o["y"] - y_min) / self.grid_res)
            r_cell = int((o["radius_mm"] + self.robot_r) / self.grid_res)
            for dx in range(-r_cell, r_cell+1):
                for dy in range(-r_cell, r_cell+1):
                    gx_c = cx_cell + dx; gy_c = cy_cell + dy
                    if 0 <= gx_c < w and 0 <= gy_c < h:
                        if math.hypot(dx, dy) <= r_cell:
                            grid_data[gx_c, gy_c] = 1

        grid = Grid(bounds=[[x_min, x_max], [y_min, y_max]],
                    resolution=self.grid_res, type_map=grid_data)

        # 规划
        if self.algorithm == 'astar':
            planner = AStar(map_=grid, start=(sx, sy), goal=(gx, gy))
        else:
            planner = RRTStar(map_=grid, start=(sx, sy), goal=(gx, gy),
                            max_dist=self.grid_res*3, goal_sample_rate=0.1)

        result = planner.plan()
        if result is None:
            wp = [(sx, sy, hover_z), (gx, gy, hover_z)]
            if abs(gz - hover_z) > 2: wp.append((gx, gy, gz))
            return wp

        cost, path = result
        if path is None or len(path) < 2:
            wp = [(sx, sy, hover_z), (gx, gy, hover_z)]
            if abs(gz - hover_z) > 2: wp.append((gx, gy, gz))
            return wp

        waypoints = []
        if abs(sz - hover_z) > 2: waypoints.append((sx, sy, hover_z))
        for px, py in path:
            waypoints.append((px, py, hover_z))
        waypoints[-1] = (gx, gy, gz)
        return waypoints
