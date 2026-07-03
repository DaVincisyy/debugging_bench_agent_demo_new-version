"""
基于 python-motion-planning 库的避障路径规划器
"""
import math
from python_motion_planning import Grid, RRTStar, AStar, Node as PNode
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

        w = int((x_max - x_min) / self.grid_res)
        h = int((y_max - y_min) / self.grid_res)

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
                            max_dist=self.grid_res*3, goal_sample_rate=0.1,
                            max_sample_step=3000)

        result = planner.plan()
        if result is None:
            return [(sx, sy, hover_z), (gx, gy, hover_z)]

        cost, path_dict = result
        if not path_dict.get("success"):
            return [(sx, sy, hover_z), (gx, gy, hover_z)]

        # RRT* 路径提取: 找最近 goal 的 Node, parent 回溯
        expand = path_dict["expand"]
        nodes = [v for v in expand.values() if isinstance(v, PNode)]
        if not nodes:
            return [(sx, sy, hover_z), (gx, gy, hover_z)]

        # 最近 goal 的 Node (排除 goal 本身)
        nodes_not_goal = [n for n in nodes
                          if math.hypot(n.current[0]-gx, n.current[1]-gy) > 0.1]
        if not nodes_not_goal: nodes_not_goal = nodes
        best_n = min(nodes_not_goal,
                     key=lambda n: math.hypot(n.current[0]-gx, n.current[1]-gy))
        # parent 回溯
        path_xy = []
        n = best_n
        while n is not None:
            path_xy.append(n.current)
            n = getattr(n, 'parent', None)
        path_xy.reverse()

        waypoints = []
        if abs(sz - hover_z) > 2: waypoints.append((sx, sy, hover_z))
        for px, py in path_xy:
            waypoints.append((px, py, hover_z))
        waypoints[-1] = (gx, gy, gz)
        return waypoints
