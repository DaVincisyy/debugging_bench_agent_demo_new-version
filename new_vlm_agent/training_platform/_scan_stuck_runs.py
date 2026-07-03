#!/usr/bin/env python3
"""Scan first-session (June 4) runs for stuck / max-step patterns."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
REPORTS = REPO / "training_platform" / "analysis_reports"
STEP_TOP = REPO / "training_platform" / "step_timing_top6.json"
ROUND11 = REPO / "training_platform" / "autonomous_rounds" / "round_011.json"


def main() -> None:
    rows = []
    for p in sorted(REPORTS.glob("20260604-*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        meta = d.get("run_meta") or {}
        rows.append(
            {
                "run_id": p.stem,
                "stopped_reason": meta.get("stopped_reason") or d.get("stopped_reason"),
                "step_count": meta.get("step_count") or d.get("step_count"),
                "total_s": (meta.get("part_timing") or d.get("part_timing") or {}).get("total_s"),
                "pixel_ok": (d.get("pixel_comparison") or {}).get("within_tolerance"),
            }
        )

    print("=== June 4 analysis_reports summary ===")
    print(f"count={len(rows)}")
    by_stop = Counter(r["stopped_reason"] for r in rows)
    for k, v in by_stop.most_common():
        print(f"  {k}: {v}")

    high = [r for r in rows if (r["step_count"] or 0) >= 20]
    high.sort(key=lambda x: x["step_count"] or 0, reverse=True)
    print("\n=== step_count >= 20 ===")
    for r in high:
        print(
            f"  {r['run_id']} steps={r['step_count']} stopped={r['stopped_reason']} "
            f"total_s={r['total_s']} pixel_ok={r['pixel_ok']}"
        )

    if STEP_TOP.is_file():
        top = json.loads(STEP_TOP.read_text(encoding="utf-8"))
        print("\n=== step_timing_top6.json keys ===")
        for k, v in top.items():
            pr = v.get("primary") or {}
            vr = v.get("verify") or {}
            print(
                f"  iter {k}: primary {pr.get('run_id')} steps={pr.get('step_count')} "
                f"{pr.get('stopped_reason')} | verify {vr.get('run_id')} steps={vr.get('step_count')} "
                f"{vr.get('stopped_reason')}"
            )

    if ROUND11.is_file():
        rd = json.loads(ROUND11.read_text(encoding="utf-8"))
        vr = rd.get("verify_run") or {}
        print("\n=== v2 round 11 verify (max-steps) ===")
        print(f"  run_id={vr.get('run_id')} stopped={vr.get('stopped_reason') if 'stopped' in str(vr) else 'see primary'}")
        pr = rd.get("primary_run") or {}
        print(f"  primary steps={len(pr.get('step_details') or [])} stopped={pr.get('stopped_reason')}")
        vsteps = vr.get("step_details") or []
        print(f"  verify steps={len(vsteps)}")
        if vsteps:
            tools_seq = [",".join(s.get("tools") or []) or "(none)" for s in vsteps[:20]]
            print("  verify first 20 tools:", tools_seq)
            if len(vsteps) > 25:
                tail = [",".join(s.get("tools") or []) or "(none)" for s in vsteps[-15:]]
                print("  verify last 15 tools:", tail)


if __name__ == "__main__":
    main()
