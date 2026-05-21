"""
MG400 完整控制脚本
=================
流程: 自检消除报警 -> 移动 -> 回到初始位置
"""

import socket, time

IP = "192.168.2.6"

class MG400:
    def __init__(self, ip=IP):
        self.ip = ip
        self.d = None
        self.m = None
        self.init_pose = None

    def connect(self):
        self.d = socket.create_connection((self.ip, 29999), timeout=5)
        self.m = socket.create_connection((self.ip, 30003), timeout=30)
        print("  OK")

    def close(self):
        for s in (self.d, self.m):
            if s:
                try:
                    s.close()
                except:
                    pass

    def _d(self, cmd):
        self.d.sendall(f"{cmd}\r\n".encode())
        return self.d.recv(1024).decode().strip()

    def _m(self, cmd):
        self.m.sendall(f"{cmd}\r\n".encode())
        try:
            return self.m.recv(1024).decode().strip()
        except socket.timeout:
            return "ok"

    def ok_d(self, name):
        r = self._d(name)
        return r.startswith("0,"), r

    def ok_m(self, name):
        r = self._m(name)
        ok = r.startswith("0,") or r == "ok"
        return ok, r

    def get_pose(self):
        try:
            v = self._d("GetPose()").split(",{")[1].split("}")[0].split(",")
            return tuple(float(x) for x in v[:4])
        except: return None

    def get_mode(self):
        try:
            return int(self._d("RobotMode()").split(",{")[1].split("}")[0])
        except: return -1

    def get_error(self):
        return self._d("GetErrorID()")

    # ---------- 自检 ----------
    def wait_ready(self, retry=5):
        for i in range(retry):
            self._d("ClearError()")
            self._d("ResetRobot()")
            self._d("EnableRobot(0.5)")
            time.sleep(1)
            mode = self.get_mode()
            err = self.get_error()
            pose = self.get_pose()
            print(f"  第{i+1}次: mode={mode}  err={err[:40]}...  pose={pose}")
            if mode == 5:
                self.init_pose = pose
                print("  => 就绪！")
                return True
            if mode == 9:
                print("  => 报警中，继续重试")
                continue
        print("  => 自检失败")
        return False

    # ---------- 运动 ----------
    def mov_j(self, x, y, z, r):
        return self.ok_m(f"MovJ({x},{y},{z},{r})")

    def home(self):
        if self.init_pose:
            x, y, z, r = self.init_pose
            print(f"  回到初始 ({x:.1f}, {y:.1f}, {z:.1f}, {r:.1f})")
            return self.mov_j(x, y, z, r)
        return False


# ======================================================================
print("=" * 40)
print("MG400 控制脚本")
print("=" * 40)

robot = MG400("192.168.2.6")
print("\n1. 连接")
robot.connect()

print("\n2. 自检消除报警")
if not robot.wait_ready():
    robot.close()
    exit()

print("\n3. 移动到 (300, 0, 200, 0)")
ok, _ = robot.mov_j(300, 0, 200, 0)
print(f"   {'OK' if ok else 'FAIL'}")
time.sleep(2)
print(f"   位置: {robot.get_pose()}")

print("\n4. 回到初始位置")
robot.home()
time.sleep(2)
print(f"   位置: {robot.get_pose()}")

robot.close()
print("\n完成")
