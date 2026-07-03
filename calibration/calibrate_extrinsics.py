"""
相机外参标定 — Eye-to-Hand 手眼标定
用法: python calibrate_extrinsics.py

通过 HTTP 调用 Robot Gateway 控制 MG400 移动，
同时用摄像头检测 ArUco 标记，计算相机到机器人基座标的变换矩阵。
"""
import cv2
import numpy as np
import os
import sys
import time
import yaml
import requests
import json
from datetime import datetime

# ---- 配置 ----
CAMERA_INDEX = 1
CAMERA_RESOLUTION = (2560, 1440)
ROBOT_GATEWAY_URL = "http://127.0.0.1:8010"
ARUCO_DICT = cv2.aruco.DICT_6X6_250
ARUCO_ID = 0
MARKER_SIZE_MM = 40  # ArUco 标记黑边实际边长 (mm)

# 标定用位姿列表 — MG400 工作空间内的安全位姿
# [x, y, z, r]  单位: mm, 度
# 注意: Z 必须 >= 100mm (避开低Z保护阈值 85mm)
CALIBRATION_POSES = [
    [300, 0, 120, 0],
    [300, 80, 120, 0],
    [300, -80, 120, 0],
    [250, 80, 120, 30],
    [250, -80, 120, -30],
    [300, 0, 150, 0],
    [250, 0, 150, 30],
    [300, 80, 150, 30],
    [300, -80, 150, -30],
    [350, 0, 120, 0],
    [350, 60, 120, 20],
    [350, -60, 120, -20],
    [250, 60, 130, 45],
    [250, -60, 130, -45],
    [350, 0, 150, 0],
    [300, 60, 150, 0],
    [300, -60, 150, 0],
]

# 完整 MG400 配置 (传给 Robot Gateway)
MG400_CONFIG = {
    "mode": "mg400",
    "ip": "192.168.2.6",
    "dashboardPort": 29999,
    "motionPort": 30003,
    "feedbackPort": 30004,
    "speed": 30,
    "timeoutMs": 5000,
    "autoEnable": True,
    "motionCommand": "MovJ",
}

CONFIG_YAML = os.path.join(os.path.dirname(__file__), "camera_config.yaml")
SPEED = 30  # 运动速度 (1-100)，标定时用低速保安全


def load_intrinsics():
    """加载内参"""
    with open(CONFIG_YAML, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    K = np.array(data["intrinsics"]["camera_matrix"])
    D = np.array(data["intrinsics"]["dist_coeffs"])
    return K, D


def get_robot_pose():
    """通过 Robot Gateway 获取当前机械臂位姿"""
    try:
        resp = requests.post(
            f"{ROBOT_GATEWAY_URL}/v1/robot/status",
            json={"config": MG400_CONFIG},
            timeout=5,
        )
        data = resp.json()
        pose = data.get("robot", {}).get("pose", {})
        x = pose.get("x", 0)
        y = pose.get("y", 0)
        z = pose.get("z", 0)
        r = pose.get("r", 0)
        return np.array([x, y, z, r], dtype=np.float64)
    except Exception as e:
        print(f"  [WARN] 获取位姿失败: {e}")
        return None


def move_robot(x, y, z, r, speed=SPEED):
    """通过 Robot Gateway 移动机械臂"""
    try:
        payload = {
            "config": MG400_CONFIG,
            "command": {
                "name": "move",
                "pose": {"x": x, "y": y, "z": z, "r": r},
                "motionCommand": "MovJ",
            },
        }
        resp = requests.post(
            f"{ROBOT_GATEWAY_URL}/v1/robot/command",
            json=payload,
            timeout=15,
        )
        data = resp.json()
        if data.get("ok"):
            return True
        else:
            print(f"  [WARN] 运动失败: {data.get('error', 'unknown')}")
            return False
    except Exception as e:
        print(f"  [WARN] 运动请求失败: {e}")
        return False


def detect_aruco(frame, K, D):
    """检测 ArUco 标记并返回在相机坐标系下的位姿 (旋转向量, 平移向量)"""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    aruco_dict = cv2.aruco.getPredefinedDictionary(ARUCO_DICT)
    params = cv2.aruco.DetectorParameters()
    detector = cv2.aruco.ArucoDetector(aruco_dict, params)

    corners, ids, _ = detector.detectMarkers(gray)

    if ids is None or ARUCO_ID not in ids:
        return None, None

    idx = list(ids.flatten()).index(ARUCO_ID)
    marker_corners = corners[idx].reshape(4, 2)

    # ArUco 标记的 3D 角点 (标记坐标系, 中心为原点, 单位 mm)
    half = MARKER_SIZE_MM / 2.0
    obj_points = np.array([
        [-half, -half, 0],
        [ half, -half, 0],
        [ half,  half, 0],
        [-half,  half, 0],
    ], dtype=np.float32)

    # solvePnP 求解标记在相机坐标系下的位姿
    success, rvec, tvec = cv2.solvePnP(obj_points, marker_corners, K, D)
    if not success:
        return None, None

    return rvec, tvec  # tvec 单位: mm


def main():
    print("=" * 60)
    print("  相机外参标定 — Eye-to-Hand")
    print("=" * 60)

    # 加载内参
    K, D = load_intrinsics()
    print(f"  内参已加载: fx={K[0,0]:.1f}, fy={K[1,1]:.1f}")

    # 打开摄像头
    cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_DSHOW)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAMERA_RESOLUTION[0])
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAMERA_RESOLUTION[1])
    if not cap.isOpened():
        print("[ERROR] 无法打开摄像头")
        sys.exit(1)
    print(f"  摄像头已打开: {CAMERA_RESOLUTION[0]}×{CAMERA_RESOLUTION[1]}")
    print("  预热摄像头 (等待自动曝光稳定)...")
    for _ in range(30):
        cap.read()
    time.sleep(0.5)
    # 再读 10 帧确保稳定
    for _ in range(10):
        cap.read()
    print("  预热完成")

    # 检查 Robot Gateway
    try:
        resp = requests.get(f"{ROBOT_GATEWAY_URL}/health", timeout=3)
        print(f"  Robot Gateway: {resp.json().get('service', 'unknown')} ✅")
    except Exception:
        print("[ERROR] Robot Gateway 未运行 (http://127.0.0.1:8010)")
        cap.release()
        sys.exit(1)

    # 获取初始位姿
    init_pose = get_robot_pose()
    if init_pose is None:
        print("[ERROR] 无法获取机械臂位姿，请确认 MG400 已上电使能")
        cap.release()
        sys.exit(1)
    print(f"  机械臂当前位姿: x={init_pose[0]:.1f} y={init_pose[1]:.1f} z={init_pose[2]:.1f} r={init_pose[3]:.1f}")
    print()

    # ---- 采集 ----
    R_base_to_ee = []  # 机械臂末端在基坐标系下的旋转 (3×3)
    t_base_to_ee = []  # 机械臂末端在基坐标系下的平移 (mm)
    R_cam_to_marker = []  # ArUco 标记在相机坐标系下的旋转
    t_cam_to_marker = []  # ArUco 标记在相机坐标系下的平移 (mm)
    successful_poses = []

    print(f"共 {len(CALIBRATION_POSES)} 个标定位姿，开始采集...")
    print()

    for i, (tx, ty, tz, tr) in enumerate(CALIBRATION_POSES):
        print(f"[{i+1}/{len(CALIBRATION_POSES)}] 移动到: x={tx} y={ty} z={tz} r={tr}")

        # 移动机械臂
        if not move_robot(tx, ty, tz, tr):
            print("  跳过此位姿")
            continue

        time.sleep(0.5)  # 等待运动完成并稳定

        # 获取实际位姿
        actual_pose = get_robot_pose()
        if actual_pose is None:
            print("  跳过 — 无法读取位姿")
            continue

        # 读取摄像头 — 丢弃前 8 帧让曝光重新稳定，取第 9 帧
        frame = None
        for attempt in range(10):
            ret, f = cap.read()
            if ret:
                frame = f
            time.sleep(0.05)
        ret = frame is not None

        if not ret:
            print("  跳过 — 摄像头读取失败")
            continue

        # 检测 ArUco
        rvec, tvec = detect_aruco(frame, K, D)
        if rvec is None:
            # 保存失败帧用于调试
            debug_path = os.path.join(os.path.dirname(__file__), f"debug_pose_{i+1}.jpg")
            cv2.imwrite(debug_path, cv2.resize(frame, (1280, 720)))
            print(f"  跳过 — 未检测到 ArUco 标记 (已保存 {debug_path})")
            continue

        # 转换旋转向量 → 旋转矩阵
        R_cm, _ = cv2.Rodrigues(rvec)

        # 机械臂末端姿态：MG400 是 4 轴，r 是绕 Z 轴的旋转（度）
        # 将 (x, y, z, r_deg) 转换为 4×4 变换矩阵
        ax, ay, az = actual_pose[0], actual_pose[1], actual_pose[2]
        ar_rad = np.deg2rad(actual_pose[3])

        # 末端在基坐标系下的旋转矩阵 (绕 Z 轴旋转 + 可能的固定 RPY)
        # MG400: 末端姿态简化为 Z轴旋转 (r)
        R_be = np.array([
            [np.cos(ar_rad), -np.sin(ar_rad), 0],
            [np.sin(ar_rad),  np.cos(ar_rad), 0],
            [0,               0,              1],
        ])
        t_be = np.array([[ax], [ay], [az]])

        R_base_to_ee.append(R_be)
        t_base_to_ee.append(t_be)
        R_cam_to_marker.append(R_cm)
        t_cam_to_marker.append(tvec)
        successful_poses.append(i + 1)

        # 保存调试图 (画上检测到的标记和坐标轴)
        debug_frame = cv2.resize(frame, (1280, 720))
        # 重检测以获取用于绘图的数据
        gray_d = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        detector_d = cv2.aruco.ArucoDetector(
            cv2.aruco.getPredefinedDictionary(ARUCO_DICT),
            cv2.aruco.DetectorParameters()
        )
        corners_d, ids_d, _ = detector_d.detectMarkers(gray_d)
        if corners_d:
            cv2.aruco.drawDetectedMarkers(debug_frame, corners_d, ids_d)
        debug_path = os.path.join(os.path.dirname(__file__), f"debug_pose_{i+1}.jpg")
        cv2.imwrite(debug_path, debug_frame)

        print(f"  成功! 实际位姿: x={ax:.1f} y={ay:.1f} z={az:.1f} r={ar_rad*180/np.pi:.1f}°")

    cap.release()

    print()
    print(f"采集完成: {len(successful_poses)}/{len(CALIBRATION_POSES)} 个位姿成功")

    if len(successful_poses) < 5:
        print("[ERROR] 有效数据不足，至少需要 5 个位姿")
        return

    # ---- 手眼标定 ----
    print()
    print("正在计算手眼标定...")

    R_be = np.array(R_base_to_ee)
    t_be = np.array(t_base_to_ee)
    R_cm = np.array(R_cam_to_marker)
    t_cm = np.array(t_cam_to_marker)

    # cv2.calibrateHandEye 求解 AX = XB
    # A = T_base_to_ee (机器人末端在基坐标系)
    # B = T_cam_to_marker (标记在相机坐标系)
    # 求解 X = T_base_to_cam (相机在基坐标系)
    R_cam_to_base, t_cam_to_base = cv2.calibrateHandEye(
        R_be, t_be.squeeze(2),
        R_cm, t_cm.squeeze(2),
        method=cv2.CALIB_HAND_EYE_TSAI,
    )

    print(f"  旋转矩阵 R_base_to_cam:")
    print(f"    [{R_cam_to_base[0,0]:.4f}  {R_cam_to_base[0,1]:.4f}  {R_cam_to_base[0,2]:.4f}]")
    print(f"    [{R_cam_to_base[1,0]:.4f}  {R_cam_to_base[1,1]:.4f}  {R_cam_to_base[1,2]:.4f}]")
    print(f"    [{R_cam_to_base[2,0]:.4f}  {R_cam_to_base[2,1]:.4f}  {R_cam_to_base[2,2]:.4f}]")
    print(f"  平移 t_base_to_cam (mm):")
    print(f"    [{t_cam_to_base[0,0]:.1f}, {t_cam_to_base[1,0]:.1f}, {t_cam_to_base[2,0]:.1f}]")

    # 保存到 YAML
    with open(CONFIG_YAML, "r", encoding="utf-8") as f:
        calib_data = yaml.safe_load(f)

    calib_data["extrinsics"] = {
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "method": "cv2.CALIB_HAND_EYE_TSAI",
        "num_poses": len(successful_poses),
        "marker": {
            "dictionary": "DICT_6X6_250",
            "id": ARUCO_ID,
            "size_mm": MARKER_SIZE_MM,
        },
        "T_base_to_cam": {
            "R": R_cam_to_base.tolist(),
            "t": t_cam_to_base.flatten().tolist(),
        },
    }

    with open(CONFIG_YAML, "w", encoding="utf-8") as f:
        yaml.dump(calib_data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    print()
    print(f"  外参已保存到: {CONFIG_YAML}")
    print()
    print("  下一步: python calibrate_workspace.py (工作台平面标定)")


if __name__ == "__main__":
    main()
