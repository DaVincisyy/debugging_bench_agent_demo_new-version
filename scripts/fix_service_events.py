"""Fix the broken events endpoint in service.py."""
import paramiko, time

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect("139.196.187.182", port=22, username="root", password="Debugagent666", timeout=10)

# Read the file
sftp = client.open_sftp()
with sftp.file("/opt/vlm-agent/Debugging-agent-v2/agent/service.py", "r") as f:
    content = f.read().decode("utf-8")
sftp.close()

# Find and fix the broken get_events function
old_marker = '@app.get("/v1/runs/{run_id}/events")'
newline_marker = 'async def get_events(run_id: str, request: Request, since: int = 0) -> Any:'

# Find the function boundaries
lines = content.split("\n")
start_idx = None
end_idx = None
for i, line in enumerate(lines):
    if old_marker in line:
        start_idx = i
    if start_idx is not None and '@app.' in line and i > start_idx:
        end_idx = i
        break

if end_idx is None:
    # No next route found - find end of file or webhook section
    for i in range(start_idx, len(lines)):
        if '#  GitHub webhook' in lines[i]:
            end_idx = i - 1
            break
    if end_idx is None:
        end_idx = len(lines) - 1

print(f"Replacing lines {start_idx+1} to {end_idx+1}")

# Build the fixed function
fixed = '''@app.get("/v1/runs/{run_id}/events")
async def get_events(run_id: str, request: Request, since: int = 0) -> Any:
    """Get events for a run, with optional SSE streaming."""
    run = manager.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found.")

    wants_sse = "text/event-stream" in request.headers.get("accept", "")
    if not wants_sse:
        return {"run_id": run_id, "events": manager.events_since(run_id, since)}

    async def stream():
        seq = since
        while True:
            events = manager.events_since(run_id, seq)
            for event in events:
                seq = int(event["seq"])
                yield f"event: {event['type']}\\n"
                yield f"data: {json.dumps(event, ensure_ascii=False, default=str)}\\n\\n"
            current = manager.get(run_id)
            if current is None or current.status in TERMINAL_STATES:
                break
            if await request.is_disconnected():
                break
            await asyncio.sleep(0.5)

    return StreamingResponse(stream(), media_type="text/event-stream")'''

# Replace
old_block = "\n".join(lines[start_idx:end_idx])
content = content.replace(old_block, fixed)

# Write back
sftp = client.open_sftp()
with sftp.file("/opt/vlm-agent/Debugging-agent-v2/agent/service.py", "w") as f:
    f.write(content)
sftp.close()

print(f"Wrote fixed events endpoint")

# Verify syntax on server
stdin, stdout, stderr = client.exec_command(
    'cd /opt/vlm-agent/Debugging-agent-v2 && '
    '.venv/bin/python -c "compile(open(\"agent/service.py\",encoding=\"utf-8\").read(), \"service.py\", \"exec\"); print(\"Syntax OK\")"'
)
out = stdout.read().decode().strip()
err = stderr.read().decode().strip()
print("Syntax:", out or err)

# Start service
stdin, stdout, stderr = client.exec_command(
    'cd /opt/vlm-agent/Debugging-agent-v2 && '
    'nohup .venv/bin/python -m uvicorn agent.service:app --host 0.0.0.0 --port 8000 --log-level info > /var/log/vlm-agent.log 2>&1 &'
)
time.sleep(5)

stdin, stdout, stderr = client.exec_command('curl -s http://127.0.0.1:8000/health')
print("Health:", stdout.read().decode().strip())

if '"ok":true' in stdout.read().decode():
    # Test events
    stdin, stdout, stderr = client.exec_command('curl -s "http://127.0.0.1:8000/v1/runs/e2e-qfix/events?since=0"')
    print("Events:", stdout.read().decode()[:200])

    # Commit
    stdin, stdout, stderr = client.exec_command(
        'cd /opt/vlm-agent/Debugging-agent-v2 && '
        'git add agent/service.py && '
        'git commit -m "Fix: repair broken events endpoint f-string" && '
        'git push origin master 2>&1'
    )
    print("Push:", (stdout.read().decode() + stderr.read().decode()).strip()[-200:])

client.close()
print("Done")
