"""
MG400 交互控制（基于 mg400_control_v2.py）
=========================================
输入坐标 (x y z r) -> 移动到目标 -> 停留2秒 -> 回到初始 (350,0,0,0)
"""

import socket
import time

IP = "192.168.2.6"
SPEED = 30
HOME = (350.0, 0.0, 0.0, 0.0)
LIMITS = {"x": (-450, 450), "y": (-450, 450), "z": (-250, 250), "r": (-360, 360)}


class DobotControl:
    def __init__(self, ip=IP):
        self.ip = ip
        self.dash_sock = None
        self.move_sock = None

    def connect(self):
        self.dash_sock = socket.create_connection((self.ip, 29999), timeout=5)
        self.move_sock = socket.create_connection((self.ip, 30003), timeout=30)
        print("  已连接")

    def close(self):
        for s in (self.dash_sock, self.move_sock):
            if s:
                try: s.close()
                except: pass

    def dash(self, cmd):
        self.dash_sock.sendall(f"{cmd}\r\n".encode("utf-8"))
        resp = self.dash_sock.recv(1024).decode("utf-8").strip()
        err = int(resp.split(",")[0]) if "," in resp else -999
        return err == 0, resp, err

    def move(self, cmd):
        self.move_sock.sendall(f"{cmd}\r\n".encode("utf-8"))
        try:
            resp = self.move_sock.recv(1024).decode("utf-8").strip()
            err = int(resp.split(",")[0]) if "," in resp else -999
            return err == 0, resp, err
        except socket.timeout:
            return True, "(timeout)", 0

    def enable(self, load=0.0):
        return self.dash(f"EnableRobot({load})")

    def clear_error(self):
        return self.dash("ClearError()")

    def reset_robot(self):
        return self.dash("ResetRobot()")
    
    def continue_motion(self):
        return self.dash("Continue()")

    def speed_factor(self, ratio):
        return self.dash(f"SpeedFactor({ratio})")

    def robot_mode(self):
        ok, resp, err = self.dash("RobotMode()")
        if not ok: return -1, f"error {err}"
        try:
            v = int(resp.split(",{")[1].split("}")[0])
            modes = {4:"未使能", 5:"就绪", 6:"拖拽", 7:"运行中", 9:"报警"}
            return v, modes.get(v, f"未知({v})")
        except: return -1, "解析失败"

    def get_pose(self):
        ok, resp, err = self.dash("GetPose()")
        if not ok: return None
        try:
            v = resp.split(",{")[1].split("}")[0].split(",")
            return tuple(round(float(x), 1) for x in v[:4])
        except: return None

    def get_error_id(self):
        ok, resp, err = self.dash("GetErrorID()")
        if ok:
            return resp.split(",{")[1].split("}")[0]
        return None

    def mov_j(self, x, y, z, r):
        return self.move(f"MovJ({x},{y},{z},{r},SpeedJ={SPEED})")


# ------------------------------------------------------------
def move(robot, x, y, z, r):
    """移动到指定坐标 -> 停留2秒 -> 回到初始位置"""
    ok = True
    for name, val in [("X",x),("Y",y),("Z",z),("R",r)]:
        lo, hi = LIMITS[name.lower()]
        if not (lo <= val <= hi):
            print(f"  ! {name}={val} 超出 [{lo},{hi}]")
            ok = False
    if not ok:
        return

    print(f"  移动到 ({x},{y},{z},{r}) ...")
    ok = robot.mov_j(x, y, z, r)
    time.sleep(5)
    pose = robot.get_pose()
    print(f"  到位: {pose}")
    if not ok:
        print("  ! 移动指令执行失败")

    print(f"  回初始 {HOME} ...")
    robot.mov_j(*HOME)
    time.sleep(5)
    print(f"  到位: {robot.get_pose()}")


# ============================================================
robot = DobotControl(IP)
print("=" * 45)
print("MG400 交互控制")
print("=" * 45)

print("\n连接...")
robot.connect()

# ---- 自检（和 control_v2 一致）----
print("\n自检...")
robot.clear_error()
robot.continue_motion()
robot.reset_robot()
err = robot.get_error_id()
print(f"  错误: {err}")

mode, desc = robot.robot_mode()
print(f"  模式: {mode} ({desc})")

if mode == 4:
    print("  使能...")
    robot.enable(0.5)
    time.sleep(1)
    mode, desc = robot.robot_mode()
    err = robot.get_error_id()
    print(f"  使能后: mode={mode} ({desc})  err={err}")

if mode != 5:
    print("\n! 无法就绪，请确保机械臂状态正常")
    robot.close()
    exit()

robot.speed_factor(SPEED)
print(f"\n就绪  初始: {HOME}  速度: {SPEED}%")
print("命令: x y z r | pose | q")

# ---- 主循环 ----
while True:
    try:
        line = input("\n>> ").strip()
    except (EOFError, KeyboardInterrupt):
        print(); break
    if not line:
        continue

    parts = line.split()
    c = parts[0].lower()

    if c == "q":
        break
    elif c == "pose":
        print(f"  {robot.get_pose()}")
    elif len(parts) == 4:
        try:
            x, y, z, r = float(parts[0]), float(parts[1]), float(parts[2]), float(parts[3])
        except ValueError:
            print("  坐标须为数字"); continue
        move(robot, x, y, z, r)
    else:
        print("  格式: x y z r | pose | q")

robot.close()
print("退出")
