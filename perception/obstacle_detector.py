"""
障碍物检测 v5 — 颜色差异 + 背景减除 + 改进几何高度 + 时序滤波
"""
import cv2, numpy as np, yaml, time, os, math
from perception.height_estimator import HeightEstimator


class ObstacleDetector:
    def __init__(self, camera_config_path, camera_index=1):
        with open(camera_config_path, "r") as f:
            data = yaml.safe_load(f)
        self.H = np.array(data["table_homography"]["H"])
        self.table_z = data["table_homography"]["table_z_mm"]
        self.img_w = data["calibration"]["resolution"][0]
        self.img_h = data["calibration"]["resolution"][1]
        self.height_estimator = HeightEstimator(camera_config_path)

        # 空桌面参考帧 (学习背景时保存)
        self.ref_frame = None
        self.diff_threshold = 22  # 折中: 检出稳定 + 减少假阳

        self._tracked = {}; self._next_id = 0
        self._frame_count = 0; self._confirm_frames = 2

        self.cap = cv2.VideoCapture(camera_index, cv2.CAP_DSHOW)
        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.img_w)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.img_h)
        self.kernel_open = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        self.kernel_close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (11, 11))
        self.min_area_px = 1200; self.min_radius_mm = 15; self.max_radius_mm = 150
        self.ws_x_min, self.ws_x_max = 100, 500
        self.ws_y_min, self.ws_y_max = -350, 350

        # 坐标修正偏移 (手动校准)
        self.offset_x = 0.0
        self.offset_y = 0.0

        self._warmup()

    def _warmup(self):
        for _ in range(60): self.cap.read(); time.sleep(0.02)
        time.sleep(0.5)

    def _capture(self):
        frame = None
        for _ in range(6):
            ret, f = self.cap.read()
            if ret: frame = f; time.sleep(0.03)
        return frame

    def learn_background(self, num_frames=5):
        """保存空桌面参考帧"""
        print("  学习背景 (保存空桌面)...")
        for _ in range(30): self._capture()  # 预热
        self.ref_frame = self._capture()
        if self.ref_frame is not None:
            cv2.imwrite("ref_background.jpg", cv2.resize(self.ref_frame, (1280, 720)))
            print("  参考帧已保存 (ref_background.jpg)")
        print("  背景学习完成")

    def _pixel_to_world(self, px, py):
        pt = np.array([px, py, 1.0]); w = self.H @ pt; w /= w[2]
        return w[0], w[1]

    def _raw_detect(self, frame):
        """ArUco + 帧差分 → 障碍物列表"""
        obstacles = []

        # ── ArUco 检测 (主力, 稳定) ──
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        aruco_dict = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_6X6_250)
        det = cv2.aruco.ArucoDetector(aruco_dict, cv2.aruco.DetectorParameters())
        corners, ids, _ = det.detectMarkers(gray)
        if ids is not None:
            for c in corners:
                pts = c.reshape(4, 2)
                cx = float(np.mean(pts[:,0])); cy = float(np.mean(pts[:,1]))
                wx, wy = self._pixel_to_world(cx, cy)
                wx += self.offset_x; wy += self.offset_y
                if self.ws_x_min < wx < self.ws_x_max and self.ws_y_min < wy < self.ws_y_max:
                    obstacles.append({"x": round(wx,1), "y": round(wy,1), "z": self.table_z,
                                      "radius_mm": 30.0, "height_mm": 80.0,
                                      "pixel_area": 5000, "source": "aruco"})

        # ── 帧差分 (补充) ──
        if frame is not None and self.ref_frame is not None:
            diff = cv2.absdiff(frame, self.ref_frame)
            gd = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
            _, fg = cv2.threshold(gd, self.diff_threshold, 255, cv2.THRESH_BINARY)
            fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, self.kernel_open)
            fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, self.kernel_close)
            contours, _ = cv2.findContours(fg, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for cnt in contours:
                area = cv2.contourArea(cnt)
                if area < self.min_area_px: continue
                M = cv2.moments(cnt)
                if M["m00"] < 1: continue
                cx, cy = M["m10"]/M["m00"], M["m01"]/M["m00"]
                wx, wy = self._pixel_to_world(cx, cy)
                wx += self.offset_x; wy += self.offset_y
                if not (self.ws_x_min < wx < self.ws_x_max and self.ws_y_min < wy < self.ws_y_max):
                    continue
                x, y, w, h = cv2.boundingRect(cnt)
                wl = self._pixel_to_world(x, y+h); wr = self._pixel_to_world(x+w, y+h)
                radius_mm = max(np.linalg.norm(np.array(wr)-np.array(wl))/2, self.min_radius_mm)
                if radius_mm > self.max_radius_mm: continue
                obs = {"x": round(wx,1), "y": round(wy,1), "z": self.table_z,
                       "radius_mm": round(radius_mm,1), "height_mm": 60.0,
                       "pixel_area": int(area), "source": "diff"}
                # 避免和 ArUco 重复
                dup = any(math.hypot(obs["x"]-a["x"], obs["y"]-a["y"]) < 40 for a in obstacles)
                if not dup: obstacles.append(obs)

        return obstacles

    def detect(self):
        self._frame_count += 1
        raw = self._raw_detect(self._capture())
        matched_ids = set()
        for r in raw:
            best_id, best_dist = None, 80
            for tid, t in self._tracked.items():
                if tid in matched_ids: continue
                dist = math.hypot(r["x"]-t["obs"]["x"], r["y"]-t["obs"]["y"])
                if dist < best_dist: best_dist = dist; best_id = tid
            if best_id is not None:
                self._tracked[best_id]["obs"] = r
                self._tracked[best_id]["frames_seen"] += 1
                self._tracked[best_id]["last_frame"] = self._frame_count
                matched_ids.add(best_id)
            else:
                self._tracked[self._next_id] = {"obs": r, "frames_seen": 1, "last_frame": self._frame_count}
                self._next_id += 1
        stale = [tid for tid, t in self._tracked.items() if self._frame_count - t["last_frame"] > 5]
        for tid in stale: del self._tracked[tid]
        return [t["obs"] for t in self._tracked.values() if t["frames_seen"] >= self._confirm_frames]

    def detect_with_debug(self):
        obstacles = self.detect()
        frame = self._capture()
        if frame is None: return obstacles, None
        # 帧差分前景
        if self.ref_frame is not None:
            diff = cv2.absdiff(frame, self.ref_frame)
            gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
            _, fg_clean = cv2.threshold(gray, self.diff_threshold, 255, cv2.THRESH_BINARY)
            fg_clean = cv2.morphologyEx(fg_clean, cv2.MORPH_OPEN, self.kernel_open)
            fg_clean = cv2.morphologyEx(fg_clean, cv2.MORPH_CLOSE, self.kernel_close)
        else:
            fg_clean = np.zeros((self.img_h, self.img_w), dtype=np.uint8)
        preview = cv2.resize(frame, (1280, 720))
        mask_vis = cv2.resize(fg_clean, (1280, 720))
        overlay = np.zeros_like(preview); overlay[:,:,1] = mask_vis
        debug = cv2.addWeighted(preview, 0.6, overlay, 0.4, 0)
        scale = 1280.0 / self.img_w; H_inv = np.linalg.inv(self.H)
        for obs in obstacles:
            pt = np.array([obs["x"], obs["y"], 1.0]); pi = H_inv @ pt; pi /= pi[2]
            cx, cy = int(pi[0]*scale), int(pi[1]*scale)
            if 0 < cx < 1280 and 0 < cy < 720:
                cv2.circle(debug, (cx, cy), max(int(obs["radius_mm"]*scale), 12), (0, 255, 255), 2)
                cv2.putText(debug, f"({obs['x']:.0f},{obs['y']:.0f}) H{obs['height_mm']:.0f}",
                            (cx+15, cy), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 255), 1)
        cv2.putText(debug, f"Confirmed:{len(obstacles)}", (10, 705),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
        return obstacles, debug

    def close(self):
        self.cap.release()
