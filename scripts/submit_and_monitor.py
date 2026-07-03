"""Submit case-015 via Node.js and monitor full execution on cloud server."""
import requests, base64, json, time, os, sys

session = requests.Session()
session.trust_env = False

# 1. Verify services
print("=== Checking services ===")
for url, label in [
    ("http://127.0.0.1:3000/health", "Node.js"),
    ("http://127.0.0.1:8010/health", "Robot Gateway"),
    ("http://139.196.187.182:8000/health", "Cloud VLM Agent"),
]:
    try:
        r = session.get(url, timeout=5)
        ok = "ok" in r.text.lower()
        print(f"  [{('OK' if ok else 'FAIL')}] {label}: {url}")
    except Exception as e:
        print(f"  [DOWN] {label}: {e}")

# 2. Build payload
print()
print("=== Building payload ===")
case = r"C:\Users\ZRR24\Desktop\KPIT\debugging_bench_agent_demo\Vlm agent\Debugging-agent-v2\data\cases\case-015"
files = os.listdir(case)

payload = {
    "Instruction": "测试输入电压是否正确",
    "Case_ID": "case-015",
    "Operator": "claude-monitor",
}

# Camera
with open(os.path.join(case, "PCBA_IMG.jpg"), "rb") as fh:
    cam_b64 = base64.b64encode(fh.read()).decode()
payload["Camera_image"] = {
    "name": "PCBA_IMG.jpg", "type": "image/jpeg",
    "dataUrl": f"data:image/jpeg;base64,{cam_b64}"
}
print(f"  Camera: PCBA_IMG.jpg ({len(cam_b64)} b64)")

# Bit images
bit_imgs = []
for f in files:
    if f in ("PCBA_IMG.jpg", "task.yaml"):
        continue
    ext = f.rsplit(".", 1)[-1].lower()
    mime = "image/png" if ext == "png" else "application/pdf"
    with open(os.path.join(case, f), "rb") as fh:
        data_b64 = base64.b64encode(fh.read()).decode()
    url = f"data:{mime};base64,{data_b64}"
    bit_imgs.append({"kind": "bit_image", "name": f, "type": mime, "dataUrl": url})
    print(f"  Bit: {f} ({len(data_b64)} b64)")
payload["Bit_image"] = bit_imgs

# Schematic
with open(os.path.join(case, "voyah_hvac_v01_20240729_01.png"), "rb") as fh:
    sch_b64 = base64.b64encode(fh.read()).decode()
payload["Schematic_Diagram"] = [{
    "kind": "schematic_diagram", "name": "voyah_hvac_v01_20240729_01.png",
    "type": "image/png", "dataUrl": f"data:image/png;base64,{sch_b64}"
}]
print(f"  Schematic: voyah_hvac_v01_20240729_01.png ({len(sch_b64)} b64)")

# 3. Submit
print()
print("=== Submitting ===")
resp = session.post("http://127.0.0.1:3000/api/runs", json=payload, timeout=300)
r = resp.json()
run_id = r.get("runId")
print(f"  HTTP {resp.status_code}")
print(f"  runId: {run_id}")
print(f"  state: {r.get('state')}")
print(f"  serviceMode: {r.get('serviceMode')}")

if not run_id:
    print("ERROR: No runId returned!")
    print(json.dumps(r, indent=2, ensure_ascii=False)[:500])
    sys.exit(1)

# 4. Monitor
print()
print("=== Monitoring cloud server ===")
start = time.time()
last_events = 0

for i in range(180):  # up to 15 min
    time.sleep(5)
    elapsed = time.time() - start

    try:
        r2 = session.get(f"http://139.196.187.182:8000/v1/runs/{run_id}", timeout=5)
        if r2.status_code != 200:
            print(f"  [{elapsed:5.0f}s] HTTP {r2.status_code} - waiting...")
            continue

        s = r2.json()
        status = s.get("status", "?")
        events = s.get("event_count", 0)
        error = s.get("error")
        fa = s.get("final_answer")

        change = ""
        if events > last_events:
            change = f" (+{events - last_events})"
            last_events = events

        if error:
            print(f"  [{elapsed:5.0f}s] ERROR: {str(error)[:120]}")
            break
        elif status == "succeeded":
            print(f"  [{elapsed:5.0f}s] SUCCESS! events={events}{change}")
            if fa:
                print(f"    tp={fa.get('tp_id')} pixel={fa.get('pixel')} confidence={fa.get('confidence')}")
            break
        elif status == "failed":
            print(f"  [{elapsed:5.0f}s] FAILED: {s.get('stopped_reason')} events={events}")
            break
        else:
            print(f"  [{elapsed:5.0f}s] {status} events={events}{change}")
    except Exception as e:
        print(f"  [{elapsed:5.0f}s] poll error: {e}")

# 5. Check Node.js final state
print()
print("=== Final state ===")
try:
    r3 = session.get(f"http://139.196.187.182:8000/v1/runs/{run_id}", timeout=5)
    s = r3.json()
    print(f"Cloud: status={s.get('status')} events={s.get('event_count')}")
    fa = s.get("final_answer")
    if fa:
        print(f"  tp={fa.get('tp_id')} pixel={fa.get('pixel')} confidence={fa.get('confidence')}")
    err = s.get("error")
    if err:
        print(f"  error: {str(err)[:200]}")
except Exception as e:
    print(f"Cloud: error={e}")

try:
    r4 = session.get(f"http://127.0.0.1:3000/api/runs/{run_id}", timeout=5)
    n = r4.json()
    print(f"Node.js: state={n.get('state')} report={'YES' if n.get('report') else 'NO'}")
    if n.get("report"):
        print(f"  Report generated!")
    if n.get("error"):
        print(f"  Node error: {n.get('error')[:200]}")
except Exception as e:
    print(f"Node.js: error={e}")

print(f"\nTotal time: {time.time()-start:.0f}s")
print(f"Run ID: {run_id}")
print("Done")
