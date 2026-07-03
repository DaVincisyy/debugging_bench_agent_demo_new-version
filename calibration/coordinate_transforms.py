"""
像素 → 机器人基座标系 坐标转换模块

转换链:
  像素(u,v) → 去畸变 → 相机归一化坐标 → 相机3D射线
  → 变换到机器人基座标系 → 与工作台平面求交 → 世界坐标(X,Y,Z)

用法:
  from coordinate_transforms import PixelToWorld
  converter = PixelToWorld("camera_config.yaml")
  world_xyz = converter.pixel_to_table(u=800, v=600)
"""
import numpy as np
import cv2
import yaml
import os


class PixelToWorld:
    """像素坐标到机器人世界坐标的转换器"""

    def __init__(self, config_path=None, table_z=None):
        """
        config_path: camera_config.yaml 路径
        table_z: 工作台面在机器人基座标系中的 Z 高度 (mm)
        """
        if config_path is None:
            config_path = os.path.join(os.path.dirname(__file__), "camera_config.yaml")

        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        # 内参
        self.K = np.array(data["intrinsics"]["camera_matrix"], dtype=np.float64)
        self.D = np.array(data["intrinsics"]["dist_coeffs"], dtype=np.float64)
        self.img_w = data["calibration"]["resolution"][0]
        self.img_h = data["calibration"]["resolution"][1]

        # 外参 — 相机在机器人基座标系中的位姿
        ext = data.get("extrinsics", {}).get("T_base_to_cam", {})
        self.R = np.array(ext.get("R", np.eye(3)), dtype=np.float64)
        self.t = np.array(ext.get("t", [[0], [0], [0]]), dtype=np.float64).reshape(3, 1)

        # 构建 4×4 变换矩阵 T_base_to_cam
        self.T_base_to_cam = np.eye(4)
        self.T_base_to_cam[:3, :3] = self.R
        self.T_base_to_cam[:3, 3] = self.t.flatten()

        # T_cam_to_base = T_base_to_cam 的逆
        self.T_cam_to_base = np.linalg.inv(self.T_base_to_cam)

        # 工作台平面 Z (mm)，在机器人基座标系中
        self.table_z = table_z

        print(f"  内参: fx={self.K[0,0]:.1f} cx={self.K[0,2]:.1f} cy={self.K[1,2]:.1f}")
        print(f"  畸变: k1={self.D[0,0]:.4f} k2={self.D[0,1]:.4f}")
        print(f"  相机位置 (基座标系): x={self.t[0,0]:.0f} y={self.t[1,0]:.0f} z={self.t[2,0]:.0f}")
        print(f"  桌面 Z: {self.table_z}" if self.table_z else "  桌面 Z: 未设置")

    def pixel_to_camera_ray(self, u, v):
        """
        像素坐标 → 相机坐标系下的归一化方向向量
        返回: (dir_3d, cam_origin)
          dir_3d: 归一化方向向量 (3×1), 相机坐标系
          cam_origin: [0, 0, 0] (相机光心)
        """
        pts = np.array([[u, v]], dtype=np.float32)
        # 去畸变
        undistorted = cv2.undistortPoints(pts, self.K, self.D, P=self.K)
        x_norm = undistorted[0, 0, 0]  # (u - cx) / fx
        y_norm = undistorted[0, 0, 1]  # (v - cy) / fy

        # 相机坐标系下的方向 (Z=1 平面上的点)
        dir_vec = np.array([[x_norm], [y_norm], [1.0]], dtype=np.float64)
        dir_vec = dir_vec / np.linalg.norm(dir_vec)
        return dir_vec

    def pixel_to_world_ray(self, u, v):
        """
        像素坐标 → 机器人基座标系下的射线
        返回: (origin, direction)
          origin: 射线起点 (相机光心在基座标系中)
          direction: 射线方向 (基座标系中，归一化)
        """
        dir_cam = self.pixel_to_camera_ray(u, v)

        # 方向变换: d_world = R_cam_to_base * d_cam
        R_cam_to_base = self.T_cam_to_base[:3, :3]
        dir_world = R_cam_to_base @ dir_cam
        dir_world = dir_world / np.linalg.norm(dir_world)

        # 起点: 相机光心在基座标系中
        origin_world = self.t.reshape(3)

        return origin_world, dir_world.reshape(3)

    def pixel_to_table(self, u, v):
        """
        像素坐标 → 工作台平面上的 3D 世界坐标
        Z = table_z (常数), 求解射线与水平面的交点
        返回: (x, y, z) 或 None (如果射线向上/平行于桌面)
        """
        if self.table_z is None:
            raise ValueError("table_z 未设置! 请先标定工作台高度: converter.set_table_z(z)")

        origin, direction = self.pixel_to_world_ray(u, v)

        oz = origin[2]
        dz = direction[2]

        # 检查是否与桌面平行或方向朝上（远离桌面）
        if abs(dz) < 1e-6:
            return None  # 视线平行于桌面

        t = (self.table_z - oz) / dz
        if t <= 0:
            return None  # 交点在相机后方

        x = origin[0] + direction[0] * t
        y = origin[1] + direction[1] * t
        return (float(x), float(y), float(self.table_z))

    def set_table_z(self, z):
        """设置工作台面在机器人基座标系中的 Z 高度"""
        self.table_z = z
        print(f"  桌面 Z 已设置为: {z:.1f} mm")

    def world_to_pixel(self, x, y, z):
        """
        世界坐标 → 像素坐标 (反向投影，用于验证)
        返回: (u, v) 或 None (如果在视野外)
        """
        pt_world = np.array([[x, y, z]], dtype=np.float64).T
        R_base_to_cam = self.R
        t_base_to_cam = self.t

        # 变换到相机坐标系
        pt_cam = R_base_to_cam.T @ (pt_world - t_base_to_cam)
        if pt_cam[2, 0] <= 0:
            return None  # 在相机后方

        # 透视投影
        xc, yc, zc = pt_cam[0, 0], pt_cam[1, 0], pt_cam[2, 0]
        u = (xc / zc) * self.K[0, 0] + self.K[0, 2]
        v = (yc / zc) * self.K[1, 1] + self.K[1, 2]

        # 检查是否在图像范围内
        if u < 0 or u >= self.img_w or v < 0 or v >= self.img_h:
            return None

        return (float(u), float(v))


# ---- 测试 ----
if __name__ == "__main__":
    converter = PixelToWorld()

    # 设置桌面高度 (根据实际标定结果)
    TABLE_Z = -228.0  # 从机械臂触碰桌面读取的 Z 坐标
    converter.set_table_z(TABLE_Z)

    print()
    print("=" * 60)
    print("  像素→世界坐标 转换测试")
    print("=" * 60)
    print()

    # 测试几个像素点
    test_pixels = [
        (640, 360, "左上区域"),
        (1280, 720, "画面中心"),
        (1920, 1080, "右下区域"),
        (200, 1200, "桌面近处中间"),
        (2400, 1200, "桌面近处右侧"),
    ]

    for u, v, desc in test_pixels:
        result = converter.pixel_to_table(u, v)
        if result:
            x, y, z = result
            print(f"  像素 ({u:4d}, {v:4d}) {desc:12s} → 世界 (x={x:7.1f}, y={y:7.1f}, z={z:6.1f}) mm")
        else:
            print(f"  像素 ({u:4d}, {v:4d}) {desc:12s} → 无法投影 (超出桌面)")
