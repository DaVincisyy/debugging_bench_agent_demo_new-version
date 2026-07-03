#!/usr/bin/env python3
"""
YOLO-only threshold test script.

Tests different confidence thresholds on a single image to determine
if YOLO can detect objects or if the issue is threshold/model related.

Usage:
    python scripts/yolo_only_test.py \
        --image workspace/perception_debug/scan_XXX/00_frame.jpg \
        --confs 0.05 0.10 0.15 0.25
"""

import argparse
import json
import os
import sys
from pathlib import Path

import cv2
import numpy as np


def imwrite(filepath, img):
    """Save image handling Chinese/Unicode paths on Windows."""
    _, buf = cv2.imencode(
        os.path.splitext(filepath)[1],
        img,
        [int(cv2.IMWRITE_JPEG_QUALITY), 90] if filepath.endswith('.jpg') else []
    )
    buf.tofile(filepath)


def run_yolo_test(image_path, model_path, conf_thresh, out_dir):
    """Run YOLO inference with specific confidence threshold."""
    from ultralytics import YOLO

    model = YOLO(model_path)
    class_names = model.names

    # Load image
    frame = cv2.imread(image_path)
    if frame is None:
        raise FileNotFoundError(f"Cannot load image: {image_path}")

    H, W = frame.shape[:2]

    # Run inference
    results = model(frame, conf=conf_thresh, verbose=False)

    # Collect detections
    detections = []
    combined_mask = np.zeros((H, W), dtype=np.uint8)

    for r in results:
        if r.masks is None:
            continue

        masks_arr = r.masks.data
        if hasattr(masks_arr, "cpu"):
            masks_arr = masks_arr.cpu().numpy()
        else:
            masks_arr = np.array(masks_arr)

        boxes = r.boxes
        for i, seg in enumerate(masks_arr):
            cls_id = int(boxes.cls[i]) if boxes is not None and boxes.cls is not None else -1
            conf = float(boxes.conf[i]) if boxes is not None and boxes.conf is not None else 1.0

            # Resize mask to match frame size if needed
            if seg.shape != (H, W):
                seg = cv2.resize(seg, (W, H), interpolation=cv2.INTER_NEAREST)

            # Create binary mask
            binary_seg = (seg > 0.5).astype(np.uint8) * 255
            combined_mask = cv2.bitwise_or(combined_mask, binary_seg)

            # Get bbox
            bbox = boxes.xyxy[i].cpu().numpy() if boxes is not None else []
            bbox_xyxy = bbox.tolist() if len(bbox) else []

            mask_area = int((binary_seg > 0).sum())

            detections.append({
                "class_id": cls_id,
                "class_name": class_names.get(cls_id, f"class_{cls_id}"),
                "confidence": round(conf, 4),
                "bbox_xyxy": [int(v) for v in bbox_xyxy],
                "mask_area_px": mask_area,
            })

    # Save mask
    mask_path = os.path.join(out_dir, f"mask_conf_{int(conf_thresh * 100):03d}.png")
    imwrite(mask_path, combined_mask)

    # Save overlay
    overlay = frame.copy()
    if detections:
        # Draw masks
        mask_color = np.zeros_like(frame)
        mask_color[combined_mask > 0] = [0, 255, 0]  # green
        overlay = cv2.addWeighted(overlay, 0.8, mask_color, 0.2, 0)

        # Draw bboxes and labels
        for det in detections:
            x1, y1, x2, y2 = det["bbox_xyxy"]
            name = det["class_name"]
            conf = det["confidence"]

            cv2.rectangle(overlay, (x1, y1), (x2, y2), (255, 255, 0), 2)
            label = f"{name} {conf:.2f}"
            cv2.putText(overlay, label, (x1, y1 - 5),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 0), 1)

    overlay_path = os.path.join(out_dir, f"overlay_conf_{int(conf_thresh * 100):03d}.jpg")
    imwrite(overlay_path, overlay)

    # Save detections JSON
    total_mask_area = int((combined_mask > 0).sum())
    det_json = {
        "conf_threshold": conf_thresh,
        "image_path": image_path,
        "model_path": model_path,
        "image_shape": [H, W],
        "detections": detections,
        "total_detections": len(detections),
        "total_mask_area_px": total_mask_area,
    }

    json_path = os.path.join(out_dir, f"detections_conf_{int(conf_thresh * 100):03d}.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(det_json, f, indent=2, ensure_ascii=False)

    return det_json


def main():
    parser = argparse.ArgumentParser(description="YOLO-only threshold test")
    parser.add_argument("--image", required=True, help="Input image path")
    parser.add_argument("--model", default=None, help="YOLO model path (default: bundled yolov8n-seg.pt)")
    parser.add_argument("--out", default=None, help="Output directory (default: same as image)")
    parser.add_argument("--confs", type=float, nargs="+", default=[0.05, 0.10, 0.15, 0.25],
                        help="Confidence thresholds to test")

    args = parser.parse_args()

    # Resolve paths
    image_path = os.path.abspath(args.image)
    if not os.path.exists(image_path):
        print(f"ERROR: Image not found: {image_path}")
        sys.exit(1)

    if args.model:
        model_path = os.path.abspath(args.model)
    else:
        # Use bundled model
        model_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "..", "perception", "yolov8n-seg.pt")
        model_path = os.path.abspath(model_path)

    if not os.path.exists(model_path):
        print(f"ERROR: Model not found: {model_path}")
        sys.exit(1)

    if args.out:
        out_dir = os.path.abspath(args.out)
    else:
        out_dir = os.path.dirname(image_path)

    os.makedirs(out_dir, exist_ok=True)

    print(f"Image: {image_path}")
    print(f"Model: {model_path}")
    print(f"Output: {out_dir}")
    print(f"Testing confs: {args.confs}")
    print()

    # Run tests
    results = []
    for conf in args.confs:
        print(f"Testing conf={conf:.2f}...")
        try:
            result = run_yolo_test(image_path, model_path, conf, out_dir)
            results.append(result)
            print(f"  -> {result['total_detections']} detections, "
                  f"{result['total_mask_area_px']} mask pixels")
        except Exception as e:
            print(f"  -> ERROR: {e}")
            results.append(None)
        print()

    # Print summary table
    print("=" * 60)
    print(f"{'conf':<8} | {'detections':<12} | {'classes':<20} | {'mask_area':<10}")
    print("-" * 60)
    for r in results:
        if r is None:
            print(f"{'ERROR':<8} | {'ERROR':<12} | {'ERROR':<20} | {'ERROR':<10}")
        else:
            classes = ", ".join(set(d["class_name"] for d in r["detections"])) or "none"
            print(f"{r['conf_threshold']:<8.2f} | {r['total_detections']:<12} | "
                  f"{classes[:18]:<20} | {r['total_mask_area_px']:<10}")
    print("=" * 60)
    print()

    # Analysis
    print("Analysis:")
    has_005 = results[0] is not None and results[0]["total_detections"] > 0 if len(results) > 0 else False
    has_025 = results[-1] is not None and results[-1]["total_detections"] > 0 if len(results) > 0 else False

    if has_005 and not has_025:
        print("=> Threshold too high. Consider using conf=0.10~0.15")
    elif not has_005:
        print("=> YOLOv8n-seg cannot detect objects in this view")
        print("   Consider: background subtraction as primary, YOLO as辅助")
    elif has_005 and has_025:
        print("=> YOLO detects at all thresholds. conf=0.25 is safe")
    else:
        print("=> Mixed results. Review individual outputs")


if __name__ == "__main__":
    main()
