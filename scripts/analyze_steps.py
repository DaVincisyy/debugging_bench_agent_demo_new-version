import json, sys, os, glob

# Find the latest run directory
base = "/opt/vlm-agent/Debugging-agent-v2"
pattern = os.path.join(base, "C:*", "workspace", "vlm-agent-runs", "*", "runs", "*")
dirs = glob.glob(pattern)
if not dirs:
    # Try alternative
    for root, dirs_, files in os.walk(base):
        for d in dirs_:
            if "run_mq7heopk" in d:
                dirs.append(os.path.join(root, d))

if not dirs:
    print("No run dir found")
    sys.exit(1)

run_dir = sorted(dirs)[-1]
print(f"Run dir: {run_dir}")

step_file = os.path.join(run_dir, "step_records.jsonl")
if not os.path.exists(step_file):
    print(f"No step_records.jsonl at {step_file}")
    sys.exit(1)

steps = []
with open(step_file) as f:
    for line in f:
        try:
            steps.append(json.loads(line))
        except:
            pass

print(f"\nTotal steps: {len(steps)}")
print(f"\n{'='*60}")
print(f"{'Step':>4} | {'Tools':<30} | {'Time':>6} | {'Phase'}")
print(f"{'='*60}")

phase_counts = {}
for s in steps:
    idx = s.get("index", "?")
    tc = s.get("assistant_tool_calls", [])
    tools = ",".join([t.get("name", "?") for t in tc[:2]])
    timing = s.get("timing", {})
    ts = timing.get("step_total_s", 0)

    # Infer phase from tools
    phase = "?"
    if any("signal_to_tp" in str(t) or "schematic" in str(t).lower() for t in tc):
        phase = "Part0-A"
    elif any("search_pdf" in t.get("name","") or "pdf_page" in t.get("name","") or "mark_tp" in t.get("name","") for t in tc):
        phase = "Part0-BC"
    elif any("assembly_largest_ic" in t.get("name","") or "assembly_vlm_hints" in str(t) for t in tc):
        phase = "PartB"
    elif any("board_largest_ic" in t.get("name","") or "board_vlm_hints" in str(t) for t in tc):
        phase = "PartA"
    elif any("case12" in str(t) or "step08" in str(t) or "finish" in t.get("name","") for t in tc):
        phase = "PartD"
    elif any("run_python" in t.get("name","") for t in tc):
        phase = "run_python"
    elif any("view_image" in t.get("name","") for t in tc):
        phase = "view_image"
    elif any("read_text" in t.get("name","") or "list_files" in t.get("name","") for t in tc):
        phase = "file_ops"

    phase_counts[phase] = phase_counts.get(phase, 0) + 1
    print(f"{idx:>4} | {tools:<30} | {ts:>5.0f}s | {phase}")

print(f"\n{'='*60}")
print("Phase breakdown:")
for phase, count in sorted(phase_counts.items(), key=lambda x: -x[1]):
    print(f"  {phase}: {count} steps")
