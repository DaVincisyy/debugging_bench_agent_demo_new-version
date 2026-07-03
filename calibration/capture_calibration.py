"""
标定图像采集工具 — 棋盘格拍照
用法: python capture_calibration.py

操作说明 (在终端里输入, 不需要点 OpenCV 窗口):
  回车键 (Enter) → 采集一帧
  c + Enter      → 切换角点检测叠加
  r + Enter      → 重置采集计数
  q + Enter      → 退出

采集建议 (至少 20 张):
  - 棋盘格占据画面 1/4 ~ 1/2
  - 覆盖画面四角 + 中心
  - 覆盖不同倾斜角度 (棋盘格不要始终平行于摄像头)
  - 覆盖不同距离 (10cm ~ 40cm 距离范围)
"""
import cv2
import os
import sys
import msvcrt
from datetime import datetime


def main():
    # 配置参数
    CAMERA_INDEX = 1  # 海康 DS-UVC-E24Sa (2K USB Camera)
    OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "calibration_images")
    CHESSBOARD_SIZE = (9, 6)  # 内角点数 (列, 行) — 9×6 棋盘格
    SQUARE_SIZE_MM = 20       # 每格边长 mm (根据你打印的棋盘格修改!)
    CAPTURE_RESOLUTION = (2560, 1440)  # 海康 2K
    DISPLAY_WIDTH = 960       # 预览窗口宽度

    # ---- 初始化 ----
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_DSHOW)
    if not cap.isOpened():
        print(f"[ERROR] 无法打开摄像头 index={CAMERA_INDEX}")
        sys.exit(1)

    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAPTURE_RESOLUTION[0])
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAPTURE_RESOLUTION[1])

    actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if actual_w < 640 or actual_h < 480:
        print(f"[ERROR] 摄像头分辨率异常: {actual_w}×{actual_h}")
        cap.release()
        sys.exit(1)

    display_scale = DISPLAY_WIDTH / actual_w
    display_h = int(actual_h * display_scale)
    cv2.namedWindow("Calibration Capture", cv2.WINDOW_NORMAL)
    cv2.resizeWindow("Calibration Capture", DISPLAY_WIDTH, display_h)

    captured_count = len([f for f in os.listdir(OUTPUT_DIR) if f.endswith('.jpg')])
    show_corners = False
    last_flash_time = 0

    print("=" * 60)
    print("  棋盘格标定图像采集")
    print(f"  棋盘格: {CHESSBOARD_SIZE[0]}×{CHESSBOARD_SIZE[1]} 内角点, {SQUARE_SIZE_MM}mm/格")
    print(f"  分辨率: {actual_w}×{actual_h}")
    print(f"  保存到: {OUTPUT_DIR}")
    print(f"  已采集: {captured_count} 张")
    print("=" * 60)
    print()
    print("  终端操作 (在此窗口输入, 不需要点 OpenCV 窗口):")
    print("    [Enter]     → 拍一张")
    print("    c [Enter]   → 切换角点检测叠加")
    print("    r [Enter]   → 清空重来")
    print("    q [Enter]   → 退出")
    print()
    print("  请调整棋盘格位置 → 敲回车拍照 → 换位置 → 再敲回车...")
    print()

    while True:
        # 读取一帧
        ret, frame = cap.read()
        if not ret:
            print("[WARN] 读取帧失败,重试...")
            continue

        # 缩小用于显示
        display_frame = cv2.resize(frame, (DISPLAY_WIDTH, display_h))
        overlay = display_frame.copy()

        # 角点检测叠加
        if show_corners:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            found, corners = cv2.findChessboardCorners(
                gray, CHESSBOARD_SIZE,
                cv2.CALIB_CB_ADAPTIVE_THRESH + cv2.CALIB_CB_FAST_CHECK
            )
            if found:
                criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.001)
                corners_sub = cv2.cornerSubPix(gray, corners, (11, 11), (-1, -1), criteria)
                corners_scaled = corners_sub * display_scale
                cv2.drawChessboardCorners(overlay, CHESSBOARD_SIZE, corners_scaled, True)
                cv2.putText(overlay, f"CORNERS OK ({len(corners_sub)})",
                            (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
            else:
                cv2.putText(overlay, "NO CORNERS",
                            (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)

        # 采集闪光
        now = cv2.getTickCount() / cv2.getTickFrequency()
        if last_flash_time > 0 and (now - last_flash_time) < 0.3:
            cv2.rectangle(overlay, (5, 5), (DISPLAY_WIDTH-5, display_h-5), (0, 255, 0), 3)

        cv2.putText(overlay, f"Count: {captured_count} | Corners: {'ON' if show_corners else 'OFF'} | [Enter]=Capture [q]=Quit",
                    (10, display_h - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)

        cv2.imshow("Calibration Capture", overlay)

        # 同时检查 OpenCV 窗口按键 和 终端输入
        key = cv2.waitKey(5) & 0xFF

        # 终端输入 (Windows)
        term_key = None
        if msvcrt.kbhit():
            ch = msvcrt.getch()
            if ch == b'\r':       # Enter
                term_key = 'enter'
            elif ch == b'c':
                term_key = 'c'
            elif ch == b'C':
                term_key = 'c'
            elif ch == b'r':
                term_key = 'r'
            elif ch == b'R':
                term_key = 'r'
            elif ch == b'q':
                term_key = 'q'
            elif ch == b'Q':
                term_key = 'q'
            elif ch == b' ':      # 空格也行
                term_key = 'enter'

        # 处理采集
        if term_key == 'enter' or key == ord(' ') or key == 13:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"calib_{timestamp}_{captured_count+1:03d}.jpg"
            filepath = os.path.join(OUTPUT_DIR, filename)
            cv2.imwrite(filepath, frame)
            captured_count += 1
            last_flash_time = cv2.getTickCount() / cv2.getTickFrequency()

            # 同时存一张带角点叠加的预览图方便检查
            if show_corners:
                preview_name = f"calib_{timestamp}_{captured_count:03d}_corners.jpg"
                cv2.imwrite(os.path.join(OUTPUT_DIR, preview_name), overlay)

            print(f"  [{captured_count}] {filename}  (分辨率: {frame.shape[1]}×{frame.shape[0]})")

        elif term_key == 'c' or key == ord('c'):
            show_corners = not show_corners
            print(f"  角点检测: {'ON (绿色=检测成功)' if show_corners else 'OFF'}")

        elif term_key == 'r' or key == ord('r'):
            print("  确认清空所有已采集图片? 输入 yes: ", end="", flush=True)
            # 等待终端输入 yes
            confirm = input().strip().lower()
            if confirm == 'yes':
                for f in os.listdir(OUTPUT_DIR):
                    if f.endswith('.jpg'):
                        os.remove(os.path.join(OUTPUT_DIR, f))
                captured_count = 0
                print("  已清空。")
            else:
                print("  取消。")

        elif term_key == 'q' or key == ord('q') or key == 27:
            break

    cap.release()
    cv2.destroyAllWindows()

    print()
    print(f"采集完成。共 {captured_count} 张图片，保存在:")
    print(f"  {OUTPUT_DIR}")
    if captured_count < 10:
        print(f"  [WARN] 建议至少 20 张！当前仅 {captured_count} 张。")
    elif captured_count < 20:
        print(f"  [INFO] 建议 20 张以上。当前 {captured_count} 张。")
    else:
        print(f"  [OK] {captured_count} 张充足。下一步: python calibrate_intrinsics.py")


if __name__ == "__main__":
    main()
