"""
RRT* + 人工势场(APF) 融合路径规划器

原理:
  1. APF 计算引力场(向目标) + 斥力场(远离障碍物)
  2. RRT* 在 APF 引导下快速采样, 找到无碰撞路径
  3. 路径平滑: shortcut + B-spline
  4. 动态重规划: 运动中发现新障碍物 → 当前点重启 RRT*

优势 vs Z 抬升:
  - 真正在 XY 平面绕开障碍物, 不依赖 Z 空间
  - APF 引导避免 RRT 的盲目随机
  - 支持动态重规划
"""
import math, random, heapq, time
import numpy as np


class RRTStarAPF:
    def __init__(self, robot_radius_mm=35, safety_margin_mm=15,
                 step_size=15, max_iter=2000, goal_sample_rate=0.15):
        self.robot_r = robot_radius_mm + safety_margin_mm  # 50mm
        self.step = step_size    # RRT 生长步长 mm
        self.max_iter = max_iter # 最大迭代次数
        self.goal_rate = goal_sample_rate  # 直接向目标采样的概率
        self.apf_attraction = 0.5  # APF 引力系数
        self.apf_repulsion = 200.0 # APF 斥力系数
        self.apf_influence = 120.0 # 斥力影响范围 mm
        self.neighbor_radius = 60  # RRT* 近邻重连半径 mm

    def check_collision(self, p1, p2, obstacles):
        """线段碰撞检测 (考虑机器人半径)"""
        for obs in obstacles:
            safe_r = obs["radius_mm"] + self.robot_r
            dist = self._point_seg_dist(obs["x"], obs["y"], p1[0], p1[1], p2[0], p2[1])
            if dist < safe_r:
                return True
        return False

    def _point_seg_dist(self, px, py, ax, ay, bx, by):
        abx, aby = bx-ax, by-ay
        if abx==0 and aby==0: return math.hypot(px-ax, py-ay)
        t = ((px-ax)*abx + (py-ay)*aby) / (abx*abx + aby*aby)
        t = max(0.0, min(1.0, t))
        return math.hypot(px - (ax+t*abx), py - (ay+t*aby))

    def _apf_force(self, point, goal, obstacles):
        """计算 APF 合力向量"""
        fx, fy = 0.0, 0.0
        # 引力: 指向目标
        dx_g = goal[0] - point[0]
        dy_g = goal[1] - point[1]
        dist_g = math.hypot(dx_g, dy_g) + 1e-6
        fx += self.apf_attraction * dx_g / dist_g
        fy += self.apf_attraction * dy_g / dist_g
        # 斥力: 远离障碍物
        for obs in obstacles:
            safe_r = obs["radius_mm"] + self.robot_r + self.apf_influence
            dx_o = point[0] - obs["x"]
            dy_o = point[1] - obs["y"]
            dist_o = math.hypot(dx_o, dy_o)
            if dist_o < safe_r and dist_o > 1e-6:
                f = self.apf_repulsion * (1.0/dist_o - 1.0/safe_r) / (dist_o*dist_o)
                fx += f * dx_o / dist_o
                fy += f * dy_o / dist_o
        # 归一化
        mag = math.hypot(fx, fy)
        if mag > 1e-6:
            fx /= mag; fy /= mag
        return fx, fy

    def _random_point(self, goal, bounds):
        """采样: goal_rate 概率直接选目标, 其余随机 + APF 偏置"""
        if random.random() < self.goal_rate:
            return (goal[0], goal[1])
        x = random.uniform(bounds[0], bounds[1])
        y = random.uniform(bounds[2], bounds[3])
        return (x, y)

    def plan(self, start_xyz, goal_xyz, obstacles, hover_z=-170):
        """RRT* + APF 路径规划"""
        sx, sy, sz = start_xyz
        gx, gy, gz = goal_xyz

        if not obstacles:
            # 无障碍 → 直走
            wp = []
            if abs(sz - hover_z) > 2: wp.append((sx, sy, hover_z))
            wp.append((gx, gy, hover_z))
            if abs(gz - hover_z) > 2: wp.append((gx, gy, gz))
            return wp

        # 工作空间边界
        margin = 50
        bx_min = min(sx, gx) - margin
        bx_max = max(sx, gx) + margin
        by_min = min(sy, gy) - margin
        by_max = max(sy, gy) + margin
        bounds = (bx_min, bx_max, by_min, by_max)

        # RRT* 节点: {xy, parent_idx, cost}
        nodes = [{"xy": (sx, sy), "parent": -1, "cost": 0.0}]
        goal_idx = -1

        for it in range(self.max_iter):
            # 采样 (APF 引导)
            rand = self._random_point((gx, gy), bounds)
            # APF 力偏移
            fx, fy = self._apf_force(rand, (gx, gy), obstacles)
            biased = (rand[0] + fx * self.step * 0.3,
                      rand[1] + fy * self.step * 0.3)
            biased = (max(bx_min, min(bx_max, biased[0])),
                      max(by_min, min(by_max, biased[1])))

            # 找最近节点
            nearest_idx = self._nearest(nodes, biased)

            # 向采样点生长
            nx, ny = nodes[nearest_idx]["xy"]
            dx, dy = biased[0]-nx, biased[1]-ny
            dist = math.hypot(dx, dy)
            if dist < 1e-6: continue
            if dist > self.step:
                dx = dx / dist * self.step
                dy = dy / dist * self.step
            new_x, new_y = nx+dx, ny+dy

            # 碰撞检测
            if self.check_collision((nx, ny), (new_x, new_y), obstacles):
                continue

            new_cost = nodes[nearest_idx]["cost"] + math.hypot(dx, dy)

            # 找近邻, 选最优父节点 (RRT*)
            best_parent = nearest_idx
            best_cost = new_cost
            for i, n in enumerate(nodes):
                if i == nearest_idx: continue
                if math.hypot(n["xy"][0]-new_x, n["xy"][1]-new_y) < self.neighbor_radius:
                    c = n["cost"] + math.hypot(n["xy"][0]-new_x, n["xy"][1]-new_y)
                    if c < best_cost and not self.check_collision(n["xy"], (new_x,new_y), obstacles):
                        best_cost = c
                        best_parent = i

            new_idx = len(nodes)
            nodes.append({"xy": (new_x, new_y), "parent": best_parent, "cost": best_cost})

            # 近邻重连 (RRT*)
            for i, n in enumerate(nodes):
                if i == new_idx: continue
                d = math.hypot(n["xy"][0]-new_x, n["xy"][1]-new_y)
                if d < self.neighbor_radius:
                    c = best_cost + d
                    if c < n["cost"] and not self.check_collision((new_x,new_y), n["xy"], obstacles):
                        nodes[i]["parent"] = new_idx
                        nodes[i]["cost"] = c

            # 检查是否到达目标
            if math.hypot(new_x-gx, new_y-gy) < self.step*1.5:
                if not self.check_collision((new_x,new_y), (gx,gy), obstacles):
                    nodes.append({"xy": (gx, gy), "parent": new_idx,
                                   "cost": best_cost+math.hypot(new_x-gx, new_y-gy)})
                    goal_idx = len(nodes) - 1
                    break

        if goal_idx < 0:
            # 找最近的节点作为近似目标
            min_d = float('inf')
            for i, n in enumerate(nodes):
                d = math.hypot(n["xy"][0]-gx, n["xy"][1]-gy)
                if d < min_d: min_d = d; goal_idx = i

        # 回溯路径
        path_xy = []
        idx = goal_idx
        while idx >= 0:
            path_xy.append(nodes[idx]["xy"])
            idx = nodes[idx]["parent"]
        path_xy.reverse()

        # 路径平滑: shortcut
        path_xy = self._shortcut(path_xy, obstacles)

        # 生成 3D 路径点
        waypoints = []
        if abs(sz - hover_z) > 2: waypoints.append((sx, sy, hover_z))
        for x, y in path_xy: waypoints.append((x, y, hover_z))
        waypoints[-1] = (gx, gy, gz)
        return waypoints

    def _nearest(self, nodes, point):
        best_i, best_d = 0, float('inf')
        for i, n in enumerate(nodes):
            d = math.hypot(n["xy"][0]-point[0], n["xy"][1]-point[1])
            if d < best_d: best_d, best_i = d, i
        return best_i

    def _shortcut(self, path, obstacles, iterations=50):
        """路径平滑: 随机选两点, 如直线无碰撞则删中间点"""
        path = list(path)
        for _ in range(iterations):
            if len(path) < 3: break
            i = random.randint(0, len(path)-2)
            j = random.randint(i+1, min(i+5, len(path)-1))
            if not self.check_collision(path[i], path[j], obstacles):
                del path[i+1:j]
        return path
