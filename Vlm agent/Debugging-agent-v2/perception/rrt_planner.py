"""手写 RRT* 路径规划器 — 零依赖, ~80行"""
import math, random
import numpy as np

class SimpleRRTStar:
    def __init__(self, robot_r=50, step=20, max_iter=2000, search_r=60):
        self.robot_r = robot_r; self.step = step
        self.max_iter = max_iter; self.search_r = search_r

    def plan(self, start_xyz, goal_xyz, obstacles, hover_z=-170):
        sx,sy,sz=start_xyz; gx,gy,gz=goal_xyz
        if not obstacles:
            wp=[(sx,sy,hover_z),(gx,gy,hover_z)]
            if abs(gz-hover_z)>2:wp.append((gx,gy,gz))
            return wp

        # 建树
        nodes=[(sx,sy,-1,0.0)]  # (x,y,parent_idx,cost)
        margin=80
        x_min=min(sx,gx)-margin;x_max=max(sx,gx)+margin
        y_min=min(sy,gy)-margin;y_max=max(sy,gy)+margin

        for _ in range(self.max_iter):
            # 采样
            if random.random()<0.05: rx,ry=gx,gy
            else: rx=random.uniform(x_min,x_max); ry=random.uniform(y_min,y_max)
            # 最近节点
            ni,nd=0,float('inf')
            for i,(nx,ny,_,_2) in enumerate(nodes):
                d=math.hypot(nx-rx,ny-ry)
                if d<nd:nd=d;ni=i
            nx,ny= nodes[ni][0],nodes[ni][1]
            # 生长
            dx=rx-nx;dy=ry-ny;d=math.hypot(dx,dy)
            if d<1e-6:continue
            if d>self.step:dx=dx/d*self.step;dy=dy/d*self.step
            new_x,new_y=nx+dx,ny+dy
            # 碰撞
            if self._collision(nx,ny,new_x,new_y,obstacles):continue
            new_cost=nodes[ni][3]+math.hypot(dx,dy)
            # RRT*重连
            best_p, best_c = ni, new_cost
            for i,(ix,iy,_,ic) in enumerate(nodes):
                if math.hypot(ix-new_x,iy-new_y)<self.search_r:
                    c=ic+math.hypot(ix-new_x,iy-new_y)
                    if c<best_c and not self._collision(ix,iy,new_x,new_y,obstacles):
                        best_c=c;best_p=i
            nodes.append((new_x,new_y,best_p,best_c))
            # 到目标?
            if math.hypot(new_x-gx,new_y-gy)<self.step*1.5:
                if not self._collision(new_x,new_y,gx,gy,obstacles):
                    nodes.append((gx,gy,len(nodes)-1,best_c+math.hypot(new_x-gx,new_y-gy)))
                    break
        # 回溯
        best_i=min(range(len(nodes)),key=lambda i:math.hypot(nodes[i][0]-gx,nodes[i][1]-gy))
        path=[];i=best_i
        while i>=0:path.append((nodes[i][0],nodes[i][1]));i=nodes[i][2]
        path.reverse()
        # 3D路径
        wp=[(sx,sy,hover_z)] if abs(sz-hover_z)>2 else []
        for px,py in path:wp.append((px,py,hover_z))
        wp[-1]=(gx,gy,gz)
        return wp

    def check_path_blocked(self, start_xyz, goal_xyz, obstacles):
        """检查直线是否被障碍物阻挡"""
        sx,sy,sz=start_xyz; gx,gy,gz=goal_xyz
        for o in obstacles:
            if self._pt_seg(o["x"],o["y"],sx,sy,gx,gy) < o["radius_mm"]+self.robot_r:
                return True, o
        return False, None

    def _collision(self,x1,y1,x2,y2,obs):
        for o in obs:
            r=o["radius_mm"]+self.robot_r
            d=self._pt_seg(o["x"],o["y"],x1,y1,x2,y2)
            if d<r:return True
        return False

    @staticmethod
    def _pt_seg(px,py,ax,ay,bx,by):
        abx,aby=bx-ax,by-ay
        if abx==0 and aby==0:return math.hypot(px-ax,py-ay)
        t=max(0,min(1,((px-ax)*abx+(py-ay)*aby)/(abx*abx+aby*aby)))
        return math.hypot(px-(ax+t*abx),py-(ay+t*aby))
