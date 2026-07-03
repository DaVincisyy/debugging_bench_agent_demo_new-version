"""
避障 HTTP 服务 — 后台运行, Web UI 通过 API 调用
启动: python perception/avoidance_service.py
端口: 8001
"""
import sys, os, time, math, json, threading
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

class ThreadingServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
from perception.obstacle_detector import ObstacleDetector
from perception.path_planner_rrt import RRTStarAPF
import requests

CAMERA_CONFIG = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                              "calibration", "camera_config.yaml")
ROBOT_URL = "http://127.0.0.1:8010"
CONFIG = {"mode": "mg400", "ip": "192.168.2.6",
          "dashboardPort": 29999, "motionPort": 30003,
          "speed": 30, "timeoutMs": 5000, "autoEnable": True, "motionCommand": "MovJ"}
HOME = (400, 0, -60, 0)
HOVER_Z = -170

detector = None
planner = None
_state = {"ready": False, "running": False, "last_result": "", "obstacles": []}


def init():
    global detector, planner, _state
    if detector is not None:
        return {"ok": True, "msg": "已就绪"}
    detector = ObstacleDetector(CAMERA_CONFIG, camera_index=1)
    planner = RRTStarAPF(robot_radius_mm=35, safety_margin_mm=15)
    detector.learn_background(5)
    _state["ready"] = True
    return {"ok": True, "msg": "避障就绪, 背景已学习"}


def robot(action, payload=None):
    r = requests.post(f"{ROBOT_URL}/v1/robot/{action}", json=payload or {"config": CONFIG}, timeout=25)
    return r.json()


def robot_move(x, y, z, r=0):
    return robot("command", {"config": CONFIG, "command": {
        "name": "move", "pose": {"x": x, "y": y, "z": z, "r": r}, "motionCommand": "MovJ"}})


def get_pose():
    r = robot("status")
    p = r.get("robot", {}).get("pose", {})
    return (p.get("x", 0), p.get("y", 0), p.get("z", 0))


def scan():
    obs = detector.detect()
    _state["obstacles"] = obs
    return {"ok": True, "obstacles": obs}


def go_to(gx, gy, gz):
    global _state
    if not _state["ready"]: return {"ok": False, "error": "未初始化"}
    _state["running"] = True
    try:
        sx, sy, sz = get_pose()
        obstacles = detector.detect()
        waypoints = planner.plan((sx, sy, sz), (gx, gy, gz), obstacles, HOVER_Z)
        if not waypoints:
            _state["running"] = False
            return {"ok": False, "error": "无法规划路径"}
        for wx, wy, wz in waypoints:
            cur = get_pose()
            while math.hypot(wx-cur[0], wy-cur[1]) < 5:
                break
            result = robot_move(wx, wy, wz)
            if not result.get("ok"):
                _state["running"] = False
                return {"ok": False, "error": f"移动失败: {result.get('error','?')}", "waypoint": (wx, wy, wz)}
            time.sleep(0.2)
            # 途中检测新障碍物
            new_obs = detector.detect()
            for o in new_obs:
                blocked, _ = planner.check_path_blocked((wx, wy, wz), waypoints[-1], [o]) if waypoints else (False, None)
                if blocked:
                    _state["running"] = False
                    return {"ok": False, "error": "路径被新障碍物阻挡, 需重规划",
                            "obstacle": o, "waypoint": (wx, wy, wz)}
        _state["running"] = False
        return {"ok": True, "msg": "到达目标"}
    except Exception as e:
        _state["running"] = False
        return {"ok": False, "error": str(e)}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self._json({"ok": True, "service": "avoidance", "ready": _state["ready"]})
        elif self.path == "/status":
            self._json({"ok": True, "ready": _state["ready"], "running": _state["running"],
                         "obstacles": _state["obstacles"]})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or "{}")
        if self.path == "/init":
            self._json(init())
        elif self.path == "/scan":
            self._json(scan())
        elif self.path == "/move":
            gx = body.get("x"); gy = body.get("y"); gz = body.get("z")
            if gx is None: self._json({"ok": False, "error": "需要 x,y,z"})
            else: self._json(go_to(gx, gy, gz))
        elif self.path == "/home":
            robot_move(*HOME)
            self._json({"ok": True, "msg": "已回原点"})
        elif self.path == "/calib":
            detector.offset_x = body.get("ox", 0)
            detector.offset_y = body.get("oy", 0)
            self._json({"ok": True, "offset": [detector.offset_x, detector.offset_y]})
        else:
            self._json({"error": "not found"}, 404)

    def _json(self, data, code=200):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, format, *args):
        pass  # 静默日志


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8001
    print(f"避障服务启动: http://127.0.0.1:{port}")
    print("自动初始化...")
    try:
        init()
    except Exception as e:
        print(f"初始化失败(可稍后重试): {e}")
    ThreadingServer(("127.0.0.1", port), Handler).serve_forever()
