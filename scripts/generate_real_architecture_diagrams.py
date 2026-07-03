#!/usr/bin/env python3
"""
Generate high-quality PNG architecture diagrams for PCBA Debugging Bench Agent.
Style: Professional technical poster, matching debugging-bench-flow-case013.png
Based strictly on current 2026-06 codebase (real files, real case-013 18-step path).
"""

from pathlib import Path
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch, Rectangle, FancyArrowPatch
import matplotlib.patheffects as path_effects

# Output directory
OUT_DIR = Path(__file__).resolve().parents[1] / "docs" / "assets"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Color palette (matching reference images)
C_HEADER = "#0f172a"
C_SUBHEADER = "#1e293b"
C_BLUE = "#dbeafe"
C_GREEN = "#dcfce7"
C_YELLOW = "#fef9c3"
C_ORANGE = "#fed7aa"
C_PURPLE = "#ede9fe"
C_LIGHT = "#f8fafc"
C_BORDER = "#94a3b8"
C_TEXT = "#1e293b"
C_MUTED = "#64748b"
C_WHITE = "#ffffff"
C_ACCENT = "#3b82f6"

STATE_COLORS = {
    "IDLE": "#93c5fd",
    "PREPARING": "#86efac",
    "EXECUTING": "#fde047",
    "REPORTING": "#fdba74",
}

def add_text_with_bg(ax, x, y, text, fontsize=7, color=C_TEXT, bg_color=None, bold=False, ha="center", va="center"):
    """Add text with optional background for readability."""
    weight = "bold" if bold else "normal"
    txt = ax.text(x, y, text, fontsize=fontsize, color=color, weight=weight, ha=ha, va=va,
                  linespacing=1.25)
    if bg_color:
        txt.set_path_effects([
            path_effects.withStroke(linewidth=3, foreground=bg_color),
            path_effects.Normal()
        ])

def draw_rounded_box(ax, x, y, w, h, text, fc=C_WHITE, ec=C_BORDER, fontsize=6.5, bold=False, text_color=C_TEXT, alpha=1.0):
    """Draw a professional rounded box with centered text."""
    box = FancyBboxPatch(
        (x, y), w, h,
        boxstyle="round,pad=0.008,rounding_size=0.012",
        linewidth=1.0, edgecolor=ec, facecolor=fc, alpha=alpha
    )
    ax.add_patch(box)
    weight = "bold" if bold else "normal"
    ax.text(x + w/2, y + h/2, text, ha="center", va="center", fontsize=fontsize,
            color=text_color, weight=weight, wrap=True, linespacing=1.2)

def draw_arrow(ax, x1, y1, x2, y2, color=C_MUTED, lw=1.2, style="-|>", connectionstyle="arc3,rad=0"):
    ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
                arrowprops=dict(arrowstyle=style, color=color, lw=lw,
                               connectionstyle=connectionstyle))

def create_system_architecture_poster():
    """Create the main comprehensive architecture poster."""
    fig, ax = plt.subplots(figsize=(28, 18))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    fig.patch.set_facecolor("white")

    # === HEADER ===
    ax.add_patch(Rectangle((0, 0.945), 1, 0.055, facecolor=C_HEADER, edgecolor="none"))
    ax.text(0.5, 0.972, "PCBA DEBUGGING BENCH AGENT — REAL ARCHITECTURE (2026-06 Current Codebase)",
            ha="center", va="center", fontsize=18, weight="bold", color="white")
    ax.text(0.5, 0.952, "Validated on case-013 (18-step clean success path + 56-step debug path)  |  Ports: Web 3000 • VLM 8000 • Gateway 8010",
            ha="center", va="center", fontsize=9, color="#94a3b8")

    # === SECTION 1: SYSTEM ARCHITECTURE (5 Layers) ===
    y_top = 0.88
    ax.add_patch(Rectangle((0.015, y_top-0.005), 0.97, 0.032, facecolor="#f1f5f9", edgecolor="none"))
    ax.text(0.025, y_top + 0.01, "1. SYSTEM ARCHITECTURE — 5 Real Layers (from actual code)", fontsize=11, weight="bold", color=C_HEADER)

    layers = [
        ("A. USER / INPUT LAYER", C_BLUE, [
            "Browser Web UI (webPage.js)",
            "Input: Instruction + Case ID",
            "Camera / Bit / Schematic uploads",
            "POST /api/runs  →  SSE subscription"
        ]),
        ("B. NODE ORCHESTRATION", C_GREEN, [
            "server.js (HTTP :3000)",
            "states.js (IDLE→PREPARING→EXECUTING→REPORTING)",
            "vlmAgentCaseAdapter.js → creates case-013/",
            "vlmAgentServiceRunner.js + planner.js"
        ]),
        ("C. PYTHON VLM AGENT", C_YELLOW, [
            "FastAPI Service (agent/service.py :8000)",
            "Agent Core (agent/agent.py + plan-guard)",
            "Tools: case12_*, run_python, save_text_file...",
            "Injects STANDARD_WORKFLOW.md + SKILL.md"
        ]),
        ("D. EXECUTION GATEWAY", C_ORANGE, [
            "Robot Gateway (robotGatewayServer.js :8010)",
            "Reachability Guard",
            "MG400 TCP / MuJoCo simulation",
            "MockEquipmentController (real HW pending)"
        ]),
        ("E. OUTPUT & REPORTING", C_PURPLE, [
            "summary.json + final_answer",
            "ReportGenerator",
            "SSE events + GET /api/runs/:id",
            "Web UI real-time update"
        ]),
    ]

    layer_w = 0.178
    layer_h = 0.145
    start_x = 0.025
    gap = 0.012
    layer_y = 0.69

    for i, (title, color, items) in enumerate(layers):
        x = start_x + i * (layer_w + gap)
        # Layer container
        ax.add_patch(FancyBboxPatch((x, layer_y), layer_w, layer_h,
                                    boxstyle="round,pad=0.008,rounding_size=0.01",
                                    linewidth=1.5, edgecolor=C_BORDER, facecolor=color))
        # Title bar
        ax.add_patch(Rectangle((x, layer_y + layer_h - 0.028), layer_w, 0.028,
                               facecolor=C_SUBHEADER, edgecolor="none"))
        ax.text(x + layer_w/2, layer_y + layer_h - 0.014, title,
                ha="center", va="center", fontsize=7.5, weight="bold", color="white")

        # Content
        content = "\n".join(f"• {item}" for item in items)
        ax.text(x + 0.008, layer_y + layer_h - 0.035, content,
                ha="left", va="top", fontsize=6.0, color=C_TEXT, linespacing=1.35)

        # Arrow to next layer
        if i < len(layers) - 1:
            ax.annotate("", xy=(x + layer_w + 0.003, layer_y + layer_h/2),
                        xytext=(x + layer_w - 0.003, layer_y + layer_h/2),
                        arrowprops=dict(arrowstyle="->", color=C_ACCENT, lw=1.8))

    # === SECTION 2: CASE-013 18-STEP SUCCESS WORKFLOW ===
    y2 = 0.52
    ax.add_patch(Rectangle((0.015, y2-0.005), 0.97, 0.028, facecolor="#f1f5f9", edgecolor="none"))
    ax.text(0.025, y2 + 0.008, "2. CASE-013 VALIDATED 18-STEP SUCCESS PATH (real run: run_mpupbued_2eeibqys, ~5min40s)", 
            fontsize=11, weight="bold", color=C_HEADER)

    workflow_steps = [
        ("1. Web UI Submit", "Instruction\n+ images\n+ case-013"),
        ("2. Node Server", "BenchRun\nPREPARING"),
        ("3. Case Adapter", "task.yaml\n+ workspace"),
        ("4. VLM Service", "POST /v1/runs\n:8000"),
        ("5. Part 0", "TP5\nsignal_to_tp.json"),
        ("6. Part B", "Assembly IC\n+ grid hints"),
        ("7. Part A", "Board IC\n(≤2 hints rev)"),
        ("8. Part C+D", "case12 align\n+ board_tp_marked"),
        ("9. Finish", "TP5 @ [47,938]\nconf=0.95"),
        ("10. Plan + Exec", "Gateway\nMG400/MuJoCo"),
        ("11. Report", "SSE + final\nresponse"),
    ]

    wf_y = 0.34
    wf_h = 0.12
    wf_w = 0.078
    wf_start_x = 0.025
    wf_gap = 0.007

    for i, (title, detail) in enumerate(workflow_steps):
        x = wf_start_x + i * (wf_w + wf_gap)
        color = [C_BLUE, C_GREEN, C_YELLOW, "#fed7aa", C_ORANGE, "#fef08a", "#bae6fd", C_PURPLE, "#bbf7d0", "#c4b5fd", "#86efac"][i]
        draw_rounded_box(ax, x, wf_y, wf_w, wf_h, f"{title}\n{detail}", fc=color, fontsize=5.8, bold=(i==0 or i==8))

        if i < len(workflow_steps)-1:
            ax.annotate("", xy=(x + wf_w + 0.002, wf_y + wf_h/2),
                        xytext=(x + wf_w - 0.002, wf_y + wf_h/2),
                        arrowprops=dict(arrowstyle="->", color=C_ACCENT, lw=1.3))

    # Key artifacts callout
    ax.text(0.5, 0.305, "Key real artifacts (from actual run): case10_signal_to_tp.json → case12_board_points_aligned.json → step08_final_tp.png + board_tp_marked.png → final_answer pixel [47,938]",
            ha="center", fontsize=7, color=C_MUTED, style="italic")

    # === SECTION 3: STATE MACHINE ===
    y3 = 0.25
    ax.add_patch(Rectangle((0.015, y3-0.005), 0.46, 0.028, facecolor="#f1f5f9", edgecolor="none"))
    ax.text(0.025, y3 + 0.008, "3. REAL STATE MACHINE (from states.js + server.js)", fontsize=10, weight="bold", color=C_HEADER)

    states = [
        ("IDLE", 0.04, 0.13, C_BLUE),
        ("PREPARING", 0.18, 0.13, C_GREEN),
        ("EXECUTING", 0.32, 0.13, C_YELLOW),
        ("REPORTING", 0.46, 0.13, C_ORANGE),
    ]

    for name, x, y, color in states:
        draw_rounded_box(ax, x, y, 0.11, 0.055, name, fc=color, fontsize=8, bold=True)

    # Transitions
    ax.annotate("", xy=(0.18, 0.157), xytext=(0.15, 0.157), arrowprops=dict(arrowstyle="->", color=C_ACCENT, lw=1.5))
    ax.text(0.165, 0.168, "POST /api/runs", fontsize=5.5, color=C_MUTED, ha="center")

    ax.annotate("", xy=(0.32, 0.157), xytext=(0.29, 0.157), arrowprops=dict(arrowstyle="->", color=C_ACCENT, lw=1.5))
    ax.text(0.305, 0.168, "VLM started", fontsize=5.5, color=C_MUTED, ha="center")

    ax.annotate("", xy=(0.46, 0.157), xytext=(0.43, 0.157), arrowprops=dict(arrowstyle="->", color=C_ACCENT, lw=1.5))
    ax.text(0.445, 0.168, "final_answer", fontsize=5.5, color=C_MUTED, ha="center")

    # Failure paths
    ax.text(0.25, 0.105, "Failure paths: VLM unavailable → CLI fallback | Unreachable pose → BLOCKED | Contract error → REPORTING with error",
            fontsize=6, color="#dc2626", ha="center")

    # === SECTION 4: DATA FLOW (Simplified) ===
    ax.add_patch(Rectangle((0.51, y3-0.005), 0.475, 0.028, facecolor="#f1f5f9", edgecolor="none"))
    ax.text(0.52, y3 + 0.008, "4. CORE DATA FLOW (real artifacts)", fontsize=10, weight="bold", color=C_HEADER)

    data_items = [
        "User JSON", "case-013/task.yaml", "Part0- D artifacts\n(step08 + board_tp_marked)", 
        "final_answer", "Execution Plan", "Gateway + Robot", "Report + SSE"
    ]
    df_y = 0.13
    df_w = 0.062
    df_start = 0.52
    for i, item in enumerate(data_items):
        x = df_start + i * (df_w + 0.005)
        color = C_FLOW[i % len(C_FLOW)] if 'C_FLOW' in globals() else "#e0f2fe"
        draw_rounded_box(ax, x, df_y, df_w, 0.055, item, fc=color, fontsize=5.5)
        if i < len(data_items)-1:
            ax.annotate("", xy=(x + df_w + 0.001, df_y + 0.027), xytext=(x + df_w - 0.001, df_y + 0.027),
                        arrowprops=dict(arrowstyle="->", color=C_ACCENT, lw=1.0))

    # === FOOTER / LEGEND ===
    ax.add_patch(Rectangle((0.015, 0.015), 0.97, 0.065, facecolor="#f8fafc", edgecolor=C_BORDER, linewidth=0.8))
    ax.text(0.025, 0.065, "LEGEND & REALITY CHECK", fontsize=9, weight="bold", color=C_HEADER)

    legend_text = (
        "Ports: Web/API :3000  •  VLM FastAPI :8000  •  Robot Gateway :8010\n"
        "Key files: Inputdemo/src/api/server.js + states.js  |  Vlm agent/Debugging-agent-v2/agent/{service.py,agent.py,builtin_tools.py}  |  data/skills/STANDARD_WORKFLOW.md\n"
        "Validated: case-013 18-step clean path (TP5 @ [47,938]) + 56-step debug path  |  Current limitations: MockEquipment, no real HW calibration, no voice input, no entity arm reset flow"
    )
    ax.text(0.025, 0.052, legend_text, fontsize=6.2, color=C_TEXT, va="top", linespacing=1.3)

    # Save
    out_path = OUT_DIR / "real-architecture-poster-2026-06.png"
    plt.savefig(out_path, dpi=150, bbox_inches="tight", facecolor="white", pad_inches=0.2)
    plt.close()
    print(f"Generated: {out_path}")
    return out_path

if __name__ == "__main__":
    create_system_architecture_poster()
    print("Done. Open docs/assets/real-architecture-poster-2026-06.png")