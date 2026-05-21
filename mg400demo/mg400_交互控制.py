"""
MG400 交互控制
==============
输入坐标 (x y z r) -> 移动到目标 -> 停留2秒 -> 回到初始 (350,0,0,0)

交互命令:
  x y z r   移动到指定坐标
  home      回到初始位置
  drag      进入拖拽模式（手动调整）
  pose      查看当前位置
  q         退出
"""

import socket, time

IP = "192.168.2.6"
SPEED = 20
HOME = (350.0, 0.0, 0.0, 0.0)
LIMITS = {"x": (-440, 440), "y": (-440, 440), "z": (50, 500), "r": (-360, 360)}


class MG400:
    def __init__(self):
        self.d = socket.create_connection((IP, 29999), timeout=5)
        self.m = socket.create_connection((IP, 30003), timeout=30)

    def send(self, sock, cmd):
        sock.sendall(f"{cmd}\r\n".encode())
        try:
            return sock.recv(1024).decode().strip()
        except:
            return "ok"

    def dash(self, c): return self.send(self.d, c)
    def move(self, c): return self.send(self.m, c)

    def get_pose(self):
        try:
            v = self.dash("GetPose()").split(",{")[1].split("}")[0].split(",")
            return tuple(round(float(x), 1) for x in v[:4])
        except:
            return None

    def get_mode(self):
        r = self.dash("RobotMode()")
        try:
            return int(r.split(",{")[1].split("}")[0])
        except:
            return -1

    def get_error(self):
        return self.dash("GetErrorID()")

    def wait_ready(self):
        print()
        for i in range(5):
            self.dash("ClearError()")
            self.dash("ResetRobot()")
            self.dash("DisableRobot()")
            time.sleep(0.3)
            self.dash("EnableRobot()")
            time.sleep(1)
            mode = self.get_mode()
            err = self.get_error()
            print(f"  [{i+1}] mode={mode}  err={err[:60]}")
            if mode == 5:
                return True
            if mode == 9:
                print("  -> 报警中")
        return False

    def mov_j(self, x, y, z, r):
        return self.move(f"MovJ({x},{y},{z},{r},SpeedJ={SPEED})")

    def close(self):
        self.d.close()
        self.m.close()


def check(x, y, z, r):
    for name, val in [("X", x), ("Y", y), ("Z", z), ("R", r)]:
        lo, hi = LIMITS[name.lower()]
        if not (lo <= val <= hi):
            return False, f"{name}={val} 超出范围 [{lo}, {hi}]"
    return True, ""


# ============================================================
print("=" * 45)
print("MG400 交互控制")
print("=" * 45)

robot = MG400()

print("\n连接成功")

# ---- 自检 ----
print("\n--- 自检 ---")
if not robot.wait_ready():
    print("\n! 自检失败，当前状态:")
    print(f"  mode: {robot.get_mode()}")
    print(f"  pose: {robot.get_pose()}")
    print(f"  err:  {robot.get_error()}")
    while True:
        c = input("\n[d]rag拖拽  [r]etry重试  [q]退出: ").strip().lower()
        if c == "q":
            robot.close()
            exit()
        elif c == "r":
            if robot.wait_ready():
                break
        elif c == "d":
            robot.dash("ClearError()")
            robot.dash("StartDrag()")
            input("  手动调整位置后按 Enter 继续...")
            robot.dash("StopDrag()")
            if robot.wait_ready():
                break

print(f"\n就绪  初始: {HOME}  速度: {SPEED}%")

# ---- 主循环 ----
while True:
    try:
        line = input("\n>> ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        break
    if not line:
        continue

    parts = line.split()
    cmd = parts[0].lower()

    if cmd == "q":
        break
    elif cmd == "pose":
        print(f"  当前位置: {robot.get_pose()}")
    elif cmd == "home":
        print(f"  回到初始 {HOME} ...")
        robot.mov_j(*HOME)
        time.sleep(2)
        print(f"  到位: {robot.get_pose()}")
    elif cmd == "drag":
        robot.dash("ClearError()")
        robot.dash("StartDrag()")
        input("  拖拽模式，调整后按 Enter 继续...")
        robot.dash("StopDrag()")
        robot.dash("EnableRobot()")
        time.sleep(1)
        print(f"  mode: {robot.get_mode()}  pose: {robot.get_pose()}")

    elif len(parts) == 4:
        try:
            x, y, z, r = float(parts[0]), float(parts[1]), float(parts[2]), float(parts[3])
        except:
            print("  坐标须为数字"); continue

        ok, msg = check(x, y, z, r)
        if not ok:
            print(f"  ! {msg}"); continue

        print(f"  移动到 ({x}, {y}, {z}, {r}) ...")
        robot.mov_j(x, y, z, r)
        time.sleep(2)
        print(f"  到位: {robot.get_pose()}")

        print(f"  回到初始 {HOME} ...")
        robot.mov_j(*HOME)
        time.sleep(2)
        print(f"  到位: {robot.get_pose()}")
    else:
        print("  格式: x y z r | home | drag | pose | q")

robot.close()
print("退出")
