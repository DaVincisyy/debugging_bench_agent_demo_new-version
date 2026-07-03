"""
避障运动系统 v3 — FastSAM检测 + Z抬升策略
"""
import sys, os, time, math, cv2, requests, numpy as np, msvcrt
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from perception.obstacle_detector import ObstacleDetector

ROBOT_URL = "http://127.0.0.1:8010"
CAMERA_CONFIG = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                              "calibration", "camera_config.yaml")
CONFIG = {"mode": "mg400", "ip": "192.168.2.6",
          "dashboardPort": 29999, "motionPort": 30003,
          "speed": 30, "timeoutMs": 5000, "autoEnable": True, "motionCommand": "MovJ"}
HOME = (400, 0, -60, 0)
HOVER_Z = -170; SAFE_Z = 0
PW, PH = 960, 540


class MotionWithAvoidance:
    def __init__(self):
        print("=" * 50); print("  避障系统 v3 — FastSAM + Z抬升"); print("=" * 50)
        print("[1/2] 摄像头+FastSAM...")
        self.detector = ObstacleDetector(CAMERA_CONFIG, camera_index=1)
        print("[2/2] 机械臂...")
        self._robot_ok = False
        try:
            self._check_robot()
            self._robot_ok = True
        except Exception as e:
            print(f"    [WARN] 机械臂未连接: {e}")
        cv2.namedWindow("Live Monitor", cv2.WINDOW_NORMAL)
        cv2.resizeWindow("Live Monitor", PW, PH)
        self._overlay = None; self._overlay_until = 0

    def _check_robot(self):
        r = requests.post(f"{ROBOT_URL}/v1/robot/status", json={"config": CONFIG}, timeout=5)
        d = r.json()
        if not d.get("ok"): raise RuntimeError(f"连接失败: {d}")
        p = d["robot"]["pose"]
        mode = d["robot"]["mode"]
        label = mode.get("label") or f"mode={mode.get('response','?')}"
        print(f"    状态: {label}  位姿: X={p['x']:.0f} Y={p['y']:.0f} Z={p['z']:.0f}")

    def _get_pose(self):
        r = requests.post(f"{ROBOT_URL}/v1/robot/status", json={"config": CONFIG}, timeout=5)
        p = r.json()["robot"]["pose"]
        return (p["x"], p["y"], p["z"])

    def _move_to(self, x, y, z, r=0):
        payload = {"config": CONFIG, "command": {"name": "move",
                    "pose": {"x": x, "y": y, "z": z, "r": r}, "motionCommand": "MovJ"}}
        r = requests.post(f"{ROBOT_URL}/v1/robot/command", json=payload, timeout=20)
        d = r.json()
        if not d.get("ok"): print(f"    [ERR] {d.get('error','?')}"); return False
        return True

    def _filter_self(self, obs_list, rx, ry):
        return [o for o in obs_list if math.hypot(o["x"]-rx, o["y"]-ry) > 100]

    def _refresh(self):
        frame = self.detector._capture()
        if frame is None: return
        display = cv2.resize(frame, (PW, PH))
        if self._overlay is not None and time.time() < self._overlay_until:
            display = cv2.addWeighted(display, 0.5, self._overlay, 0.5, 0)
        cv2.putText(display, "LIVE", (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0,255,0), 1)
        cv2.imshow("Live Monitor", display); cv2.waitKey(5)

    def scan(self, quick=4):
        best, best_dbg = [], None
        for _ in range(quick):
            frame = self.detector._capture()
            if frame is None: continue
            raw = self.detector._raw_detect(frame)
            if len(raw) > len(best): best = raw
            _, best_dbg = self.detector.detect_with_debug()
            self._refresh(); time.sleep(0.03)
        if best_dbg is not None:
            self._overlay = cv2.resize(best_dbg, (PW, PH))
            self._overlay_until = time.time() + 4.0
        self._refresh()
        if not best: print("  [SCAN] 未检测到障碍物")
        else:
            print(f"  [SCAN] {len(best)} 个障碍物:")
            for o in best:
                print(f"    ({o['x']:.0f},{o['y']:.0f}) R={o['radius_mm']:.0f}mm src={o.get('source','?')}")
        return best

    def go_home(self):
        self._move_to(*HOME)

    def go_to(self, gx, gy, gz):
        if not self._robot_ok: print("  [ERR] 机械臂未连接"); return False
        sx, sy, sz = self._get_pose()
        print(f"\n  >>> ({sx:.0f},{sy:.0f},{sz:.0f}) → ({gx:.0f},{gy:.0f},{gz:.0f})")
        self._refresh()

        obstacles = self.scan()

        # 判断是否需要用 Z 抬升
        need_lift = False
        if obstacles:
            for o in obstacles:
                # 目标被挡 或 起点到目标的直线被挡
                if math.hypot(o["x"]-gx, o["y"]-gy) < o["radius_mm"]+50:
                    print(f"  ⚡ 目标被挡 → Z=0 高空飞行")
                    need_lift = True; break
                d = self._pt_seg(o["x"],o["y"], sx,sy, gx,gy)
                if d < o["radius_mm"]+50:
                    print(f"  ⚡ 路径被挡 → Z=0 高空绕行")
                    need_lift = True; break

        if need_lift:
            waypoints = [(sx, sy, SAFE_Z), (gx, gy, SAFE_Z)]
            print(f"  (停在 Z={SAFE_Z}, 不下探)")
        else:
            waypoints = [(gx, gy, HOVER_Z)]
            if abs(gz - HOVER_Z) > 2: waypoints.append((gx, gy, gz))
            print(f"  → 无障碍, 正常移动")

        print(f"  路径: {len(waypoints)} 点")
        for i, wp in enumerate(waypoints): print(f"    {i+1}. ({wp[0]:.0f},{wp[1]:.0f},{wp[2]:.0f})")

        # 执行
        for i, (wx, wy, wz) in enumerate(waypoints):
            # 移动前检测
            frame = self.detector._capture()
            new_obs = self._filter_self(
                self.detector._raw_detect(frame) if frame else [], wx, wy)
            if new_obs and i == len(waypoints)-1 and wz < SAFE_Z:
                blocked = any(math.hypot(o["x"]-gx, o["y"]-gy) < o["radius_mm"]+50 for o in new_obs)
                if blocked:
                    print(f"  ⚠ 下探路径被挡, 停在 Z={max(wz,HOVER_Z)}")
                    wz = max(wz, HOVER_Z)

            print(f"  → [{i+1}/{len(waypoints)}] ({wx:.0f},{wy:.0f},{wz:.0f})")
            if not self._move_to(wx, wy, wz): return False
            for _ in range(4): self._refresh(); time.sleep(0.05)

        print("  ✅ 到达!"); return True

    def close(self):
        self.detector.close(); cv2.destroyAllWindows()

    @staticmethod
    def _pt_seg(px,py,ax,ay,bx,by):
        abx,aby=bx-ax,by-ay
        if abx==0 and aby==0: return math.hypot(px-ax,py-ay)
        t=max(0,min(1,((px-ax)*abx+(py-ay)*aby)/(abx*abx+aby*aby)))
        return math.hypot(px-(ax+t*abx),py-(ay+t*aby))


class InputBuffer:
    def __init__(self): self.buf = ""
    def update(self):
        while msvcrt.kbhit():
            ch = msvcrt.getch()
            if ch == b'\r': line = self.buf; self.buf = ""; return line
            elif ch == b'\x08': self.buf = self.buf[:-1]; sys.stdout.write('\b \b')
            elif ch == b'\x03': raise KeyboardInterrupt
            else:
                try: c = ch.decode('utf-8'); self.buf += c; sys.stdout.write(c)
                except: pass
            sys.stdout.flush()
        return None


def main():
    motion = MotionWithAvoidance()
    print("\n" + "=" * 50)
    print("  学习安全背景 (确保桌面无障碍物!)")
    print("=" * 50)
    print("  按 Enter 开始...")
    inp = InputBuffer()
    while True:
        motion._refresh()
        line = inp.update()
        if line is not None:
            break
    motion.go_home(); time.sleep(1)
    motion.detector.learn_background(5)
    print("\n  就绪! 命令: X Y Z | scan | home | q")
    inp = InputBuffer()
    try:
        while True:
            motion._refresh()
            line = inp.update()
            if line is None:
                continue
            line = line.strip()
            if not line:
                pass
            elif line in ('q', 'quit', 'exit'):
                break
            elif line == 'home':
                motion.go_home()
            elif line == 'scan':
                motion.scan()
            else:
                parts = line.split()
                if len(parts) >= 3:
                    try:
                        motion.go_to(float(parts[0]), float(parts[1]), float(parts[2]))
                    except ValueError:
                        print("  格式: X Y Z")
                else:
                    print(f"  未知: {line}")
    except KeyboardInterrupt: print("\n  停止!")
    finally: motion.close(); print("退出。")


if __name__ == "__main__": main()
