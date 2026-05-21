"""
测试 LAN2 口 TCP/IP 控制
========================
电脑 IP:   192.168.2.40
机械臂 IP: 192.168.2.6 (LAN2 默认)
端口:      29999 (Dashboard) + 30003 (运动)
"""

import socket
import time

def test_connection(ip="192.168.2.6", port=29999):
    """测试能否连接指定端口"""
    try:
        sock = socket.create_connection((ip, port), timeout=3)
        print(f"  ✓ 端口 {port} 连接成功")
        sock.close()
        return True
    except Exception as e:
        print(f"  ✗ 端口 {port} 连接失败: {e}")
        return False

def send_cmd(sock, cmd):
    """发送指令并返回响应"""
    sock.sendall((cmd + "\r\n").encode("utf-8"))
    return sock.recv(1024).decode("utf-8").strip()

# ============================================================
# 1. 检查网络连通性
# ============================================================
print("=" * 50)
print("LAN2 口控制测试")
print("=" * 50)
print()
print("请确认：")
print("  1. 电脑 IP 设为 192.168.2.40，掩码 255.255.255.0")
print("  2. 网线接在控制柜 LAN2 口")
print("  3. 机械臂已上电")
print()

# ============================================================
# 2. 连接测试
# ============================================================
print("--- 端口连接测试 ---")
ok1 = test_connection("192.168.2.6", 29999)
ok2 = test_connection("192.168.2.6", 30003)

if not ok1 or not ok2:
    print("\nLAN2 连接失败，可能原因：")
    print("  1. 电脑 IP 没设对 -> 检查是否为 192.168.2.40")
    print("  2. 网线没插好 -> 检查 LAN2 口指示灯")
    print("  3. 控制柜没上电")
    print("  4. 换根网线试试")
    exit()

# ============================================================
# 3. 发送命令测试
# ============================================================
print("--- 指令收发测试 ---")

# 建连
dash = socket.create_connection(("192.168.2.6", 29999), timeout=5)

# 查询机器人型号
resp = send_cmd(dash, "RobotMode()")
print(f"  机器人状态: {resp}")

# 获取当前关节角度
resp = send_cmd(dash, "GetAngle()")
print(f"  当前关节角度: {resp}")

# 获取当前笛卡尔坐标
resp = send_cmd(dash, "GetPose()")
print(f"  当前位姿: {resp}")

dash.close()
print()
print("测试完成")
