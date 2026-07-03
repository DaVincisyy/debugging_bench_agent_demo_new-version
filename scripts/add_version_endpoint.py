"""Add version info to health endpoint."""
import paramiko

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect("139.196.187.182", port=22, username="root", password="Debugagent666", timeout=10)

# Just do it on the server with sed
stdin, stdout, stderr = client.exec_command(
    "cd /opt/vlm-agent/Debugging-agent-v2 && "
    "sed -i 's|return {\"ok\": True, \"service\": \"debugging-agent-v2\"}|"
    "return {\"ok\": True, \"service\": \"debugging-agent-v2\", \"version\": __import__(\\\"subprocess\\\").run([\\\"git\\\",\\\"log\\\",\\\"-1\\\",\\\"--format=%h %s (%ci)\\\"],cwd=\\\"/opt/vlm-agent/Debugging-agent-v2\\\",capture_output=True,text=True,timeout=5,env={**__import__(\\\"os\\\").environ,\\\"GIT_DIR\\\":\\\"/opt/vlm-agent/Debugging-agent-v2/.git\\\"}).stdout.strip() or \\\"unknown\\\"}|' "
    "agent/service.py 2>&1"
)
print("Sed:", stdout.read().decode().strip(), stderr.read().decode().strip())

# Write back
sftp = client.open_sftp()
with sftp.file("/opt/vlm-agent/Debugging-agent-v2/agent/service.py", "w") as f:
    f.write(content)
sftp.close()

# Check syntax
stdin, stdout, stderr = client.exec_command(
    "cd /opt/vlm-agent/Debugging-agent-v2 && .venv/bin/python -c \"compile(open('agent/service.py').read(), 'service.py', 'exec'); print('Syntax OK')\" 2>&1"
)
print("Syntax:", stdout.read().decode().strip() + stderr.read().decode().strip())

# Commit + push
stdin, stdout, stderr = client.exec_command(
    "cd /opt/vlm-agent/Debugging-agent-v2 && "
    "git add agent/service.py && "
    "git commit -m 'Health endpoint now includes git version' && "
    "git push origin master 2>&1"
)
print("Push:", (stdout.read().decode() + stderr.read().decode()).strip()[-300:])

# Wait for auto-deploy
import time
time.sleep(6)

# Verify
stdin, stdout, stderr = client.exec_command("curl -s http://127.0.0.1:8000/health")
print("Health:", stdout.read().decode().strip())

client.close()
print("Done")
