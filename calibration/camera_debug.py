"""
摄像头快速排查 — 找出能用的 camera index 和后端
用法: python camera_debug.py
"""
import cv2


def try_open(index, backend, backend_name, width=1920, height=1080):
    """尝试打开摄像头并读取一帧"""
    cap = cv2.VideoCapture(index, backend)
    if not cap.isOpened():
        return None

    # 先不设 MJPG，用默认格式测试
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)

    actual_w = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
    actual_h = cap.get(cv2.CAP_PROP_FRAME_HEIGHT)

    # 尝试读 3 帧（有时第一帧是黑的）
    for attempt in range(3):
        ret, frame = cap.read()
        if ret and frame is not None and frame.shape[0] > 0 and frame.shape[1] > 0:
            mean_val = frame.mean()
            cap.release()
            return {
                "index": index,
                "backend": backend_name,
                "resolution": f"{int(actual_w)}×{int(actual_h)}",
                "actual_shape": f"{frame.shape[1]}×{frame.shape[0]}",
                "mean_brightness": f"{mean_val:.1f}",
            }

    cap.release()
    return None


def main():
    print("=" * 60)
    print("  摄像头排查")
    print("=" * 60)

    # 列出所有可能的摄像头索引和三个常用后端
    backends = [
        (cv2.CAP_DSHOW, "DSHOW (Windows)"),
        (cv2.CAP_MSMF, "MSMF (Windows)"),
        (cv2.CAP_ANY,  "AUTO"),
    ]

    results = []
    for index in range(4):  # 试 0,1,2,3
        for backend, name in backends:
            result = try_open(index, backend, name)
            if result:
                results.append(result)

    print()
    if not results:
        print("  [FAIL] 所有 index/backend 组合都无法打开摄像头！")
        print()
        print("  请检查:")
        print("    1. 摄像头 USB 线是否连好（灯亮了吗？）")
        print("    2. Windows 设备管理器 → 照相机 → 是否有 'Hikvision' 设备")
        print("    3. 是否有其他程序占用摄像头（微信/腾讯会议/浏览器等）")
        print("       → 关掉所有可能用摄像头的程序再试")
        print("    4. 尝试换一个 USB 口")
        print("    5. 如果有多个摄像头，index 可能不是 0")
    else:
        print(f"  找到 {len(results)} 个可用配置:")
        print()
        for r in results:
            print(f"    index={r['index']}  backend={r['backend']}  "
                  f"分辨率={r['resolution']}  实际帧={r['actual_shape']}  "
                  f"亮度={r['mean_brightness']}")
        print()
        best = results[0]
        print(f"  推荐使用: index={best['index']}, backend={best['backend']}")
        print()
        print(f"  测试代码:")
        print(f"    cap = cv2.VideoCapture({best['index']}, cv2.CAP_DSHOW)")
        print(f"    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)")
        print(f"    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)")
        print(f"    ret, frame = cap.read()  # 读一帧")
        print()

    # 额外：检测 Windows 摄像头是否被占用
    print("  如果以上都失败，试一下 Windows 自带'相机'应用:")
    print("    按 Win 键 → 输入 '相机' → 打开 → 看是否有画面")
    print("    如果自带相机也没画面 → 硬件问题")
    print("    如果自带相机有画面 → Python/OpenCV 配置问题")


if __name__ == "__main__":
    main()
