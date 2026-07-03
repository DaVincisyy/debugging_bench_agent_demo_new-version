"""
相机内参标定 — 从棋盘格图片计算相机矩阵和畸变系数
用法: python calibrate_intrinsics.py

输入: calibration_images/ 文件夹中的棋盘格图片
输出: camera_config.yaml (内参矩阵 K + 畸变系数 D)
"""
import cv2
import numpy as np
import os
import yaml
from glob import glob


def calibrate():
    # 配置 — 必须和采集时一致
    CHESSBOARD_SIZE = (9, 6)   # 内角点数 (列, 行)
    SQUARE_SIZE_MM = 20        # 棋盘格每格边长 (mm)
    IMAGE_DIR = os.path.join(os.path.dirname(__file__), "calibration_images")
    OUTPUT_YAML = os.path.join(os.path.dirname(__file__), "camera_config.yaml")

    # 世界坐标系中的棋盘格角点 (z=0 平面)
    objp = np.zeros((CHESSBOARD_SIZE[0] * CHESSBOARD_SIZE[1], 3), np.float32)
    objp[:, :2] = np.mgrid[0:CHESSBOARD_SIZE[0], 0:CHESSBOARD_SIZE[1]].T.reshape(-1, 2)
    objp *= SQUARE_SIZE_MM  # 单位: mm

    # 存储
    obj_points = []   # 3D 世界坐标
    img_points = []   # 2D 像素坐标
    used_files = []
    failed_files = []

    image_files = sorted(glob(os.path.join(IMAGE_DIR, "*.jpg")))

    if not image_files:
        print("[ERROR] calibration_images/ 中没有 jpg 图片")
        return

    print(f"找到 {len(image_files)} 张图片, 开始检测角点...")
    print()

    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.001)
    img_size = None

    for fpath in image_files:
        fname = os.path.basename(fpath)
        img = cv2.imread(fpath)

        if img is None:
            failed_files.append((fname, "无法读取"))
            continue

        if img_size is None:
            img_size = (img.shape[1], img.shape[0])

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        # 检测棋盘格角点
        found, corners = cv2.findChessboardCorners(
            gray, CHESSBOARD_SIZE,
            cv2.CALIB_CB_ADAPTIVE_THRESH + cv2.CALIB_CB_FAST_CHECK
        )

        if not found:
            failed_files.append((fname, "未检测到角点"))
            print(f"  [FAIL] {fname} — 未检测到角点")
            continue

        # 亚像素精确化
        corners_sub = cv2.cornerSubPix(gray, corners, (11, 11), (-1, -1), criteria)

        obj_points.append(objp)
        img_points.append(corners_sub)
        used_files.append(fname)
        print(f"  [OK]   {fname} — {len(corners_sub)} 个角点")

    print()
    print(f"成功: {len(used_files)} 张, 失败: {len(failed_files)} 张")

    if len(used_files) < 10:
        print("[ERROR] 有效图片不足 10 张，无法可靠标定，请重新采集")
        return

    # ---- 标定 ----
    print()
    print("正在标定...")
    ret, K, D, rvecs, tvecs = cv2.calibrateCamera(
        obj_points, img_points, img_size, None, None
    )

    # 重投影误差
    total_error = 0
    errors = []
    for i in range(len(obj_points)):
        projected, _ = cv2.projectPoints(obj_points[i], rvecs[i], tvecs[i], K, D)
        err = cv2.norm(img_points[i], projected, cv2.NORM_L2) / len(projected)
        errors.append(err)
        total_error += err
    mean_error = total_error / len(obj_points)

    print(f"  RMS 重投影误差: {ret:.4f} 像素")
    print(f"  平均重投影误差: {mean_error:.4f} 像素")

    # ---- 输出 ----
    print()
    print("=" * 60)
    print("  标定结果")
    print("=" * 60)
    print()
    print("  相机内参矩阵 K:")
    print(f"    fx={K[0,0]:.2f}  fy={K[1,1]:.2f}")
    print(f"    cx={K[0,2]:.2f}  cy={K[1,2]:.2f}")
    print()
    print("  畸变系数 D:")
    print(f"    k1={D[0,0]:.6f}  k2={D[0,1]:.6f}")
    print(f"    p1={D[0,2]:.6f}  p2={D[0,3]:.6f}")
    print(f"    k3={D[0,4]:.6f}")
    print()

    # 保存 YAML
    calib_data = {
        "calibration": {
            "date": __import__('datetime').datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "camera_model": "Hikvision DS-UVC-E24Sa",
            "resolution": [img_size[0], img_size[1]],
            "chessboard": {
                "pattern": f"{CHESSBOARD_SIZE[0]}x{CHESSBOARD_SIZE[1]}",
                "square_size_mm": SQUARE_SIZE_MM,
                "images_used": len(used_files),
            },
        },
        "intrinsics": {
            "camera_matrix": K.tolist(),
            "dist_coeffs": D.tolist(),
        },
        "quality": {
            "rms_error_px": float(ret),
            "mean_error_px": float(mean_error),
            "per_image_errors_px": [float(e) for e in errors],
        },
    }

    with open(OUTPUT_YAML, "w", encoding="utf-8") as f:
        yaml.dump(calib_data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    print(f"  已保存到: {OUTPUT_YAML}")
    print()

    # 质量判定
    if mean_error < 0.3:
        print("  [GOOD] 标定精度良好 (<0.3 px)")
    elif mean_error < 0.5:
        print("  [OK]   标定精度可接受 (<0.5 px)")
    elif mean_error < 1.0:
        print("  [WARN] 标定精度一般 (<1.0 px)，建议补拍几张低误差图片")
    else:
        print("  [FAIL] 重投影误差 >1.0 px，建议重新采集")

    print()
    print("  下一步: python calibrate_extrinsics.py (外参/手眼标定)")


if __name__ == "__main__":
    calibrate()
