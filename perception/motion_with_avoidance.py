"""
障碍物避让运动系统 v3.2 — YOLO-Seg 感知 + 标定配置 + 持久预览 + 调试 dump

Changes v3.1 → v3.2:
- 每次 scan 保存完整调试数据到 perception_debug/scan_YYYYMMDD_HHMMSS/
- 包含原图、YOLO overlay/mask、背景差分、arm mask、final mask、连通域标注
- result.json + diagnostics.json 完整记录每一层的状态
"""
import sys, os, time, math, cv2, requests, numpy as np, msvcrt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from perception.pipeline import PerceptionPlanningPipeline
from perception.debug_dump import dump_scan

ROBOT_URL = "http://127.0.0.1:8010"
CAMERA_CONFIG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "calibration", "camera_config.yaml")
CONFIG = {"mode": "mg400", "ip": "192.168.2.6",
          "dashboardPort": 29999, "motionPort": 30003,
          "speed": 30, "timeoutMs": 5000, "autoEnable": True, "motionCommand": "MovJ"}

HOME = (400, 0, -60, 0)       # 真机安全原点 (工作区边缘)
HOVER_Z = -170                # 真机桌面上方移动高度
PW, PH = 960, 540
DEBUG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "workspace", "perception_debug")


class MotionWithAvoidance:
    def __init__(self):
        print("=" * 50); print("  障碍物避让系统 v3.1 (YOLO-Seg)"); print("=" * 50)
        print("[1/3] YOLO 感知 pipeline...")

        # Load pipeline with camera calibration
        cam_cfg = CAMERA_CONFIG if os.path.exists(CAMERA_CONFIG) else None
        if cam_cfg:
            print(f"    加载标定: {CAMERA_CONFIG}")
            self.pipeline = PerceptionPlanningPipeline(camera_config_path=cam_cfg)
        else:
            print(f"    [WARN] 未找到 {CAMERA_CONFIG}，使用默认标定")
            self.pipeline = PerceptionPlanningPipeline()

        # Check depth estimator
        depth_est = self.pipeline._get_depth_estimator()
        print(f"    深度估计器: {type(depth_est).__name__}")
        print(f"    ONNX 路径: {getattr(depth_est, 'onnx_path', 'N/A')}")
        print(f"    process_res: {getattr(depth_est, 'process_res', 'N/A')}")

        self._masker = self.pipeline._get_mask_generator()
        self._bg_learned = False
        self._camera = None

        print("[2/3] Z抬升规划器...")
        from perception.planner import RRTPlanner
        self._RRTPlanner = RRTPlanner
        print("[3/3] 机械臂...")
        self._check_robot()
        # 持久预览
        cv2.namedWindow("Live Monitor", cv2.WINDOW_NORMAL)
        cv2.resizeWindow("Live Monitor", PW, PH)
        self._overlay = None
        self._overlay_until = 0

    def _check_robot(self):
        try:
            r = requests.post(f"{ROBOT_URL}/v1/robot/status", json={"config": CONFIG}, timeout=5)
            d = r.json()
            if not d.get("ok"): raise RuntimeError(f"连接失败: {d}")
            p = d["robot"]["pose"]
            mode = d["robot"]["mode"]
            label = mode.get("label") or f"mode={mode.get('response','?')}"
            print(f"    状态: {label}  位姿: X={p['x']:.0f} Y={p['y']:.0f} Z={p['z']:.0f}")
        except Exception as e:
            print(f"    [WARN] 机械臂网关连接失败: {e}")
            print(f"    [WARN] 感知层仍可工作，但无法控制机械臂")
            return
        print(f"    状态: {label}  位姿: X={p['x']:.0f} Y={p['y']:.0f} Z={p['z']:.0f}")

    def _get_pose(self):
        r = requests.post(f"{ROBOT_URL}/v1/robot/status", json={"config": CONFIG}, timeout=5)
        p = r.json()["robot"]["pose"]
        return (p["x"], p["y"], p["z"])

    def _move_to(self, x, y, z, r=0):
        self._refresh(); self._refresh()
        payload = {"config": CONFIG, "command": {"name": "move",
                    "pose": {"x": x, "y": y, "z": z, "r": r}, "motionCommand": "MovJ"}}
        r = requests.post(f"{ROBOT_URL}/v1/robot/command", json=payload, timeout=20)
        self._refresh()
        d = r.json()
        if not d.get("ok"): print(f"    [ERR] {d.get('error','?')}"); return False
        return True

    # ------------------------------------------------------------------ #
    #  Perception via YOLO pipeline                                       #
    # ------------------------------------------------------------------ #

    _camera = None

    def _get_or_create_camera(self):
        if self._camera is not None:
            return self._camera

        # Try multiple index + backend combinations
        attempts = [
            (0, cv2.CAP_DSHOW, "index=0, DSHOW"),
            (0, cv2.CAP_ANY,    "index=0, AUTO"),
            (1, cv2.CAP_DSHOW, "index=1, DSHOW"),
            (1, cv2.CAP_ANY,    "index=1, AUTO"),
        ]

        for idx, backend, label in attempts:
            try:
                cap = cv2.VideoCapture(idx, backend)
                time.sleep(0.3)
                if not cap.isOpened():
                    cap.release()
                    continue

                # Verify we can actually read frames
                ret, frame = cap.read()
                if not ret or frame is None:
                    cap.release()
                    time.sleep(0.2)
                    continue

                # Set resolution
                w, h = 640, 480
                if os.path.exists(CAMERA_CONFIG):
                    import yaml
                    with open(CAMERA_CONFIG, encoding="utf-8") as f:
                        cal = yaml.safe_load(f)
                    if "calibration" in cal and "resolution" in cal["calibration"]:
                        w, h = cal["calibration"]["resolution"]

                cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
                time.sleep(0.3)

                # Warmup
                for _ in range(10):
                    cap.read()
                time.sleep(0.2)

                self._camera = cap
                print(f"  [CAMERA] Opened: {label} ({w}x{h})")
                return cap

            except Exception as e:
                print(f"  [CAMERA] Failed {label}: {e}")

        raise RuntimeError("Cannot open any camera. Check if camera is in use by another app.")

    def _capture_frame(self):
        """Capture a frame from the camera (multi-read for stability)."""
        cam = self._get_or_create_camera()
        frame = None
        for _ in range(5):
            ret, f = cam.read()
            if ret and f is not None:
                frame = f
                time.sleep(0.02)
            else:
                # Camera may have crashed — try to reopen
                print("  [_capture] read failed, reopening camera...")
                cam.release()
                self._camera = None
                cam = self._get_or_create_camera()
        return frame

    def learn_background(self, num_frames=30):
        """
        显式背景学习 — 在空桌面上调用，保存参考帧用于背景差分回退。
        同时学习机械臂排除层 (arm_mask)。

        Flow:
            1. 机械臂回 home 位
            2. 桌面清空
            3. 采集 N 帧
            4. 生成 background_reference
            5. 用 YOLO 检测机械臂区域
            6. 生成 arm_mask
            7. 保存调试图
        """
        cam = self._get_or_create_camera()
        print("  [BACKGROUND] 学习空桌面背景...")

        # Warmup
        for _ in range(30):
            cam.read()
        time.sleep(0.5)

        # Accumulate frames for average background
        accum = None
        yolo_masks = []
        for i in range(num_frames):
            ret, frame = cam.read()
            if not ret:
                continue
            frame_f = frame.astype(np.float32)
            if accum is None:
                accum = frame_f
            else:
                cv2.accumulateWeighted(frame_f, accum, 0.05)

            # Run YOLO to detect arm position
            try:
                yolo_result = self._masker.yolo_masker.generate_mask(frame)
                if yolo_result.mask.sum() > 0:
                    yolo_masks.append(yolo_result.mask.copy())
            except Exception:
                pass  # YOLO may fail, ignore

            if i % 10 == 0:
                print(f"    采集 {i+1}/{num_frames}...")
            self._refresh()
            time.sleep(0.05)

        if accum is None:
            print("  [BACKGROUND] [WARN] 无法获取相机帧，背景学习失败")
            return False

        # Set as background for the bg_subtraction fallback
        bg_gray = cv2.cvtColor(accum.astype(np.uint8), cv2.COLOR_BGR2GRAY)
        self._masker.bg_masker._background = bg_gray.astype(np.float32)
        self._bg_learned = True

        # Save reference image
        ref_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "ref_background.jpg")
        cv2.imwrite(ref_path, accum.astype(np.uint8))
        print(f"  [BACKGROUND] 参考帧已保存: {ref_path}")

        # Learn arm_mask from YOLO detections + static zones
        arm_mask = self._learn_arm_mask(yolo_masks, accum.astype(np.uint8))
        print("  [BACKGROUND] 背景学习完成 [OK]")
        return True

    def _learn_arm_mask(self, yolo_masks, reference_frame):
        """Generate arm exclusion mask from YOLO detections + static zones."""
        H, W = reference_frame.shape[:2]
        arm_mask = np.zeros((H, W), dtype=np.uint8)

        # 1. Combine YOLO masks from learning frames
        if yolo_masks:
            # Average YOLO masks to find stable regions
            combined = np.zeros((H, W), dtype=np.float32)
            for m in yolo_masks:
                combined += m.astype(np.float32)
            combined /= len(yolo_masks)

            # Threshold to find stable detections
            stable_mask = (combined > 128).astype(np.uint8) * 255

            # Filter by area (arm should be large)
            contours, _ = cv2.findContours(stable_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for cnt in contours:
                area = cv2.contourArea(cnt)
                if area > 10000:  # Minimum area for arm
                    cv2.drawContours(arm_mask, [cnt], -1, 255, -1)

        # 2. Add static exclusion zones if configured
        if self._masker.arm_masker is not None:
            static_mask = self._masker.arm_masker.generate_exclusion_mask((H, W, 3))
            arm_mask = cv2.bitwise_or(arm_mask, static_mask)

        # 3. Dilate to cover arm body + safety margin
        if arm_mask.sum() > 0:
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (41, 41))
            arm_mask = cv2.dilate(arm_mask, kernel, iterations=2)

        # Save arm_mask
        if arm_mask.sum() > 0:
            debug_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                    "workspace", "perception_debug")
            os.makedirs(debug_dir, exist_ok=True)

            # Save arm mask
            arm_path = os.path.join(debug_dir, "learned_arm_mask.png")
            cv2.imwrite(arm_path, arm_mask)
            print(f"  [BACKGROUND] 机械臂排除层已保存: {arm_path}")

            # Save arm overlay
            overlay = reference_frame.copy()
            overlay_color = np.zeros_like(overlay)
            overlay_color[arm_mask > 0] = [0, 0, 255]  # red
            overlay = cv2.addWeighted(overlay, 0.7, overlay_color, 0.3, 0)
            overlay_path = os.path.join(debug_dir, "learned_arm_overlay.jpg")
            cv2.imwrite(overlay_path, overlay)
            print(f"  [BACKGROUND] 机械臂覆盖层已保存: {overlay_path}")

            # Store in masker for later use
            self._masker._learned_arm_mask = arm_mask
            print(f"  [BACKGROUND] 机械臂排除面积: {arm_mask.sum() // 255} px")
        else:
            print("  [BACKGROUND] [WARN] 未检测到机械臂，arm_mask 为空")
            self._masker._learned_arm_mask = None

        return arm_mask

    def _detect_obstacles(self, frame):
        """
        Run one perception cycle → return obstacles in legacy format
        [{x, y, radius_mm, height_mm, pixel_area}, ...].

        Also collects all intermediate results for debug dump.

        Returns
        -------
        (obs_list, debug_frame, debug_data) where debug_data contains
        all intermediate masks and diagnostics for dump_scan().
        """
        masker = self._masker
        arm_pose = None  # can be set from robot pose if available

        # Run mask pipeline with debug output
        mask_result, debug_info = masker.process_with_debug(frame, arm_pose)

        # Get binary mask from result
        obstacle_mask_arr = mask_result.mask

        # Run depth estimation
        depth_est = self.pipeline._get_depth_estimator()
        print(f"    [DEBUG] 深度估计器: {type(depth_est).__name__}")
        depth_result = depth_est.estimate(frame)
        print(f"    [DEBUG] 深度结果类型: {type(depth_result).__name__}")

        # Handle both old (DepthMap) and new (tuple) return types
        if isinstance(depth_result, tuple):
            depth_map, raw_depth_full, table_median = depth_result
            depth_array = depth_map.depth
            print(f"    [DEBUG] raw_depth_full shape: {raw_depth_full.shape}")
            print(f"    [DEBUG] raw_depth_full min/max: {raw_depth_full.min():.4f}/{raw_depth_full.max():.4f}")
            print(f"    [DEBUG] table_median: {table_median:.4f}")
        else:
            depth_map = depth_result
            depth_array = depth_map.depth
            raw_depth_full = depth_array
            table_median = 0.0

        # Run reconstruction
        recon = self.pipeline._get_reconstructor()
        obstacles = recon.process(depth_array, obstacle_mask_arr)

        # Convert to legacy format
        obs_list = []
        for obs in obstacles:
            hull = obs.footprint
            if hull.shape[0] == 0:
                continue
            cx = float(np.mean(hull[:, 0]))
            cy = float(np.mean(hull[:, 1]))
            radius = float(np.max(np.linalg.norm(hull - np.array([cx, cy]), axis=1)))
            # Use actual pixel area from connected components
            area_px = int((obstacle_mask_arr > 0).sum()) if obstacle_mask_arr is not None else 0
            obs_list.append({
                "x": cx, "y": cy,
                "radius_mm": radius, "height_mm": obs.height_p90,
                "pixel_area": area_px, "confidence": obs.confidence,
                "obstacle_id": obs.cluster_id,
                "source": "3d_reconstruction",
            })

        # Fallback: if 3D reconstruction produced nothing but final_mask has components,
        # convert connected components to 2D obstacles with approximate dimensions.
        if not obs_list and obstacle_mask_arr.sum() > 0:
            num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(
                obstacle_mask_arr, connectivity=8
            )
            min_area = 500  # same as config min_contour_area
            for i in range(1, num_labels):
                area = int(stats[i, cv2.CC_STAT_AREA])
                if area < min_area:
                    continue
                cx = float(centroids[i, 0])
                cy = float(centroids[i, 1])
                # Estimate radius from bounding box
                w, h = stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT]
                radius = max(w, h) / 2.0
                # Default height estimate (conservative)
                height_est = 50.0  # mm — placeholder until real depth is available
                obs_list.append({
                    "x": cx, "y": cy,
                    "radius_mm": radius, "height_mm": height_est,
                    "pixel_area": int(area), "confidence": 0.3,
                    "obstacle_id": i,
                    "source": "2d_mask_fallback",
                })

        # Build final mask and overlay
        final_mask = mask_result.mask.copy()
        final_overlay = frame.copy()
        if final_mask.sum() > 0:
            overlay_red = np.zeros_like(frame)
            overlay_red[final_mask > 0] = [0, 0, 255]
            final_overlay = cv2.addWeighted(final_overlay, 0.7, overlay_red, 0.3, 0)

        # Build depth colormap for debug visualization (use RAW depth)
        depth_colormap = None
        depth_overlay = None
        table_depth_median = 0.0
        depth_direction = "unknown"  # "larger_closer" or "smaller_closer"

        if raw_depth_full is not None:
            d = raw_depth_full.copy()
            H_d, W_d = d.shape
            table_mask = np.zeros((H_d, W_d), dtype=bool)
            table_mask[int(0.65 * H_d):, int(0.1 * W_d):int(0.9 * W_d)] = True

            if table_mask.sum() > 0:
                table_depth_median = float(np.median(d[table_mask]))

                # Determine depth direction by checking obstacle regions
                # If final_mask has areas, check if they have larger or smaller depth than table
                if final_mask.sum() > 0:
                    obstacle_depth = float(np.median(d[final_mask > 0]))
                    if obstacle_depth > table_depth_median:
                        depth_direction = "larger_closer"  # larger value = closer to camera
                    else:
                        depth_direction = "smaller_closer"  # smaller value = closer to camera

            # Normalize raw depth to 0-255 for visualization
            d_min, d_max = d.min(), d.max()
            if d_max > d_min:
                d_norm = ((d - d_min) / (d_max - d_min) * 255).astype(np.uint8)
            else:
                d_norm = np.zeros_like(d, dtype=np.uint8)
            depth_colormap = cv2.applyColorMap(d_norm, cv2.COLORMAP_JET)

            # Depth overlay: semi-transparent raw depth on original frame
            if depth_colormap is not None:
                depth_overlay = cv2.addWeighted(frame, 0.6, depth_colormap, 0.4, 0)

        # Add depth stats to each obstacle
        for obs in obs_list:
            if depth_array is not None:
                # Get mask for this obstacle (connected component)
                num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(
                    final_mask, connectivity=8
                )
                # Find the component closest to obstacle centroid
                ox, oy = int(obs["x"]), int(obs["y"])
                best_label = 0
                best_dist = float("inf")
                for i in range(1, num_labels):
                    cx, cy = int(centroids[i, 0]), int(centroids[i, 1])
                    dist = math.hypot(cx - ox, cy - oy)
                    if dist < best_dist:
                        best_dist = dist
                        best_label = i

                if best_label > 0 and best_label < num_labels:
                    comp_mask = (labels == best_label)
                    comp_raw_depth = raw_depth_full[comp_mask] if raw_depth_full is not None else np.array([])
                    if comp_raw_depth.size > 0:
                        obs["depth_p50"] = float(np.median(comp_raw_depth))
                        obs["depth_p90"] = float(np.percentile(comp_raw_depth, 90))
                        obs["table_depth_median"] = table_depth_median
                        obs["relative_depth_signed"] = float(obs["depth_p50"] - table_depth_median)
                        obs["relative_depth_abs"] = float(abs(obs["depth_p50"] - table_depth_median))
                        obs["depth_direction"] = depth_direction
                        obs["depth_valid"] = True

                        # DA3 risk classification: mask 是否真的有高度差
                        rel = obs["relative_depth_abs"]
                        if rel < 0.02:
                            obs["risk"] = "flat_noise"  # 平面噪声，可能是阴影/线缆
                        elif rel < 0.06:
                            obs["risk"] = "low_obstacle"  # 低矮障碍
                        else:
                            obs["risk"] = "tall_obstacle"  # 真实障碍
                    else:
                        obs["depth_valid"] = False
                        obs["risk"] = "no_depth"
                else:
                    obs["depth_valid"] = False
                    obs["risk"] = "no_depth"
            else:
                obs["depth_valid"] = False
                obs["risk"] = "no_depth"

        # Build obstacle depth stats image
        stats_img = frame.copy()
        if final_mask.sum() > 0 and depth_array is not None:
            num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(
                final_mask, connectivity=8
            )
            min_area = 500
            for i in range(1, num_labels):
                area = int(stats[i, cv2.CC_STAT_AREA])
                if area < min_area:
                    continue
                cx, cy = int(centroids[i, 0]), int(centroids[i, 1])
                comp_mask = (labels == i)
                # Use raw_depth_full consistently for stats visualization
                comp_raw = raw_depth_full[comp_mask] if raw_depth_full is not None else np.array([])

                if comp_raw.size > 0:
                    d_p90 = float(np.percentile(comp_raw, 90))
                    rel_depth = d_p90 - table_depth_median
                    # Draw contour
                    contour_mask = comp_mask.astype(np.uint8) * 255
                    contours, _ = cv2.findContours(contour_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                    if contours:
                        cv2.drawContours(stats_img, contours, -1, (0, 255, 0), 2)
                    # Color based on relative depth
                    color = (0, 255, 0) if rel_depth > 0.005 else (0, 0, 255)
                    cv2.circle(stats_img, (cx, cy), 5, color, -1)
                    label = f"#{i} {area}px\np90={d_p90:.3f}\nrel={rel_depth:.3f}"
                    y_offset = 0
                    for line in label.split("\n"):
                        cv2.putText(stats_img, line, (cx - 40, cy - 15 + y_offset),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.35, color, 1)
                        y_offset += 12

        # Collect debug data
        debug_data = {
            "frame": frame.copy(),
            "yolo_mask": debug_info.get("yolo_mask"),
            "yolo_detections": debug_info.get("yolo_detections", []),
            "yolo_result": mask_result,
            "yolo_status": debug_info.get("yolo_status", "unknown"),
            "yolo_error": debug_info.get("yolo_error"),
            "yolo_raw_mask_area_px": debug_info.get("yolo_raw_mask_area_px", 0),
            "bg_diff": debug_info.get("bg_diff"),
            "bg_mask": debug_info.get("bg_mask"),
            "bg_computed": debug_info.get("background_computed", False),
            "bg_mask_area_px": debug_info.get("background_mask_area_px", 0),
            "arm_mask": debug_info.get("arm_mask"),
            "arm_enabled": debug_info.get("arm_exclusion_enabled", False),
            "arm_zones_count": debug_info.get("arm_exclusion_zones_count", 0),
            "arm_mask_area_px": debug_info.get("arm_mask_area_px", 0),
            "candidate_union": debug_info.get("candidate_union"),
            "yolo_only_area_px": debug_info.get("yolo_only_area_px", 0),
            "bg_only_area_px": debug_info.get("bg_only_area_px", 0),
            "yolo_and_bg_area_px": debug_info.get("yolo_and_bg_area_px", 0),
            "removed_by_arm_px": debug_info.get("removed_by_arm_px", 0),
            "final_mask": final_mask,
            "final_overlay": final_overlay,
            "final_mask_area_px": debug_info.get("final_mask_area_px", int((final_mask > 0).sum())),
            "depth_array": depth_array,
            "depth_colormap": depth_colormap,
            "depth_overlay": depth_overlay,
            "depth_stats_img": stats_img if final_mask.sum() > 0 else None,
            "depth_residual": depth_map.residual if depth_map else 0,
            "table_depth_median": table_depth_median,
            "raw_depth_full": raw_depth_full,
            "raw_min": float(raw_depth_full.min()) if raw_depth_full is not None else 0,
            "raw_max": float(raw_depth_full.max()) if raw_depth_full is not None else 0,
            "raw_mean": float(raw_depth_full.mean()) if raw_depth_full is not None else 0,
            "depth_direction": depth_direction,
            "obstacles": obs_list,
        }

        return obs_list, final_overlay, debug_data

    def _filter_self(self, obs_list, rx, ry):
        return [o for o in obs_list if math.hypot(o["x"]-rx, o["y"]-ry) > 100]

    def _refresh(self, show_errors=True):
        """刷新 Live Monitor 窗口"""
        try:
            frame = self._capture_frame()
        except Exception as e:
            if show_errors:
                print(f"  [_refresh] capture error: {e}")
            return
        if frame is None:
            if show_errors:
                print("  [_refresh] no frame from camera")
            return
        try:
            display = cv2.resize(frame, (PW, PH))
            if self._overlay is not None and time.time() < self._overlay_until:
                display = cv2.addWeighted(display, 0.5, self._overlay, 0.5, 0)
            cv2.putText(display, "LIVE [YOLO]", (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0,255,0), 1)
            cv2.imshow("Live Monitor", display)
        except Exception as e:
            if show_errors:
                print(f"  [_refresh] display error: {e}")
        cv2.waitKey(5)

    def _show_detection(self, obstacles, debug_frame):
        if debug_frame is not None:
            self._overlay = cv2.resize(debug_frame, (PW, PH))
            self._overlay_until = time.time() + 5.0
        self._refresh()

    def scan(self, quick_confirm=8, save_debug=True):
        """扫描障碍物 — 连续多帧取稳定结果。

        每次 scan 自动保存调试数据到 perception_debug/scan_YYYYMMDD_HHMMSS/
        """
        best_obs = []
        best_dbg = None
        best_debug_data = None
        best_frame = None

        for _ in range(quick_confirm):
            frame = self._capture_frame()
            if frame is None:
                continue
            raw, dbg, debug_data = self._detect_obstacles(frame)
            final_area = int((debug_data["final_mask"] > 0).sum()) if debug_data["final_mask"] is not None else 0

            # Priority 1: more obstacles
            if len(raw) > len(best_obs):
                best_obs = raw
                best_dbg = dbg
                best_debug_data = debug_data
                best_frame = frame

            # Priority 2: larger final mask area (when no obstacles)
            elif len(raw) == 0 and final_area > 0 and best_debug_data is None:
                best_debug_data = debug_data
                best_frame = frame
                best_dbg = dbg

            self._refresh(); time.sleep(0.03)

        self._show_detection(best_obs, best_dbg)

        if not best_obs:
            print("  [SCAN] 未检测到障碍物")
        else:
            print(f"  [SCAN] {len(best_obs)} 个障碍物:")
            for o in best_obs:
                risk = o.get("risk", "unknown")
                rel = o.get("relative_depth_abs", 0)
                print(f"    ({o['x']:.0f},{o['y']:.0f}) R={o['radius_mm']:.0f}mm "
                      f"rel_depth={rel:.3f} [{risk}]")

        # Save debug dump — always save at least first frame's data
        if save_debug and best_debug_data is not None and best_frame is not None:
            try:
                d = best_debug_data
                dump_scan(
                    output_dir=DEBUG_DIR,
                    frame=d["frame"],
                    yolo_result=d["yolo_result"],
                    bg_diff=d["bg_diff"],
                    bg_mask=d["bg_mask"],
                    arm_mask=d["arm_mask"],
                    candidate_union=d.get("candidate_union"),
                    final_mask=d["final_mask"],
                    depth_colormap=d.get("depth_colormap"),
                    depth_overlay=d.get("depth_overlay"),
                    depth_stats_img=d.get("depth_stats_img"),
                    obstacles=best_obs,
                    diagnostics={
                        "scan_frames": quick_confirm,
                        "yolo": {
                            "enabled": True,
                            "status": d.get("yolo_status", "unknown"),  # detected | no_detection | error
                            "model": d["yolo_result"].method if d["yolo_result"] else "none",
                            "conf_thresh": self._masker.config.yolo_conf_thresh,
                            "detections": [
                                {"class": self._class_name(dd[0]), "confidence": dd[1],
                                 "bbox_xyxy": dd[2], "mask_area_px": int((d["yolo_mask"] > 0).sum()) if d["yolo_mask"] is not None else 0}
                                for dd in d["yolo_detections"]
                            ],
                            "yolo_raw_mask_area_px": d.get("yolo_raw_mask_area_px", 0),
                            "error": d.get("yolo_error"),  # None if no error
                        },
                        "background": {
                            "available": self._bg_learned,
                            "computed": d.get("bg_computed", False),
                            "used_for_final": True,  # always used in fusion
                            "diff_threshold": self._masker.config.threshold,
                            "raw_diff_mean": float(d["bg_diff"].mean()) if d["bg_diff"] is not None else 0,
                            "mask_area_px": d.get("bg_mask_area_px", 0),
                        },
                        "arm_exclusion": {
                            "enabled": d.get("arm_enabled", False),
                            "mask_area_px": d.get("arm_mask_area_px", 0),
                            "zones_count": d.get("arm_zones_count", 0),
                        },
                        "final": {
                            "method": d["yolo_result"].method if d["yolo_result"] else "none",
                            "mask_area_px": d.get("final_mask_area_px", 0),
                            "connected_components": 0,
                            "yolo_only_area_px": d.get("yolo_only_area_px", 0),
                            "bg_only_area_px": d.get("bg_only_area_px", 0),
                            "yolo_and_bg_area_px": d.get("yolo_and_bg_area_px", 0),
                            "removed_by_arm_px": d.get("removed_by_arm_px", 0),
                        },
                        "depth": {
                            "backend": type(self.pipeline._get_depth_estimator()).__name__,
                            "model_name": getattr(self.pipeline._get_depth_estimator(), "model_id", "unknown"),
                            "model_path": getattr(self.pipeline._get_depth_estimator(), "onnx_path", "unknown"),
                            "provider": "CPUExecutionProvider",
                            "process_res": getattr(self.pipeline._get_depth_estimator(), "process_res", 0),
                            "is_metric": False,
                            "used_for_control": False,
                            "residual": d["depth_residual"],  # STRONG: fail if missing
                            "raw_min": d["raw_min"],  # STRONG: fail if missing
                            "raw_max": d["raw_max"],  # STRONG: fail if missing
                            "raw_mean": d["raw_mean"],  # STRONG: fail if missing
                            "table_depth_median": d["table_depth_median"],  # STRONG: fail if missing
                            "depth_direction": d.get("depth_direction", "unknown"),
                            "has_colormap": d.get("depth_colormap") is not None,
                            "obstacle_stats": [
                                {
                                    "id": o.get("obstacle_id", -1),
                                    "area_px": o.get("pixel_area", 0),
                                    "depth_p50": o.get("depth_p50", 0),
                                    "depth_p90": o.get("depth_p90", 0),
                                    "relative_depth_signed": o.get("relative_depth_signed", 0),
                                    "relative_depth_abs": o.get("relative_depth_abs", 0),
                                    "risk": o.get("risk", "unknown"),  # DA3 risk classification
                                    "depth_direction": o.get("depth_direction", "unknown"),
                                    "depth_valid": o.get("depth_valid", False),
                                    "source": o.get("source", "unknown"),
                                }
                                for o in best_obs
                            ],
                        },
                    },
                )
                print(f"  [DEBUG] 已保存: {DEBUG_DIR}/scan_*/")
            except Exception as e:
                print(f"  [DEBUG] 保存失败: {e}")

        return best_obs

    def _class_name(self, cls_id):
        """Get COCO class name from id."""
        try:
            names = self._masker.yolo_masker.get_class_names()
            return names.get(cls_id, f"class_{cls_id}")
        except Exception:
            return f"class_{cls_id}"
        return best_obs

    def go_home(self):
        self._move_to(*HOME)

    def go_to(self, gx, gy, gz):
        sx, sy, sz = self._get_pose()
        print(f"\n  >>> ({sx:.0f},{sy:.0f},{sz:.0f}) → ({gx:.0f},{gy:.0f},{gz:.0f})")
        self._refresh()

        # 初始扫描
        obstacles = self.scan()
        # 检查目标位置是否被障碍物占据 → 停在目标上方
        target_blocked = False
        for obs in obstacles:
            if math.hypot(obs["x"]-gx, obs["y"]-gy) < obs["radius_mm"] + 60:
                print(f"  [WARN] 目标位置有障碍物! ({obs['x']:.0f},{obs['y']:.0f}) R={obs['radius_mm']:.0f} H={obs['height_mm']:.0f}")
                print(f"    将停在目标上方 {HOVER_Z}mm 处。")
                target_blocked = True
        if target_blocked:
            gz = HOVER_Z  # 目标 Z 改为悬停高度

        # Plan path using obstacle map
        waypoints = self._plan_path((sx, sy, sz), (gx, gy, gz), obstacles)
        if not waypoints:
            print("  [FAIL] 无法规划"); return False
        print(f"  路径: {len(waypoints)} 点")
        for i, wp in enumerate(waypoints):
            print(f"    {i+1}. ({wp[0]:.0f},{wp[1]:.0f},{wp[2]:.0f})")

        idx, replans = 0, 0
        while idx < len(waypoints):
            cur = self._get_pose()
            # 跳过已到点
            while idx < len(waypoints):
                if math.hypot(waypoints[idx][0]-cur[0], waypoints[idx][1]-cur[1]) > 5:
                    break
                idx += 1
            if idx >= len(waypoints):
                print("  [OK] 到达!"); return True

            wx, wy, wz = waypoints[idx]
            is_last = (idx == len(waypoints) - 1)

            # 最后一步(下探)前重新扫描
            if is_last and abs(wz - gz) < 5:
                self._refresh()
                print(f"  → 最终下探前扫描...")
                frame = self._capture_frame()
                final_obs, _ = self._detect_obstacles(frame) if frame is not None else ([], None)
                final_obs = self._filter_self(final_obs, wx, wy)
                for obs in final_obs:
                    if math.hypot(obs["x"]-gx, obs["y"]-gy) < obs["radius_mm"] + 50:
                        print(f"  [WARN] 目标位置有障碍物! 停止下探。")
                        return False

            print(f"  → [{idx+1}/{len(waypoints)}] ({wx:.0f},{wy:.0f},{wz:.0f})")

            if not self._move_to(wx, wy, wz):
                return False
            # 移动后刷新
            for _ in range(6):
                self._refresh(); time.sleep(0.05)

            cur = self._get_pose()
            remaining = waypoints[idx+1:]
            if not remaining:
                idx += 1; continue

            # 途中检测
            frame = self._capture_frame()
            real_obs, _ = self._detect_obstacles(frame) if frame is not None else ([], None)
            real_obs = self._filter_self(real_obs, cur[0], cur[1])

            blocked = self._check_path_blocked(cur, remaining[0], real_obs) if real_obs else False

            if blocked and replans < 3:
                print(f"  [WARN] 阻挡! 重规划 ({len(real_obs)}障碍物, 第{replans+1}次)")
                replans += 1
                new_wp = self._plan_path(cur, (gx, gy, gz), real_obs)
                if new_wp:
                    waypoints = new_wp; idx = 0
                    for _ in range(10): self._refresh(); time.sleep(0.03)
                    continue
            idx += 1
        print("  [OK] 到达!"); return True

    # ------------------------------------------------------------------ #
    #  Planner helpers (2.5D Z-lift strategy, simplified)                 #
    # ------------------------------------------------------------------ #

    def _plan_path(self, start, goal, obstacles, hover_z=HOVER_Z):
        """Plan a Z-lift path around obstacles using the obstacle grid map."""
        sx, sy, sz = start
        gx, gy, gz = goal

        # Build obstacle list for RRT
        obs_boxes = []
        for obs in obstacles:
            xmin = obs["x"] - obs["radius_mm"]
            ymin = obs["y"] - obs["radius_mm"]
            xmax = obs["x"] + obs["radius_mm"]
            ymax = obs["y"] + obs["radius_mm"]
            obs_boxes.append((np.array([xmin, ymin]), np.array([xmax, ymax])))

        # Use RRT for 2D planning
        bounds = ((-500, 500), (-500, 500))
        rrt = self._RRTPlanner(workspace_bounds=bounds, obstacles=obs_boxes)
        path_2d = rrt.plan((sx, sy), (gx, gy), max_iter=1000, goal_bias=0.1)

        if path_2d is None:
            return None

        # Convert to 3D waypoints with Z-lift strategy
        waypoints = []
        # Lift to hover height
        waypoints.append((sx, sy, max(sz, hover_z)))
        for x, y in path_2d:
            waypoints.append((x, y, hover_z))
        # Descend to goal Z
        waypoints.append((gx, gy, gz))
        return waypoints

    def _check_path_blocked(self, start, goal, obstacles):
        """Check if straight line from start to goal intersects any obstacle."""
        sx, sy = start[0], start[1]
        gx, gy = goal[0], goal[1]
        dist = math.hypot(gx - sx, gy - sy)
        if dist == 0:
            return False
        steps = max(1, int(dist / 10.0))  # check every 10mm
        for i in range(steps + 1):
            t = i / steps
            px = sx + t * (gx - sx)
            py = sy + t * (gy - sy)
            for obs in obstacles:
                if math.hypot(px - obs["x"], py - obs["y"]) < obs["radius_mm"] + 15:
                    return True
        return False

    def close(self):
        if self._camera is not None:
            self._camera.release()
        cv2.destroyAllWindows()


# ── 非阻塞输入 ──
class InputBuffer:
    def __init__(self):
        self.buf = ""
    def update(self):
        while msvcrt.kbhit():
            ch = msvcrt.getch()
            if ch == b'\r':
                line = self.buf; self.buf = ""; return line
            elif ch == b'\x08':
                self.buf = self.buf[:-1]; sys.stdout.write('\b \b')
            elif ch == b'\x03':
                raise KeyboardInterrupt
            else:
                try:
                    c = ch.decode('utf-8'); self.buf += c; sys.stdout.write(c)
                except: pass
            sys.stdout.flush()
        return None


def main():
    motion = MotionWithAvoidance()

    print("\n" + "=" * 50)
    print("  步骤 1/2: 学习安全背景")
    print("  [WARN] 确保桌面无障碍物!")
    print("  按 Enter 开始学习背景...")

    inp = InputBuffer()
    while True:
        motion._refresh(show_errors=False)  # silent in init loop
        line = inp.update()
        if line is not None: break
        time.sleep(0.05)  # prevent tight loop

    motion.go_home()
    time.sleep(1)
    motion.learn_background(num_frames=30)

    print("\n" + "=" * 50)
    print("  步骤 2/2: 放置障碍物后测试")
    print("  命令: scan  |  X Y Z  |  home  |  q")
    print("=" * 50)

    inp = InputBuffer()
    try:
        while True:
            motion._refresh()
            line = inp.update()
            if line is None:
                time.sleep(0.05)  # prevent tight loop, keep cv2 window alive
                continue
            line = line.strip()
            if not line: pass
            elif line in ('q','quit','exit'): break
            elif line == 'home': motion.go_home()
            elif line == 'scan': motion.scan()
            elif line == 'relearn':
                print("  请清空桌面后按 Enter...")
                while True:
                    motion._refresh()
                    if inp.update() is not None: break
                motion.learn_background()
            elif line.startswith('calib '):
                parts = line.split()
                if len(parts) == 3:
                    print(f"  偏移: X+={parts[1]} Y+={parts[2]} (需重启生效)")
                else:
                    print("  格式: calib <offset_x> <offset_y>")
            else:
                parts = line.split()
                if len(parts) >= 3:
                    try:
                        motion.go_to(float(parts[0]), float(parts[1]), float(parts[2]))
                    except ValueError: print("  格式: X Y Z")
                else: print(f"  未知: {line}")
    except KeyboardInterrupt:
        print("\n  停止!")
    finally:
        motion.close()
        print("退出。")


if __name__ == "__main__":
    main()
