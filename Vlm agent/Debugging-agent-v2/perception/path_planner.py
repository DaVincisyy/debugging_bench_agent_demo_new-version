"""
路径规划模块 — A* 栅格搜索 + 障碍物避让

输入: 起点(x,y), 终点(x,y), 障碍物列表, 工作空间范围
输出: 路径点列表 [(x1,y1,z1), ...]

算法: A* 在 XY 平面上搜索, 障碍物建模为膨胀后的圆形区域
"""
import numpy as np
import heapq
import math


class PathPlanner:
    def __init__(self, grid_resolution_mm=10, robot_radius_mm=35, safety_margin_mm=15):
        self.grid_res = grid_resolution_mm
        self.robot_radius = robot_radius_mm
        self.safety_margin = safety_margin_mm
        self.effective_radius = robot_radius_mm + safety_margin_mm

    def _world_to_grid(self, x, y, origin_x, origin_y):
        """世界坐标 → 栅格坐标"""
        gx = int(round((x - origin_x) / self.grid_res))
        gy = int(round((y - origin_y) / self.grid_res))
        return gx, gy

    def _grid_to_world(self, gx, gy, origin_x, origin_y):
        """栅格坐标 → 世界坐标"""
        wx = origin_x + gx * self.grid_res
        wy = origin_y + gy * self.grid_res
        return wx, wy

    def _build_occupancy_grid(self, obstacles, origin, size):
        """
        构建占据栅格
        obstacles: [{x, y, radius_mm}, ...]
        origin: (ox, oy) 栅格原点世界坐标
        size: (w, h) 栅格尺寸
        """
        ox, oy = origin
        w, h = size
        grid = np.zeros((h, w), dtype=np.uint8)

        for obs in obstacles:
            obs_r = obs["radius_mm"] + self.effective_radius
            # 障碍物覆盖的栅格范围
            cx, cy = self._world_to_grid(obs["x"], obs["y"], ox, oy)
            radius_cells = int(math.ceil(obs_r / self.grid_res))
            # 标记障碍物区域
            for dy in range(-radius_cells, radius_cells + 1):
                for dx in range(-radius_cells, radius_cells + 1):
                    gx, gy = cx + dx, cy + dy
                    if 0 <= gx < w and 0 <= gy < h:
                        wx, wy = self._grid_to_world(gx, gy, ox, oy)
                        dist = math.hypot(wx - obs["x"], wy - obs["y"])
                        if dist < obs_r:
                            grid[gy, gx] = 1  # 占据
        return grid

    def _astar(self, grid, start_xy, goal_xy, origin):
        """A* 搜索"""
        ox, oy = origin
        h, w = grid.shape
        sx, sy = self._world_to_grid(start_xy[0], start_xy[1], ox, oy)
        gx, gy = self._world_to_grid(goal_xy[0], goal_xy[1], ox, oy)

        # 检查起终点合法性
        if not (0 <= sx < w and 0 <= sy < h and grid[sy, sx] == 0):
            return None
        if not (0 <= gx < w and 0 <= gy < h and grid[gy, gx] == 0):
            return None

        # 8 邻域
        neighbors = [(-1, -1), (0, -1), (1, -1), (-1, 0),
                     (1, 0), (-1, 1), (0, 1), (1, 1)]

        open_set = []
        heapq.heappush(open_set, (0, sx, sy))
        came_from = {}
        g_score = {(sx, sy): 0}

        while open_set:
            _, cx, cy = heapq.heappop(open_set)

            if cx == gx and cy == gy:
                # 重建路径
                path = [(gx, gy)]
                while (cx, cy) in came_from:
                    cx, cy = came_from[(cx, cy)]
                    path.append((cx, cy))
                path.reverse()
                return [self._grid_to_world(px, py, ox, oy) for px, py in path]

            for dx, dy in neighbors:
                nx, ny = cx + dx, cy + dy
                if not (0 <= nx < w and 0 <= ny < h):
                    continue
                if grid[ny, nx] == 1:
                    continue

                # 对角移动时检查相邻格是否可通过
                if dx != 0 and dy != 0:
                    if grid[cy][nx] == 1 or grid[ny][cx] == 1:
                        continue

                move_cost = math.hypot(dx, dy) * self.grid_res
                tentative_g = g_score[(cx, cy)] + move_cost

                if tentative_g < g_score.get((nx, ny), float('inf')):
                    came_from[(nx, ny)] = (cx, cy)
                    g_score[(nx, ny)] = tentative_g
                    h = math.hypot(nx - gx, ny - gy) * self.grid_res
                    heapq.heappush(open_set, (tentative_g + h, nx, ny))

        return None  # 无路径

    def plan(self, start_xyz, goal_xyz, obstacles, hover_z=-170):
        """
        规划从起点到终点的无碰撞路径
        """
        sx, sy, sz = start_xyz
        gx, gy, gz = goal_xyz

        # 无障碍物 → 直接三段式路径: 升到hover高度 → 平移到目标XY → 下探
        if not obstacles:
            waypoints = []
            # 如果起点 Z 低于 hover_z, 先升到 hover_z
            if sz < hover_z:
                waypoints.append((sx, sy, hover_z))
            # 平移到目标 XY (在 hover_z)
            waypoints.append((gx, gy, hover_z))
            # 下探到目标 Z
            if abs(gz - hover_z) > 2:
                waypoints.append((gx, gy, gz))
            return waypoints

        # 有障碍物 → 检查直线是否被挡
        blocked, blocking_obs = self.check_path_blocked(
            (sx, sy, hover_z), (gx, gy, hover_z), obstacles
        )

        if not blocked:
            # 直线无阻挡, 同无障碍路径
            waypoints = []
            if sz < hover_z:
                waypoints.append((sx, sy, hover_z))
            waypoints.append((gx, gy, hover_z))
            if abs(gz - hover_z) > 2:
                waypoints.append((gx, gy, gz))
            return waypoints

        # 直线被挡 → A* 绕行 (在 hover_z 平面)
        print(f"    直线被障碍物阻挡 (X={blocking_obs['x']:.0f} Y={blocking_obs['y']:.0f} R={blocking_obs['radius_mm']:.0f})")

        margin = 100
        ox = min(sx, gx) - margin
        oy = min(sy, gy) - margin
        w = int((max(sx, gx) + margin - ox) / self.grid_res) + 1
        h = int((max(sy, gy) + margin - oy) / self.grid_res) + 1

        grid = self._build_occupancy_grid(obstacles, (ox, oy), (w, h))
        path_xy = self._astar(grid, (sx, sy), (gx, gy), (ox, oy))

        if path_xy is None:
            print("    [FAIL] A* 无法找到绕行路径!")
            return None

        # 生成路径点
        waypoints = []
        if sz < hover_z:
            waypoints.append((sx, sy, hover_z))
        for x, y in path_xy:
            waypoints.append((x, y, hover_z))
        if abs(gz - hover_z) > 2:
            waypoints[-1] = (gx, gy, gz)
        return waypoints

    def check_path_blocked(self, start_xyz, goal_xyz, obstacles):
        """检查直线路径是否被任何障碍物阻挡"""
        sx, sy, sz = start_xyz
        gx, gy, gz = goal_xyz

        for obs in obstacles:
            # 线段到圆形障碍物的最短距离
            dist = self._point_to_segment_dist(
                obs["x"], obs["y"],
                sx, sy, gx, gy
            )
            safe_dist = obs["radius_mm"] + self.effective_radius
            if dist < safe_dist:
                return True, obs
        return False, None

    @staticmethod
    def _point_to_segment_dist(px, py, ax, ay, bx, by):
        """点到线段的最短距离"""
        abx, aby = bx - ax, by - ay
        if abx == 0 and aby == 0:
            return math.hypot(px - ax, py - ay)
        t = ((px - ax) * abx + (py - ay) * aby) / (abx * abx + aby * aby)
        t = max(0.0, min(1.0, t))
        cx, cy = ax + t * abx, ay + t * aby
        return math.hypot(px - cx, py - cy)
