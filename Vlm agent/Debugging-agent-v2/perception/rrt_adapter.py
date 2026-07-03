"""
RRT* 适配器 — 使用 jarooz/RRTStar-Dynamic-Obstacle-Optimization 的 RRT 引擎

将自定义障碍物格式适配到 shapely Polygon 格式
"""
import sys, math, numpy as np
sys.path.insert(0, r"C:\Users\ZRR24\Desktop\KPIT\debugging_bench_agent_demo\RRTStar-Dynamic-Obstacle-Optimization")
from shapely.geometry import Point, Polygon
from RRT import RRT


class RRTAdapter:
    def __init__(self, robot_r=25, max_iter=3000):
        self.robot_r = robot_r
        self.max_iter = max_iter

    def _obs_to_polygons(self, obstacles):
        """将 {x,y,radius_mm} → shapely 圆形 Polygon"""
        polys = []
        for o in obstacles:
            r = o["radius_mm"] + self.robot_r
            cx, cy = o["x"], o["y"]
            # 用 20 边形近似圆
            pts = [(cx + r * math.cos(2*math.pi*i/20),
                    cy + r * math.sin(2*math.pi*i/20)) for i in range(20)]
            polys.append(Polygon(pts))
        return polys

    def plan(self, start_xyz, goal_xyz, obstacles, hover_z=-170):
        sx, sy, sz = start_xyz
        gx, gy, gz = goal_xyz

        if not obstacles:
            wp = [(sx,sy,hover_z),(gx,gy,hover_z)]
            if abs(gz-hover_z)>2:wp.append((gx,gy,gz))
            return wp

        polys = self._obs_to_polygons(obstacles)
        rrt = RRT(polys)
        rrt.start = np.array([sx, sy])
        rrt.goal = np.array([gx, gy])
        rrt.nodes[0][0:2] = rrt.start
        rrt.nodes[0][2] = 0  # cost
        rrt.nodes[0][3] = 0  # parent

        # RRT* 主循环 (对照示例代码)
        for i in range(self.max_iter):
            x = rrt.sample()
            nearest_idx = rrt.nearest(x)
            if nearest_idx is None: continue

            x_new, cost = rrt.steer(x, rrt.nodes[nearest_idx][0:2])
            if rrt.is_in_collision(x_new, rrt.nodes[nearest_idx][0:2]): continue

            near_indices = rrt.get_near_list(x_new)
            rrt.connect(x_new, nearest_idx, cost, new=True)
            if near_indices.size > 0:
                best_idx, best_cost = rrt.nearest_from_list(x_new, near_indices, reconnect=False)
                if best_idx is not None:
                    better_cost = rrt.get_dist(x_new, rrt.nodes[best_idx][0:2])
                    rrt.connect(x_new, best_idx, better_cost, new=False)
                rrt.rewire(x_new, near_indices)

        # 找到目标路径
        found, goal_idx = rrt.check_goal()
        if not found:
            return [(sx,sy,hover_z),(gx,gy,hover_z)]

        path_indices = rrt.find_path(goal_idx)
        path_indices.reverse()

        path_xy = [(rrt.nodes[int(i)][0], rrt.nodes[int(i)][1]) for i in path_indices]

        # 3D 路点
        wp = []
        if abs(sz-hover_z)>2: wp.append((sx,sy,hover_z))
        for px, py in path_xy:
            wp.append((px, py, hover_z))
        wp[-1] = (gx, gy, gz)
        return wp
