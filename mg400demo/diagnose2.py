"""
MG400 诊断 v2 - 检查连接时序
"""
import socket, time

IP = "192.168.2.6"

print("=== 1. 建连后等一秒 ===")
dash = socket.create_connection((IP, 29999), timeout=5)
time.sleep(1)

# 先收一下有没有欢迎消息
dash.settimeout(0.5)
try:
    banner = dash.recv(1024)
    print(f"  欢迎消息: {banner}")
except:
    print("  无欢迎消息")
dash.settimeout(5)

print("\n=== 2. 发第一条指令 ===")
dash.sendall(b"RobotMode()\r\n")
resp = dash.recv(1024).decode("utf-8").strip()
print(f"  RobotMode() => {resp}")

print("\n=== 3. 换 \\n 试 (不加 \\r) ===")
motion = socket.create_connection((IP, 30003), timeout=5)
motion.sendall(b"GetPose()\n")
resp = motion.recv(1024).decode("utf-8").strip()
print(f"  GetPose() => {resp}")

dash.close()
motion.close()
