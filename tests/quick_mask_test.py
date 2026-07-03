"""快速 mask 验证 — 拍一帧，跑 YOLO，保存带红色 mask 的结果图"""
import sys, os, time, cv2, numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from perception.mask_generator import YoloObstacleMasker, MaskConfig

print("=== Quick Mask Test ===")

# Open camera
cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
time.sleep(0.5)
if not cap.isOpened():
    cap = cv2.VideoCapture(0)
    time.sleep(0.5)
if not cap.isOpened():
    print("ERROR: Cannot open camera")
    sys.exit(1)

# Warmup
for _ in range(30):
    cap.read()
time.sleep(0.3)

# Capture frame
ret, frame = cap.read()
cap.release()
if not ret or frame is None:
    print("ERROR: Cannot capture frame")
    sys.exit(1)

print(f"Captured: {frame.shape}")

# Run YOLO
model_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "perception", "yolov8n-seg.pt")
cfg = MaskConfig(yolo_model_path=model_path)
masker = YoloObstacleMasker(cfg)
result = masker.generate_mask(frame)

print(f"Method: {result.method}")
print(f"Detections: {len(result.detections)}")
for cls_id, conf, bbox in result.detections:
    model_names = masker.get_class_names()
    name = model_names.get(cls_id, str(cls_id))
    x1, y1, x2, y2 = bbox
    print(f"  {name}: conf={conf:.3f}, box=({x1:.0f},{y1:.0f},{x2:.0f},{y2:.0f})")

# Draw result
debug = frame.copy()
mask = result.mask
# Red overlay for mask
overlay = np.zeros_like(debug)
overlay[mask > 0] = [0, 0, 255]  # Red in BGR
debug = cv2.addWeighted(debug, 0.7, overlay, 0.3, 0)

# Draw bounding boxes
for cls_id, conf, bbox in result.detections:
    if len(bbox) == 4:
        x1, y1, x2, y2 = [int(v) for v in bbox]
        cv2.rectangle(debug, (x1, y1), (x2, y2), (0, 255, 255), 2)
        model_names = masker.get_class_names()
        name = model_names.get(cls_id, str(cls_id))
        cv2.putText(debug, f"{name} {conf:.2f}", (x1, y1 - 5),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)

# Save
out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mask_test_result.jpg")
cv2.imwrite(out_path, debug)
print(f"\nResult saved: {out_path}")
print(f"Open this file to see YOLO mask overlay (red = detected)")
print("If no detections, try placing an object (cup, bottle, keyboard, etc.) in view and re-run.")
