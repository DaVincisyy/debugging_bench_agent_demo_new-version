"""Fix health endpoint in service.py to include git version."""
path = "/opt/vlm-agent/Debugging-agent-v2/agent/service.py"
with open(path, "r") as f:
    content = f.read()

old = 'return {"ok": True, "service": "debugging-agent-v2"}'
new = (
    'import subprocess, os\n'
    '    version = "unknown"\n'
    '    try:\n'
    '        r = subprocess.run(\n'
    '            ["git", "log", "-1", "--format=%h %s (%ci)"],\n'
    '            cwd="/opt/vlm-agent/Debugging-agent-v2",\n'
    '            capture_output=True, text=True, timeout=5,\n'
    '            env={**os.environ, "GIT_DIR": "/opt/vlm-agent/Debugging-agent-v2/.git"}\n'
    '        )\n'
    '        version = r.stdout.strip() or "unknown"\n'
    '    except Exception:\n'
    '        pass\n'
    '    return {"ok": True, "service": "debugging-agent-v2", "version": version}'
)

if old in content:
    content = content.replace(old, new)
    with open(path, "w") as f:
        f.write(content)
    print("Replaced health endpoint")
else:
    print("Pattern not found - file may already be updated")
    if '"version"' in content:
        print("Version field already exists in health endpoint")
    else:
        print("ERROR: Could not find health endpoint pattern")
