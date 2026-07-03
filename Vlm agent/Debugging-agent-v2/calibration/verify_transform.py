"""
验证坐标转换精度

用法:
  1. 把 ArUco 标记放在工作台桌面上任意位置
  2. 运行: python verify_transform.py
  3. 脚本检测 ArUco → 算出世界坐标 (X,Y) → 移动机械臂到该位置
  4. 观察机械臂末端是否对准 ArUco 标记
"""
import cv2
import numpy as np
import os
import sys
import time
import requests
import yaml

from coordinate_transforms import PixelToWorld

# 配置
CAMERA_INDEX = 1
ROBOT_GATEWAY_URL = "http://127.0.0.1:8010"
ARUCO_DICT = cv2.aruco.DICT_6X6_250
ARUCO_ID = 0
MARKER_SIZE_MM = 40
TABLE_Z = -228.0
CAMERA_RESOLUTION = (2560, 1440)

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


def get_robot_pose():
    resp = requests.post(f"{ROBOT_GATEWAY_URL}/v1/robot/status",
                         json={"config": MG400_CONFIG}, timeout=5)
    data = resp.json()
    pose = data.get("robot", {}).get("pose", {})
    return (pose.get("x", 0), pose.get("y", 0), pose.get("z", 0), pose.get("r", 0))


def move_robot(x, y, z, r=0):
    payload = {
        "config": MG400_CONFIG,
        "command": {
            "name": "move",
            "pose": {"x": x, "y": y, "z": z, "r": r},
            "motionCommand": "MovJ",
        },
    }
    resp = requests.post(f"{ROBOT_GATEWAY_URL}/v1/robot/command",
                         json=payload, timeout=15)
    data = resp.json()
    return data.get("ok", False)


def detect_aruco_on_table(frame, converter):
    """检测桌面上 ArUco 标记的世界坐标"""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    aruco_dict = cv2.aruco.getPredefinedDictionary(ARUCO_DICT)
    detector = cv2.aruco.ArucoDetector(aruco_dict, cv2.aruco.DetectorParameters())
    corners, ids, _ = detector.detectMarkers(gray)

    if ids is None or ARUCO_ID not in ids:
        return None, None

    idx = list(ids.flatten()).index(ARUCO_ID)
    marker_corners = corners[idx].reshape(4, 2)

    # 标记中心像素
    cx = float(np.mean(marker_corners[:, 0]))
    cy = float(np.mean(marker_corners[:, 1]))

    # 像素 → 桌面世界坐标
    world = converter.pixel_to_table(cx, cy)
    return world, (cx, cy)


def main():
    print("=" * 60)
    print("  坐标转换精度验证")
    print("=" * 60)

    # 初始化转换器
    config_path = os.path.join(os.path.dirname(__file__), "camera_config.yaml")
    converter = PixelToWorld(config_path, table_z=TABLE_Z)

    # 打开摄像头
    cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_DSHOW)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAMERA_RESOLUTION[0])
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAMERA_RESOLUTION[1])

    # 预热 — 等自动曝光稳定
    print("预热摄像头 (等待自动曝光)...")
    for _ in range(60):
        cap.read()
        time.sleep(0.02)
    time.sleep(1.0)
    # 再读 30 帧确保画面稳定
    for _ in range(30):
        cap.read()
        time.sleep(0.02)

    # 实时预览，确认画面清晰
    print()
    print("预览画面 (按 q 确认清晰后继续, 按 s 跳过等待):")
    cv2.namedWindow("Verify Preview", cv2.WINDOW_NORMAL)
    cv2.resizeWindow("Verify Preview", 960, 540)
    frame = None
    while True:
        ret, f = cap.read()
        if ret:
            frame = f
            preview = cv2.resize(frame, (960, 540))
            cv2.putText(preview, "Press 'q' if clear, 's' to skip",
                        (10, 520), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 1)
            cv2.imshow("Verify Preview", preview)
        key = cv2.waitKey(30) & 0xFF
        if key == ord('q') or key == ord('s'):
            break
    cv2.destroyWindow("Verify Preview")

    if frame is None:
        print("[ERROR] 无法读取摄像头")
        cap.release()
        return

    # 检测 ArUco
    print("检测桌面上的 ArUco 标记...")

    world_xyz, pixel_center = detect_aruco_on_table(frame, converter)

    if world_xyz is None:
        print("[FAIL] 未检测到 ArUco 标记！")
        print("请把 ArUco 标记 (40mm 那个) 放在桌面上可见位置，重新运行")
        # 保存当前画面用于调试
        cv2.imwrite("verify_debug.jpg", cv2.resize(frame, (1280, 720)))
        print("已保存 verify_debug.jpg 用于排查")
        cap.release()
        return

    wx, wy, wz = world_xyz
    print()
    print(f"  ArUco 像素位置: ({pixel_center[0]:.0f}, {pixel_center[1]:.0f})")
    print(f"  推算世界坐标:   X={wx:.1f}  Y={wy:.1f}  Z={wz:.1f} mm")
    print()

    # 检查可达性
    radius = np.hypot(wx, wy)
    if radius > 450 or radius < 130:
        print(f"[WARN] 推算位置超出工作范围 (r={radius:.0f}mm)，跳过机械臂验证")
        cap.release()
        return

    # 移动到目标上方 50mm 处
    hover_z = wz + 50  # 先悬停在桌面上方 50mm
    print(f"移动机械臂到 ArUco 上方 50mm: X={wx:.1f} Y={wy:.1f} Z={hover_z:.1f}")
    if not move_robot(wx, wy, hover_z, 0):
        print("[FAIL] 移动失败")
        cap.release()
        return

    time.sleep(1)
    print("  已到达悬停位置。请观察机械臂末端是否在 ArUco 标记正上方。")
    print()

    # 保存验证图片
    _, verify_frame = cap.read()
    if verify_frame is not None:
        display = cv2.resize(verify_frame, (1280, 720))
        # 画检测到的位置
        px, py = int(pixel_center[0] * 1280 / CAMERA_RESOLUTION[0]), \
                 int(pixel_center[1] * 720 / CAMERA_RESOLUTION[1])
        cv2.circle(display, (px, py), 15, (0, 255, 0), 3)
        cv2.putText(display, f"Target: ({wx:.0f},{wy:.0f})",
                    (px + 20, py), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
        cv2.imwrite("verify_result.jpg", display)
        print("  已保存 verify_result.jpg")

    cap.release()
    print()
    print("=" * 60)
    print("  验证完成")
    print("=" * 60)


if __name__ == "__main__":
    main()
