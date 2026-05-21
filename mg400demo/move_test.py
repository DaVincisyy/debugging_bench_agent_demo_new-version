"""
MG400 运动控制测试
==================
目标: 从 (350, 0, 0, 0) 移动到 (300, 0, 0, 0)
连接: LAN2 (192.168.2.6)
"""

import socket
import time

IP = "192.168.2.6"

def cmd(sock, text):
    """发指令并打印结果"""
    sock.sendall((text + "\r\n").encode("utf-8"))
    resp = sock.recv(1024).decode("utf-8").strip()
    print(f"  >> {text}")
    print(f"  << {resp}")
    print()
    return resp

# 连接
print("=== 连接机械臂 ===")
dash = socket.create_connection((IP, 29999), timeout=5)
motion = socket.create_connection((IP, 30003), timeout=5)
print("  已连接\n")

# 1. 清除报警
print("=== 1. 清除报警 ===")
cmd(dash, "ClearError()")
cmd(dash, "Continue()")

# 2. 使能
print("=== 2. 使能 ===")
cmd(dash, "EnableRobot(0.5)")

time.sleep(1)

# 3. 查询当前位姿
print("=== 3. 当前位置 ===")
cmd(dash, "GetPose()")
cmd(dash, "GetAngle()")

# 4. 移动到 (300, 0, 0, 0)
print("=== 4. 移动到 (300, 0, 0, 0) ===")
cmd(motion, "MovJ(300, 0, 0, 0)")

# 等待运动完成
time.sleep(2)

# 5. 确认到位
print("=== 5. 最终位置 ===")
cmd(dash, "GetPose()")

dash.close()
motion.close()
print("=== 完成 ===")
