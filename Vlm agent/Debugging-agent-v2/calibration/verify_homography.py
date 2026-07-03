"""
验证单应性标定精度

用法:
  1. 把 ArUco 放在桌面上任意位置
  2. 运行: python verify_homography.py
  3. 摄像头检测 → 计算世界坐标 → 显示
  4. 可选: 手动移动机械臂到该位置验证
"""
import cv2
import numpy as np
import os
import sys
import time
import requests
import yaml

CAMERA_INDEX = 1
ROBOT_GATEWAY_URL = "http://127.0.0.1:8010"
ARUCO_DICT = cv2.aruco.DICT_6X6_250
ARUCO_ID = 0
CAMERA_RESOLUTION = (2560, 1440)

MG400_CONFIG = {
    "mode": "mg400", "ip": "192.168.2.6",
    "dashboardPort": 29999, "motionPort": 30003,
    "speed": 20, "timeoutMs": 5000, "autoEnable": True,
    "motionCommand": "MovJ",
}


def main():
    config_path = os.path.join(os.path.dirname(__file__), "camera_config.yaml")
    with open(config_path, "r") as f:
        data = yaml.safe_load(f)

    H_data = data.get("table_homography")
    if not H_data:
        print("[ERROR] camera_config.yaml 中没有 table_homography，请先运行 calibrate_table_homography.py")
        return

    H = np.array(H_data["H"])
    table_z = H_data["table_z_mm"]
    mean_err = H_data.get("mean_error_mm", "?")
    print(f"  已加载单应矩阵 H (标定误差: {mean_err} mm)")

    cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_DSHOW)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAMERA_RESOLUTION[0])
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAMERA_RESOLUTION[1])
    for _ in range(60):
        cap.read()
        time.sleep(0.02)
    time.sleep(1)

    while True:
        print()
        print("  把 ArUco 放在桌面上 → 敲回车检测  [q]=退出  [m]=移动到该位置")
        choice = input("  > ").strip().lower()

        if choice == 'q':
            break

        frame = None
        for _ in range(10):
            ret, f = cap.read()
            if ret:
                frame = f
                time.sleep(0.03)
        if frame is None:
            print("  [FAIL] 摄像头")
            continue

        # 检测 ArUco
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        detector = cv2.aruco.ArucoDetector(
            cv2.aruco.getPredefinedDictionary(ARUCO_DICT),
            cv2.aruco.DetectorParameters()
        )
        corners, ids, _ = detector.detectMarkers(gray)
        if ids is None or ARUCO_ID not in ids:
            print("  [FAIL] 未检测到 ArUco")
            cv2.imwrite("verify_debug.jpg", cv2.resize(frame, (1280, 720)))
            print("    保存 verify_debug.jpg")
            continue

        idx = list(ids.flatten()).index(ARUCO_ID)
        c = corners[idx].reshape(4, 2)
        cx = float(np.mean(c[:, 0]))
        cy = float(np.mean(c[:, 1]))

        # 单应性变换
        pt_pixel = np.array([cx, cy, 1.0])
        pt_world = H @ pt_pixel
        pt_world /= pt_world[2]
        wx, wy = pt_world[0], pt_world[1]

        r = np.hypot(wx, wy)
        print(f"    像素: ({cx:.0f}, {cy:.0f})")
        print(f"    世界: X={wx:.1f}  Y={wy:.1f}  Z={table_z}  (r={r:.0f}mm)")

        if choice == 'm':
            if r > 450 or r < 130:
                print(f"    [WARN] 超出工作范围")
                continue
            hover_z = table_z + 30  # 桌面上方 30mm，方便肉眼判断对准
            payload = {
                "config": MG400_CONFIG,
                "command": {
                    "name": "move",
                    "pose": {"x": float(wx), "y": float(wy), "z": hover_z, "r": 0},
                    "motionCommand": "MovJ",
                },
            }
            resp = requests.post(f"{ROBOT_GATEWAY_URL}/v1/robot/command",
                                 json=payload, timeout=15)
            if resp.json().get("ok"):
                print(f"    已移动到 X={wx:.1f} Y={wy:.1f} Z={hover_z}")
            else:
                print(f"    移动失败: {resp.json().get('error')}")

    cap.release()


if __name__ == "__main__":
    main()
