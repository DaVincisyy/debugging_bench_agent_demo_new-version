"""
桌面单应性标定 — 像素 → 桌面世界坐标

原理: 在桌面上标记 4+ 个已知世界坐标的点，
      用摄像头检测这些点的像素位置，
      计算透视变换矩阵 H (3×3)，
      使得 世界坐标 = H × 像素坐标

用法:
  1. 把 ArUco 标记放在桌面上
  2. 手动移动机械臂末端到 ArUco 正上方 (轻触标记)
  3. 读取机械臂坐标 → 这就是该标记的世界坐标
  4. 摄像头检测 ArUco → 得到像素坐标
  5. 重复 4+ 次 (放在不同位置)
  6. 自动计算 H 并保存
"""
import cv2
import numpy as np
import os
import sys
import time
import requests
import yaml

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
    "speed": 20,
    "timeoutMs": 5000,
    "autoEnable": True,
    "motionCommand": "MovJ",
}


def get_robot_pose():
    resp = requests.post(f"{ROBOT_GATEWAY_URL}/v1/robot/status",
                         json={"config": MG400_CONFIG}, timeout=5)
    data = resp.json()
    pose = data.get("robot", {}).get("pose", {})
    return (pose.get("x", 0), pose.get("y", 0), pose.get("z", 0))


def detect_aruco_center(frame):
    """检测 ArUco 标记的像素中心"""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    aruco_dict = cv2.aruco.getPredefinedDictionary(ARUCO_DICT)
    detector = cv2.aruco.ArucoDetector(aruco_dict, cv2.aruco.DetectorParameters())
    corners, ids, _ = detector.detectMarkers(gray)

    if ids is None or ARUCO_ID not in ids:
        return None

    idx = list(ids.flatten()).index(ARUCO_ID)
    marker_corners = corners[idx].reshape(4, 2)
    cx = float(np.mean(marker_corners[:, 0]))
    cy = float(np.mean(marker_corners[:, 1]))
    return (cx, cy)


def main():
    print("=" * 60)
    print("  桌面单应性标定")
    print("=" * 60)
    print()
    print("  操作步骤 (重复 4 次以上):")
    print("    1. 把 ArUco 标记放在桌面上新位置")
    print("    2. 敲回车 → 摄像头拍 ArUco 像素位置")
    print("    3. 手动移动机械臂末端轻触该位置")
    print("    4. 再敲回车 → 记录机械臂世界坐标")
    print("    5. 抬离机械臂, 把 ArUco 移到下一个位置")
    print()

    config_path = os.path.join(os.path.dirname(__file__), "camera_config.yaml")

    # 打开摄像头
    cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_DSHOW)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAMERA_RESOLUTION[0])
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAMERA_RESOLUTION[1])
    # 预热
    print("预热摄像头...")
    for _ in range(60):
        cap.read()
        time.sleep(0.02)
    time.sleep(1)
    print("就绪。")
    print()

    pixel_points = []  # 像素坐标
    world_points = []  # 世界坐标 (仅 XY, Z=TABLE_Z)

    # 临时存储当前这一轮的像素坐标
    pending_pixel = None

    while True:
        print(f"  已采集: {len(pixel_points)} 个点")
        print("  [Enter]=拍照/读坐标  [d]=删上一个  [c]=计算  [q]=退出")
        choice = input("  > ").strip().lower()

        if choice == 'q':
            break

        elif choice == 'd':
            if pixel_points:
                pixel_points.pop()
                world_points.pop()
                print(f"  已删除。剩余 {len(pixel_points)} 个点")
            else:
                print("  没有可删除的点")
            pending_pixel = None

        elif choice == 'c':
            if len(pixel_points) < 4:
                print(f"  [ERROR] 至少需要 4 个点，当前只有 {len(pixel_points)} 个")
                continue
            break

        else:  # Enter
            if pending_pixel is None:
                # 第一步: 拍 ArUco 像素位置 (机械臂还没挡住)
                print("  拍摄 ArUco... (请确保机械臂不遮挡)")
                frame = None
                for _ in range(10):
                    ret, f = cap.read()
                    if ret:
                        frame = f
                        time.sleep(0.03)
                if frame is None:
                    print("  [FAIL] 摄像头读取失败")
                    continue

                pixel_center = detect_aruco_center(frame)
                if pixel_center is None:
                    print("  [FAIL] 未检测到 ArUco! 保存 homography_debug.jpg")
                    cv2.imwrite("homography_debug.jpg", cv2.resize(frame, (1280, 720)))
                    continue

                pending_pixel = pixel_center
                print(f"    像素: ({pixel_center[0]:.0f}, {pixel_center[1]:.0f})")
                print(f"  >>> 现在手动移动机械臂末端轻触该 ArUco, 然后敲回车 <<<")

            else:
                # 第二步: 读机械臂坐标
                rx, ry, rz = get_robot_pose()
                world_points.append([rx, ry])
                pixel_points.append([pending_pixel[0], pending_pixel[1]])
                print(f"    世界: X={rx:.1f}  Y={ry:.1f}  Z={rz:.1f}")
                print(f"  [OK] 点 #{len(pixel_points)}: 像素({pending_pixel[0]:.0f},{pending_pixel[1]:.0f}) → 世界({rx:.1f}, {ry:.1f})")
                pending_pixel = None

    cap.release()

    if len(pixel_points) < 4:
        print("点数不足, 退出。")
        return

    # ---- 计算单应性矩阵 ----
    print()
    print("=" * 60)
    print("  计算单应矩阵...")
    print("=" * 60)

    pixel_pts = np.array(pixel_points, dtype=np.float32)
    world_pts = np.array(world_points, dtype=np.float32)

    H, mask = cv2.findHomography(pixel_pts, world_pts, cv2.RANSAC, 5.0)

    if H is None:
        print("[ERROR] 单应性矩阵计算失败")
        return

    # 验证重投影误差
    print()
    print("  重投影误差:")
    total_err = 0
    for i in range(len(pixel_points)):
        pt_pixel = np.array([pixel_points[i][0], pixel_points[i][1], 1.0])
        pt_world_pred = H @ pt_pixel
        pt_world_pred /= pt_world_pred[2]
        err = np.linalg.norm(pt_world_pred[:2] - world_points[i])
        total_err += err
        inlier = "*" if mask is None or mask[i] else ""
        print(f"    点{i+1}: 误差={err:.1f}mm  {inlier}")
    mean_err = total_err / len(pixel_points)
    print(f"  平均误差: {mean_err:.1f} mm")

    # 保存
    with open(config_path, "r", encoding="utf-8") as f:
        calib_data = yaml.safe_load(f)

    calib_data["table_homography"] = {
        "date": __import__('datetime').datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "method": "cv2.findHomography + RANSAC",
        "table_z_mm": TABLE_Z,
        "num_points": len(pixel_points),
        "mean_error_mm": float(mean_err),
        "H": H.tolist(),
        "point_pairs": [
            {"pixel": [float(p[0]), float(p[1])], "world": [float(w[0]), float(w[1])]}
            for p, w in zip(pixel_points, world_points)
        ],
    }

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(calib_data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    print()
    print(f"  单应矩阵 H 已保存到 {config_path}")
    print()
    if mean_err < 5:
        print("  [GOOD] 精度良好 (<5mm)")
    elif mean_err < 10:
        print("  [OK]   精度可接受 (<10mm)")
    else:
        print("  [WARN] 精度不足，建议增加标定点")
    print()
    print("  下一步: python verify_homography.py (验证精度)")


if __name__ == "__main__":
    main()
