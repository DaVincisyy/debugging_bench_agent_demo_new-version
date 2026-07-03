"""
海康威视 DS-UVC-E24Sa 摄像头快速测试
用法: python camera_test.py
验证摄像头能否正常打开，显示各分辨率下的帧率。
"""
import cv2
import time


def test_camera(index=1):
    """测试摄像头各分辨率模式的可用性"""
    print("=" * 60)
    print("  海康威视 DS-UVC-E24Sa 摄像头测试")
    print("=" * 60)

    # 测试不同分辨率和编码格式
    test_configs = [
        # (width, height, fourcc_code, fourcc_name)
        (2560, 1440, cv2.VideoWriter_fourcc(*'MJPG'), 'MJPG'),
        (1920, 1080, cv2.VideoWriter_fourcc(*'MJPG'), 'MJPG'),
        (1280, 720,  cv2.VideoWriter_fourcc(*'MJPG'), 'MJPG'),
        (640,  480,  cv2.VideoWriter_fourcc(*'MJPG'), 'MJPG'),
        # YUV 格式（高分辨率下帧率很低，仅做兼容性测试）
        (640, 480, 0, 'YUV(default)'),
    ]

    working_config = None

    for width, height, fourcc, fourcc_name in test_configs:
        cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)  # Windows 用 DSHOW 后端更稳定
        if not cap.isOpened():
            print(f"  [FAIL] 无法打开摄像头 index={index}")
            continue

        if fourcc != 0:
            cap.set(cv2.CAP_PROP_FOURCC, fourcc)

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)

        actual_w = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
        actual_h = cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
        actual_fourcc = int(cap.get(cv2.CAP_PROP_FOURCC))
        fourcc_str = "".join([chr((actual_fourcc >> i) & 0xFF) for i in range(0, 32, 8)]).strip("\x00")

        # 测量帧率：连续读 30 帧
        start = time.time()
        frames_read = 0
        for _ in range(30):
            ret, _ = cap.read()
            if ret:
                frames_read += 1
        elapsed = time.time() - start
        fps = frames_read / elapsed if elapsed > 0 else 0

        status = "OK" if actual_w == width and actual_h == height and fps > 5 else "WARN"
        print(f"  [{status}] {width}×{height}  fourcc={fourcc_name}({fourcc_str})  "
              f"actual={int(actual_w)}×{int(actual_h)}  fps={fps:.1f}")

        if status == "OK" and working_config is None:
            working_config = (width, height, fourcc, fourcc_name)

        cap.release()

    print("-" * 60)

    if working_config is None:
        print("  [FAIL] 未找到可用的摄像头配置！")
        print("  请检查:")
        print("    1. 摄像头 USB 线是否插好")
        print("    2. Windows 设备管理器中是否能看到 'Hikvision' 设备")
        print("    3. 是否有其他程序占用了摄像头")
        return None
    else:
        w, h, fc, fn = working_config
        print(f"  [PASS] 推荐配置: {w}×{h} @ {fn}")
        print(f"  OpenCV 调用代码:")
        print(f"    cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)")
        if fc != 0:
            print(f"    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'{fn}'))")
        print(f"    cap.set(cv2.CAP_PROP_FRAME_WIDTH, {w})")
        print(f"    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, {h})")
        return working_config


def preview_camera(index=1, width=2560, height=1440, use_mjpg=True):
    """打开摄像头预览窗口，按 q 退出"""
    cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
    if not cap.isOpened():
        print(f"无法打开摄像头 index={index}")
        return

    if use_mjpg:
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)

    actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    print(f"预览中... 实际分辨率: {actual_w}×{actual_h}")
    print("按 'q' 键退出预览")
    print("按 's' 键截图保存为 test_snapshot.jpg")

    # 预览窗口缩小显示（2K 全尺寸太大）
    display_w = min(actual_w, 1280)
    display_h = int(actual_h * (display_w / actual_w))
    cv2.namedWindow("Camera Preview", cv2.WINDOW_NORMAL)
    cv2.resizeWindow("Camera Preview", display_w, display_h)

    while True:
        ret, frame = cap.read()
        if not ret:
            print("读取帧失败")
            break

        cv2.imshow("Camera Preview", frame)
        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'):
            print("退出预览")
            break
        elif key == ord('s'):
            cv2.imwrite("test_snapshot.jpg", frame)
            print(f"已保存: test_snapshot.jpg ({frame.shape[1]}×{frame.shape[0]})")

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    config = test_camera(1)  # 海康 DS-UVC-E24Sa
    if config:
        print("\n是否进入实时预览? (输入 y 确认,其他键跳过)")
        choice = input("> ").strip().lower()
        if choice == 'y':
            w, h, fc, fn = config
            preview_camera(1, w, h, use_mjpg=(fn == 'MJPG'))  # 海康 DS-UVC-E24Sa
