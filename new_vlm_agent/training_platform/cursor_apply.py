#!/usr/bin/env python3
"""Apply structured improvements via Cursor SDK (local agent)."""

from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from training_platform.cursor_sdk_winfix import apply_windows_bridge_patch
from training_platform.optimization_directives import (
    cursor_apply_constraint_lines,
    cursor_apply_surgical_policy_lines,
)


def _prepare_cursor_sdk() -> None:
    apply_windows_bridge_patch()


def extract_cursor_usage(agent_result: Any) -> dict[str, Any]:
    """Best-effort token/usage from Cursor SDK RunResult."""
    if agent_result is None:
        return {}
    for attr in ("usage", "token_usage", "usage_stats"):
        val = getattr(agent_result, attr, None)
        if val:
            if isinstance(val, dict):
                return dict(val)
            if hasattr(val, "model_dump"):
                return val.model_dump()
    if hasattr(agent_result, "model_dump"):
        dumped = agent_result.model_dump()
        if isinstance(dumped, dict) and dumped.get("usage"):
            u = dumped["usage"]
            return u if isinstance(u, dict) else {}
    return {}


def _read_reflection_markdown(
    reflection_path: Path | None,
    *,
    max_chars: int = 12000,
) -> tuple[str, str]:
    """Return (absolute_path, markdown body) for Cursor prompt."""
    if reflection_path is None or not reflection_path.is_file():
        return "", ""
    text = reflection_path.read_text(encoding="utf-8").strip()
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "\n\n…(reflection truncated for prompt size)"
    return str(reflection_path.resolve()), text


def _build_apply_prompt(
    apply_plan: dict[str, Any],
    repo_root: Path,
    run_id: str,
    *,
    reflection_path: Path | None = None,
    reflection_text: str = "",
    history_markdown: str = "",
) -> str:
    actions = apply_plan.get("actions") or []

    refl_abs, refl_body = (
        (str(reflection_path.resolve()), reflection_text)
        if reflection_text
        else _read_reflection_markdown(reflection_path)
    )
    if not refl_body and reflection_path and reflection_path.is_file():
        refl_abs, refl_body = _read_reflection_markdown(reflection_path)

    if not actions and not refl_body:
        return ""

    single_actions = actions[:1] if actions else []

    lines = [
        "You are an autonomous code-improvement agent for a PCBA TP localization repo.",
        f"Repository root: {repo_root.resolve()}",
        f"Triggered by run: {run_id}",
        "",
        "=== PRIMARY SOURCE (read this first) ===",
        f"Full VLM post-run reflection file: {refl_abs or '(missing)'}",
        "The reflection markdown below is the source of truth for WHAT to change.",
        "Sections 改进建议 and 优先级 define the intended P0 — implement only that one item.",
        "",
    ]
    if refl_body:
        lines.extend([
            "--- reflection markdown start ---",
            refl_body,
            "--- reflection markdown end ---",
            "",
        ])
    else:
        lines.append("(Reflection file missing — fall back to apply_plan JSON only.)\n")

    if history_markdown.strip():
        lines.extend([
            "=== IMPROVEMENT HISTORY (cross-round memory) ===",
            "Do NOT repeat changes that were reverted or failed in prior rounds.",
            history_markdown.strip(),
            "",
        ])

    lines.extend([
        "Hard constraints (must follow):",
        "- Implement ONLY ONE highest-priority P0 improvement from the reflection.",
        "- Do NOT batch multiple reflection items; one surgical change per apply pass.",
        "- Do NOT change golden files under training_platform/golden/ or tests/.",
        "- Do NOT disable phase_isolated_context or remove plan-guard safety checks.",
        "- Do NOT edit .env or commit secrets.",
        "- Do NOT create new agent modules (tool_router, context_manager, orchestrator, etc.).",
        "- After edits, append a short entry to training_platform/EFFICIENCY_LOG.md:",
        "  date, run_id, files changed, expected speed impact, risk.",
        "- Keep changes minimal and focused (typically one file, <30 lines changed).",
    ])
    lines.extend(cursor_apply_surgical_policy_lines())
    lines.extend(cursor_apply_constraint_lines())
    lines.extend([
        "",
        "=== SECONDARY HINT (apply_plan JSON — may diverge from reflection; prefer reflection) ===",
        "Acceptance: next agent run must still pass pixel tolerance 10px vs golden.",
        "",
        "apply_plan JSON:",
        json.dumps({"actions": single_actions}, ensure_ascii=False, indent=2),
        "",
        "Task: Open and read the reflection file if needed, then implement exactly ONE P0 from it.",
    ])
    return "\n".join(lines)


def apply_from_plan(
    apply_plan_path: Path,
    repo_root: Path,
    *,
    model: str | None = None,
    api_key: str | None = None,
    reflection_path: Path | None = None,
    history_markdown: str = "",
) -> dict[str, Any]:
    """Run Cursor SDK local agent to implement apply_plan.json."""
    t0 = time.time()
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
    except Exception:  # noqa: BLE001
        pass

    plan = json.loads(apply_plan_path.read_text(encoding="utf-8"))
    run_id = str(plan.get("run_id") or apply_plan_path.parent.name)
    actions = plan.get("actions") or []

    if reflection_path is None:
        candidate = apply_plan_path.parent / "vlm_self_reflection.md"
        if candidate.is_file():
            reflection_path = candidate
    if not reflection_path and plan.get("reflection_source"):
        candidate = Path(str(plan["reflection_source"]))
        if candidate.is_file():
            reflection_path = candidate

    result: dict[str, Any] = {
        "run_id": run_id,
        "apply_plan_path": str(apply_plan_path),
        "reflection_path": str(reflection_path) if reflection_path else None,
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "action_count": len(actions),
        "status": "skipped",
        "model": model or os.environ.get("CURSOR_APPLY_MODEL", "composer-2.5"),
    }

    refl_text = ""
    if reflection_path and reflection_path.is_file():
        _, refl_text = _read_reflection_markdown(reflection_path)

    if not actions and not refl_text:
        result["message"] = "No actions and no reflection; skip apply."
        result["elapsed_s"] = round(time.time() - t0, 2)
        return result

    key = api_key or os.environ.get("CURSOR_API_KEY")
    if not key:
        result["status"] = "error"
        result["error"] = (
            "CURSOR_API_KEY is not set. Create a key in Cursor Dashboard → Integrations."
        )
        result["elapsed_s"] = round(time.time() - t0, 2)
        return result

    prompt = _build_apply_prompt(
        plan,
        repo_root,
        run_id,
        reflection_path=reflection_path,
        reflection_text=refl_text,
        history_markdown=history_markdown,
    )
    if not prompt:
        result["message"] = "Empty prompt; skip."
        result["elapsed_s"] = round(time.time() - t0, 2)
        return result

    try:
        from cursor_sdk import Agent, AgentOptions, LocalAgentOptions
    except ImportError as e:
        result["status"] = "error"
        result["error"] = f"cursor-sdk not installed: {e}. Run: pip install cursor-sdk"
        result["elapsed_s"] = round(time.time() - t0, 2)
        return result

    _prepare_cursor_sdk()

    try:
        from cursor_sdk import CursorAgentError
    except ImportError:
        CursorAgentError = Exception  # type: ignore[misc, assignment]

    model_id = result["model"]
    print(f"[cursor_apply] Starting local Cursor agent (model={model_id}) ...")
    try:
        agent_result = Agent.prompt(
            prompt,
            AgentOptions(
                api_key=key,
                model=model_id,
                local=LocalAgentOptions(cwd=str(repo_root.resolve())),
            ),
        )
    except CursorAgentError as e:
        result["status"] = "error"
        result["error"] = f"CursorAgentError: {e}"
        result["elapsed_s"] = round(time.time() - t0, 2)
        return result
    except OSError as e:
        result["status"] = "error"
        result["error"] = f"Cursor local bridge failed on Windows ({e})"
        result["elapsed_s"] = round(time.time() - t0, 2)
        return result

    result["elapsed_s"] = round(time.time() - t0, 2)
    result["cursor_status"] = getattr(agent_result, "status", None)
    result["cursor_result"] = (getattr(agent_result, "result", None) or "")[:8000]
    result["usage"] = extract_cursor_usage(agent_result)
    if getattr(agent_result, "status", None) == "error":
        result["status"] = "error"
        result["error"] = result["cursor_result"] or "Cursor agent run failed"
    else:
        result["status"] = "ok"
        result["message"] = "Cursor apply agent finished"
    return result


def smoke_test(repo_root: Path, *, model: str | None = None, api_key: str | None = None) -> dict[str, Any]:
    t0 = time.time()
    try:
        from dotenv import load_dotenv
        load_dotenv(repo_root / ".env", override=False)
    except Exception:  # noqa: BLE001
        pass
    key = (api_key or os.environ.get("CURSOR_API_KEY") or "").strip()
    if not key:
        return {"status": "error", "mode": "smoke_test", "error": "CURSOR_API_KEY not set", "elapsed_s": round(time.time() - t0, 2)}
    from cursor_sdk import Agent, AgentOptions, LocalAgentOptions
    _prepare_cursor_sdk()
    try:
        from cursor_sdk import CursorAgentError
    except ImportError:
        CursorAgentError = Exception  # type: ignore[misc, assignment]
    model_id = model or os.environ.get("CURSOR_APPLY_MODEL", "composer-2.5")
    print(f"[cursor_apply] Smoke test (model={model_id}) ...")
    try:
        agent_result = Agent.prompt(
            "Smoke test only. Reply with exactly one word: PONG. Do not modify any files.",
            AgentOptions(api_key=key, model=model_id, local=LocalAgentOptions(cwd=str(repo_root.resolve()))),
        )
    except (CursorAgentError, OSError) as e:
        return {"status": "error", "mode": "smoke_test", "model": model_id, "error": str(e), "elapsed_s": round(time.time() - t0, 2)}
    ok = getattr(agent_result, "status", None) != "error"
    return {
        "status": "ok" if ok else "error",
        "mode": "smoke_test",
        "model": model_id,
        "cursor_result": (getattr(agent_result, "result", None) or "")[:500],
        "usage": extract_cursor_usage(agent_result),
        "elapsed_s": round(time.time() - t0, 2),
    }


def main() -> int:
    import argparse
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Cursor SDK apply / smoke test")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("smoke", help="Minimal PONG test (~30s)")
    p_apply = sub.add_parser("apply")
    p_apply.add_argument("plan", type=str)
    args = parser.parse_args()
    os.chdir(repo_root)
    if args.command == "smoke":
        result = smoke_test(repo_root)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] == "ok" else 1
    plan_path = Path(args.plan)
    result = apply_from_plan(plan_path, repo_root)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
