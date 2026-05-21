"""
MG400 诊断脚本 - 排查为什么不动
"""
import socket, time

IP = "192.168.2.6"

def cmd(sock, text):
    sock.sendall((text + "\r\n").encode("utf-8"))
    resp = sock.recv(1024).decode("utf-8").strip()
    print(f"  {text:35s} => {resp}")
    return resp

print("=== 连接 ===")
dash = socket.create_connection((IP, 29999), timeout=5)
motion = socket.create_connection((IP, 30003), timeout=5)

print("\n=== 1. 检查当前状态 ===")
cmd(dash, "RobotMode()")
cmd(dash, "GetErrorID()")
cmd(dash, "GetAngle()")
cmd(dash, "GetPose()")

print("\n=== 2. 清除报警 ===")
cmd(dash, "ClearError()")
cmd(dash, "Continue()")

print("\n=== 3. 再次检查状态 ===")
cmd(dash, "RobotMode()")
cmd(dash, "GetErrorID()")

print("\n=== 4. 使能 ===")
cmd(dash, "EnableRobot()")

time.sleep(1)

print("\n=== 5. 使能后状态 ===")
cmd(dash, "RobotMode()")

print("\n=== 6. 发运动指令 ===")
cmd(motion, "MovJ(300, 0, 0, 0)")

time.sleep(3)

print("\n=== 7. 运动后状态 ===")
cmd(dash, "RobotMode()")
cmd(dash, "GetPose()")

dash.close()
motion.close()
print("\n=== 完成 ===")
