"""
快速验证 TCP/IP 控制是否正常
"""
import socket, time

IP = "192.168.2.6"

dash = socket.create_connection((IP, 29999), timeout=5)
motion = socket.create_connection((IP, 30003), timeout=5)

def ok(cmd, sock=dash):
    sock.sendall((cmd + "\r\n").encode("utf-8"))
    resp = sock.recv(1024).decode("utf-8").strip()
    err = int(resp.split(",")[0])
    print(f"  {cmd:35s} => {'✓' if err == 0 else '✗'}  {resp}")
    return err == 0

print("=== 状态查询 ===")
ok("RobotMode()")
ok("GetPose()")
ok("GetAngle()")

print("\n=== 使能 ===")
ok("EnableRobot(0.5)")
time.sleep(1)
ok("RobotMode()")

print("\n=== 运动测试 ===")
ok("MovJ(300, 0, 0, 0)", motion)
time.sleep(2)
ok("GetPose()")

print("\n=== 完成 ===")
dash.close()
motion.close()
