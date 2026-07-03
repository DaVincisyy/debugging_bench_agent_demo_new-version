"""Generate PCBA Debugging Bench Agent architecture diagram (case-013 validated flow)."""
from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

OUT = Path(__file__).resolve().parents[1] / "docs" / "assets" / "debugging-bench-flow-case013.png"

# Palette aligned with debugging-bench-flow-version-3.png
C_HEADER = "#1e3a5f"
C_BORDER = "#cbd5e1"
C_TEXT = "#1e293b"
C_MUTED = "#64748b"
C_A = "#dbeafe"
C_B = "#dcfce7"
C_C = "#fef9c3"
C_D = "#fee2e2"
C_E = "#ede9fe"
C_FLOW = ["#bfdbfe", "#bbf7d0", "#fde68a", "#fecaca", "#ddd6fe", "#bae6fd", "#fcd34d", "#fca5a5", "#c4b5fd", "#86efac", "#93c5fd"]
STATE_COLORS = {
    "IDLE": "#93c5fd",
    "PREPARING": "#86efac",
    "EXECUTING": "#fde047",
    "REPORTING": "#fdba74",
    "END": "#1e3a5f",
}


def box(ax, x, y, w, h, text, fc="#ffffff", ec=C_BORDER, fontsize=7, bold=False, text_color=C_TEXT):
    patch = FancyBboxPatch(
        (x, y), w, h,
        boxstyle="round,pad=0.02,rounding_size=0.015",
        linewidth=1.2, edgecolor=ec, facecolor=fc,
    )
    ax.add_patch(patch)
    weight = "bold" if bold else "normal"
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=fontsize,
            color=text_color, weight=weight, wrap=True)


def section_title(ax, x, y, w, title):
    ax.add_patch(FancyBboxPatch((x, y), w, 0.035, boxstyle="square,pad=0", linewidth=0, facecolor="#f1f5f9"))
    ax.text(x + 0.01, y + 0.017, title, fontsize=11, weight="bold", color=C_HEADER, va="center")


def arrow_h(ax, x1, x2, y, color="#64748b"):
    ax.annotate("", xy=(x2, y), xytext=(x1, y),
                arrowprops=dict(arrowstyle="-|>", color=color, lw=1.2, shrinkA=2, shrinkB=2))


def main() -> None:
    fig, ax = plt.subplots(figsize=(24, 15))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    fig.patch.set_facecolor("white")

    # Header
    ax.add_patch(FancyBboxPatch((0, 0.955), 1, 0.045, boxstyle="square,pad=0", linewidth=0, facecolor=C_HEADER))
    ax.text(0.5, 0.977, "PCBA DEBUGGING BENCH AGENT — ARCHITECTURE & CASE-013 VALIDATED FLOW",
            ha="center", va="center", fontsize=16, weight="bold", color="white")
    ax.text(0.5, 0.958, "Web UI + Node Orchestration + Python VLM Service + MG400 Gateway  |  Reference: case-013 (TP5 → pixel [47,938])",
            ha="center", va="center", fontsize=9, color="#cbd5e1")

    # ── SYSTEM ARCHITECTURE ──
    section_title(ax, 0.02, 0.88, 0.96, "SYSTEM ARCHITECTURE")
    layers = [
        ("A. USER / INPUT LAYER", C_A, [
            "Engineer / Operator",
            "Inputdemo Web UI  :3000",
            "Instruction + Case ID (case-013)",
            "Operator name",
            "Camera / Bit / Schematic uploads",
            "POST /api/runs",
        ]),
        ("B. NODE ORCHESTRATION", C_B, [
            "server.js  (HTTP API)",
            "inputCore.js  (standardize)",
            "createBenchRun + SSE events",
            "VlmAgentCaseAdapter",
            "→ data/cases/case-013/",
            "→ task.yaml + workspace",
        ]),
        ("C. PYTHON VLM LAYER", C_C, [
            "FastAPI VLM Service  :8000",
            "(CLI fallback if down)",
            "Agent tool loop + plan-guard",
            "STANDARD_WORKFLOW.md",
            "Part0→B→A→C→D (case12)",
            "summary.json / final_answer",
        ]),
        ("D. EXECUTION LAYER", C_D, [
            "planner.js",
            "vlmTargetExecutionMapper",
            "Reachability Guard",
            "Robot Gateway  :8010",
            "MG400 real / MuJoCo sim",
            "MockEquipmentController",
        ]),
        ("E. OUTPUT LAYER", C_E, [
            "vlmObservation",
            "modelOutput + plan",
            "ReportGenerator",
            "HTTP response",
            "GET /api/runs/:id",
            "SSE / Web UI updated",
        ]),
    ]
    lx, lw = 0.025, 0.182
    for i, (title, color, lines) in enumerate(layers):
        x = lx + i * (lw + 0.008)
        y, lh = 0.72, 0.145
        ax.add_patch(FancyBboxPatch((x, y), lw, lh, boxstyle="round,pad=0.01", linewidth=1.5, edgecolor=C_BORDER, facecolor=color))
        ax.text(x + lw / 2, y + lh - 0.018, title, ha="center", va="top", fontsize=8, weight="bold", color=C_HEADER)
        body = "\n".join(f"• {line}" for line in lines)
        ax.text(x + 0.008, y + lh - 0.035, body, ha="left", va="top", fontsize=6.2, color=C_TEXT, linespacing=1.35)

    # inter-layer arrows
    ay = 0.792
    for i in range(4):
        x1 = lx + (i + 1) * (lw + 0.008) - 0.004
        x2 = x1 + 0.012
        arrow_h(ax, x1, x2, ay)

    # ── DATA FLOW ──
    section_title(ax, 0.02, 0.655, 0.96, "DATA FLOW  (case-013 end-to-end)")
    steps = [
        ("1", "User\nInput JSON"),
        ("2", "Standardized\nInput"),
        ("3", "case-013\ntask.yaml"),
        ("4", "Assets in\nvlm-agent-runs/"),
        ("5", "VLM Service\nPOST /v1/runs"),
        ("6", "Part0–D\nTool Loop"),
        ("7", "debug/*\nArtifacts"),
        ("8", "summary.json\nfinal_answer"),
        ("9", "Execution\nPlan"),
        ("10", "Robot\nGateway"),
        ("11", "Report +\nSSE / UI"),
    ]
    sx, sw, sy, sh = 0.03, 0.078, 0.585, 0.055
    for i, (num, label) in enumerate(steps):
        x = sx + i * (sw + 0.006)
        box(ax, x, sy, sw, sh, f"{num}. {label}", fc=C_FLOW[i % len(C_FLOW)], fontsize=6.5, bold=False)
        if i < len(steps) - 1:
            arrow_h(ax, x + sw + 0.002, x + sw + 0.006, sy + sh / 2)

    ax.text(0.5, 0.555, "case-013 key artifacts: case10_signal_to_tp.json (TP5) → assembly/board IC hints → case12_board_points_aligned.json → step08_final_tp.png + board_tp_marked.png → finish pixel [47,938]",
            ha="center", fontsize=7, color=C_MUTED, style="italic")

    # ── CASE-013 VLM PIPELINE (success path) ──
    section_title(ax, 0.02, 0.495, 0.96, "CASE-013 VLM PIPELINE  (successful run path)")
    vlm_steps = [
        ("Part 0", "Schematic + question\n→ TP5\ncase10_signal_to_tp.json\nPDF search + green circle"),
        ("Part B", "Assembly largest IC\ngrid hints + OpenCV\ncase10_assembly_largest_ic_box.png"),
        ("Part A", "Board largest IC\nvlm_hints (≤2 rev)\nfull-image ROI fallback"),
        ("Part C", "Copy step02 anchors\nlocator + board PNG"),
        ("Part D", "case12_build_and_align\nemit_step08_from_case12_aligned\nauto board_tp_marked.png"),
        ("Finish", "finish-tool-called\ntp_id=TP5\npixel=[47,938]\nconfidence=0.95"),
    ]
    vx, vw, vy, vh = 0.03, 0.145, 0.405, 0.075
    for i, (phase, text) in enumerate(vlm_steps):
        x = vx + i * (vw + 0.012)
        fc = [C_A, C_B, "#fef08a", "#fed7aa", C_C, "#bbf7d0"][i]
        box(ax, x, vy, vw, vh, f"{phase}\n{text}", fc=fc, fontsize=6.3, bold=(i == 0 or i == 5))
        if i < len(vlm_steps) - 1:
            arrow_h(ax, x + vw + 0.003, x + vw + 0.01, vy + vh / 2, color=C_HEADER)

    # ── STATE TRANSITION TABLE ──
    section_title(ax, 0.02, 0.33, 0.46, "STATE TRANSITION TABLE")
    headers = ["Current", "Trigger", "Key Output", "Next", "Failure"]
    rows = [
        ["IDLE", "POST /api/runs", "BenchRun created", "PREPARING", "400 invalid input"],
        ["PREPARING", "Adapt case + VLM start", "task.yaml, workspace", "EXECUTING", "VLM service fail → CLI"],
        ["EXECUTING", "Plan + robot steps", "arm/equipment logs", "REPORTING", "BLOCKED unreachable pose"],
        ["REPORTING", "Report built", "report.json", "END", "partial artifacts kept"],
        ["END", "Run complete", "SSE final event", "—", "—"],
    ]
    tx, ty = 0.03, 0.195
    col_w = [0.07, 0.095, 0.115, 0.07, 0.095]
    row_h = 0.022
    # header row
    cx = tx
    for j, h in enumerate(headers):
        box(ax, cx, ty + 5 * row_h, col_w[j], row_h, h, fc="#e2e8f0", fontsize=6, bold=True)
        cx += col_w[j] + 0.004
    state_keys = ["IDLE", "PREPARING", "EXECUTING", "REPORTING", "END"]
    for i, row in enumerate(rows):
        cx = tx
        ry = ty + (4 - i) * row_h
        for j, cell in enumerate(row):
            fc = STATE_COLORS.get(row[0], "#ffffff") if j == 0 else "#ffffff"
            tc = "white" if j == 0 and row[0] == "END" else C_TEXT
            box(ax, cx, ry, col_w[j], row_h, cell, fc=fc, fontsize=5.5, text_color=tc)
            cx += col_w[j] + 0.004

    # Step kind table
    ax.text(tx, ty - 0.012, "STEP KIND", fontsize=7, weight="bold", color=C_HEADER)
    kinds = [
        ("ARM_MOTION", "Mg400ArmController", "Robot Gateway"),
        ("EQUIPMENT_MEASUREMENT", "MockEquipmentController", "mock/sample"),
        ("VISUAL_CAPTURE", "post-run capture", "debug PNG"),
    ]
    ky = ty - 0.055
    for i, (kind, handler, note) in enumerate(kinds):
        box(ax, tx + i * 0.145, ky, 0.14, 0.035, f"{kind}\n{handler}\n{note}", fc="#f8fafc", fontsize=5.5)

    # ── AGENT TOOL LOOP ──
    section_title(ax, 0.52, 0.33, 0.22, "AGENT TOOL LOOP")
    tool_y = 0.38
    tool_boxes = [
        (0.535, "Parse task.yaml\n+ inject STANDARD_WORKFLOW"),
        (0.535, "VLM plan → tool call"),
        (0.535, "search_pdf / view_image\nsave_text_file / run_python"),
        (0.535, "detect_largest_ic\nmark_tp / case12 align"),
        (0.535, "emit_step08 + finish\n(contract gate)"),
    ]
    ty_pos = [0.44, 0.395, 0.35, 0.305, 0.26]
    for (txb, label), yp in zip(tool_boxes, ty_pos):
        box(ax, txb, yp, 0.19, 0.035, label, fc="#fffbeb", fontsize=6)
        if yp > 0.26:
            ax.annotate("", xy=(txb + 0.095, yp - 0.005), xytext=(txb + 0.095, yp + 0.03),
                        arrowprops=dict(arrowstyle="-|>", color=C_MUTED, lw=1))

    box(ax, 0.535, 0.215, 0.19, 0.03, "plan-guard / loop-guard\n(reject invalid tool order)", fc="#fee2e2", fontsize=5.8)

    # ── FLOW CHART (Node + Services) ──
    section_title(ax, 0.76, 0.33, 0.22, "SERVICE FLOW")
    flow_items = [
        "1. Web UI submit",
        "2. Node :3000 POST /api/runs",
        "3. PREPARING: adapt case-013",
        "4. VLM Service :8000",
        "5. Poll /events + /runs",
        "6. Map pixel → plan",
        "7. Gateway :8010",
        "8. MG400 / simulation",
        "9. REPORTING + SSE",
    ]
    fx = 0.765
    for i, item in enumerate(flow_items):
        y = 0.44 - i * 0.024
        box(ax, fx, y, 0.21, 0.02, item, fc=C_FLOW[i % len(C_FLOW)], fontsize=5.8)
        if i < len(flow_items) - 1:
            ax.annotate("", xy=(fx + 0.105, y - 0.003), xytext=(fx + 0.105, y + 0.017),
                        arrowprops=dict(arrowstyle="-|>", color=C_MUTED, lw=0.9))

    # ── LEGEND ──
    ax.add_patch(FancyBboxPatch((0.02, 0.02), 0.96, 0.055, boxstyle="round,pad=0.01", linewidth=1, edgecolor=C_BORDER, facecolor="#f8fafc"))
    ax.text(0.03, 0.055, "LEGEND", fontsize=8, weight="bold", color=C_HEADER)
    legend_states = ["IDLE", "PREPARING", "EXECUTING", "REPORTING", "END"]
    lx0 = 0.08
    for i, st in enumerate(legend_states):
        ax.add_patch(FancyBboxPatch((lx0 + i * 0.11, 0.038), 0.025, 0.015, boxstyle="square,pad=0", facecolor=STATE_COLORS[st], edgecolor=C_BORDER))
        tc = "white" if st == "END" else C_TEXT
        ax.text(lx0 + i * 0.11 + 0.03, 0.045, st, fontsize=6, color=tc, va="center")

    ax.text(0.62, 0.048, "Ports: Web/API :3000  |  VLM Service :8000  |  Robot Gateway :8010", fontsize=7, color=C_MUTED)
    ax.text(0.62, 0.032, "case-013 validated: 56 agent steps, ~23 min, finish-tool-called, TP5 @ [47,938]", fontsize=7, color=C_MUTED)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, dpi=150, bbox_inches="tight", facecolor="white", pad_inches=0.15)
    plt.close(fig)
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
