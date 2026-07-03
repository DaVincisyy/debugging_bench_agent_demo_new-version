"""Command-line entry point.

Usage:
    python -m agent run tasks/example_tp_locate.yaml
    python -m agent run tasks/example_tp_locate.yaml --model gpt-4.1 --max-steps 30
    python -m agent run tasks/example_tp_locate.yaml --mode vlm_test
    python -m agent run tasks/example_tp_locate.yaml --post-run-reflect
    python -m agent --post-run-reflect run tasks/example_tp_locate.yaml
    python -m agent ask "where is TP12 in the front camera?" \\
        --image locator=data/locator.png --image front=data/front.jpg

The whole point of the CLI is that switching VLM is a matter of editing
`.env` (or overriding on the command line) — no code changes.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from .agent import Agent
from .config import compose_agent_question, load_config, load_task

_BOOL_FLAGS = (
    "no_native_tools",
    "flat_agent",
    "no_phase_isolation",
    "post_run_reflect",
)


def _add_shared_cli_args(p: argparse.ArgumentParser) -> None:
    """Flags shared by the root parser and run/ask subcommands."""
    p.add_argument("--env", default=".env", help="Path to the .env file (default: .env)")
    p.add_argument("--model", help="Override VLM_MODEL for this run.")
    p.add_argument("--base-url", help="Override VLM_BASE_URL for this run.")
    p.add_argument("--max-steps", type=int, default=None,
                   help="Maximum planning/tool steps (default: 20).")
    p.add_argument("--workspace", default="workspace",
                   help="Workspace directory for artifacts and run logs.")
    p.add_argument("--no-native-tools", action="store_true",
                   help="Force JSON-tag fallback instead of native tool calling.")
    p.add_argument("--flat-agent", action="store_true",
                   help="Disable hierarchical planner/worker mode.")
    p.add_argument("--no-phase-isolation", action="store_true",
                   help="Disable per-phase fresh context (keep single rolling messages).")
    p.add_argument("--post-run-reflect", action="store_true",
                   help=(
                       "After the run finishes, ask the same VLM to review efficiency "
                       "(writes workspace/runs/<id>/vlm_self_reflection.md). "
                       "Env: VLM_POST_RUN_REFLECT=true"
                   ))


def _build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    _add_shared_cli_args(common)

    p = argparse.ArgumentParser(
        prog="agent",
        description="VLM agent for PCBA TP localization.",
    )
    _add_shared_cli_args(p)

    sub = p.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser(
        "run",
        help="Run the agent on a YAML task spec.",
        parents=[common],
    )
    p_run.add_argument("task_file", help="Path to a task YAML file.")
    p_run.add_argument(
        "--mode",
        default="default",
        choices=("default", "vlm_test"),
        metavar="MODE",
        help=(
            "Workflow variant: "
            "`default` = STANDARD_WORKFLOW Part D `case12_step02_opencv_ic_align`; "
            "`vlm_test` = skip Part A board IC box, pure-VLM Part D (`case12_step02_vlm_ic_align`). "
            "(Env override: `VLM_AGENT_WORKFLOW_MODE`)"
        ),
    )
    p_run.add_argument("--name", help="Optional run name suffix.")

    p_ask = sub.add_parser(
        "ask",
        help="Run the agent on an ad-hoc question.",
        parents=[common],
    )
    p_ask.add_argument("question", help="Natural-language question.")
    p_ask.add_argument("--image", action="append", default=[],
                       metavar="KEY=PATH",
                       help="Attach an image input. Can be repeated.")
    p_ask.add_argument("--file", action="append", default=[],
                       metavar="KEY=PATH",
                       help="Attach a text/other file input. Can be repeated.")
    p_ask.add_argument("--note", action="append", default=[],
                       metavar="KEY=TEXT",
                       help="Attach a plain-text note. Can be repeated.")
    p_ask.add_argument("--name", help="Optional run name suffix.")

    return p


def _merge_cli_namespaces(
    before_sub: argparse.Namespace,
    after_sub: argparse.Namespace,
) -> argparse.Namespace:
    """Merge global flags parsed before `run`/`ask` with the subcommand namespace."""
    merged = argparse.Namespace(**vars(after_sub))
    for key in _BOOL_FLAGS:
        if getattr(before_sub, key, False) or getattr(merged, key, False):
            setattr(merged, key, True)
    for key in ("model", "base_url", "max_steps"):
        before_val = getattr(before_sub, key, None)
        after_val = getattr(merged, key, None)
        if after_val is not None:
            setattr(merged, key, after_val)
        elif before_val is not None:
            setattr(merged, key, before_val)
    if getattr(merged, "env", None) in (None, ".env") and getattr(before_sub, "env", None):
        merged.env = before_sub.env
    if getattr(merged, "workspace", None) in (None, "workspace") and getattr(before_sub, "workspace", None):
        merged.workspace = before_sub.workspace
    return merged


def _parse_cli_args(argv: list[str] | None = None) -> argparse.Namespace:
    argv = list(argv if argv is not None else sys.argv[1:])
    pre = argparse.ArgumentParser(add_help=False)
    _add_shared_cli_args(pre)
    pre_ns, remaining = pre.parse_known_args(argv)
    args = _build_parser().parse_args(remaining)
    return _merge_cli_namespaces(pre_ns, args)


def _parse_kv(items: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for it in items:
        if "=" not in it:
            raise SystemExit(f"Expected KEY=VALUE, got: {it!r}")
        k, v = it.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def main(argv: list[str] | None = None) -> int:
    args = _parse_cli_args(argv)

    overrides: dict[str, Any] = {
        "workspace_dir": Path(args.workspace),
    }
    if args.max_steps is not None:
        overrides["max_steps"] = args.max_steps
    if args.model:
        overrides["VLM_MODEL"] = args.model
    if args.base_url:
        overrides["VLM_BASE_URL"] = args.base_url
    if args.no_native_tools:
        overrides["VLM_USE_NATIVE_TOOLS"] = "false"
    if args.flat_agent:
        overrides["VLM_HIERARCHICAL_AGENT_MODE"] = "false"
    if args.no_phase_isolation:
        overrides["VLM_PHASE_ISOLATED_CONTEXT"] = "false"
    if getattr(args, "post_run_reflect", False):
        overrides["VLM_POST_RUN_REFLECT"] = "true"

    if args.cmd == "run":
        overrides["workflow_mode"] = getattr(args, "mode", "default")

    cfg = load_config(env_path=args.env, overrides=overrides)
    agent = Agent(cfg)

    if args.cmd == "run":
        task = load_task(args.task_file)
        result = agent.run(
            question=compose_agent_question(task),
            inputs=task.get("inputs", {}),
            run_name=args.name or Path(args.task_file).stem,
        )
    elif args.cmd == "ask":
        inputs: dict[str, Any] = {}
        inputs.update(_parse_kv(args.image))
        inputs.update(_parse_kv(args.file))
        inputs.update(_parse_kv(args.note))
        result = agent.run(
            question=args.question,
            inputs=inputs,
            run_name=args.name,
        )
    else:  # pragma: no cover — argparse already enforces this.
        raise SystemExit(f"Unknown command: {args.cmd}")

    return 0 if result.final_answer is not None else 2


if __name__ == "__main__":
    sys.exit(main())
