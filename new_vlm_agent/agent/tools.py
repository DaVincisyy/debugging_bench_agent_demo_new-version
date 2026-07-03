"""Tool registry and base types.

A "tool" here is a small, side-effectful capability the agent can invoke
via function-calling. Each tool declares a JSON schema so it can be
advertised to the VLM in the OpenAI `tools=[...]` format, and returns a
`ToolResult` that may carry plain text and/or image artifacts for the
next assistant turn.
"""

from __future__ import annotations

import traceback
from dataclasses import dataclass, field
from typing import Any, Callable


# --------------------------------------------------------------------------- #
# Result type
# --------------------------------------------------------------------------- #

@dataclass
class ToolResult:
    """Return value of a tool invocation.

    * `text`        - textual feedback that will be shown to the VLM (as a
                       `role=tool` message content).
    * `images`      - optional list of local image paths the agent should
                       attach as image parts in the NEXT user turn
                       (providers generally refuse images inside `role=tool`).
    * `is_final`    - set by the `finish` tool to break the loop.
    * `final_data`  - JSON-serialisable answer payload when `is_final=True`.
    * `ok`          - False if the tool failed; the text contains the error.
    """
    text: str
    images: list[str] = field(default_factory=list)
    is_final: bool = False
    final_data: Any = None
    ok: bool = True


# --------------------------------------------------------------------------- #
# Tool definition
# --------------------------------------------------------------------------- #

ToolFn = Callable[..., ToolResult]


def normalize_finish_arguments(arguments: Any) -> dict[str, Any]:
    """Ensure `finish` receives `{"answer": ...}`. VLMs often flatten fields."""
    if not isinstance(arguments, dict):
        return {"answer": arguments}
    if "answer" in arguments:
        return dict(arguments)
    noise = {"name", "tool", "arguments", "args", "_raw"}
    body = {k: v for k, v in arguments.items() if k not in noise}
    return {"answer": body}


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]   # JSON schema for the `function.parameters`
    fn: ToolFn

    def to_openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    """Holds all tools the agent can call and dispatches invocations."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Duplicate tool name: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def openai_schema(self) -> list[dict[str, Any]]:
        return [t.to_openai_schema() for t in self._tools.values()]

    def describe_for_prompt(self) -> str:
        """Human-readable tool list, used in the system prompt. Useful for
        providers that do not support native tool calling."""
        lines = []
        for t in self._tools.values():
            params = t.parameters.get("properties", {})
            required = set(t.parameters.get("required", []))
            arg_desc = ", ".join(
                f"{k}{'*' if k in required else ''}: {v.get('type', 'any')}"
                for k, v in params.items()
            ) or "(no args)"
            lines.append(f"- {t.name}({arg_desc}) — {t.description}")
        return "\n".join(lines)

    def run(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(
                text=f"[tool-error] unknown tool: {name!r}. "
                     f"Known tools: {self.names()}",
                ok=False,
            )
        try:
            args = arguments if isinstance(arguments, dict) else {}
            return tool.fn(**args)
        except TypeError as e:
            return ToolResult(
                text=f"[tool-error] bad arguments for {name}: {e}",
                ok=False,
            )
        except Exception as e:  # noqa: BLE001 — we want to surface all errors
            tb = traceback.format_exc(limit=5)
            return ToolResult(
                text=f"[tool-error] {name} raised {type(e).__name__}: {e}\n{tb}",
                ok=False,
            )
