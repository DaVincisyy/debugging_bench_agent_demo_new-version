"""
自动对准 + 高精度标定点采集

原理:
  1. 放 ArUco → 摄像头拍像素
  2. 机械臂粗定位到附近 (不遮挡 ArUco)
  3. 终端微调: w/s=前后  a/d=左右  q/e=上下  [Enter]=确认对准
  4. 记录精确世界坐标 → 与像素配对

用法: python auto_align_calibrate.py
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
TABLE_Z = -228.0
CAMERA_RESOLUTION = (2560, 1440)
SAFE_HOVER_Z = -170  # 安全高度
STEP_XY = 3.0  # XY 微调步长 mm
STEP_Z = 2.0   # Z 微调步长 mm

MG400_CONFIG = {
    "mode": "mg400", "ip": "192.168.2.6",
    "dashboardPort": 29999, "motionPort": 30003,
    "speed": 20, "timeoutMs": 5000, "autoEnable": True,
    "motionCommand": "MovL",  # 微调用直线运动
}


def robot_request(command_name, pose=None):
    payload = {"config": MG400_CONFIG, "command": {"name": command_name}}
    if pose:
        payload["command"]["pose"] = pose
        payload["command"]["motionCommand"] = "MovL"
    resp = requests.post(f"{ROBOT_GATEWAY_URL}/v1/robot/command",
                         json=payload, timeout=15)
    return resp.json()


def get_pose():
    resp = requests.post(f"{ROBOT_GATEWAY_URL}/v1/robot/status",
                         json={"config": MG400_CONFIG}, timeout=5)
    p = resp.json()["robot"]["pose"]
    return (p["x"], p["y"], p["z"], p.get("r", 0))


def jog_to(x, y, z):
    """移动到指定位置 (MovL 直线)"""
    return robot_request("move", {"x": x, "y": y, "z": z, "r": 0})


def detect_aruco(frame):
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    detector = cv2.aruco.ArucoDetector(
        cv2.aruco.getPredefinedDictionary(ARUCO_DICT),
        cv2.aruco.DetectorParameters()
    )
    corners, ids, _ = detector.detectMarkers(gray)
    if ids is None or ARUCO_ID not in ids:
        return None
    idx = list(ids.flatten()).index(ARUCO_ID)
    c = corners[idx].reshape(4, 2)
    return (float(np.mean(c[:, 0])), float(np.mean(c[:, 1])))


def capture_frame(cap):
    """拍一帧稳定画面"""
    frame = None
    for _ in range(8):
        ret, f = cap.read()
        if ret:
            frame = f
            time.sleep(0.03)
    return frame


def pixel_to_world_xy(px, py, H):
    pt = np.array([px, py, 1.0])
    w = H @ pt
    w /= w[2]
    return (w[0], w[1])


def main():
    # 加载单应矩阵
    config_path = os.path.join(os.path.dirname(__file__), "camera_config.yaml")
    with open(config_path, "r") as f:
        data = yaml.safe_load(f)
    H_data = data.get("table_homography")
    if not H_data:
        print("[ERROR] 请先运行 calibrate_table_homography.py")
        return
    H = np.array(H_data["H"])

    # 打开摄像头
    cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_DSHOW)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAMERA_RESOLUTION[0])
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAMERA_RESOLUTION[1])
    print("预热摄像头...")
    for _ in range(60):
        cap.read()
        time.sleep(0.02)
    time.sleep(1)

    pixel_points = []
    world_points = []

    print()
    print("=" * 60)
    print("  自动对准标定采集")
    print("=" * 60)
    print()
    print("  操作流程 (每个点):")
    print("    1. 把 ArUco 放在桌面新位置")
    print("    2. 敲 Enter → 自动粗定位到 ArUco 附近")
    print("    3. 用键盘微调对准:")
    print("       w/s = 前/后 (Y±3mm)    a/d = 左/右 (X±3mm)")
    print("       q/e = 升/降 (Z±2mm)")
    print("    4. 对准后敲 Enter → 记录坐标")
    print("    5. 敲 n 开始下一个点, 敲 c 计算保存")
    print()

    while True:
        print(f"\n  === 已采集 {len(pixel_points)} 个点 ===")
        print("  [Enter]=新点  [c]=计算结果  [d]=删上一个  [q]=退出")
        choice = input("  > ").strip().lower()

        if choice == 'q':
            break
        elif choice == 'd':
            if pixel_points:
                pixel_points.pop(); world_points.pop()
                print(f"  已删除, 剩余 {len(pixel_points)}")
            continue
        elif choice == 'c':
            if len(pixel_points) >= 4:
                break
            print(f"  至少 4 个点, 当前 {len(pixel_points)}")
            continue

        # === Step 1: 拍 ArUco 像素 ===
        print("\n  [Step 1] 拍摄 ArUco...")
        frame = capture_frame(cap)
        if frame is None:
            print("  [FAIL] 摄像头")
            continue
        pixel = detect_aruco(frame)
        if pixel is None:
            print("  [FAIL] 未检测到 ArUco")
            cv2.imwrite("align_debug.jpg", cv2.resize(frame, (1280, 720)))
            continue
        px, py = pixel
        print(f"    像素: ({px:.0f}, {py:.0f})")

        # 用单应性粗估世界坐标
        est_x, est_y = pixel_to_world_xy(px, py, H)
        print(f"    粗估世界: X={est_x:.1f} Y={est_y:.1f}")

        # === Step 2: 粗定位 (移到 ArUco 旁边, 不遮挡) ===
        # 先移到安全高度, 再移到 ArUco 上方偏一点
        print(f"  [Step 2] 移动到 ArUco 附近...")
        r = np.hypot(est_x, est_y)
        if r < 130 or r > 450:
            print(f"    [WARN] 粗估位置超范围 (r={r:.0f})，跳过")
            continue

        # 偏移 40mm 避免遮挡 ArUco
        approach_x = est_x + 40
        approach_y = est_y
        result = jog_to(approach_x, approach_y, SAFE_HOVER_Z)
        if not result.get("ok"):
            print(f"    移动失败: {result.get('error')}")
            continue
        time.sleep(0.5)

        # 下探到接近桌面
        approach_z = TABLE_Z + 50  # 桌面上 50mm
        result = jog_to(approach_x, approach_y, approach_z)
        if not result.get("ok"):
            print(f"    下探失败: {result.get('error')}")
            continue
        time.sleep(0.3)

        # === Step 3: 微调 ===
        print(f"\n  [Step 3] 微调对准 (靠近 ArUco 后用小幅步进):")
        print(f"    大步:  WS=前后(Y)±10mm  AD=左右(X)±10mm  QE=升降(Z)±5mm")
        print(f"    小步:  ws=前后(Y)±2mm   ad=左右(X)±2mm   qe=升降(Z)±1mm")
        print(f"    [Enter]=确认对准  [r]=放弃回安全高度")

        while True:
            rx, ry, rz, _ = get_pose()
            print(f"    当前: X={rx:.1f} Y={ry:.1f} Z={rz:.1f} | [Enter]=确认 WS/AD=大步 ws/ad=小步 QE/qe=升降 r=放弃")
            cmd = input("    微调 > ")

            if cmd == '':
                rx, ry, rz, _ = get_pose()
                world_points.append([rx, ry])
                pixel_points.append([px, py])
                print(f"    [OK] 点 #{len(pixel_points)}: 像素({px:.0f},{py:.0f}) → 世界({rx:.1f},{ry:.1f})")
                break
            elif cmd == 'r':
                jog_to(rx, ry, SAFE_HOVER_Z)
                print("    已回安全高度。")
                break

            # 解析步长: 大写=大步, 小写=小步
            step_xy = 10 if cmd in 'WSAD' else 2 if cmd in 'wsad' else 0
            step_z = 5 if cmd in 'QE' else 1 if cmd in 'qe' else 0

            if cmd in ('W', 'w'):
                jog_to(rx, ry + step_xy, rz)
            elif cmd in ('S', 's'):
                jog_to(rx, ry - step_xy, rz)
            elif cmd in ('D', 'd'):
                jog_to(rx + step_xy, ry, rz)
            elif cmd in ('A', 'a'):
                jog_to(rx - step_xy, ry, rz)
            elif cmd in ('E', 'e'):
                jog_to(rx, ry, rz + step_z)
            elif cmd in ('Q', 'q'):
                if rz - step_z >= TABLE_Z - 10:
                    jog_to(rx, ry, rz - step_z)
                else:
                    print(f"    [WARN] 太接近桌面! 最低 Z={TABLE_Z}")
            else:
                print("    未知: WSAD=大步(10/5mm) wsad(字母小写)=小步(2/1mm) QE=升降 Enter=确认")
            time.sleep(0.15)

    cap.release()

    if len(pixel_points) < 4:
        print("点数不足, 退出。")
        return

    # ---- 计算单应性矩阵 ----
    print("\n" + "=" * 60)
    print("  计算新单应矩阵...")

    pixel_pts = np.array(pixel_points, dtype=np.float32)
    world_pts = np.array(world_points, dtype=np.float32)
    H_new, mask = cv2.findHomography(pixel_pts, world_pts, cv2.RANSAC, 3.0)

    if H_new is None:
        print("[ERROR] 计算失败")
        return

    # 验证
    total_err = 0
    for i in range(len(pixel_points)):
        pt = np.array([pixel_points[i][0], pixel_points[i][1], 1.0])
        w = H_new @ pt; w /= w[2]
        err = np.linalg.norm(w[:2] - world_points[i])
        total_err += err
        inlier = "*" if mask is None or mask[i] else ""
        print(f"    点{i+1}: 误差={err:.1f}mm {inlier}")
    mean_err = total_err / len(pixel_points)
    print(f"  平均误差: {mean_err:.1f} mm")

    # 保存
    with open(config_path, "r", encoding="utf-8") as f:
        calib_data = yaml.safe_load(f)

    calib_data["table_homography"] = {
        "date": __import__('datetime').datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "method": "auto-align + cv2.findHomography + RANSAC",
        "table_z_mm": TABLE_Z,
        "num_points": len(pixel_points),
        "mean_error_mm": float(mean_err),
        "H": H_new.tolist(),
        "point_pairs": [
            {"pixel": [float(p[0]), float(p[1])], "world": [float(w[0]), float(w[1])]}
            for p, w in zip(pixel_points, world_points)
        ],
    }

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(calib_data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    print(f"\n  已保存到 {config_path}")
    print(f"  下一步: python verify_homography.py")


if __name__ == "__main__":
    main()
