"""Fix config.py on server to skip text content from path resolution."""
import paramiko, time

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect("139.196.187.182", port=22, username="root", password="Debugagent666", timeout=10)

# Read the file
sftp = client.open_sftp()
with sftp.file("/opt/vlm-agent/Debugging-agent-v2/agent/config.py", "r") as f:
    content = f.read().decode("utf-8")
sftp.close()

# Replace: add guard before Path(value) for multi-line/long text
old = "        candidate = Path(value)\n        if candidate.is_absolute():"
new = "        # Skip values that are clearly text content, not file paths\n        if \"\\n\" in value or len(value) > 200:\n            continue\n        candidate = Path(value)\n        if candidate.is_absolute():"

if old in content:
    content = content.replace(old, new, 1)
    print("Replaced")
else:
    print("Pattern not found!")
    # Show context
    for i, line in enumerate(content.split("\n")):
        if "candidate = Path(value)" in line:
            print(f"Line {i}: {line}")
            break

# Write back
sftp = client.open_sftp()
with sftp.file("/opt/vlm-agent/Debugging-agent-v2/agent/config.py", "w") as f:
    f.write(content)
sftp.close()

# Verify syntax
stdin, stdout, stderr = client.exec_command(
    'cd /opt/vlm-agent/Debugging-agent-v2 && '
    '.venv/bin/python -c "compile(open(\"agent/config.py\").read(), \"config.py\", \"exec\"); print(\"Syntax OK\")" 2>&1'
)
out = stdout.read().decode().strip()
err = stderr.read().decode().strip()
print("Syntax:", out or err)

# Commit + push
stdin, stdout, stderr = client.exec_command(
    'cd /opt/vlm-agent/Debugging-agent-v2 && '
    'git add agent/config.py && '
    'git commit -m "Fix: skip path resolution for multi-line/long text in inputs" && '
    'git push origin master 2>&1'
)
print("Push:", (stdout.read().decode() + stderr.read().decode()).strip()[-200:])

# Wait for auto-deploy
time.sleep(6)

# Verify
stdin, stdout, stderr = client.exec_command("curl -s http://127.0.0.1:8000/health")
print("Health:", stdout.read().decode().strip())

client.close()
print("Done")
