"""
Dobot MG400 TCP/IP Python 控制示例
====================================
端口说明:
  - 29999: Dashboard 指令 (使能、设置、状态查询、IO等)
  - 30003: 运动指令 (MovJ、MovL、JointMovJ等)
  - 30004: 实时反馈 (8ms间隔, 1440字节数据包)

指令格式:
  下发: CommandName(param1,param2,...)
  返回: ErrorID,{value,...},CommandName(params);
  ErrorID=0 表示成功

依赖: 无 (仅使用标准库 socket)
"""

import socket
import time
import threading


class DobotMG400:
    """MG400 TCP/IP 控制类"""

    def __init__(self, ip="192.168.1.6"):
        self.ip = ip
        self.dashboard_port = 29999
        self.motion_port = 30003
        self.feedback_port = 30004
        self.dashboard_sock = None
        self.motion_sock = None

    # ------------------------------------------------------------------
    # 连接管理
    # ------------------------------------------------------------------

    def connect(self):
        """建立 Dashboard 和 运动 两个通道的连接"""
        self.dashboard_sock = socket.create_connection(
            (self.ip, self.dashboard_port), timeout=5)
        self.motion_sock = socket.create_connection(
            (self.ip, self.motion_port), timeout=5)
        print(f"✓ 已连接到 {self.ip}")

    def disconnect(self):
        """断开连接"""
        for sock in (self.dashboard_sock, self.motion_sock):
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass
        print("✓ 已断开连接")

    # ------------------------------------------------------------------
    # 底层通信
    # ------------------------------------------------------------------

    def _send_dashboard(self, cmd):
        """向29999端口发送Dashboard指令，返回解析后的结果"""
        full_cmd = cmd + "\r\n"
        self.dashboard_sock.sendall(full_cmd.encode("utf-8"))
        resp = self.dashboard_sock.recv(1024).decode("utf-8").strip()
        # 解析响应: ErrorID,{value,...},CommandName(params);
        parts = resp.split(",", 1)
        error_id = int(parts[0])
        return error_id, resp

    def _send_motion(self, cmd):
        """向30003端口发送运动指令"""
        full_cmd = cmd + "\r\n"
        self.motion_sock.sendall(full_cmd.encode("utf-8"))
        resp = self.motion_sock.recv(1024).decode("utf-8").strip()
        parts = resp.split(",", 1)
        error_id = int(parts[0])
        return error_id, resp

    # ------------------------------------------------------------------
    # Dashboard 指令 - 控制相关
    # ------------------------------------------------------------------

    def enable_robot(self, load=None, cx=0, cy=0, cz=0):
        """使能机械臂。允许设置负载(kg)和偏心(mm)"""
        if load is None:
            cmd = "EnableRobot()"
        elif isinstance(load, (int, float)):
            cmd = f"EnableRobot({load},{cx},{cy},{cz})"
        else:
            cmd = "EnableRobot()"
        err, resp = self._send_dashboard(cmd)
        print(f"  EnableRobot: {'OK' if err == 0 else f'FAIL ({err})'}")
        return err == 0

    def disable_robot(self):
        """下使能"""
        err, _ = self._send_dashboard("DisableRobot()")
        return err == 0

    def clear_error(self):
        """清除报警"""
        err, _ = self._send_dashboard("ClearError()")
        return err == 0

    def robot_mode(self):
        """获取机器人状态，返回 (code, description)"""
        _, resp = self._send_dashboard("RobotMode()")
        # 响应: 0,{5},RobotMode();
        try:
            value_str = resp.split(",")[1].strip("{}")
            code = int(value_str)
            modes = {
                1: "初始化", 2: "抱闸松开", 3: "未上电",
                4: "未使能", 5: "使能空闲 ✓", 6: "拖拽模式",
                7: "运行中", 8: "轨迹录制", 9: "报警 ✗",
                10: "暂停", 11: "点动中"
            }
            return code, modes.get(code, f"未知({code})")
        except (IndexError, ValueError):
            return -1, "解析失败"

    def get_pose(self):
        """获取当前笛卡尔坐标 (X,Y,Z,R)"""
        _, resp = self._send_dashboard("GetPose()")
        # 响应: 0,{473,-141,469,-180},GetPose();
        try:
            values = resp.split(",{")[1].split("}")[0].split(",")
            return tuple(float(v) for v in values)
        except (IndexError, ValueError):
            return None

    def get_angle(self):
        """获取当前关节角度 (J1,J2,J3,J4)"""
        _, resp = self._send_dashboard("GetAngle()")
        try:
            values = resp.split(",{")[1].split("}")[0].split(",")
            return tuple(float(v) for v in values)
        except (IndexError, ValueError):
            return None

    def speed_factor(self, ratio):
        """设置全局速度 1~100%"""
        err, _ = self._send_dashboard(f"SpeedFactor({ratio})")
        return err == 0

    # ------------------------------------------------------------------
    # Dashboard 指令 - IO 相关
    # ------------------------------------------------------------------

    def set_do(self, index, state):
        """设置数字输出 (队列指令)"""
        err, _ = self._send_dashboard(f"DO({index},{int(state)})")
        return err == 0

    def get_di(self, index):
        """读取数字输入"""
        _, resp = self._send_dashboard(f"DI({index})")
        try:
            value = resp.split(",{")[1].split("}")[0]
            return int(value)
        except (IndexError, ValueError):
            return None

    # ------------------------------------------------------------------
    # 运动指令 (通过30003端口)
    # ------------------------------------------------------------------

    def mov_j(self, x, y, z, r, speed=None, acc=None, cp=None, user=None, tool=None):
        """关节运动到笛卡尔坐标目标点"""
        cmd = f"MovJ({x},{y},{z},{r}"
        cmd = self._append_optional_params(cmd, speed, acc, cp, user, tool)
        cmd += ")"
        err, _ = self._send_motion(cmd)
        return err == 0

    def mov_l(self, x, y, z, r, speed=None, acc=None, cp=None, user=None, tool=None):
        """直线运动到笛卡尔坐标目标点"""
        cmd = f"MovL({x},{y},{z},{r}"
        cmd = self._append_optional_params(cmd, speed, acc, cp, user, tool)
        cmd += ")"
        err, _ = self._send_motion(cmd)
        return err == 0

    def joint_mov_j(self, j1, j2, j3, j4, speed=None, acc=None, cp=None):
        """关节运动到指定关节角度"""
        cmd = f"JointMovJ({j1},{j2},{j3},{j4}"
        cmd = self._append_optional_params(cmd, speed, acc, cp)
        cmd += ")"
        err, _ = self._send_motion(cmd)
        return err == 0

    def arc(self, x1, y1, z1, r1, x2, y2, z2, r2):
        """圆弧运动: 经过P1(x1,y1,z1,r1)到达P2(x2,y2,z2,r2)"""
        cmd = f"Arc({x1},{y1},{z1},{r1},{x2},{y2},{z2},{r2})"
        err, _ = self._send_motion(cmd)
        return err == 0

    def circle(self, count, x1, y1, z1, r1, x2, y2, z2, r2):
        """整圆运动: P1→P2为直径, 转count圈"""
        cmd = f"Circle({count},{x1},{y1},{z1},{r1},{x2},{y2},{z2},{r2})"
        err, _ = self._send_motion(cmd)
        return err == 0

    def sync(self):
        """等待所有队列指令执行完毕"""
        err, _ = self._send_motion("Sync()")
        return err == 0

    def wait(self, ms):
        """队列延时 (ms)"""
        err, _ = self._send_motion(f"Wait({ms})")
        return err == 0

    @staticmethod
    def _append_optional_params(cmd, speed=None, acc=None, cp=None,
                                user=None, tool=None):
        if speed is not None:
            cmd += f",SpeedJ={speed}"  # MovJ用SpeedJ
        if acc is not None:
            cmd += f",AccJ={acc}"
        if cp is not None:
            cmd += f",CP={cp}"
        if user is not None:
            cmd += f",User={user}"
        if tool is not None:
            cmd += f",Tool={tool}"
        return cmd

    def pause(self):
        """暂停运动 (不清除队列)"""
        err, _ = self._send_dashboard("Pause()")
        return err == 0

    def continue_motion(self):
        """继续运动 (或报警清除后恢复队列)"""
        err, _ = self._send_dashboard("Continue()")
        return err == 0

    # ------------------------------------------------------------------
    # 反馈数据接收 (示例: 30004端口实时接收)
    # ------------------------------------------------------------------

    def start_feedback(self, callback=None):
        """启动反馈接收线程 (30004端口, 8ms间隔)"""
        def _loop():
            sock = socket.create_connection(
                (self.ip, self.feedback_port), timeout=5)
            while True:
                data = sock.recv(1440)
                if len(data) < 1440:
                    continue
                # 1440字节的解析可参考文档第4章
                # 这里简化为打印前几个值
                if callback:
                    callback(data)
        t = threading.Thread(target=_loop, daemon=True)
        t.start()
        print("✓ 反馈接收已启动 (30004端口)")
        return t


# ======================================================================
# 使用示例
# ======================================================================

def demo_basic_motion():
    """示例1: 基本往复运动"""
    robot = DobotMG400("192.168.1.6")
    robot.connect()

    # 1. 清除报警并使能
    robot.clear_error()
    robot.enable_robot(load=0.5)

    # 2. 获取当前状态
    code, desc = robot.robot_mode()
    print(f"  机器人状态: {desc}")

    if code != 5:  # 5=使能空闲
        print("  机械臂未就绪，请检查")
        robot.disconnect()
        return

    # 3. 设置速度 50%
    robot.speed_factor(50)

    # 4. 往复运动
    print("\n  开始往复运动...")
    for i in range(3):
        print(f"  第 {i+1} 次")
        robot.mov_j(300, 0, 200, 0)     # 关节运动到右侧
        robot.mov_j(-300, 0, 200, 0)    # 关节运动到左侧

    robot.sync()  # 等待运动完成
    print("  运动完成")

    # 5. 获取最终位姿
    pose = robot.get_pose()
    angle = robot.get_angle()
    print(f"  当前位置: {pose}")
    print(f"  当前关节: {angle}")

    # 6. 下使能
    robot.disable_robot()
    robot.disconnect()


def demo_pick_and_place():
    """示例2: 模拟抓取-放置 (含过渡点和IO控制)"""
    robot = DobotMG400("192.168.1.6")
    robot.connect()
    robot.clear_error()
    robot.enable_robot(load=0.3)
    robot.speed_factor(60)

    # 示教点 (实际使用时需根据现场调整)
    PICK = {"x": 250, "y": 100, "z": 200, "r": 0}
    PICK_DOWN = {"x": 250, "y": 100, "z": 50, "r": 0}
    PLACE = {"x": -200, "y": 150, "z": 200, "r": 0}
    PLACE_DOWN = {"x": -200, "y": 150, "z": 50, "r": 0}
    PICK_ABOVE = {"x": 250, "y": 100, "z": 250, "r": 0}
    PLACE_ABOVE = {"x": -200, "y": 150, "z": 250, "r": 0}

    def mov_j(p):
        robot.mov_j(p["x"], p["y"], p["z"], p["r"])

    def mov_l(p):
        robot.mov_l(p["x"], p["y"], p["z"], p["r"])

    print("\n  开始抓取-放置循环...")
    for i in range(3):
        print(f"  循环 {i+1}")

        # 移动到取料点上方 → 直线下降 → 夹爪闭合 → 上升
        mov_j(PICK_ABOVE)
        mov_l(PICK_DOWN)
        robot.set_do(1, 1)              # 夹爪闭合 (DO1=ON)
        robot.wait(300)                  # 等待夹爪动作
        mov_l(PICK_ABOVE)

        # 移动到放料点上方 → 直线下降 → 夹爪张开 → 上升
        mov_j(PLACE_ABOVE)
        mov_l(PLACE_DOWN)
        robot.set_do(1, 0)              # 夹爪张开 (DO1=OFF)
        robot.wait(300)
        mov_l(PLACE_ABOVE)

    robot.sync()
    print("  完成!")

    robot.disable_robot()
    robot.disconnect()


def demo_monitor():
    """示例3: 实时监控位置变化"""
    robot = DobotMG400("192.168.1.6")
    robot.connect()
    robot.clear_error()
    robot.enable_robot()

    # 启动反馈线程
    def on_feedback(data):
        # 1440字节协议的详细解析请参考TCP/IP文档第4章
        # 前4个字节: 消息头; 后续4字节float依次为X,Y,Z,R,J1-J4等
        import struct
        vals = struct.unpack_from("<" + "f" * 40, data, 0)
        print(f"\r  X={vals[9]:.1f}  Y={vals[10]:.1f}  Z={vals[11]:.1f}  R={vals[12]:.1f}  ", end="")

    robot.start_feedback(callback=on_feedback)

    # 一边运动一边看反馈
    robot.mov_j(300, 0, 200, 0)
    robot.wait(500)
    robot.mov_j(-300, 0, 200, 0)
    robot.sync()

    robot.disable_robot()
    robot.disconnect()


if __name__ == "__main__":
    print("=" * 50)
    print("Dobot MG400 Python 控制示例")
    print("=" * 50)
    print()
    print("请先确认:")
    print("  1. 电脑IP为 192.168.1.40, 网线连接控制柜LAN1")
    print("  2. 机械臂已上电")
    print("  3. DobotStudio Pro 未占用连接 (或先断开)")
    print()
    print("选择运行模式:")
    print("  1 - 基本往复运动")
    print("  2 - 抓取-放置模拟")
    print("  3 - 实时监控")
    print()

    # 取消注释要运行的示例:
    demo_basic_motion()
    # demo_pick_and_place()
    # demo_monitor()
