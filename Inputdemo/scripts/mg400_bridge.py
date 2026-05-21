import json
import socket
import sys
import time


DEFAULT_LIMITS = {
    "x": (-450.0, 450.0),
    "y": (-450.0, 450.0),
    "z": (-250.0, 250.0),
    "r": (-360.0, 360.0),
}

MODE_LABELS = {
    1: "INIT",
    2: "BRAKE_OPEN",
    3: "POWER_OFF",
    4: "DISABLED",
    5: "ENABLED_IDLE",
    6: "DRAG",
    7: "RUNNING",
    8: "RECORDING",
    9: "ERROR",
    10: "PAUSED",
    11: "JOGGING",
}


class Mg400Error(RuntimeError):
    pass


def friendly_socket_error(error, ip, port):
    if isinstance(error, socket.timeout):
        return f"连接 MG400 超时：{ip}:{port}。请检查本机以太网 IP、网线、机械臂 IP 和 TCP/IP 二次开发是否开启。"
    if isinstance(error, ConnectionRefusedError):
        return f"MG400 拒绝连接：{ip}:{port}。请检查端口是否开启，或 DobotStudio 是否占用连接。"
    if isinstance(error, OSError):
        return f"无法连接 MG400：{ip}:{port}，系统错误 {error}。"
    return str(error)


class Mg400:
    def __init__(self, config):
        self.config = config
        self.ip = config["ip"]
        self.dashboard_port = int(config.get("dashboardPort", 29999))
        self.motion_port = int(config.get("motionPort", 30003))
        self.timeout = float(config.get("timeoutMs", 5000)) / 1000.0
        self.dashboard = None
        self.motion = None

    def connect_dashboard(self):
        try:
            self.dashboard = socket.create_connection(
                (self.ip, self.dashboard_port), timeout=self.timeout
            )
        except OSError as error:
            raise Mg400Error(friendly_socket_error(error, self.ip, self.dashboard_port)) from error

    def connect_motion(self):
        try:
            self.motion = socket.create_connection(
                (self.ip, self.motion_port), timeout=max(self.timeout, 30.0)
            )
        except OSError as error:
            raise Mg400Error(friendly_socket_error(error, self.ip, self.motion_port)) from error

    def connect(self):
        self.connect_dashboard()
        self.connect_motion()

    def close(self):
        for sock in (self.dashboard, self.motion):
            if sock:
                try:
                    sock.close()
                except OSError:
                    pass

    def _send(self, sock, command):
        sock.sendall((command + "\r\n").encode("utf-8"))
        response = sock.recv(2048).decode("utf-8", errors="replace").strip()
        try:
            error_id = int(response.split(",", 1)[0])
        except ValueError:
            error_id = -999
        return {"command": command, "response": response, "errorId": error_id, "ok": error_id == 0}

    def dash(self, command):
        if not self.dashboard:
            self.connect_dashboard()
        return self._send(self.dashboard, command)

    def move(self, command):
        if not self.motion:
            self.connect_motion()
        try:
            return self._send(self.motion, command)
        except socket.timeout:
            return {"command": command, "response": "timeout; command may still be accepted", "errorId": 0, "ok": True}

    def robot_mode(self):
        result = self.dash("RobotMode()")
        code = None
        label = None
        if result["ok"]:
            try:
                code = int(result["response"].split(",{", 1)[1].split("}", 1)[0])
                label = MODE_LABELS.get(code, f"UNKNOWN_{code}")
            except (IndexError, ValueError):
                label = "PARSE_FAILED"
        return {"code": code, "label": label, "raw": result}

    def get_pose(self):
        result = self.dash("GetPose()")
        pose = None
        if result["ok"]:
            try:
                values = result["response"].split(",{", 1)[1].split("}", 1)[0].split(",")
                pose = {
                    "x": float(values[0]),
                    "y": float(values[1]),
                    "z": float(values[2]),
                    "r": float(values[3]),
                }
            except (IndexError, ValueError):
                pose = None
        return {"pose": pose, "raw": result}


def read_payload():
    raw = sys.stdin.read().strip()
    return json.loads(raw) if raw else {}


def emit(payload, code=0):
    print(json.dumps(payload, ensure_ascii=True, indent=2))
    sys.exit(code)


def require_ok(result):
    if not result["ok"]:
        raise Mg400Error(f"{result['command']} failed: {result['response']}")
    return result


def validate_pose(pose):
    clean = {}
    for key, (low, high) in DEFAULT_LIMITS.items():
        value = float(pose[key])
        if not low <= value <= high:
            raise Mg400Error(f"{key.upper()}={value} is outside safe range [{low}, {high}]")
        clean[key] = value
    return clean


def resolve_partial_pose(robot, pose):
    current = robot.get_pose()["pose"]
    if not current:
        raise Mg400Error("Current robot pose is unavailable; fill X/Y/Z/R explicitly.")
    merged = {}
    for key in ("x", "y", "z", "r"):
        value = pose.get(key)
        merged[key] = current[key] if value is None or value == "" else value
    return validate_pose(merged)


def format_number(value):
    number = float(value)
    return str(int(number)) if number.is_integer() else str(number)


def speed_value(config):
    return int(float(config.get("speed", 30)))


def status(robot):
    mode = robot.robot_mode()
    pose = robot.get_pose()
    return {
        "mode": mode,
        "pose": pose["pose"],
        "poseRaw": pose["raw"],
    }


def prepare_for_motion(robot, config):
    responses = []
    responses.append(require_ok(robot.dash("ClearError()")))
    responses.append(require_ok(robot.dash("Continue()")))
    mode = robot.robot_mode()
    if mode["code"] == 4 and config.get("autoEnable", True):
        responses.append(enable_robot(robot, config))
        time.sleep(1)
        mode = robot.robot_mode()
    if mode["code"] not in (5, 7, 11):
        raise Mg400Error(f"Robot is not ready for motion: {mode['label']} ({mode['code']})")
    responses.append(require_ok(robot.dash(f"SpeedFactor({speed_value(config)})")))
    return responses


def enable_robot(robot, config):
    attempts = ["EnableRobot()"]
    load = config.get("load")
    if load is not None:
        attempts.append(f"EnableRobot({float(load)})")
        attempts.append(f"EnableRobot({float(load)},0,0,0)")

    results = []
    for command in attempts:
        result = robot.dash(command)
        results.append(result)
        if result["ok"]:
            return {"command": command, "response": result["response"], "errorId": result["errorId"], "ok": True, "attempts": results}

    last = results[-1]
    raise Mg400Error(f"EnableRobot failed after {len(results)} attempts: {last['response']}")


def action_test(payload):
    config = payload["config"]
    robot = Mg400(config)
    started = time.time()
    try:
        robot.connect()
        data = status(robot)
        return {
            "ok": True,
            "action": "test",
            "robot": data,
            "elapsedMs": round((time.time() - started) * 1000),
        }
    finally:
        robot.close()


def action_status(payload):
    config = payload["config"]
    robot = Mg400(config)
    try:
        robot.connect_dashboard()
        return {"ok": True, "action": "status", "robot": status(robot)}
    finally:
        robot.close()


def action_execute(payload):
    config = payload["config"]
    pose = validate_pose(payload["pose"])
    command_name = config.get("motionCommand", "MovJ")
    speed_key = "SpeedL" if command_name == "MovL" else "SpeedJ"
    command = (
        f"{command_name}({format_number(pose['x'])},{format_number(pose['y'])},"
        f"{format_number(pose['z'])},{format_number(pose['r'])},"
        f"{speed_key}={speed_value(config)})"
    )

    robot = Mg400(config)
    responses = []
    try:
        robot.connect()
        responses.extend(prepare_for_motion(robot, config))
        responses.append(require_ok(robot.move(command)))
        responses.append(require_ok(robot.move("Sync()")))
        if config.get("returnHome"):
            home = validate_pose(config["homePose"])
            home_command = (
                f"MovJ({format_number(home['x'])},{format_number(home['y'])},"
                f"{format_number(home['z'])},{format_number(home['r'])},SpeedJ={speed_value(config)})"
            )
            responses.append(require_ok(robot.move(home_command)))
            responses.append(require_ok(robot.move("Sync()")))
        return {
            "ok": True,
            "action": "execute",
            "command": command,
            "responses": responses,
            "robot": status(robot),
        }
    finally:
        robot.close()


def action_command(payload):
    config = payload["config"]
    command = payload.get("command", {})
    name = command.get("name")
    robot = Mg400(config)
    try:
        robot.connect()
        if name == "pose":
            return {"ok": True, "action": name, "robot": status(robot)}
        if name == "clearError":
            result = require_ok(robot.dash("ClearError()"))
        elif name == "enable":
            result = enable_robot(robot, config)
        elif name == "disable":
            result = require_ok(robot.dash("DisableRobot()"))
        elif name == "pause":
            result = require_ok(robot.dash("Pause()"))
        elif name == "continue":
            result = require_ok(robot.dash("Continue()"))
        elif name == "reset":
            result = require_ok(robot.dash("ResetRobot()"))
        elif name == "jog":
            prepare = prepare_for_motion(robot, config)
            axis = str(command.get("axis", "")).upper()
            if axis not in {"X+", "X-", "Y+", "Y-", "Z+", "Z-", "R+", "R-", "J1+", "J1-", "J2+", "J2-", "J3+", "J3-", "J4+", "J4-"}:
                raise Mg400Error(f"Unsupported jog axis: {axis}")
            result = require_ok(robot.move(f"MoveJog({axis})"))
            result = {"motionPrep": prepare, **result}
        elif name == "jogStop":
            result = require_ok(robot.move("MoveJog()"))
        elif name == "move":
            prepare = prepare_for_motion(robot, config)
            pose = resolve_partial_pose(robot, command.get("pose", {}))
            move_name = command.get("motionCommand") or config.get("motionCommand", "MovJ")
            speed_key = "SpeedL" if move_name == "MovL" else "SpeedJ"
            result = require_ok(robot.move(
                f"{move_name}({format_number(pose['x'])},{format_number(pose['y'])},"
                f"{format_number(pose['z'])},{format_number(pose['r'])},{speed_key}={speed_value(config)})"
            ))
            result = {"motionPrep": prepare, "resolvedPose": pose, **result}
        else:
            raise Mg400Error(f"Unsupported command: {name}")
        return {"ok": True, "action": name, "result": result, "robot": status(robot)}
    finally:
        robot.close()


HANDLERS = {
    "test": action_test,
    "status": action_status,
    "execute": action_execute,
    "command": action_command,
}


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    if action not in HANDLERS:
        emit({"ok": False, "error": f"Unknown action: {action}"}, 2)
    try:
        emit(HANDLERS[action](read_payload()))
    except Exception as error:
        emit({"ok": False, "error": str(error)}, 1)


if __name__ == "__main__":
    main()
