import os, json, requests
for d in sorted(os.listdir("/opt/vlm-agent/Debugging-agent-v2/data/cases")):
    if not d.startswith("run_"): continue
    try:
        r = requests.get(f"http://127.0.0.1:8000/v1/runs/{d}", timeout=3)
        s = r.json()
        print(f"{d}: {s.get('status')} ev={s.get('event_count')} err={str(s.get('error',''))[:60]}")
    except Exception as e:
        print(f"{d}: {e}")
