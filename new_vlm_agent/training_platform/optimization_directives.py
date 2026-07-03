"""Standing optimization themes for autonomous reflect / apply (platform layer only)."""

from __future__ import annotations

OPTIMIZATION_THEMES_ZH = """\
## 长期优化方向（autonomous 每轮优先对照）

1. **根据上下文合并流程**
   - 减少 phase 切换与 context reset 次数；同一 Part 内连续完成可合并的步骤。
   - 避免 planner 线索在 phase 边界丢失导致 explore 循环。
   - 优先改 `agent/agent.py` 的 phase 编排 / handoff，而非堆更多 prompt 文字。

2. **合并工具使用**
   - 合并连续、同目标的工具调用（重复 `view_image`、反复读写同一 JSON）。
   - 用一体化 deterministic tool 替代碎片 `run_python`。
   - 减少 ToolResult 回灌体积（尤其图片附件）。

3. **finish 阶段审查**
   - `finish` 前只校验契约字段（`pixel`、`step08_result.json`、`mapping_method` 等）。
   - 避免 finish 前重复 `view_image` / `annotate_image` / 全量 re-read。
   - 精简 finish handoff 与 plan-guard 分支；pixel 校验与 `emit_step08` clamp 语义一致。"""


def augment_reflect_system(base: str) -> str:
    return f"{base.rstrip()}\n\n{OPTIMIZATION_THEMES_ZH}"


def augment_apply_plan_system(base: str) -> str:
    extra = (
        "\n\n额外规则：\n"
        "- 最多 **1** 条 action（仅 1 个 P0）；禁止一次输出多个并列改动。\n"
        "- 优先从上述长期方向 1–3 中选 P0 action；"
        " concrete_steps 须可映射到其中至少一条。\n"
        "- target_files 最多 1 个；禁止新建 agent/core、agent/guards、tool_router 等模块。\n"
        "- debug PNG 延后/裁剪由人工维护 agent，autonomous 循环不要自动改此类产物。"
    )
    return f"{base.rstrip()}{extra}"


def cursor_apply_constraint_lines() -> list[str]:
    return [
        "",
        "Standing optimization themes (prefer actions aligned with these):",
        "- (1) Merge workflow steps using context; fewer phase resets.",
        "- (2) Merge redundant tool calls; prefer composite deterministic tools.",
        "- (3) Streamline finish: validate contract fields only; consistent pixel clamp checks.",
        "- Do NOT auto-apply debug-PNG deferral in agent (maintained manually outside autonomous loop).",
    ]


def cursor_apply_surgical_policy_lines() -> list[str]:
    """Cursor SDK apply agent: one change only, reflection-first."""
    return [
        "",
        "Surgical apply policy (mandatory — overrides apply_plan if conflicting):",
        "- SOURCE OF TRUTH: the full VLM reflection markdown below (sections 改进建议 / 优先级).",
        "- apply_plan JSON is a secondary hint only; if it diverges from reflection P0, follow reflection.",
        "- Implement exactly ONE highest-priority P0 item from the reflection — not two, not a bundle.",
        "- Edit at most ONE file (two only if physically inseparable, e.g. prompt + skill pair).",
        "- Minimal diff; no new Python modules under agent/core, agent/guards, agent/routing, etc.",
        "- Do NOT implement context trim, tool_router, orchestrator, or multi-file refactors in one pass.",
        "- If history shows a similar change was reverted or failed, pick a different approach or skip.",
    ]
