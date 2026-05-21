"""
MG400 安全运动测试
"""
import socket, time

IP = "192.168.2.6"

class Robot:
    def __init__(self):
        self.d = socket.create_connection((IP, 29999), timeout=5)
        self.m = socket.create_connection((IP, 30003), timeout=30)

    def dash(self, cmd):
        self.d.sendall(f"{cmd}\r\n".encode())
        r = self.d.recv(1024).decode().strip()
        return r

    def move(self, cmd):
        self.m.sendall(f"{cmd}\r\n".encode())
        try:
            return self.m.recv(1024).decode().strip()
        except socket.timeout:
            return "ok(timeout)"

    def close(self):
        self.d.close()
        self.m.close()


robot = Robot()

print("=== 1. 清除报警 ===")
print(f"  ClearError: {robot.dash('ClearError()')}")
print(f"  Continue:   {robot.dash('Continue()')}")
time.sleep(1)

print("\n=== 2. 使能 ===")
print(f"  EnableRobot: {robot.dash('EnableRobot(0.5)')}")
time.sleep(1)

print(f"  RobotMode: {robot.dash('RobotMode()')}")
print(f"  GetErrorID: {robot.dash('GetErrorID()')}")

print("\n=== 3. 当前位置 ===")
print(f"  Pose:  {robot.dash('GetPose()')}")
print(f"  Angle: {robot.dash('GetAngle()')}")

# 读状态码
r = robot.dash("RobotMode()")
if ",{5}," in r:
    print("\n=== 4. 沿 X+ 移 20mm ===")
    print(f"  {robot.move('RelMovJUser(20, 0, 0, 0, 0)')}")
    time.sleep(2)
    print(f"  新位置: {robot.dash('GetPose()')}")

    print("\n=== 5. 沿 X- 移回 20mm ===")
    print(f"  {robot.move('RelMovJUser(-20, 0, 0, 0, 0)')}")
    time.sleep(2)
    print(f"  新位置: {robot.dash('GetPose()')}")

    print("\n=== 6. 移动到 (350, 0, 200, 0) ===")
    print(f"  {robot.move('MovJ(350, 0, 200, 0)')}")
    time.sleep(2)
    print(f"  位置: {robot.dash('GetPose()')}")

    print("\n=== 7. 移动到 (300, 0, 200, 0) ===")
    print(f"  {robot.move('MovJ(300, 0, 200, 0)')}")
    time.sleep(2)
    print(f"  位置: {robot.dash('GetPose()')}")

else:
    print("\n✗ 机器人未就绪，跳过运动")

robot.close()
print("\n=== 完成 ===")
