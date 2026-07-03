"""Merge new VLM Agent version into server and push to GitHub."""
import paramiko, os

SERVER_IP = "139.196.187.182"
SERVER_PASS = "Debugagent666"
REMOTE = "/opt/vlm-agent/Debugging-agent-v2"
LOCAL_NEW = r"C:\Users\ZRR24\Desktop\KPIT\test_platform_whole - v3 - final - 副本"

SKIP_DIRS = {"__pycache__", ".pytest_cache", ".pytest_tmp", "workspace", ".git", "node_modules"}
SKIP_FILES = {".env", "Copy of HW Test.xlsx"}
PRESERVE = ("service.py", "logging_setup.py")

print("Connecting...")
client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(SERVER_IP, port=22, username="root", password=SERVER_PASS, timeout=15)
sftp = client.open_sftp()

# 1. Backup on server
print("Backing up HTTP layer...")
stdin, stdout, stderr = client.exec_command(
    "mkdir -p /tmp/vlm_backup && "
    f"cp {REMOTE}/agent/service.py /tmp/vlm_backup/ && "
    f"cp {REMOTE}/agent/logging_setup.py /tmp/vlm_backup/ && "
    "echo OK"
)
out = stdout.read().decode()
err = stderr.read().decode()
print((out + err).strip())

# 2. Upload new version
print("Uploading new version...")
uploaded = 0
for root, dirs, files in os.walk(LOCAL_NEW):
    dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
    rel = os.path.relpath(root, LOCAL_NEW)
    if rel == ".":
        rel = ""
    remote_dir = os.path.join(REMOTE, rel).replace("\\", "/")

    if rel:
        try:
            sftp.stat(remote_dir)
        except FileNotFoundError:
            try:
                sftp.mkdir(remote_dir)
            except Exception:
                pass

    for f in files:
        if f in SKIP_FILES:
            continue
        if f in PRESERVE:
            print(f"  SKIP (preserved): {f}")
            continue
        local_file = os.path.join(root, f)
        remote_file = os.path.join(remote_dir, f).replace("\\", "/")
        try:
            sftp.put(local_file, remote_file)
            uploaded += 1
        except Exception as e:
            print(f"  FAIL {f}: {e}")

# 3. Restore
print("Restoring HTTP layer...")
stdin, stdout, stderr = client.exec_command(
    f"cp /tmp/vlm_backup/service.py {REMOTE}/agent/service.py && "
    f"cp /tmp/vlm_backup/logging_setup.py {REMOTE}/agent/logging_setup.py && "
    "echo OK"
)
print((stdout.read().decode() + stderr.read().decode()).strip())

sftp.close()

# 4. Git commit and push
print("Git commit and push...")
stdin, stdout, stderr = client.exec_command(
    f"cd {REMOTE} && "
    "git add -A && "
    "git status --short 2>&1 | head -25 && "
    'git commit -m \"Update: add training_platform, post_run_reflect.py\" && '
    "git push origin master 2>&1"
)
out = stdout.read().decode()
err = stderr.read().decode()
print(out[-1000:] if len(out) > 1000 else out)
if err:
    print("ERR:", err[-400:])

# 5. Verify
stdin, stdout, stderr = client.exec_command(
    f"echo '=== agent/ ===' && ls {REMOTE}/agent/ && "
    f"echo '=== training_platform/ ===' && ls {REMOTE}/training_platform/ 2>/dev/null || echo '(new dir)'"
)
print(stdout.read().decode())

client.close()
print(f"Done! Uploaded {uploaded} files.")
