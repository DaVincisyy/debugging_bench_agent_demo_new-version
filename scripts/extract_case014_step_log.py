from pathlib import Path
import json
import re

run = Path(
    r"C:\Users\ZRR24\Desktop\KPIT\debugging_bench_agent_demo\Vlm agent\Debugging-agent-v2\workspace\vlm-agent-runs\case-014\runs\20260601-131640-run_mpuran69_wnckrbwc"
)

step_file = run / "step_records.jsonl"
log_file = run / "agent.log"
summary_file = run / "summary.json"

if not step_file.exists():
    raise SystemExit(f"Missing {step_file}")

# Parse summary with only key fields.
summary = json.loads(summary_file.read_text(encoding="utf-8", errors="replace"))
stopped_reason = summary.get("stopped_reason")
final_answer = summary.get("final_answer")

# Parse timestamp + timing from agent.log
pat = re.compile(
    r"\[([^\]]+)\]\s+step=(\d+)\s+total_s=([\d.]+)\s+llm_s=([\d.]+)\s+tools_s=([\d.]+)\s+assistant_return=(.*?)\s+final_answer=(.*)$"
)
timing_by_step = {}
for line in log_file.read_text(encoding="utf-8", errors="replace").splitlines():
    m = pat.search(line)
    if not m:
        continue
    ts, idx, total_s, llm_s, tools_s, _assistant_prev, _final_prev = m.groups()
    timing_by_step[int(idx)] = {
        "timestamp": ts,
        "total_s": float(total_s),
        "llm_s": float(llm_s),
        "tools_s": float(tools_s),
    }

records = [
    json.loads(line)
    for line in step_file.read_text(encoding="utf-8", errors="replace").splitlines()
]

print("=" * 120)
print("CASE-014 STEP LOG REPORT")
print(f"Run Dir        : {run}")
print(f"Stopped Reason : {stopped_reason}")
print(f"Final Answer   : {'YES' if final_answer is not None else 'NO'}")
print(f"Total Steps    : {len(records)}")
print("=" * 120)
print("step | timestamp           | total_s | llm_s | tools_s | status | tool | feedback | fail_reason")
print("-" * 120)

for rec in records:
    idx = rec.get("index", -1)
    timing = timing_by_step.get(idx, {})
    ts = timing.get("timestamp", "N/A")
    total_s = timing.get("total_s", "N/A")
    llm_s = timing.get("llm_s", "N/A")
    tools_s = timing.get("tools_s", "N/A")

    calls = rec.get("assistant_tool_calls") or []
    tool_names = []
    for c in calls:
        name = c.get("name") if isinstance(c, dict) else None
        if not name and isinstance(c, dict):
            fn = c.get("function")
            if isinstance(fn, dict):
                name = fn.get("name")
        if name:
            tool_names.append(name)
    tool_name = ",".join(tool_names) if tool_names else "none"

    results = rec.get("tool_results") or []
    step_ok = all(r.get("ok", True) for r in results) if results else True
    status = "OK" if step_ok else "FAIL"

    feedback_parts = []
    fail_parts = []
    for r in results:
        text = " ".join(str(r.get("text", "")).split())
        if len(text) > 220:
            text = text[:220] + "..."
        if text:
            feedback_parts.append(text)
        if r.get("ok") is False and text:
            fail_parts.append(text)

    feedback = " | ".join(feedback_parts[:2]) if feedback_parts else "-"
    if len(feedback_parts) > 2:
        feedback += " | ..."
    fail_reason = "; ".join(fail_parts) if fail_parts else "-"

    print(
        f"{idx:>4} | {ts:<19} | {str(total_s):>7} | {str(llm_s):>5} | {str(tools_s):>7} | {status:<6} | {tool_name:<20} | {feedback} | {fail_reason}"
    )

print("-" * 120)
