"""OpenAI-compatible chat client with vision + tool-calling support.

The design goal is that swapping the backend VLM only requires editing
`.env` (`VLM_BASE_URL`, `VLM_API_KEY`, `VLM_MODEL`). Every mainstream
provider — OpenAI, DeepSeek, Qwen/DashScope, Zhipu GLM-4V, Doubao,
Moonshot, local vLLM — exposes an OpenAI-compatible Chat Completions
endpoint, so a single client is enough.

Two tool-calling modes are supported:

* Native `tools=[...]` + `tool_choice="auto"` (preferred, OpenAI-style).
* JSON-tag fallback for providers whose tool protocol is incomplete /
  buggy. The model is asked to emit `<tool_call>{"name":...}</tool_call>`
  which we parse on our side.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass
from typing import Any, Iterable

from openai import APIConnectionError, APITimeoutError, BadRequestError, OpenAI

from .config import Config


# --------------------------------------------------------------------------- #
# Data types exchanged with the agent core
# --------------------------------------------------------------------------- #

@dataclass
class ToolInvocation:
    """A single tool call the model wants the agent runtime to execute."""
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class AssistantReply:
    """Normalised view of one assistant turn.

    `raw_message` is the dict we must append back into `messages` so the
    provider can resolve follow-up `tool` messages against the same
    `tool_call_id`s it emitted. We keep it opaque on purpose.
    """
    content: str
    tool_calls: list[ToolInvocation]
    raw_message: dict[str, Any]


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #

_TOOL_TAG_BLOCK_RE = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.DOTALL)


def _as_bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _dedupe_finish_calls(invocations: list[ToolInvocation]) -> list[ToolInvocation]:
    """If the model emits multiple ``finish`` tags in one turn, keep the last one."""
    finishes = [i for i in invocations if i.name == "finish"]
    if len(finishes) <= 1:
        return invocations
    others = [i for i in invocations if i.name != "finish"]
    return others + [finishes[-1]]


def _parse_tagged_tool_calls(content: str) -> list[ToolInvocation]:
    """Parse ``<tool_call>{...}</tool_call>`` blocks.

    The previous regex used ``\\{.*?\\}``, which stops at the *first* ``}`` and
    breaks nested JSON (e.g. ``finish`` with ``arguments.answer``).
    """
    invocations: list[ToolInvocation] = []
    for i, m in enumerate(_TOOL_TAG_BLOCK_RE.finditer(content)):
        raw = (m.group(1) or "").strip()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        name = payload.get("name") or payload.get("tool")
        args = payload.get("arguments") or payload.get("args") or {}
        if not name:
            continue
        invocations.append(ToolInvocation(
            id=f"tag_{i}",
            name=name,
            arguments=args if isinstance(args, dict) else {"_raw": args},
        ))
    return _dedupe_finish_calls(invocations)


class LLMClient:
    """Thin wrapper around `openai.OpenAI` with multimodal helpers."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._client = self._build_client()

    def _build_client(self) -> OpenAI:
        import httpx

        default_headers: dict[str, str] = {}
        if _as_bool_env("VLM_FORCE_CONNECTION_CLOSE", False):
            default_headers["Connection"] = "close"

        # Use a direct HTTP transport with no proxy to bypass Windows system
        # proxy. The system proxy interferes with TLS to DashScope/other
        # OpenAI-compatible endpoints, causing WinError 10054 / connection
        # resets when large image payloads are exchanged.
        http_client = httpx.Client(
            timeout=httpx.Timeout(self.cfg.http_timeout_sec),
            mounts={"all://": httpx.HTTPTransport()},
        )

        return OpenAI(
            base_url=self.cfg.base_url,
            api_key=self.cfg.api_key,
            timeout=self.cfg.http_timeout_sec,
            max_retries=self.cfg.http_max_retries,
            default_headers=default_headers or None,
            http_client=http_client,
        )

    # ------------------------------------------------------------------ #
    # Message builders — kept here so tests and the agent share them.
    # ------------------------------------------------------------------ #

    @staticmethod
    def text_part(text: str) -> dict[str, Any]:
        return {"type": "text", "text": text}

    @staticmethod
    def image_part(data_url: str, detail: str = "high") -> dict[str, Any]:
        return {
            "type": "image_url",
            "image_url": {"url": data_url, "detail": detail},
        }

    @staticmethod
    def user_message(parts: Iterable[dict[str, Any]] | str) -> dict[str, Any]:
        if isinstance(parts, str):
            return {"role": "user", "content": parts}
        return {"role": "user", "content": list(parts)}

    @staticmethod
    def system_message(text: str) -> dict[str, Any]:
        return {"role": "system", "content": text}

    @staticmethod
    def tool_result_message(tool_call_id: str,
                            content: str | list[dict[str, Any]]) -> dict[str, Any]:
        """Build a `role=tool` message. Some providers only accept strings,
        others accept multimodal parts; we default to string but allow a
        list for providers that tolerate images inside tool results.
        """
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": content,
        }

    # ------------------------------------------------------------------ #
    # Main entry point
    # ------------------------------------------------------------------ #

    def chat(self, messages: list[dict[str, Any]],
             tools_schema: list[dict[str, Any]] | None = None) -> AssistantReply:
        """Call the underlying chat.completions endpoint once.

        `tools_schema` follows the OpenAI tool schema
        (`[{"type": "function", "function": {"name":..., "parameters":...}}, ...]`).
        """
        kwargs: dict[str, Any] = {
            "model": self.cfg.model,
            "messages": messages,
            "temperature": self.cfg.temperature,
            "max_tokens": self.cfg.max_tokens,
        }
        if self.cfg.use_native_tools and tools_schema:
            kwargs["tools"] = tools_schema
            kwargs["tool_choice"] = "auto"

        # --- thinking / reasoning controls ---------------------------- #
        # 1) DeepSeek V4: extra_body={"thinking":{"type":"enabled"}}
        # 2) Intern-S1 family (LMDeploy Chat API): extra_body.thinking_mode=<bool>
        # 3) Optional reasoning_effort for providers that support it.
        model_lower = self.cfg.model.lower()
        extra_body: dict[str, Any] = {}

        if self.cfg.enable_thinking:
            extra_body["thinking"] = {"type": "enabled"}

        if model_lower.startswith("intern-s1") and self.cfg.thinking_mode is not None:
            extra_body["thinking_mode"] = bool(self.cfg.thinking_mode)

        if extra_body:
            kwargs["extra_body"] = extra_body

        # Intern-S1 docs emphasise `thinking_mode`; avoid sending an
        # unsupported `reasoning_effort` there unless explicitly needed.
        if self.cfg.reasoning_effort and not model_lower.startswith("intern-s1"):
            kwargs["reasoning_effort"] = self.cfg.reasoning_effort

        resp = None
        attempts = max(1, int(self.cfg.connect_retries))
        for attempt in range(attempts):
            try:
                resp = self._client.chat.completions.create(**kwargs)
                break
            except BadRequestError as e:
                body = str(e)
                if "unknown variant `image_url`, expected `text`" in body:
                    raise RuntimeError(
                        "Current model/provider rejected inline image blocks "
                        "(`image_url`). This usually means the selected model is "
                        "text-only. For DeepSeek, switch to a vision-capable model "
                        "(e.g. a `...vl...` / `...vision...` model) or keep the "
                        "current model and rely on tool-driven image processing only."
                    ) from e
                if "fc related" in body.lower() or "-20009" in body:
                    raise RuntimeError(
                        "This OpenAI-compatible endpoint rejected native function-calling "
                        "(`tools` / `tool_choice`). Intern chat gateways sometimes return "
                        "code -20009 / 'Failed to parse fc related info…' when the model "
                        "or route does not accept the advertised tool schema.\n\n"
                        "Try:\n"
                        "  • Set VLM_USE_NATIVE_TOOLS=false in .env, or run:\n"
                        "      python -m agent run <task.yaml> --no-native-tools\n"
                        "    (uses <tool_call>…</tool_call> in the assistant text instead.)\n"
                        "  • Or switch to a model/endpoint on that platform that explicitly "
                        "supports OpenAI-style tools for your account."
                    ) from e
                raise
            except (APIConnectionError, APITimeoutError) as e:
                if attempt + 1 >= attempts:
                    raise
                self._client = self._build_client()
                delay = min(4.0 * (2 ** attempt), 60.0)
                print(
                    "[Network Retry] 侦测到链路断开，正在重建 Python OpenAI client "
                    f"并进行第 {attempt + 2}/{attempts} 次重试: {type(e).__name__}: {e}; "
                    f"delay={delay:.1f}s",
                    file=sys.stderr,
                )
                time.sleep(delay)
        if resp is None:  # pragma: no cover
            raise RuntimeError("LLMClient.chat: failed without response")

        choice = resp.choices[0]
        msg = getattr(choice, "message", None)

        # Some OpenAI-compatible providers occasionally return a choice
        # whose `message` is null (or malformed) when multimodal/tool
        # turns are mixed. Do not crash; degrade gracefully.
        if msg is None:
            fallback_content = ""
            if hasattr(choice, "text") and isinstance(choice.text, str):
                fallback_content = choice.text
            elif hasattr(choice, "model_dump"):
                try:
                    dumped = choice.model_dump()
                    maybe_text = dumped.get("text")
                    if isinstance(maybe_text, str):
                        fallback_content = maybe_text
                except Exception:  # noqa: BLE001
                    pass
            fallback_content = (fallback_content or "").strip()
            if not fallback_content:
                fallback_content = (
                    "[provider-warning] assistant message is null; "
                    "continue with tools or call finish."
                )
            return AssistantReply(
                content=fallback_content,
                tool_calls=[],
                raw_message={"role": "assistant", "content": fallback_content},
            )

        content = msg.content or ""
        tool_calls: list[ToolInvocation] = []
        raw_message: dict[str, Any] = {"role": "assistant", "content": content}

        # DeepSeek's docs state: when the assistant emitted tool calls,
        # subsequent turns MUST re-send `reasoning_content` or the API
        # returns 400. We therefore attach it to raw_message; the agent
        # loop appends raw_message verbatim to `messages`, so it will
        # round-trip to the provider unchanged. Providers that do not
        # recognise the field typically ignore extra JSON keys.
        reasoning = getattr(msg, "reasoning_content", None)
        if reasoning:
            raw_message["reasoning_content"] = reasoning

        # Native tool_calls path ---------------------------------------- #
        native_calls = getattr(msg, "tool_calls", None) or []
        if native_calls:
            raw_tc: list[dict[str, Any]] = []
            for tc in native_calls:
                args_raw = tc.function.arguments or "{}"
                try:
                    args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
                except json.JSONDecodeError:
                    args = {"_raw": args_raw}
                tool_calls.append(ToolInvocation(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=args,
                ))
                raw_tc.append({
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": args_raw if isinstance(args_raw, str) else json.dumps(args_raw),
                    },
                })
            raw_message["tool_calls"] = raw_tc

        # JSON-tag fallback --------------------------------------------- #
        # Only parse tags when the provider did NOT emit native tool_calls,
        # otherwise we'd double-execute the same call.
        elif content:
            tool_calls = _parse_tagged_tool_calls(content)

        return AssistantReply(
            content=content,
            tool_calls=tool_calls,
            raw_message=raw_message,
        )
