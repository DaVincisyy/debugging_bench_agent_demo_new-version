import json, os, glob

base = "/opt/vlm-agent/Debugging-agent-v2/workspace/runs"
dirs = sorted(glob.glob(os.path.join(base, "*")), key=os.path.getmtime, reverse=True)
if not dirs:
    print("No runs found")
    exit(1)

latest = dirs[0]
print(f"Latest: {latest}")

sr_path = os.path.join(latest, "step_records.jsonl")
if not os.path.exists(sr_path):
    print(f"No step_records at {sr_path}")
    exit(1)

tools = {}
steps = []
with open(sr_path) as f:
    for line in f:
        s = json.loads(line)
        steps.append(s)
        for t in s.get("assistant_tool_calls", []):
            n = t.get("name", "?")
            tools[n] = tools.get(n, 0) + 1

print(f"\nTotal steps: {len(steps)}")
print("\nTool distribution:")
for n, c in sorted(tools.items(), key=lambda x: -x[1]):
    pct = c / len(steps) * 100
    print(f"  {n}: {c} ({pct:.0f}%)")

print("\nLast 10 steps:")
for s in steps[-10:]:
    idx = s.get("index", "?")
    tc = s.get("assistant_tool_calls", [])
    tnames = ",".join([t.get("name", "?") for t in tc])
    errs = []
    for r in s.get("tool_results", []):
        if not r.get("ok", True):
            txt = r.get("text", "")
            if "[python-error]" in txt:
                errs.append(txt.split("\n")[0][:100])
            elif "[mark-tp-tool]" in txt:
                errs.append(txt.split("\n")[0][:100])
            else:
                errs.append(txt[:80])
    print(f"  Step {idx}: {tnames} {'ERROR: '+str(errs) if errs else ''}")
