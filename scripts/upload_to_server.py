"""Upload VLM Agent code to remote server via SFTP."""
import os, sys
import paramiko

SERVER_IP = "139.196.187.182"
SERVER_USER = "root"
SERVER_PASSWORD = "Debugagent666"
REMOTE_BASE = "/opt/vlm-agent/Debugging-agent-v2"
LOCAL_BASE = r"C:\Users\ZRR24\Desktop\KPIT\debugging_bench_agent_demo\Vlm agent\Debugging-agent-v2"

EXCLUDE_DIRS = {
    "__pycache__", ".pytest_cache", ".pytest_tmp",
    "workspace", "node_modules", ".git", "deploy",
}
EXCLUDE_FILES = {
    "Copy of HW Test.xlsx", "voyah_hvac_v01_20240729.pdf",
    "位号图7.29.pdf",  # large PDF test files, not needed on server
}

print(f"Connecting to {SERVER_IP}...")
client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(SERVER_IP, port=22, username=SERVER_USER, password=SERVER_PASSWORD, timeout=15)

# Create base directory
stdin, stdout, stderr = client.exec_command(f"mkdir -p {REMOTE_BASE} && ls {REMOTE_BASE}/..")
print("Server:", stdout.read().decode().strip())

# Upload via SFTP
sftp = client.open_sftp()
uploaded = 0
skipped = 0

for root, dirs, files in os.walk(LOCAL_BASE):
    dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
    rel_path = os.path.relpath(root, LOCAL_BASE)
    if rel_path == ".":
        rel_path = ""

    remote_dir = os.path.join(REMOTE_BASE, rel_path).replace("\\", "/")
    if rel_path:
        try:
            sftp.stat(remote_dir)
        except FileNotFoundError:
            sftp.mkdir(remote_dir)

    for f in files:
        if f in EXCLUDE_FILES:
            skipped += 1
            continue
        local_file = os.path.join(root, f)
        remote_file = os.path.join(remote_dir, f).replace("\\", "/")
        try:
            sftp.put(local_file, remote_file)
            uploaded += 1
        except Exception as e:
            print(f"  SKIP {f}: {e}")
            skipped += 1

    if uploaded % 10 == 0 and uploaded > 0:
        print(f"  Uploaded {uploaded} files...")

sftp.close()
print(f"Upload complete: {uploaded} files uploaded, {skipped} skipped")

# Verify key files exist
stdin, stdout, stderr = client.exec_command(
    "ls /opt/vlm-agent/Debugging-agent-v2/agent/ && "
    "echo '---' && "
    "wc -l /opt/vlm-agent/Debugging-agent-v2/agent/service.py"
)
print(stdout.read().decode())

client.close()
print("DONE")
