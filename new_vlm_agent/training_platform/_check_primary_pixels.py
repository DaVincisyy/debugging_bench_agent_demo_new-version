import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
hist = {}
for line in (REPO / "training_platform/autonomous_improvement_history.jsonl").read_text(encoding="utf-8").splitlines():
    r = json.loads(line)
    if r.get("seeded_from"):
        continue
    if not str(r.get("primary_run_id", "")).startswith("20260630"):
        continue
    hist[r["iteration"]] = r


def pixel_from_run(rid: str):
    rep = REPO / "training_platform/analysis_reports" / f"{rid}.json"
    if rep.is_file():
        pc = json.loads(rep.read_text(encoding="utf-8")).get("pixel_comparison") or {}
        return pc.get("actual"), pc.get("distance"), pc.get("within_tolerance")
    summ = REPO / "workspace/runs" / rid / "summary.json"
    if summ.is_file():
        ans = json.loads(summ.read_text(encoding="utf-8")).get("final_answer") or {}
        return ans.get("pixel"), None, None
    return None, None, None


for i in range(1, 16):
    if i == 13 and i not in hist:
        row = {"primary_run_id": "20260630-162043-task", "apply_outcome": "kept"}
    else:
        row = hist[i]
    rid = row["primary_run_id"]
    outcome = row["apply_outcome"]
    actual, dist, ok = pixel_from_run(rid)
    m = row.get("metrics") or {}
    d = dist if dist is not None else m.get("primary_pixel_distance_px")
    print(f"{i:2d}  {outcome:32s}  pixel={actual}  dist={d}  within_10px={ok}")
