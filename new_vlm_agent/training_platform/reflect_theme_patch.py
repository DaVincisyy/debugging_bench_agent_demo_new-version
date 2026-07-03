"""Inject platform optimization themes + cross-round history into baseline reflect (runtime only)."""

from __future__ import annotations

from typing import Any

_patched = False
_reflect_ctx: dict[str, Any] = {"baseline_ref": None}


def set_reflect_context(*, baseline_ref: str | None = None) -> None:
    """Called by autonomous_loop before each iteration."""
    _reflect_ctx["baseline_ref"] = baseline_ref


def _history_markdown() -> str:
    from training_platform.improvement_history import history_payload_for_reflect

    payload = history_payload_for_reflect(baseline_ref=_reflect_ctx.get("baseline_ref"))
    return str(payload.get("history_markdown_zh") or "")


def _append_history_to_user_content(content: str, history_md: str) -> str:
    if not history_md.strip():
        return content
    block = (
        "\n\n---\n\n"
        "## 历史改进记录（跨轮记忆，供反思时对照）\n\n"
        f"{history_md.strip()}\n"
    )
    return content + block


def apply_reflect_theme_patch() -> None:
    """Patch REFLECTION/APPLY_PLAN prompts and inject history into reflect calls."""
    global _patched
    if _patched:
        return
    from training_platform.optimization_directives import (
        augment_apply_plan_system,
        augment_reflect_system,
    )

    import agent.post_run_reflect as pr

    pr.REFLECTION_SYSTEM_ZH = augment_reflect_system(pr.REFLECTION_SYSTEM_ZH)
    pr.APPLY_PLAN_SYSTEM_ZH = augment_apply_plan_system(pr.APPLY_PLAN_SYSTEM_ZH)

    _orig_run = pr.run_post_run_reflection
    _orig_plan = pr.generate_apply_plan

    def generate_apply_plan_wrapped(
        client: Any,
        cfg: Any,
        run_dir: Any,
        digest: dict[str, Any],
        reflection_md: str,
    ) -> dict[str, Any]:
        import datetime as _dt
        import json as _json

        history_md = _history_markdown()
        payload: dict[str, Any] = {
            "run_id": run_dir.name,
            "phase_isolated_context": getattr(cfg, "phase_isolated_context", True),
            "digest_summary": {
                "part_timing": digest.get("part_timing"),
                "plan_guard_count": digest.get("plan_guard_count"),
                "slowest_steps": digest.get("slowest_steps"),
                "tool_call_counts": digest.get("tool_call_counts"),
            },
            "reflection_markdown_zh": reflection_md[:8000],
            "reflection_file": str(run_dir / "vlm_self_reflection.md"),
        }
        if history_md:
            payload["improvement_history_zh"] = history_md[:4000]
        user_text = (
            "根据以下 JSON 生成 apply_plan（仅输出 JSON 对象，最多 1 条 P0 action）：\n\n"
            f"{_json.dumps(payload, ensure_ascii=False, indent=2)}"
        )
        reply = client.chat(
            [
                client.system_message(pr.APPLY_PLAN_SYSTEM_ZH),
                client.user_message(user_text),
            ],
            tools_schema=None,
        )
        plan = pr._extract_json_object(reply.content or "") or {"actions": []}
        if "actions" not in plan or not isinstance(plan["actions"], list):
            plan = {"actions": []}
        if plan["actions"]:
            plan["actions"] = plan["actions"][:1]
        plan["run_id"] = run_dir.name
        plan["generated_at"] = _dt.datetime.now().isoformat(timespec="seconds")
        plan["model"] = cfg.model
        plan["generation_usage"] = dict(reply.usage or {})
        plan["reflection_source"] = str(run_dir / "vlm_self_reflection.md")
        return plan

    def run_post_run_reflection_wrapped(
        client: Any,
        cfg: Any,
        run_dir: Any,
        result: Any,
        infer_step_part: Any,
        console: Any = None,
    ) -> dict[str, Any] | None:
        history_md = _history_markdown()
        orig_chat = client.chat

        def chat_with_history(messages: Any, tools_schema: Any = None) -> Any:
            if history_md and messages:
                last = messages[-1]
                content = getattr(last, "content", None)
                if content is None and isinstance(last, dict):
                    content = last.get("content")
                if isinstance(content, str) and "效率摘要" in content:
                    new_content = _append_history_to_user_content(content, history_md)
                    if hasattr(last, "content"):
                        last.content = new_content
                    elif isinstance(last, dict):
                        last["content"] = new_content
            return orig_chat(messages, tools_schema=tools_schema)

        client.chat = chat_with_history
        try:
            return _orig_run(
                client, cfg, run_dir, result, infer_step_part, console=console
            )
        finally:
            client.chat = orig_chat

    pr.run_post_run_reflection = run_post_run_reflection_wrapped
    pr.generate_apply_plan = generate_apply_plan_wrapped
    _patched = True
