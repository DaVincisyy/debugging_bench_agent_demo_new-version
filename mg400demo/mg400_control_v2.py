"""
MG400 Python TCP/IP 完整控制脚本
=================================
使用: 纯 socket (无需额外安装SDK)

前提: DobotStudio Pro → 设置 → 远程控制 → TCP/IP二次开发 模式

端口:
  29999 - Dashboard (使能、状态、IO)
  30003 - 运动指令 (MovJ, MovL, ...)
  30004 - 实时反馈 (8ms间隔)
"""

import socket
import time
import struct
import threading


class DobotControl:
    """MG400 TCP/IP 控制类"""

    def __init__(self, ip="192.168.2.6"):
        self.ip = ip
        self.dash_sock = None
        self.move_sock = None
        self.feed_sock = None

    # ------------------------------------------------------------------
    # 连接管理
    # ------------------------------------------------------------------

    def connect(self):
        self.dash_sock = socket.create_connection((self.ip, 29999), timeout=5)
        self.move_sock = socket.create_connection((self.ip, 30003), timeout=30)  # 运动指令可能执行时间长
        print("✓ 已连接")
        return self

    def close(self):
        for s in (self.dash_sock, self.move_sock, self.feed_sock):
            if s:
                try:
                    s.close()
                except:
                    pass

    # ------------------------------------------------------------------
    # 指令发送
    # ------------------------------------------------------------------

    def dash(self, cmd):
        """发送 Dashboard 指令 (29999)"""
        self.dash_sock.sendall(f"{cmd}\r\n".encode("utf-8"))
        resp = self.dash_sock.recv(1024).decode("utf-8").strip()
        err = int(resp.split(",")[0]) if "," in resp else -999
        ok = err == 0
        return ok, resp, err

    def move(self, cmd):
        """发送运动指令 (30003)"""
        self.move_sock.sendall(f"{cmd}\r\n".encode("utf-8"))
        try:
            resp = self.move_sock.recv(1024).decode("utf-8").strip()
            err = int(resp.split(",")[0]) if "," in resp else -999
            ok = err == 0
            return ok, resp, err
        except socket.timeout:
            return True, "(timeout, command accepted)", 0

    # ------------------------------------------------------------------
    # 控制指令
    # ------------------------------------------------------------------

    def enable(self, load=0.0):
        return self.dash(f"EnableRobot({load})")

    def disable(self):
        return self.dash("DisableRobot()")

    def clear_error(self):
        return self.dash("ClearError()")

    def reset_robot(self):
        return self.dash("ResetRobot()")

    def continue_motion(self):
        return self.dash("Continue()")

    def pause(self):
        return self.dash("Pause()")

    def emergency_stop(self):
        return self.dash("EmergencyStop()")

    def speed_factor(self, ratio):
        return self.dash(f"SpeedFactor({ratio})")

    # ------------------------------------------------------------------
    # 查询指令
    # ------------------------------------------------------------------

    def robot_mode(self):
        """获取机器人模式, 返回 (code, desc)"""
        ok, resp, err = self.dash("RobotMode()")
        if not ok:
            return -1, f"error {err}"
        try:
            # 响应: 0,{5},RobotMode();
            value = int(resp.split(",{")[1].split("}")[0])
            modes = {1:"初始化",3:"未上电",4:"未使能",5:"使能空闲✓",
                     6:"拖拽",7:"运行中",9:"报警✗",10:"暂停",11:"点动"}
            return value, modes.get(value, f"未知({value})")
        except:
            return -1, "解析失败"

    def get_pose(self):
        """获取笛卡尔坐标 (x,y,z,r)"""
        ok, resp, err = self.dash("GetPose()")
        if not ok:
            return None
        try:
            vals = resp.split(",{")[1].split("}")[0].split(",")
            return tuple(float(v) for v in vals[:4])
        except:
            return None

    def get_angle(self):
        """获取关节角度 (j1,j2,j3,j4)"""
        ok, resp, err = self.dash("GetAngle()")
        if not ok:
            return None
        try:
            vals = resp.split(",{")[1].split("}")[0].split(",")
            return tuple(float(v) for v in vals[:4])
        except:
            return None

    def get_error_id(self):
        ok, resp, err = self.dash("GetErrorID()")
        if not ok:
            return None
        try:
            return resp.split(",{")[1].split("}")[0]
        except:
            return None

    # ------------------------------------------------------------------
    # IO 指令
    # ------------------------------------------------------------------

    def set_do(self, index, state):
        return self.dash(f"DO({index},{int(state)})")

    def get_di(self, index):
        ok, resp, err = self.dash(f"DI({index})")
        if ok:
            try:
                return int(resp.split(",{")[1].split("}")[0])
            except:
                return None
        return None

    # ------------------------------------------------------------------
    # 运动指令
    # ------------------------------------------------------------------

    def mov_j(self, x, y, z, r, speed=None):
        cmd = f"MovJ({x},{y},{z},{r}"
        if speed is not None:
            cmd += f",SpeedJ={speed}"
        cmd += ")"
        return self.move(cmd)[0]

    def mov_l(self, x, y, z, r, speed=None):
        cmd = f"MovL({x},{y},{z},{r}"
        if speed is not None:
            cmd += f",SpeedL={speed}"
        cmd += ")"
        return self.move(cmd)[0]

    def joint_mov_j(self, j1, j2, j3, j4, speed=None):
        cmd = f"JointMovJ({j1},{j2},{j3},{j4}"
        if speed is not None:
            cmd += f",SpeedJ={speed}"
        cmd += ")"
        return self.move(cmd)[0]

    def move_jog(self, axis):
        """
        点动: axis = "J4+", "J4-", "X+", "X-" 等
        停止: axis = "" 或 None
        """
        if axis:
            return self.move(f"MoveJog({axis})")
        else:
            return self.move("MoveJog()")

    def sync(self):
        return self.move("Sync()")

    def wait_ms(self, ms):
        return self.move(f"Wait({ms})")

    def arc(self, x1, y1, z1, r1, x2, y2, z2, r2, speed=None):
        cmd = f"Arc({x1},{y1},{z1},{r1},{x2},{y2},{z2},{r2}"
        if speed is not None:
            cmd += f",SpeedJ={speed}"
        cmd += ")"
        return self.move(cmd)[0]

    def circle(self, count, x1, y1, z1, r1, x2, y2, z2, r2):
        return self.move(f"Circle({count},{{{x1},{y1},{z1},{r1}}},{{{x2},{y2},{z2},{r2}}})")[0]

    # ------------------------------------------------------------------
    # 实时反馈 (30004)
    # ------------------------------------------------------------------

    def start_feedback(self, callback=None):
        """启动反馈接收线程"""
        def loop():
            self.feed_sock = socket.create_connection((self.ip, 30004), timeout=5)
            while True:
                try:
                    data = self.feed_sock.recv(1440)
                    if len(data) >= 1440:
                        if callback:
                            callback(data)
                        else:
                            # 默认打印位置
                            vals = struct.unpack_from("<" + "d" * 25, data, 56)
                            print(f"\r  X={vals[10]:7.1f} Y={vals[11]:7.1f} Z={vals[12]:7.1f} R={vals[13]:7.1f}  ", end="")
                except:
                    break
        t = threading.Thread(target=loop, daemon=True)
        t.start()
        return t


# ======================================================================
# 使用示例
# ======================================================================

def move():
    """完整的控制流程"""
    robot = DobotControl("192.168.2.6")
    robot.connect()

    # 1. 清除报警
    print("\n1. 清除报警 + 检查错误")
    robot.clear_error()
    robot.continue_motion()
    robot.reset_robot()
    err = robot.get_error_id()
    print(f"   当前错误: {err}")

    # 2. 读取状态
    print("\n2. 机器人状态")
    mode, desc = robot.robot_mode()
    print(f"   模式: {mode} ({desc})")

    pose = robot.get_pose()
    angle = robot.get_angle()
    print(f"   当前位置: {pose}")
    print(f"   当前关节: {angle}")

    # 3. 使能 (如果状态是 4=未使能)
    if mode == 4:
        print("\n3. 使能")
        robot.enable(load=0.5)
        time.sleep(1)
        mode, desc = robot.robot_mode()
        err = robot.get_error_id()
        print(f"   使能后: {mode} ({desc})  错误码: {err}")

    if mode == 5:
        # 4. 设置速度 30%
        print("\n4. 设置速度 30%")
        robot.speed_factor(30)

        # 5. 移动测试
        print("\n5. 移动到 (300, 20, 0, 0)")
        ok = robot.mov_j(350, 0, 0, 0)
        print(f"   {'✓ 成功' if ok else '✗ 失败'}")

        # 等运动完成
        time.sleep(2)

        # 6. 查看最终位置
        print("\n6. 最终位置")
        pose = robot.get_pose()
        print(f"   {pose}")

    else:
        print("\n✗ 机器人未就绪，跳过运动")

    robot.close()
    print("\n=== 完成 ===")


def demo_feedback():
    """实时反馈示例"""
    robot = DobotControl("192.168.2.6")
    robot.connect()
    robot.clear_error()
    robot.enable(0.5)

    def on_feed(data):
        vals = struct.unpack_from("<" + "d" * 25, data, 56)
        print(f"\r  X={vals[10]:7.1f} Y={vals[11]:7.1f} Z={vals[12]:7.1f} R={vals[13]:7.1f}", end="")

    robot.start_feedback(callback=on_feed)
    print("反馈已启动，同时运动...")

    robot.mov_j(300, 0, 200, 0)
    robot.sync()
    time.sleep(2)
    robot.mov_j(-300, 0, 200, 0)
    robot.sync()

    robot.close()


# ======================================================================
if __name__ == "__main__":
    print("=" * 50)
    print("MG400 TCP/IP 控制脚本 v2")
    print("=" * 50)
    print()
    print("前提条件:")
    print("  1. DobotStudio Pro → 设置 → 远程控制 → TCP/IP二次开发")
    print("  2. 网线接 LAN2, 电脑 IP=192.168.2.40")
    print("  3. 机械臂已上电, 无报警")
    print()
    move()
