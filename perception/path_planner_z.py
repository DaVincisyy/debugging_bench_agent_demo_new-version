"""
Z轴抬升避障路径规划器

策略: XY被挡 → 抬Z绕过 → 平移 → 降Z
只在障碍物太高时才 fallback 到 A* 绕行
"""
import heapq
import math
import numpy as np


class PathPlannerZ:
    def __init__(self, robot_radius_mm=35, safety_margin_mm=15,
                 max_z_mm=-60, min_z_mm=-220):
        self.robot_r = robot_radius_mm + safety_margin_mm  # 有效半径
        self.max_z = max_z_mm      # 最高可抬升 Z (基座标)
        self.min_z = min_z_mm      # 最低可下探 Z (桌面附近)
        self.grid_res = 10         # A* fallback 用

    def check_path_blocked(self, start_xyz, goal_xyz, obstacles):
        """检查线段是否被障碍物阻挡 (考虑 Z 高度)"""
        sx, sy, sz = start_xyz
        gx, gy, gz = goal_xyz
        min_z = min(sz, gz)
        for obs in obstacles:
            # 障碍物顶部 Z
            obs_top = obs.get("z", self.min_z) + obs.get("height_mm", 100)
            # 如果整段路径都在障碍物上方 → 不阻挡
            if min_z >= obs_top:
                continue
            dist = self._point_to_segment_dist(
                obs["x"], obs["y"], sx, sy, gx, gy
            )
            if dist < obs["radius_mm"] + self.robot_r:
                return True, obs
        return False, None

    def _point_to_segment_dist(self, px, py, ax, ay, bx, by):
        abx, aby = bx - ax, by - ay
        if abx == 0 and aby == 0:
            return math.hypot(px - ax, py - ay)
        t = ((px - ax) * abx + (py - ay) * aby) / (abx * abx + aby * aby)
        t = max(0.0, min(1.0, t))
        cx, cy = ax + t * abx, ay + t * aby
        return math.hypot(px - cx, py - cy)

    def _calc_safe_z(self, obstacles):
        """计算能越过所有障碍物的安全 Z 高度"""
        max_obs_top = self.min_z  # 最低从桌面算起
        for obs in obstacles:
            obs_top = obs.get("z", self.min_z) + obs.get("height_mm", 100)
            if obs_top > max_obs_top:
                max_obs_top = obs_top
        safe_z = max_obs_top + 30  # 30mm 安全余量
        return min(safe_z, self.max_z)  # 不超过最大可抬升高度

    def plan(self, start_xyz, goal_xyz, obstacles, hover_z=-170):
        """
        规划路径

        逻辑:
          1. 起点→终点 直线在 hover_z 高度是否被挡？
             - 否 → 三段式直走(升→移→降)
             - 是 → 尝试 Z 抬升
          2. Z 抬升后直线是否被挡？
             - 否 → 五段式(升→更高Z→移→降Z→降)
             - 是 → A* 绕行 (fallback)
        """
        sx, sy, sz = start_xyz
        gx, gy, gz = goal_xyz

        # 无障碍 → 三段式
        if not obstacles:
            return self._direct_path(sx, sy, sz, gx, gy, gz, hover_z)

        # 检查在 hover_z 高度 XY 直线是否被挡
        blocked, _ = self.check_path_blocked(
            (sx, sy, hover_z), (gx, gy, hover_z), obstacles
        )

        if not blocked:
            return self._direct_path(sx, sy, sz, gx, gy, gz, hover_z)

        # ── 被挡 → Z 轴抬升绕行 ──
        safe_z = self._calc_safe_z(obstacles)
        print(f"    XY被挡 → Z抬升至 {safe_z:.0f}mm")

        if safe_z <= hover_z + 10:
            # 安全高度不够(或障碍物太高), fallback A*
            print(f"    Z抬升不足, fallback A*")
            return self._astar_fallback(sx, sy, sz, gx, gy, gz, obstacles, hover_z)

        # 检查抬升后 XY 直线是否被挡
        blocked_high, _ = self.check_path_blocked(
            (sx, sy, safe_z), (gx, gy, safe_z), obstacles
        )

        if blocked_high:
            print(f"    抬升后仍被挡, fallback A*")
            return self._astar_fallback(sx, sy, sz, gx, gy, gz, obstacles, hover_z)

        # Z 轴绕行: 升 → 高Z平移 → 降
        waypoints = []
        if abs(sz - hover_z) > 2:
            waypoints.append((sx, sy, hover_z))      # 先升到 hover
        waypoints.append((sx, sy, safe_z))           # 原地抬升
        waypoints.append((gx, gy, safe_z))           # 高Z平移
        waypoints.append((gx, gy, hover_z))           # XY到位后降到 hover
        if abs(gz - hover_z) > 2:
            waypoints.append((gx, gy, gz))           # 最终下探
        return waypoints

    def _direct_path(self, sx, sy, sz, gx, gy, gz, hover_z):
        """无障碍三段式: 升hover → 平移 → 降"""
        waypoints = []
        if abs(sz - hover_z) > 2:
            waypoints.append((sx, sy, hover_z))
        if abs(sx - gx) > 2 or abs(sy - gy) > 2:
            waypoints.append((gx, gy, hover_z))
        if abs(gz - hover_z) > 2:
            waypoints.append((gx, gy, gz))
        return waypoints if waypoints else [(gx, gy, gz)]

    # ── A* Fallback ──
    def _astar_fallback(self, sx, sy, sz, gx, gy, gz, obstacles, hover_z):
        """原有 A* 备选方案"""
        margin = 100
        ox = min(sx, gx) - margin
        oy = min(sy, gy) - margin
        w = int((max(sx, gx) + margin - ox) / self.grid_res) + 1
        h = int((max(sy, gy) + margin - oy) / self.grid_res) + 1

        grid = self._build_grid(obstacles, ox, oy, w, h)
        path_xy = self._astar(grid, sx, sy, gx, gy, ox, oy, w, h)

        if path_xy is None:
            # 试试清空栅格的高空方案
            grid0 = np.zeros((h, w), dtype=np.uint8)
            path_xy = self._astar(grid0, sx, sy, gx, gy, ox, oy, w, h)
            if path_xy is None:
                return None
            hover_high = min(hover_z + 80, self.max_z)

        if path_xy is None:
            return None

        waypoints = []
        if abs(sz - hover_z) > 2:
            waypoints.append((sx, sy, hover_z))
        for x, y in path_xy:
            waypoints.append((x, y, hover_z))
        waypoints[-1] = (gx, gy, gz)
        return waypoints

    def _build_grid(self, obstacles, ox, oy, w, h):
        grid = np.zeros((h, w), dtype=np.uint8)
        for obs in obstacles:
            obs_r = obs["radius_mm"] + self.robot_r
            cx = int(round((obs["x"] - ox) / self.grid_res))
            cy = int(round((obs["y"] - oy) / self.grid_res))
            rc = int(math.ceil(obs_r / self.grid_res))
            for dy in range(-rc, rc + 1):
                for dx in range(-rc, rc + 1):
                    gx, gy = cx + dx, cy + dy
                    if 0 <= gx < w and 0 <= gy < h:
                        wx = ox + gx * self.grid_res
                        wy = oy + gy * self.grid_res
                        if math.hypot(wx - obs["x"], wy - obs["y"]) < obs_r:
                            grid[gy, gx] = 1
        return grid

    def _astar(self, grid, sx, sy, gx, gy, ox, oy, w, h):
        sgx = int(round((sx - ox) / self.grid_res))
        sgy = int(round((sy - oy) / self.grid_res))
        ggx = int(round((gx - ox) / self.grid_res))
        ggy = int(round((gy - oy) / self.grid_res))

        if not (0 <= sgx < w and 0 <= sgy < h and grid[sgy, sgx] == 0):
            return None
        if not (0 <= ggx < w and 0 <= ggy < h and grid[ggy, ggx] == 0):
            return None

        nb = [(-1,-1),(0,-1),(1,-1),(-1,0),(1,0),(-1,1),(0,1),(1,1)]
        openset = [(0, sgx, sgy)]
        came_from = {}
        g_score = {(sgx, sgy): 0}

        while openset:
            _, cx, cy = heapq.heappop(openset)
            if cx == ggx and cy == ggy:
                path = [(ggx, ggy)]
                while (cx, cy) in came_from:
                    cx, cy = came_from[(cx, cy)]
                    path.append((cx, cy))
                path.reverse()
                return [(ox + px * self.grid_res, oy + py * self.grid_res) for px, py in path]

            for dx, dy in nb:
                nx, ny = cx + dx, cy + dy
                if not (0 <= nx < w and 0 <= ny < h): continue
                if grid[ny, nx]: continue
                if dx and dy and (grid[cy][nx] or grid[ny][cx]): continue
                g = g_score[(cx, cy)] + math.hypot(dx, dy) * self.grid_res
                if g < g_score.get((nx, ny), float('inf')):
                    came_from[(nx, ny)] = (cx, cy)
                    g_score[(nx, ny)] = g
                    h = math.hypot(nx - ggx, ny - ggy) * self.grid_res
                    heapq.heappush(openset, (g + h, nx, ny))
        return None
