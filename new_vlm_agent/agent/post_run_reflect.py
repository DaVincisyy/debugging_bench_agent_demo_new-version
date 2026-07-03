"""Post-run efficiency self-reflection using the same VLM API as the agent."""

from __future__ import annotations

import datetime as _dt
import json
import time
from collections import Counter
from pathlib import Path
from typing import Any, Callable

from rich.console import Console
from rich.panel import Panel

from .config import Config
from .llm_client import LLMClient

REFLECTION_SYSTEM_ZH = """\
你是 PCBA 测点定位 VLM Agent 的**运行后效率分析助手**。
你会收到一次完整 run 的结构化摘要（各步耗时、工具调用、阶段划分、plan-guard 拒绝等）。
**不要**修改代码或执行工具；只基于摘要做反思。

请用中文输出，结构如下（Markdown 标题必须保留）：

## 耗时瓶颈
按影响从大到小列出 3–6 条；引用具体 step 编号、工具名、秒数。

## 冗余与低效
指出重复 view_image、被拒绝的 plan-guard、连续 save_text_file 无 OpenCV、\
过长 prefill 等可优化点。

## 改进建议
每条建议注明类别：`agent代码` / `plan技能` / `工具契约` / `prompt体积` / `流程编排`。
说明**改什么**、**预期节省**（秒或步数量级）、**准确性风险**（低/中/高）。

## 优先级
用 P0/P1/P2 列出最值得先做的 3–5 项。

要求：具体、可执行；不要泛泛而谈；不要重复摘要全文。"""

APPLY_PLAN_SYSTEM_ZH = """\
你是「可自动改仓库」的改进计划生成器。输入是一次 run 的效率 digest + 中文反思摘要。
输出**唯一**一个 JSON 对象（不要 Markdown，不要解释），供外部 Cursor SDK agent 执行。

Schema（严格遵守）：
{
  "actions": [
    {
      "id": "A1",
      "priority": "P0",
      "category": "agent代码|plan技能|工具契约|prompt体积|流程编排",
      "title": "一句话标题",
      "target_files": ["相对仓库根的路径，最多2个"],
      "change_type": "edit_code",
      "concrete_steps": ["可执行步骤1", "步骤2", "步骤3"],
      "acceptance": {
        "golden_pixel_tolerance_px": 10,
        "must_not_regress_accuracy": true
      },
      "risk": "low",
      "do_not_touch": ["不要改的文件或目录"]
    }
  ]
}

规则：
- 最多 2 条 action，且 priority 必须为 P0
- risk 必须为 low（medium/high 不要输出）
- target_files 必须在仓库内真实可能存在（agent/, training_platform/, data/skills/ 等）
- 禁止建议「删除 phase 隔离」「去掉 plan-guard」「改 golden」
- concrete_steps 要具体到函数/字段/文件名，外部 agent 能直接照做
- 若无可安全自动执行的 P0 低危改动，输出 {"actions": []}"""


def build_efficiency_digest(
    result: Any,
    infer_step_part: Callable[[Any, str | None], str],
) -> dict[str, Any]:
    """Compact run stats for reflection (no images, no full task text)."""
    tool_counts: Counter[str] = Counter()
    plan_guard_events: list[dict[str, Any]] = []
    step_rows: list[dict[str, Any]] = []
    prev_part: str | None = None

    for s in result.steps:
        part = infer_step_part(s, prev_part=prev_part)
        if part != "unknown":
            prev_part = part
        tools = [c.get("name", "?") for c in (s.tool_calls or [])]
        for t in tools:
            tool_counts[str(t)] += 1
        timing = s.timing or {}
        usage = s.usage or {}
        row = {
            "step": s.index,
            "part": part,
            "tools": tools,
            "step_total_s": timing.get("step_total_s"),
            "llm_s": timing.get("llm_s"),
            "tools_s": timing.get("tools_s"),
            "llm_est_prefill_s": timing.get("llm_est_prefill_s"),
            "llm_est_decode_s": timing.get("llm_est_decode_s"),
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
        }
        step_rows.append(row)
        for tr in s.tool_results or []:
            text = str(tr.get("text", ""))
            if "[plan-guard]" in text:
                plan_guard_events.append({
                    "step": s.index,
                    "tool": tr.get("name") or (tools[0] if tools else "?"),
                    "snippet": text[:400],
                })

    slow_steps = sorted(
        step_rows,
        key=lambda r: float(r.get("step_total_s") or 0.0),
        reverse=True,
    )[:8]

    return {
        "stopped_reason": result.stopped_reason,
        "step_count": len(result.steps),
        "part_timing": result.part_timing,
        "tool_call_counts": dict(tool_counts),
        "plan_guard_count": len(plan_guard_events),
        "plan_guard_events": plan_guard_events[:12],
        "slowest_steps": slow_steps,
        "all_steps": step_rows,
    }


def _extract_json_object(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        try:
            obj = json.loads(raw[start : end + 1])
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def generate_apply_plan(
    client: LLMClient,
    cfg: Config,
    run_dir: Path,
    digest: dict[str, Any],
    reflection_md: str,
) -> dict[str, Any]:
    """Second LLM call: structured P0 actions for Cursor SDK apply agent."""
    payload = {
        "run_id": run_dir.name,
        "phase_isolated_context": getattr(cfg, "phase_isolated_context", True),
        "digest_summary": {
            "part_timing": digest.get("part_timing"),
            "plan_guard_count": digest.get("plan_guard_count"),
            "slowest_steps": digest.get("slowest_steps"),
            "tool_call_counts": digest.get("tool_call_counts"),
        },
        "reflection_markdown_zh": reflection_md[:6000],
    }
    user_text = (
        "根据以下 JSON 生成 apply_plan（仅输出 JSON 对象）：\n\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )
    reply = client.chat(
        [
            client.system_message(APPLY_PLAN_SYSTEM_ZH),
            client.user_message(user_text),
        ],
        tools_schema=None,
    )
    plan = _extract_json_object(reply.content or "") or {"actions": []}
    if "actions" not in plan or not isinstance(plan["actions"], list):
        plan = {"actions": []}
    plan["run_id"] = run_dir.name
    plan["generated_at"] = _dt.datetime.now().isoformat(timespec="seconds")
    plan["model"] = cfg.model
    plan["generation_usage"] = dict(reply.usage or {})
    return plan


def _load_phase_resets(run_dir: Path) -> list[dict[str, Any]]:
    path = run_dir / "phase_context_resets.jsonl"
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def run_post_run_reflection(
    client: LLMClient,
    cfg: Config,
    run_dir: Path,
    result: Any,
    infer_step_part: Callable[[Any, str | None], str],
    console: Console | None = None,
) -> dict[str, Any] | None:
    """One extra LLM call after the run; writes vlm_self_reflection.json + .md."""
    console = console or Console()
    digest = build_efficiency_digest(result, infer_step_part)
    phase_resets = _load_phase_resets(run_dir)

    user_payload = {
        "run_id": run_dir.name,
        "model": cfg.model,
        "workflow_mode": getattr(cfg, "workflow_mode", "default"),
        "phase_isolated_context": getattr(cfg, "phase_isolated_context", True),
        "digest": digest,
        "phase_context_resets": phase_resets,
        "final_answer_pixel": (
            (result.final_answer or {}).get("pixel")
            if isinstance(result.final_answer, dict)
            else None
        ),
    }
    user_text = (
        "以下是一次 VLM Agent run 的效率摘要（JSON）。"
        "请按 system 要求输出 Markdown 反思报告。\n\n"
        f"```json\n{json.dumps(user_payload, ensure_ascii=False, indent=2)}\n```"
    )
    messages = [
        client.system_message(REFLECTION_SYSTEM_ZH),
        client.user_message(user_text),
    ]

    console.print(Panel.fit(
        "[bold]post-run reflect[/bold] — calling VLM for efficiency review …",
        border_style="magenta",
    ))
    t0 = time.time()
    try:
        reply = client.chat(messages, tools_schema=None)
    except Exception as e:  # noqa: BLE001
        err_rec = {
            "model": cfg.model,
            "generated_at": _dt.datetime.now().isoformat(timespec="seconds"),
            "run_id": run_dir.name,
            "error": str(e)[:4000],
            "digest": digest,
        }
        (run_dir / "vlm_self_reflection.json").write_text(
            json.dumps(err_rec, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        console.print(Panel.fit(
            f"[red]post-run reflect failed[/red]: {e}",
            border_style="red",
        ))
        return err_rec

    reflect_dt = time.time() - t0
    reflection_md = (reply.content or "").strip()
    record: dict[str, Any] = {
        "model": cfg.model,
        "generated_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "run_id": run_dir.name,
        "reflection_timing_s": round(reflect_dt, 3),
        "usage": reply.usage,
        "digest": digest,
        "phase_context_resets": phase_resets,
        "reflection_markdown_zh": reflection_md,
    }
    (run_dir / "vlm_self_reflection.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    md_path = run_dir / "vlm_self_reflection.md"
    md_path.write_text(
        f"# VLM 运行后效率反思 — `{run_dir.name}`\n\n"
        f"- model: `{cfg.model}`\n"
        f"- generated: {record['generated_at']}\n"
        f"- reflection time: {reflect_dt:.1f}s\n\n"
        f"{reflection_md}\n",
        encoding="utf-8",
    )
    apply_plan: dict[str, Any] = {"actions": []}
    try:
        apply_plan = generate_apply_plan(
            client, cfg, run_dir, digest, reflection_md
        )
        (run_dir / "apply_plan.json").write_text(
            json.dumps(apply_plan, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:  # noqa: BLE001
        apply_plan = {
            "run_id": run_dir.name,
            "actions": [],
            "error": str(e)[:2000],
        }
        (run_dir / "apply_plan.json").write_text(
            json.dumps(apply_plan, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    record["apply_plan"] = apply_plan

    console.print(Panel.fit(
        f"[bold]post-run reflect[/bold] done in {reflect_dt:.1f}s\n"
        f"[bold]saved[/bold] = {run_dir / 'vlm_self_reflection.md'}\n"
        f"[bold]apply_plan[/bold] = {len(apply_plan.get('actions', []))} action(s)",
        border_style="magenta",
    ))
    return record
