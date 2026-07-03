"""Fix _initial_messages in agent.py to handle multi-line text values."""
import paramiko, time

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect("139.196.187.182", port=22, username="root", password="Debugagent666", timeout=10)

path = "/opt/vlm-agent/Debugging-agent-v2/agent/agent.py"

sftp = client.open_sftp()
with sftp.file(path, "r") as f:
    content = f.read().decode("utf-8")
sftp.close()

# Fix: add a guard before Path(value) to skip text values
# Pattern: for key, value in inputs.items():
# Replace the logic inside the loop to skip multi-line/long values

old = (
    "        for key, value in inputs.items():\n"
    "            if isinstance(value, str) and Path(value).suffix.lower() in self._IMG_EXT:"
)

new = (
    "        for key, value in inputs.items():\n"
    "            # Skip text content that is clearly not a file path\n"
    "            if isinstance(value, str) and (\"\\n\" in value or len(value) > 200):\n"
    "                text_context_lines.append(f\"- {key}: {value}\")\n"
    "                continue\n"
    "            if isinstance(value, str) and Path(value).suffix.lower() in self._IMG_EXT:"
)

if old in content:
    content = content.replace(old, new)
    print("Added text guard in _initial_messages")
else:
    print("Pattern not found!")
    # Show context
    for i, line in enumerate(content.split("\n")):
        if "for key, value in inputs.items()" in line and "isinstance(value, str) and Path(value).suffix" in content.split("\n")[i+1] if i+1 < len(content.split("\n")) else "":
            print(f"Line {i+1}: {line}")
            break
    else:
        # Find the loop
        for i, line in enumerate(content.split("\n")):
            if "for key, value in inputs.items()" in line:
                print(f"Line {i+1}: {line}")
                print(f"Line {i+2}: {content.split(chr(10))[i+1]}")
                break

# Also fix the elif check
old2 = (
    "            elif isinstance(value, str) and Path(value).exists():\n"
    "                text_context_lines.append(f\"- [file] {key} = {value}\")"
)
# This is already guarded by the above since we skip multi-line values
# But .exists() on a long path could still fail - let's be extra safe
new2 = (
    "            elif isinstance(value, str) and len(value) <= 200 and Path(value).exists():\n"
    "                text_context_lines.append(f\"- [file] {key} = {value}\")"
)
if old2 in content:
    content = content.replace(old2, new2)
    print("Added len guard to Path.exists() check")

# Write back
sftp = client.open_sftp()
with sftp.file(path, "w") as f:
    f.write(content)
sftp.close()

# Verify syntax
stdin, stdout, stderr = client.exec_command(
    'cd /opt/vlm-agent/Debugging-agent-v2 && '
    '.venv/bin/python -c "compile(open(\"agent/agent.py\").read(), \"agent.py\", \"exec\"); print(\"Syntax OK\")" 2>&1'
)
out = stdout.read().decode().strip()
err = stderr.read().decode().strip()
print("Syntax:", out or err)

# Commit + push
stdin, stdout, stderr = client.exec_command(
    'cd /opt/vlm-agent/Debugging-agent-v2 && '
    'git add agent/agent.py && '
    'git commit -m "Fix: guard Path() calls against multi-line/long text in inputs" && '
    'git push origin master 2>&1'
)
print("Push:", (stdout.read().decode() + stderr.read().decode()).strip()[-200:])

time.sleep(6)

stdin, stdout, stderr = client.exec_command("curl -s http://127.0.0.1:8000/health")
print("Health:", stdout.read().decode().strip())

client.close()
print("Done")
