"""Agent core: the plan → tool → observe loop.

Design goals:

* Keep the loop provider-agnostic. Anything VLM-specific sits inside
  `LLMClient`; anything task-specific sits inside `prompts.py`. The
  loop here just orchestrates message building, tool dispatch and
  trace logging.
* When a tool produces an image (e.g. a crop or an annotated capture),
  we attach it as an image part in the NEXT user turn, because most
  OpenAI-compatible providers reject multimodal content inside
  `role=tool` messages.
* Every run writes a JSONL trace under `workspace/runs/<timestamp>/`
  so training / evaluation scripts can replay conversations offline.
"""

from __future__ import annotations

import datetime as _dt
import json
import math
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel

from .builtin_tools import build_default_registry, set_runtime_context
from .config import Config
from .llm_client import AssistantReply, LLMClient, ToolInvocation
from .prompts import (
    CLI_WORKFLOW_MODE_VLM_TEST_APPEND_ZH,
    FALLBACK_TOOL_PROTOCOL,
    SYSTEM_PROMPT_TP_LOCATE,
)
from .tools import ToolRegistry, ToolResult, normalize_finish_arguments
from .utils import encode_image_data_url, truncate


# --------------------------------------------------------------------------- #
# Result types
# --------------------------------------------------------------------------- #

@dataclass
class AgentStep:
    index: int
    assistant_content: str
    tool_calls: list[dict[str, Any]]
    tool_results: list[dict[str, Any]]


@dataclass
class AgentRun:
    task_question: str
    steps: list[AgentStep] = field(default_factory=list)
    final_answer: Any = None
    stopped_reason: str = "unfinished"
    run_dir: Path | None = None
    # Set when the run aborts on an uncaught exception (still persisted).
    last_error: str | None = None


# --------------------------------------------------------------------------- #
# Agent
# --------------------------------------------------------------------------- #

class Agent:
    def __init__(self,
                 cfg: Config,
                 registry: ToolRegistry | None = None,
                 console: Console | None = None,
                 system_prompt: str | None = None) -> None:
        self.cfg = cfg
        self.cfg.ensure_workspace()
        self.registry = registry or build_default_registry(cfg.workspace_dir)
        self.client = LLMClient(cfg)
        self.console = console or Console()
        self.system_prompt = system_prompt or SYSTEM_PROMPT_TP_LOCATE

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def run(self, question: str,
            inputs: dict[str, Any] | None = None,
            run_name: str | None = None,
            event_sink: Any | None = None) -> AgentRun:
        """Execute the agent loop for a single task.

        `inputs` is a free-form dict. Keys whose value is a path ending in
        a common image extension are auto-attached as images in the first
        user turn; everything else is listed as text context.
        """
        wf_mode = str(getattr(self.cfg, "workflow_mode", "default") or "default").strip()
        if not wf_mode:
            wf_mode = "default"
        if wf_mode == "vlm_test":
            os.environ["VLM_AGENT_WORKFLOW_MODE"] = "vlm_test"
        else:
            os.environ.pop("VLM_AGENT_WORKFLOW_MODE", None)

        run_dir = self._prepare_run_dir(run_name)
        q_eff = self._effective_task_question(question)

        def emit(event_type: str, payload: dict[str, Any]) -> None:
            if event_sink is None:
                return
            try:
                event_sink(event_type, payload)
            except Exception:
                # UI event streaming is best-effort and must not break a run.
                pass

        emit("agent.run_dir", {
            "run_dir": str(run_dir),
            "workspace": str(self.cfg.workspace_dir),
            "workflow_mode": self.cfg.workflow_mode,
        })
        self.console.print(Panel.fit(
            f"[bold]model[/bold] = {self.cfg.model}\n"
            f"[bold]workspace[/bold] = {self.cfg.workspace_dir}\n"
            f"[bold]workflow_mode[/bold] = {self.cfg.workflow_mode}\n"
            f"[bold]run_dir[/bold] = {run_dir}\n"
            f"[bold]tools[/bold] = {', '.join(self.registry.names())}",
            title="VLM Agent starting",
        ))

        messages = self._initial_messages(q_eff, inputs or {})
        set_runtime_context(
            project_root=Path.cwd(),
            workspace=self.cfg.workspace_dir,
            input_paths={k: v for k, v in (inputs or {}).items() if isinstance(v, str)},
            workflow_mode=getattr(self.cfg, "workflow_mode", "default"),
        )
        self._log_jsonl(run_dir, "messages.init.jsonl", messages)

        result = AgentRun(task_question=q_eff, run_dir=run_dir)
        tools_schema = self.registry.openai_schema() if self.cfg.use_native_tools else None
        run_exception: BaseException | None = None

        try:
            for step_idx in range(self.cfg.max_steps):
                self._compact_old_inline_images(messages)
                t0 = time.time()
                emit("agent.waiting", {
                    "step": step_idx,
                    "message_count": len(messages),
                    "status": "waiting_for_model",
                })
                reply = self.client.chat(messages, tools_schema=tools_schema)
                dt = time.time() - t0

                self._render_assistant(step_idx, reply, dt)
                messages.append(reply.raw_message)

                if not reply.tool_calls:
                    emit("agent.assistant", {
                        "step": step_idx,
                        "content": reply.content,
                        "tool_calls": [],
                    })
                    result.steps.append(AgentStep(
                        index=step_idx,
                        assistant_content=reply.content,
                        tool_calls=[],
                        tool_results=[],
                    ))
                    # Some models occasionally emit an empty assistant turn.
                    # Nudge once to either continue with tools or terminate
                    # cleanly via `finish`, instead of silently stopping.
                    if step_idx < self.cfg.max_steps - 1:
                        messages.append(self.client.user_message(
                            "You emitted no tool call. Continue by calling the "
                            "next required tool, or call `finish` now with a "
                            "structured answer (or needs_user_help=true if "
                            "evidence is insufficient)."
                        ))
                        continue
                    result.stopped_reason = "assistant-stopped-without-tool-call"
                    break

                tool_call_payload: list[dict[str, Any]] = []
                tool_result_payload: list[dict[str, Any]] = []
                attached_images: list[str] = []
                final_answer: Any = None

                for call in reply.tool_calls:
                    exec_arguments: Any = call.arguments
                    if call.name == "finish" and isinstance(call.arguments, dict):
                        exec_arguments = normalize_finish_arguments(call.arguments)
                    result_obj = self.registry.run(call.name, exec_arguments)
                    # Hard guardrails: if model tries to finish without required
                    # skill artifacts/evidence, reject and ask it to continue.
                    if call.name == "finish" and result_obj.is_final:
                        contract_errors = self._validate_skill_contract()
                        contract_errors.extend(
                            self._validate_finish_answer(
                                exec_arguments.get("answer")
                                if isinstance(exec_arguments, dict)
                                else None,
                            )
                        )
                        if contract_errors:
                            result_obj = ToolResult(
                                text=(
                                    "[contract-error] finish was rejected because required "
                                    "skill artifacts are missing:\n- "
                                    + "\n- ".join(contract_errors)
                                    + "\nPlease continue tool calls to produce the missing "
                                      "evidence, then call finish again."
                                ),
                                ok=False,
                                is_final=False,
                                final_data=None,
                            )
                    self._render_tool(call, result_obj)
                    if self.cfg.use_native_tools:
                        messages.append(self.client.tool_result_message(
                            tool_call_id=call.id,
                            content=result_obj.text,
                        ))
                    else:
                        # Fallback: non-native providers often reject role=tool,
                        # so we feed the result back as a user message.
                        messages.append(self.client.user_message(
                            f"[tool-result name={call.name} id={call.id}]\n"
                            f"{result_obj.text}"
                        ))
                    tool_call_payload.append({
                        "id": call.id,
                        "name": call.name,
                        "arguments": exec_arguments
                        if isinstance(exec_arguments, dict)
                        else call.arguments,
                    })
                    tool_result_payload.append({
                        "id": call.id,
                        "ok": result_obj.ok,
                        "text": truncate(result_obj.text, 2000),
                        "images": result_obj.images,
                        "is_final": result_obj.is_final,
                    })
                    attached_images.extend(result_obj.images)
                    emit("agent.step", {
                        "step": step_idx,
                        "assistant_content": reply.content,
                        "tool_call": {
                            "id": call.id,
                            "name": call.name,
                            "arguments": exec_arguments
                            if isinstance(exec_arguments, dict)
                            else call.arguments,
                        },
                        "tool_result": {
                            "ok": result_obj.ok,
                            "text": truncate(result_obj.text, 2000),
                            "images": result_obj.images,
                            "is_final": result_obj.is_final,
                        },
                    })
                    if result_obj.is_final and final_answer is None:
                        final_answer = result_obj.final_data

                result.steps.append(AgentStep(
                    index=step_idx,
                    assistant_content=reply.content,
                    tool_calls=tool_call_payload,
                    tool_results=tool_result_payload,
                ))

                # If any tool produced images, push them in a follow-up user
                # message so the VLM can look at them next turn.
                if attached_images:
                    parts: list[dict[str, Any]] = [
                        self.client.text_part(
                            "Here are the image(s) produced by the previous tool call(s). "
                            "Inspect them and continue.\n"
                            "Attached file paths (for `view_image` if older attachments are "
                            "removed from context to save size):\n"
                            + "\n".join(f"- {p}" for p in attached_images)
                        )
                    ]
                    if self._supports_inline_images():
                        for img_path in attached_images:
                            try:
                                parts.append(self.client.image_part(
                                    encode_image_data_url(img_path)
                                ))
                            except Exception as e:  # noqa: BLE001
                                parts.append(self.client.text_part(
                                    f"[image-attach-error] {img_path}: {e}"
                                ))
                    else:
                        joined = "\n".join(f"- {p}" for p in attached_images)
                        parts.append(self.client.text_part(
                            "Inline image blocks are not supported by current "
                            f"model/provider. Generated image files:\n{joined}\n"
                            "Use these paths with tools in subsequent steps."
                        ))
                    messages.append(self.client.user_message(parts))

                if final_answer is not None:
                    result.final_answer = final_answer
                    result.stopped_reason = "finish-tool-called"
                    emit("agent.final", {
                        "final_answer": final_answer,
                        "stopped_reason": result.stopped_reason,
                        "run_dir": str(run_dir),
                    })
                    break
            else:
                result.stopped_reason = "max-steps-reached"
        except BaseException as e:
            run_exception = e
            result.stopped_reason = f"exception:{type(e).__name__}"
            result.last_error = str(e)[:8000]
            emit("agent.failed", {
                "error": result.last_error,
                "stopped_reason": result.stopped_reason,
                "run_dir": str(run_dir),
            })
        finally:
            self._persist_run(run_dir, result, messages)
            title = "VLM Agent done"
            if run_exception is not None:
                title = "VLM Agent stopped (error logged)"
            self.console.print(Panel.fit(
                f"[bold]stopped[/bold] = {result.stopped_reason}\n"
                f"[bold]steps[/bold]   = {len(result.steps)}\n"
                f"[bold]answer[/bold]  = "
                f"{json.dumps(result.final_answer, ensure_ascii=False) if result.final_answer else '(none)'}",
                title=title,
            ))

        if run_exception is not None:
            raise run_exception
        return result

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    _IMG_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tiff"}

    @staticmethod
    def _user_message_has_inline_image(content: Any) -> bool:
        if not isinstance(content, list):
            return False
        return any(
            isinstance(p, dict) and p.get("type") == "image_url"
            for p in content
        )

    @staticmethod
    def _inline_image_url_byte_estimate(messages: list[dict[str, Any]]) -> int:
        """Rough size of data-URL image payloads (dominates JSON request body)."""
        total = 0
        for m in messages:
            c = m.get("content")
            if not isinstance(c, list):
                continue
            for p in c:
                if not isinstance(p, dict) or p.get("type") != "image_url":
                    continue
                u = p.get("image_url")
                url = u.get("url") if isinstance(u, dict) else u
                if isinstance(url, str):
                    total += len(url.encode("utf-8"))
        return total

    @staticmethod
    def _strip_image_urls_from_user_message(msg: dict[str, Any]) -> None:
        """Remove image_url parts; keep text so paths / tool output remain."""
        content = msg.get("content")
        if not isinstance(content, list):
            return
        removed = 0
        new_parts: list[Any] = []
        for p in content:
            if isinstance(p, dict) and p.get("type") == "image_url":
                removed += 1
                continue
            new_parts.append(p)
        if removed == 0:
            return
        note = (
            f"\n\n[context compaction: removed {removed} inline image(s) to stay under "
            "API / gateway size limits; use `view_image` on paths listed in this turn "
            "or in tool text output.]"
        )
        appended = False
        for p in new_parts:
            if isinstance(p, dict) and p.get("type") == "text":
                t = p.get("text")
                p["text"] = (t if isinstance(t, str) else "") + note
                appended = True
                break
        if not appended:
            new_parts.insert(0, {"type": "text", "text": note.strip()})
        msg["content"] = new_parts

    def _compact_old_inline_images(self, messages: list[dict[str, Any]]) -> None:
        """Remove inline images only when size exceeds max; strip earliest first.

        Protected: the last ``context_image_keep_last`` user turns that still
        carry images (minimum 1 when max_bytes > 0). If still over budget,
        strip again while protecting only the newest image-bearing turn.
        """
        max_b = int(self.cfg.context_image_max_bytes)
        if max_b <= 0:
            return
        if self._inline_image_url_byte_estimate(messages) <= max_b:
            return

        keep = int(self.cfg.context_image_keep_last)
        if keep <= 0:
            keep = 1

        image_user_indices: list[int] = []
        for i, m in enumerate(messages):
            if m.get("role") != "user":
                continue
            if self._user_message_has_inline_image(m.get("content")):
                image_user_indices.append(i)

        def _strip_from_oldest(
            protected: set[int],
        ) -> None:
            for idx in image_user_indices:
                if self._inline_image_url_byte_estimate(messages) <= max_b:
                    return
                if idx in protected:
                    continue
                if not self._user_message_has_inline_image(
                    messages[idx].get("content")
                ):
                    continue
                self._strip_image_urls_from_user_message(messages[idx])

        if image_user_indices:
            protected_k = set(image_user_indices[-keep:])
            _strip_from_oldest(protected_k)

        if (
            self._inline_image_url_byte_estimate(messages) > max_b
            and len(image_user_indices) > 1
        ):
            protected_last = set(image_user_indices[-1:])
            _strip_from_oldest(protected_last)

    def _supports_inline_images(self) -> bool:
        """Best-effort capability check for `image_url` message blocks."""
        model = self.cfg.model.lower()
        base = self.cfg.base_url.lower()

        # DeepSeek V4 text models reject image_url blocks.
        if "deepseek" in base and ("deepseek-v4-pro" in model or "deepseek-v4-flash" in model):
            return False

        # Common multimodal model-name hints.
        vision_hints = (
            "vision", "vl", "4v", "gpt-4o", "gpt-4.1",
            "internvl", "llava", "minicpm-v", "doubao-vision",
        )
        if any(h in model for h in vision_hints):
            return True

        # Default optimistic for unknown providers.
        return True

    def _effective_task_question(self, question: str) -> str:
        """Append mode-specific appendix (e.g. ``--mode vlm_test``) to the YAML task."""
        text = question.strip()
        if getattr(self.cfg, "workflow_mode", "default") != "vlm_test":
            return text
        return text + "\n\n" + CLI_WORKFLOW_MODE_VLM_TEST_APPEND_ZH

    def _initial_messages(self, question: str,
                          inputs: dict[str, Any]) -> list[dict[str, Any]]:
        sys_text = self.system_prompt
        if not self.cfg.use_native_tools:
            sys_text += "\n\n" + FALLBACK_TOOL_PROTOCOL.replace(
                "{tool_list}", self.registry.describe_for_prompt()
            )
        messages: list[dict[str, Any]] = [self.client.system_message(sys_text)]

        text_context_lines: list[str] = [
            "## Task",
            question.strip(),
            "",
            "## Provided inputs",
        ]
        image_parts: list[dict[str, Any]] = []

        inline_images_ok = self._supports_inline_images()
        if not inline_images_ok:
            text_context_lines.append(
                "- [note] inline image blocks disabled for current model/provider; "
                "the agent should use file paths plus tools (`run_python`, "
                "`crop_image`, `annotate_image`) to inspect images."
            )

        for key, value in inputs.items():
            if isinstance(value, str) and Path(value).suffix.lower() in self._IMG_EXT:
                path = Path(value)
                if path.exists():
                    text_context_lines.append(f"- [image] {key} = {path}")
                    if inline_images_ok:
                        image_parts.append(self.client.image_part(
                            encode_image_data_url(path)
                        ))
                else:
                    text_context_lines.append(
                        f"- [image-missing] {key} = {path}"
                    )
            elif isinstance(value, str) and Path(value).exists():
                text_context_lines.append(f"- [file] {key} = {value}")
            else:
                text_context_lines.append(f"- {key}: {value}")

        text_context_lines.append("")
        text_context_lines.append(
            "## Path discipline (MANDATORY)\n"
            "- In `run_python`, read source files from `INPUT_PATHS[...]` whenever possible.\n"
            "- `WORKSPACE` and `PROJECT_ROOT` variables are available in `run_python`.\n"
            "- For tool output paths (crop/annotate/save), use relative paths like "
            "`progress/step_01.md` or `artifacts/result.png` (DO NOT prefix with `workspace/`)."
        )
        text_context_lines.append("")
        text_context_lines.append(
            "## Step2 gate (when Task requires red anchor boxes)\n"
            "If the **Task** text says to **skip Step2** and use only "
            "`match_green_tp_roi_to_board`, obey the Task (no red-box PNGs).\n"
            "Otherwise, for red-anchor workflows:\n"
            "- Anchor: IC with **clear silkscreen** on both images.\n"
            "- **Do not rotate/mirror/warp** images; native pixel coordinates only.\n"
            "- **Board:** prefer **`annotate_image`** `{bbox, color:red}` or `run_python` with "
            "**`INPUT_PATHS['front_board_photo']`** (never paste broken `F:\\...\\` strings — "
            "in Python `\"...\\test...\"` turns `\\t` into a tab and breaks paths).\n"
            "- **Locator:** red box on `debug/step01_locator_front_anchor.png`, save "
            "`debug/step02_locator_front_anchor.png`.\n"
            "- Do **not** call `run_step3_mapping` until both step02 files exist."
        )
        text_context_lines.append("")
        text_context_lines.append(
            "## Step3 execution constraints (MANDATORY when task starts from Step3)\n"
            "- Prefer **`match_green_tp_roi_to_board`** when the Task skips red anchors; "
            "omit `board_path` so `INPUT_PATHS['front_board_photo']` is used.\n"
            "- Otherwise prefer tool `run_step3_mapping` on Step2 red-box images.\n"
            "- Use HSV red ranges [0,100,100]-[10,255,255] and [170,100,100]-[180,255,255].\n"
            "- Use HSV green range [40,100,100]-[80,255,255].\n"
            "- Detect largest contour and use boundingRect/moments.\n"
            "- Use scalar mapping formula u,v -> px,py (no cv2.transform).\n"
            "- Do not clamp u/v by default; values outside [0,1] can be valid.\n"
            "- Emit debug/step03_mapping.json and debug/step03_prior_on_board.png.\n"
            "- Optional: prefer tool `match_green_tp_roi_to_board` (green ROI template) "
            "when red anchors are weak; same JSON schema."
        )
        text_context_lines.append("")
        wf_mode_run = getattr(self.cfg, "workflow_mode", "default")
        part_d_primary = (
            "- **Default Part D (`STANDARD_WORKFLOW`)** uses **`case12_step02_opencv_ic_align`** "
            "（两框 OpenCV）："
            "`run_build_step02_locator_graph` → `run_align_locator_graph_to_board_ic_bbox` "
            "(OpenCV **实物 IC 红框**) "
            "→ read **`board_roi_target_px_approx`** from **`case12_board_points_aligned.json`** → "
            "**`annotate_image`** on **`debug/step02_board_front_anchor.png`** → **`step08_final_tp.png`** "
            "+ **`step08_result.json`** + **`step03_mapping.json`** with **`mapping_method`** only, then **`finish`**.\n"
        )
        if wf_mode_run == "vlm_test":
            part_d_primary = (
                "- **This CLI run (`workflow_mode=vlm_test`)** uses **`case12_step02_vlm_ic_align`**: "
                "see **`vlm_test` appendix inside ## Task above** — `run_build_step02_locator_graph` → "
                "VLM → **`case12_board_largest_ic_bbox_vlm.json`** → "
                "**`run_align_locator_graph_to_board_ic_bbox_vlm`** → **`case12_board_points_aligned.json`** "
                "(**`source`=`vlm_ic_correspondence_isotropic_align`**) → **`step08_*` → `finish`**.\n"
            )
        text_context_lines.append(
            "## Step4–8 tool mandate (full-flow tasks)\n"
            "- Produce the **`debug/*.png` / `debug/*.json`** artifacts your Task requires; "
            "`progress/step_*.md` notes are **optional** (runtime does not gate `finish` on them).\n"
            + part_d_primary +
            "- **Legacy Part D** (`case10_dual_roi_layout`): **Step4** — **`read_text_file`** "
            "(step03_mapping.json), **`image_info`** (board), "
            "**`crop_image`** → `debug/step04_roi_crop.png`, **`view_image`** as needed.\n"
            "- **VLM Path C variant:** Step4 is **`annotate_image`** on "
            "`debug/step03_locator_roi.png` "
            "→ `debug/step04_locator_landmarks.png` (red landmark boxes), "
            "then board prior/mapping; "
            "board ROI crop stays `debug/step04_roi_crop.png` after mapping.\n"
            "- **Dual-ROI path Step5–7:** must call **`run_candidate_pipeline`** at least "
            "once with `roi_bbox`, "
            "`prior_board`, and board image path (see Task / SKILL).\n"
            "- **Step8:** must call **`annotate_image`** on the **full** board → "
            "`debug/step08_final_tp.png`, "
            "write consistent **`debug/step08_result.json`**, then **`finish`** with **`pixel`** [x,y] "
            "aligned "
            "to that board frame.\n"
            "- Describing a step without the matching tool call is incomplete."
        )
        text_context_lines.append("")
        text_context_lines.append(
            "Start by listing the input files, then work the problem step by step. "
            "Use tools for every non-trivial step (especially Step4–8 — each step needs real tool calls). "
            "Finish by calling the `finish` tool."
        )

        user_parts: list[dict[str, Any]] = [
            self.client.text_part("\n".join(text_context_lines))
        ]
        user_parts.extend(image_parts)
        messages.append(self.client.user_message(user_parts))
        return messages

    def _validate_finish_answer(self, answer: Any) -> list[str]:
        """Require a board pixel [x,y] in finish payload; align with step08_result when present."""
        errors: list[str] = []
        if answer is None:
            return ["finish tool requires an `answer` object"]
        if not isinstance(answer, dict):
            if isinstance(answer, str):
                try:
                    answer = json.loads(answer)
                except json.JSONDecodeError:
                    return ["finish answer must be a JSON object"]
            else:
                return ["finish answer must be a JSON object"]
        if answer.get("needs_user_help") is True:
            return errors

        pixel = answer.get("pixel")
        if not (isinstance(pixel, list) and len(pixel) == 2):
            errors.append(
                "finish.answer.pixel must be [x, y] on the full board image "
                "(same coordinate frame as debug/step08_final_tp.png) unless needs_user_help=true."
            )
        else:
            try:
                fx, fy = float(pixel[0]), float(pixel[1])
                if not (math.isfinite(fx) and math.isfinite(fy)):
                    errors.append("finish.answer.pixel values must be finite numbers")
            except Exception:
                errors.append("finish.answer.pixel must be numeric [x, y]")

        ws = self.cfg.workspace_dir.resolve()
        step08_res = next(
            (
                p
                for p in (
                    ws / "debug" / "step08_result.json",
                    ws / "workspace" / "debug" / "step08_result.json",
                )
                if p.exists()
            ),
            None,
        )
        if (
            step08_res is not None
            and isinstance(pixel, list)
            and len(pixel) == 2
            and not errors
        ):
            try:
                robj = json.loads(step08_res.read_text(encoding="utf-8"))
                sp = robj.get("pixel")
                if isinstance(sp, list) and len(sp) == 2:
                    if abs(float(sp[0]) - float(pixel[0])) > 1.0 or abs(
                        float(sp[1]) - float(pixel[1])
                    ) > 1.0:
                        errors.append(
                            "finish.answer.pixel must match debug/step08_result.json pixel (±1px)."
                        )
            except Exception as e:  # noqa: BLE001
                errors.append(f"Could not cross-check finish pixel with step08_result.json: {e}")
        return errors

    def _part_b_assembly_stepb3_qc_errors(self, ws: Path) -> list[str]:
        """Shared Part B StepB3 gates (assembly largest IC JSON + view gate + mtime order)."""
        errors: list[str] = []
        asm_ic_path = self._path_first_existing(
            [
                ws / "debug" / "case10_assembly_largest_ic.json",
                ws / "workspace" / "debug" / "case10_assembly_largest_ic.json",
            ],
        )
        if asm_ic_path is not None:
            try:
                asm_ic_obj = json.loads(asm_ic_path.read_text(encoding="utf-8"))
                qc_b = asm_ic_obj.get("part_b_stepb3_qc")
                if not isinstance(qc_b, dict):
                    errors.append(
                        "case10_assembly_largest_ic.json must include object "
                        "part_b_stepb3_qc (Part B StepB3 QC gate); see STANDARD_WORKFLOW StepB3."
                    )
                else:
                    fv = qc_b.get("final_verdict")
                    if fv not in ("QC_PASS", "QC_PASS_WITH_CAVEATS"):
                        errors.append(
                            "part_b_stepb3_qc.final_verdict must be QC_PASS or "
                            "QC_PASS_WITH_CAVEATS after StepB3 (Revise rounds must finish before "
                            "writing this JSON)."
                        )
                    if qc_b.get("viewed_largest_ic_box_png_before_final_json") is not True:
                        errors.append(
                            "part_b_stepb3_qc.viewed_largest_ic_box_png_before_final_json "
                            "must be true: call view_image(debug/case10_assembly_largest_ic_box.png), "
                            "run the StepB3 checklist, declare QC_PASS or QC_REVISE, and only "
                            "then write case10_assembly_largest_ic.json."
                        )
                    if qc_b.get(
                        "final_annotate_overwrote_png_immediately_before_json"
                    ) is not True:
                        errors.append(
                            "part_b_stepb3_qc.final_annotate_overwrote_png_immediately_before_json "
                            "must be true: after the final bbox is fixed (including after QC_REVISE), "
                            "you must call annotate_image to overwrite "
                            "debug/case10_assembly_largest_ic_box.png, then write the JSON with the "
                            "same bbox — do not update JSON without re-exporting the PNG."
                        )
                    note = qc_b.get("whole_page_largest_package_checked_zh")
                    if not isinstance(note, str) or len(note.strip()) < 6:
                        errors.append(
                            "part_b_stepb3_qc.whole_page_largest_package_checked_zh must be a "
                            "short Chinese note that the full-page largest package was verified "
                            "(not a smaller neighbor IC)."
                        )
                    qru = qc_b.get("qc_rounds_used")
                    try:
                        qru_n = int(qru)  # JSON may ship small ints only
                    except (TypeError, ValueError):
                        qru_n = -1
                    if isinstance(qru, bool):
                        qru_n = -1
                    if qru_n < 2:
                        errors.append(
                            "part_b_stepb3_qc.qc_rounds_used must be an integer >= 2 for "
                            "Part B (assembly drawing): you must run at least one QC_REVISE "
                            "cycle (revise case10_assembly_vlm_hints.json → StepB2 run_python → "
                            "re-annotate case10_assembly_largest_ic_box.png → view_image) before "
                            "final QC_PASS. Part A (board photo) has no such minimum."
                        )
            except json.JSONDecodeError as e:
                errors.append(f"Invalid JSON in case10_assembly_largest_ic.json: {e}")
            except OSError as e:
                errors.append(f"Failed to read case10_assembly_largest_ic.json: {e}")

            box_asm = self._path_first_existing(
                [
                    ws / "debug" / "case10_assembly_largest_ic_box.png",
                    ws / "workspace" / "debug" / "case10_assembly_largest_ic_box.png",
                ],
            )
            if box_asm is not None:
                gate_p = self._path_first_existing(
                    [
                        ws / "debug" / "case10_stepb3_viewed_largest_ic_box.json",
                        ws / "workspace" / "debug" / "case10_stepb3_viewed_largest_ic_box.json",
                    ],
                )
                if gate_p is None:
                    errors.append(
                        "Part B StepB3 (tool-enforced): missing "
                        "`debug/case10_stepb3_viewed_largest_ic_box.json`. "
                        "After `annotate_image` → `debug/case10_assembly_largest_ic_box.png`, "
                        "you MUST call `view_image` on that exact PNG (runtime records the gate), "
                        "then save `case10_assembly_largest_ic.json`. "
                        "QC_REVISE: re-annotate → re-view → then JSON."
                    )
                else:
                    try:
                        gobj = json.loads(gate_p.read_text(encoding="utf-8"))
                        gmt = gobj.get("png_mtime")
                        bmt = box_asm.stat().st_mtime
                        if gmt is None or not math.isclose(
                            float(gmt), float(bmt), rel_tol=0, abs_tol=1e-3
                        ):
                            errors.append(
                                "Part B StepB3 (tool-enforced): "
                                "`case10_stepb3_viewed_largest_ic_box.json` is stale — "
                                "it does not match the current on-disk "
                                "`case10_assembly_largest_ic_box.png`. "
                                "Call `view_image(debug/case10_assembly_largest_ic_box.png)` "
                                "again after the latest `annotate_image` (required after QC_REVISE)."
                            )
                    except (json.JSONDecodeError, OSError, TypeError, ValueError) as e:
                        errors.append(
                            "Part B StepB3 (tool-enforced): invalid view gate "
                            f"`debug/case10_stepb3_viewed_largest_ic_box.json`: {e}"
                        )
                    if asm_ic_path is not None:
                        try:
                            if asm_ic_path.stat().st_mtime + 0.001 < gate_p.stat().st_mtime:
                                errors.append(
                                    "Part B StepB3 (tool-enforced): "
                                    "`case10_assembly_largest_ic.json` must be saved AFTER "
                                    "`view_image` on the final red-box PNG (gate newer than JSON)."
                                )
                        except OSError:
                            pass
        return errors

    @staticmethod
    def _path_first_existing(paths: list[Path]) -> Path | None:
        for q in paths:
            if q.exists():
                return q
        return None

    @staticmethod
    def _infer_case12_mapping_method_from_workspace(ws: Path) -> str | None:
        """If ``step03_mapping.json`` lacks ``mapping_method``, infer from aligned output."""
        for rel in (
            ("debug", "case12_board_points_aligned.json"),
            ("workspace", "debug", "case12_board_points_aligned.json"),
        ):
            p = ws.joinpath(*rel)
            if not p.is_file():
                continue
            try:
                obj = json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            src = obj.get("source")
            if src == "opencv_ic_bbox_isotropic_align":
                return "case12_step02_opencv_ic_align"
            if src == "vlm_ic_correspondence_isotropic_align":
                return "case12_step02_vlm_ic_align"
        return None

    def _validate_skill_contract_case12_opencv_ic_align(self, ws: Path) -> list[str]:
        """Part D default: case12 locator graph + OpenCV red IC boxes; TP from aligned JSON."""
        errors: list[str] = []
        case10_prefix = [
            "debug/case10_signal_to_tp.json",
            "debug/case10_target_tp_pdf_search.json",
            "debug/case10_assembly_drawing.png",
            "debug/case10_target_tp_work_roi.png",
            "debug/case10_assembly_drawing_tp_marked.png",
            "debug/case10_board_landscape.png",
            "debug/case10_assembly_largest_ic_box.png",
            "debug/case10_assembly_largest_ic.json",
            "debug/case10_largest_ic_box.png",
            "debug/case10_largest_ic.json",
            "debug/step02_locator_front_anchor.png",
            "debug/step02_board_front_anchor.png",
            "debug/board_tp_marked.png",
        ]
        case12_tail = [
            "debug/step03_mapping.json",
            "debug/case12_step02_locator_graph.json",
            "debug/case12_step02_locator_graph.png",
            "debug/case12_board_points_aligned.json",
            "debug/case12_board_approx_overlay_opencv.png",
            "debug/step08_final_tp.png",
            "debug/step08_result.json",
        ]
        for rel in case10_prefix + case12_tail:
            p1 = ws / rel
            p2 = ws / "workspace" / rel
            if not (p1.exists() or p2.exists()):
                errors.append(f"Missing required debug artifact: {rel}")

        aligned_p = self._path_first_existing(
            [
                ws / "debug" / "case12_board_points_aligned.json",
                ws / "workspace" / "debug" / "case12_board_points_aligned.json",
            ],
        )
        if aligned_p is not None:
            try:
                al = json.loads(aligned_p.read_text(encoding="utf-8"))
                if al.get("source") != "opencv_ic_bbox_isotropic_align":
                    errors.append(
                        "case12_board_points_aligned.json source must be "
                        "`opencv_ic_bbox_isotropic_align` for mapping_method "
                        "`case12_step02_opencv_ic_align`."
                    )
                tp = al.get("board_roi_target_px_approx")
                if not (isinstance(tp, list) and len(tp) == 2):
                    errors.append(
                        "case12_board_points_aligned.json missing board_roi_target_px_approx [x,y]."
                    )
                else:
                    step08r = self._path_first_existing(
                        [
                            ws / "debug" / "step08_result.json",
                            ws / "workspace" / "debug" / "step08_result.json",
                        ],
                    )
                    if step08r is not None:
                        try:
                            so = json.loads(step08r.read_text(encoding="utf-8"))
                            sp = so.get("pixel")
                            if isinstance(sp, list) and len(sp) == 2:
                                if abs(float(sp[0]) - float(tp[0])) > 1.0 or abs(
                                    float(sp[1]) - float(tp[1])
                                ) > 1.0:
                                    errors.append(
                                        "step08_result.json pixel must match "
                                        "case12_board_points_aligned.json "
                                        "board_roi_target_px_approx (±1px)."
                                    )
                        except (json.JSONDecodeError, OSError, TypeError, ValueError) as e:
                            errors.append(f"Invalid step08_result.json: {e}")
            except (json.JSONDecodeError, OSError) as e:
                errors.append(f"Invalid case12_board_points_aligned.json: {e}")

        s02b = self._path_first_existing(
            [
                ws / "debug" / "step02_board_front_anchor.png",
                ws / "workspace" / "debug" / "step02_board_front_anchor.png",
            ],
        )
        step08 = self._path_first_existing(
            [
                ws / "debug" / "step08_final_tp.png",
                ws / "workspace" / "debug" / "step08_final_tp.png",
            ],
        )
        if s02b is not None and step08 is not None:
            try:
                from PIL import Image

                with Image.open(s02b) as im_b:
                    wb = im_b.size
                with Image.open(step08) as im8:
                    w8 = im8.size
                if wb != w8:
                    errors.append(
                        "step08_final_tp.png must match step02_board_front_anchor.png "
                        "width×height (full board frame)."
                    )
            except Exception as e:  # noqa: BLE001
                errors.append(f"Failed to validate step08 vs board size: {e}")

        errors.extend(self._part_b_assembly_stepb3_qc_errors(ws))
        return errors

    def _validate_skill_contract_case12_vlm_ic_align(self, ws: Path) -> list[str]:
        """Part D CLI ``vlm_test``: skip Part A board IC PNG; locator graph + VLM IC JSON → aligned."""
        errors: list[str] = []
        prefix = [
            "debug/case10_signal_to_tp.json",
            "debug/case10_target_tp_pdf_search.json",
            "debug/case10_assembly_drawing.png",
            "debug/case10_target_tp_work_roi.png",
            "debug/case10_assembly_drawing_tp_marked.png",
            "debug/case10_board_landscape.png",
            "debug/case10_assembly_largest_ic_box.png",
            "debug/case10_assembly_largest_ic.json",
            "debug/step02_locator_front_anchor.png",
            "debug/step02_board_front_anchor.png",
        ]
        tail = [
            "debug/step03_mapping.json",
            "debug/case12_step02_locator_graph.json",
            "debug/case12_step02_locator_graph.png",
            "debug/case12_board_largest_ic_bbox_vlm.json",
            "debug/case12_board_points_aligned.json",
            "debug/case12_board_approx_overlay_opencv.png",
            "debug/step08_final_tp.png",
            "debug/step08_result.json",
        ]
        for rel in prefix + tail:
            p1 = ws / rel
            p2 = ws / "workspace" / rel
            if not (p1.exists() or p2.exists()):
                errors.append(f"Missing required debug artifact: {rel}")

        map_p = self._path_first_existing(
            [
                ws / "debug" / "step03_mapping.json",
                ws / "workspace" / "debug" / "step03_mapping.json",
            ]
        )
        if map_p is not None:
            try:
                mm = json.loads(map_p.read_text(encoding="utf-8")).get(
                    "mapping_method"
                )
                if mm != "case12_step02_vlm_ic_align":
                    errors.append(
                        "step03_mapping.json mapping_method must be "
                        "`case12_step02_vlm_ic_align` when using this validator."
                    )
            except (json.JSONDecodeError, OSError, TypeError) as e:
                errors.append(f"Invalid step03_mapping.json: {e}")

        aligned_p = self._path_first_existing(
            [
                ws / "debug" / "case12_board_points_aligned.json",
                ws / "workspace" / "debug" / "case12_board_points_aligned.json",
            ]
        )
        if aligned_p is not None:
            try:
                al = json.loads(aligned_p.read_text(encoding="utf-8"))
                if al.get("source") != "vlm_ic_correspondence_isotropic_align":
                    errors.append(
                        "case12_board_points_aligned.json source must be "
                        "`vlm_ic_correspondence_isotropic_align` for "
                        "`case12_step02_vlm_ic_align`."
                    )
                tp = al.get("board_roi_target_px_approx")
                if not (isinstance(tp, list) and len(tp) == 2):
                    errors.append(
                        "case12_board_points_aligned.json missing "
                        "board_roi_target_px_approx [x,y]."
                    )
                else:
                    step08r = self._path_first_existing(
                        [
                            ws / "debug" / "step08_result.json",
                            ws / "workspace" / "debug" / "step08_result.json",
                        ]
                    )
                    if step08r is not None:
                        try:
                            so = json.loads(step08r.read_text(encoding="utf-8"))
                            sp = so.get("pixel")
                            if isinstance(sp, list) and len(sp) == 2:
                                if abs(float(sp[0]) - float(tp[0])) > 1.0 or abs(
                                    float(sp[1]) - float(tp[1])
                                ) > 1.0:
                                    errors.append(
                                        "step08_result.json pixel must match "
                                        "case12_board_points_aligned.json "
                                        "board_roi_target_px_approx (±1px)."
                                    )
                        except (
                            json.JSONDecodeError,
                            OSError,
                            TypeError,
                            ValueError,
                        ) as e:
                            errors.append(f"Invalid step08_result.json: {e}")
            except (json.JSONDecodeError, OSError) as e:
                errors.append(f"Invalid case12_board_points_aligned.json: {e}")

        s02b = self._path_first_existing(
            [
                ws / "debug" / "step02_board_front_anchor.png",
                ws / "workspace" / "debug" / "step02_board_front_anchor.png",
            ]
        )
        step08 = self._path_first_existing(
            [
                ws / "debug" / "step08_final_tp.png",
                ws / "workspace" / "debug" / "step08_final_tp.png",
            ]
        )
        if s02b is not None and step08 is not None:
            try:
                from PIL import Image

                with Image.open(s02b) as im_b:
                    wb = im_b.size
                with Image.open(step08) as im8:
                    w8 = im8.size
                if wb != w8:
                    errors.append(
                        "step08_final_tp.png must match step02_board_front_anchor.png "
                        "width×height (full board frame)."
                    )
            except Exception as e:  # noqa: BLE001
                errors.append(f"Failed to validate step08 vs board size: {e}")

        errors.extend(self._part_b_assembly_stepb3_qc_errors(ws))
        return errors

    def _validate_skill_contract(self) -> list[str]:
        """Check required debug artifacts before accepting finish() (no progress/*.md gate)."""
        errors: list[str] = []
        ws = self.cfg.workspace_dir.resolve()

        # Step 3 mapping evidence must be machine-generated.
        mapping_json_candidates = [
            ws / "debug" / "step03_mapping.json",
            ws / "workspace" / "debug" / "step03_mapping.json",
        ]
        mapping_json = next((p for p in mapping_json_candidates if p.exists()), None)
        mapping_method_early: str | None = None
        if mapping_json is not None:
            try:
                mapping_method_early = json.loads(
                    mapping_json.read_text(encoding="utf-8")
                ).get("mapping_method")
            except Exception:
                mapping_method_early = None
        if isinstance(mapping_method_early, str):
            mapping_method_early = mapping_method_early.strip() or None
        # When the model writes step03_mapping with only notes / omitting mapping_method,
        # fall back to aligned JSON produced by case12_graph (avoid legacy Step5–8 gates).
        if mapping_method_early is None:
            inferred = self._infer_case12_mapping_method_from_workspace(ws)
            if inferred:
                mapping_method_early = inferred
        if mapping_method_early == "case12_step02_opencv_ic_align":
            errors.extend(self._validate_skill_contract_case12_opencv_ic_align(ws))
            return errors
        if mapping_method_early == "case12_step02_vlm_ic_align":
            errors.extend(self._validate_skill_contract_case12_vlm_ic_align(ws))
            return errors

        if mapping_json is None:
            errors.append("Missing required mapping artifact: debug/step03_mapping.json")
        else:
            try:
                mapping_obj = json.loads(mapping_json.read_text(encoding="utf-8"))
                required_keys = {
                    "locator_box", "board_box", "tp_locator_center",
                    "u", "v", "tp_prior_board",
                }
                missing_keys = sorted(k for k in required_keys if k not in mapping_obj)
                if missing_keys:
                    errors.append(
                        "step03_mapping.json missing keys: " + ", ".join(missing_keys)
                    )
                else:
                    def _is_num(v: Any) -> bool:
                        return isinstance(v, (int, float)) and math.isfinite(float(v))

                    def _is_point(v: Any, n: int) -> bool:
                        return (
                            isinstance(v, list)
                            and len(v) == n
                            and all(_is_num(x) for x in v)
                        )

                    locator_box = mapping_obj.get("locator_box")
                    board_box = mapping_obj.get("board_box")
                    tp_locator_center = mapping_obj.get("tp_locator_center")
                    tp_prior_board = mapping_obj.get("tp_prior_board")
                    u = mapping_obj.get("u")
                    v = mapping_obj.get("v")

                    if not _is_point(locator_box, 4):
                        errors.append("step03_mapping.json locator_box must be 4 numeric values.")
                    if not _is_point(board_box, 4):
                        errors.append("step03_mapping.json board_box must be 4 numeric values.")
                    if _is_point(locator_box, 4) and not (locator_box[2] > locator_box[0] and locator_box[3] > locator_box[1]):
                        errors.append("step03_mapping.json locator_box must satisfy x2>x1 and y2>y1.")
                    if _is_point(board_box, 4) and not (board_box[2] > board_box[0] and board_box[3] > board_box[1]):
                        errors.append("step03_mapping.json board_box must satisfy x2>x1 and y2>y1.")
                    if not _is_point(tp_locator_center, 2):
                        errors.append("step03_mapping.json tp_locator_center must be 2 numeric values.")
                    if not _is_point(tp_prior_board, 2):
                        errors.append("step03_mapping.json tp_prior_board must be 2 numeric values.")
                    if not _is_num(u) or not _is_num(v):
                        errors.append("step03_mapping.json u and v must be finite numbers.")
            except Exception as e:  # noqa: BLE001
                errors.append(f"Failed to parse step03_mapping.json: {e}")

        mapping_method_tag: str | None = None
        if mapping_json is not None:
            try:
                mapping_method_tag = json.loads(
                    mapping_json.read_text(encoding="utf-8")
                ).get("mapping_method")
            except Exception:
                mapping_method_tag = None
        mapping_method_vlm = mapping_method_tag in (
            "vlm_neighborhood_layout_match",
            "case10_dual_roi_layout",
        )
        mapping_method_case10_layout = mapping_method_tag == "case10_dual_roi_layout"
        mapping_method_case12_layout = mapping_method_tag == "case12_step02_anchor_match"

        def _first_existing(paths: list[Path]) -> Path | None:
            for q in paths:
                if q.exists():
                    return q
            return None

        s01 = _first_existing(
            [
                ws / "debug" / "step01_locator_front_anchor.png",
                ws / "workspace" / "debug" / "step01_locator_front_anchor.png",
            ]
        )
        s02_loc = _first_existing(
            [
                ws / "debug" / "step02_locator_front_anchor.png",
                ws / "workspace" / "debug" / "step02_locator_front_anchor.png",
            ]
        )
        s02_board = _first_existing(
            [
                ws / "debug" / "step02_board_front_anchor.png",
                ws / "workspace" / "debug" / "step02_board_front_anchor.png",
            ]
        )
        skip_step2_gate = mapping_method_tag in (
            "green_roi_template_match",
            "vlm_neighborhood_layout_match",
            "case10_dual_roi_layout",
        )

        if s01 is not None and mapping_json is not None and not skip_step2_gate:
            if s02_loc is None or s02_board is None:
                errors.append(
                    "Full-flow Step2 required: save BOTH debug/step02_locator_front_anchor.png "
                    "and debug/step02_board_front_anchor.png (red boxes) before Step3 / prior."
                )
            else:
                t01 = s01.stat().st_mtime
                t2 = min(s02_loc.stat().st_mtime, s02_board.stat().st_mtime)
                t3 = mapping_json.stat().st_mtime
                if t2 + 1e-3 < t01:
                    errors.append(
                        "step02_*_anchor.png must be generated after "
                        "step01_locator_front_anchor.png."
                    )
                if t3 + 0.5 < t2:
                    errors.append(
                        "step03_mapping.json must be newer than both step02 anchor PNGs "
                        "(run Step3 only after saving the two red-box images)."
                    )

        # Required debug artifacts (emphasized PNG/JSON only; no progress/*.md).
        required_debug: list[str] = [
            "debug/step03_mapping.json",
            "debug/step03_prior_on_board.png",
            "debug/step04_roi_crop.png",
            "debug/step05_candidates.json",
            "debug/step57_candidates_scored.png",
            "debug/step08_final_tp.png",
            "debug/step08_result.json",
        ]
        if mapping_method_vlm:
            required_debug.insert(
                3,
                (
                    "debug/step04_locator_roi_crop.png"
                    if mapping_method_case10_layout
                    else "debug/step04_locator_landmarks.png"
                ),
            )

        if mapping_method_case10_layout:
            case10_prefix = [
                "debug/case10_signal_to_tp.json",
                "debug/case10_target_tp_pdf_search.json",
                "debug/case10_assembly_drawing.png",
                "debug/case10_target_tp_work_roi.png",
                "debug/case10_assembly_drawing_tp_marked.png",
                "debug/case10_board_landscape.png",
                "debug/case10_assembly_largest_ic_box.png",
                "debug/case10_assembly_largest_ic.json",
                "debug/case10_largest_ic_box.png",
                "debug/case10_largest_ic.json",
                "debug/step02_locator_front_anchor.png",
                "debug/step02_board_front_anchor.png",
                "debug/board_tp_marked.png",
            ]
            tail = [x for x in required_debug if x not in case10_prefix]
            required_debug = case10_prefix + tail

            path_a = _first_existing(
                [
                    ws / "debug" / "case10_tp_dual_roi_direct_vlm.json",
                    ws / "workspace" / "debug" / "case10_tp_dual_roi_direct_vlm.json",
                ]
            )
            if path_a is not None:
                anchor = "debug/step03_mapping.json"
                insert_at = required_debug.index(anchor)
                for name in (
                    "debug/case10_dual_roi_locator_refs.json",
                    "debug/step04_locator_roi_refs.png",
                    "debug/case10_tp_dual_roi_direct_vlm.json",
                    "debug/step04_dual_roi_approx_only.png",
                    "debug/case10_tp_dual_roi_direct_refined.json",
                    "debug/step04_dual_roi_direct_snap.png",
                ):
                    required_debug.insert(insert_at, name)
                    insert_at += 1
            else:
                anchor = "debug/step03_mapping.json"
                insert_at = required_debug.index(anchor)
                for name in (
                    "debug/case10_tp_roi_layout_vlm.json",
                    "debug/case10_tp_roi_layout_hints.json",
                ):
                    required_debug.insert(insert_at, name)
                    insert_at += 1

        if mapping_method_case12_layout:
            case12_prefix = [
                "debug/step02_locator_front_anchor.png",
                "debug/step02_board_front_anchor.png",
                "debug/case12_step02_locator_graph.json",
                "debug/case12_step02_locator_graph.png",
                "debug/case12_board_largest_ic_bbox_vlm.json",
                "debug/case12_board_points_aligned.json",
                "debug/case12_board_approx_overlay_opencv.png",
                "debug/case12_board_points_vlm_refine.json",
                "debug/case12_board_points_refined.json",
                "debug/case12_board_approx_overlay.png",
            ]
            aligned_case12 = _first_existing(
                [
                    ws / "debug" / "case12_board_points_aligned.json",
                    ws / "workspace" / "debug" / "case12_board_points_aligned.json",
                ]
            )
            if aligned_case12 is not None and aligned_case12.is_file():
                try:
                    _al = json.loads(aligned_case12.read_text(encoding="utf-8"))
                    if _al.get("source") == "opencv_ic_bbox_isotropic_align":
                        case12_prefix = [
                            x
                            for x in case12_prefix
                            if x != "debug/case12_board_largest_ic_bbox_vlm.json"
                        ]
                except Exception:
                    pass
            tail = [x for x in required_debug if x not in case12_prefix]
            required_debug = case12_prefix + tail

        for rel in required_debug:
            p1 = ws / rel
            p2 = ws / "workspace" / rel
            if not (p1.exists() or p2.exists()):
                errors.append(f"Missing required debug artifact: {rel}")

        if mapping_method_case10_layout:
            errors.extend(self._part_b_assembly_stepb3_qc_errors(ws))

        # Step8 final image must be on full board, not ROI-sized crop.
        step08 = next((p for p in [ws / "debug" / "step08_final_tp.png",
                                   ws / "workspace" / "debug" / "step08_final_tp.png"]
                       if p.exists()), None)
        step04_roi = next((p for p in [ws / "debug" / "step04_roi_crop.png",
                                       ws / "workspace" / "debug" / "step04_roi_crop.png"]
                           if p.exists()), None)
        step03_prior = next((p for p in [ws / "debug" / "step03_prior_on_board.png",
                                         ws / "workspace" / "debug" / "step03_prior_on_board.png"]
                             if p.exists()), None)
        if step08 is not None:
            try:
                from PIL import Image
                with Image.open(step08) as im8:
                    size8 = im8.size
                if step04_roi is not None:
                    with Image.open(step04_roi) as im4:
                        size4 = im4.size
                    if size8 == size4:
                        errors.append(
                            "step08_final_tp.png appears ROI-sized; final annotation must be on full board image."
                        )
                if step03_prior is not None:
                    with Image.open(step03_prior) as im3:
                        size3 = im3.size
                    if size8 != size3:
                        errors.append(
                            "step08_final_tp.png size mismatch with board-scale prior image "
                            "(expected same size as step03_prior_on_board.png)."
                        )
            except Exception as e:  # noqa: BLE001
                errors.append(f"Failed to validate step08_final_tp.png size: {e}")

        step03_mapping = next((p for p in [ws / "debug" / "step03_mapping.json",
                                           ws / "workspace" / "debug" / "step03_mapping.json"]
                               if p.exists()), None)
        step04_roi = next((p for p in [ws / "debug" / "step04_roi_crop.png",
                                       ws / "workspace" / "debug" / "step04_roi_crop.png"]
                           if p.exists()), None)
        step05_candidates_for_time = next((p for p in [ws / "debug" / "step05_candidates.json",
                                                       ws / "workspace" / "debug" / "step05_candidates.json"]
                                           if p.exists()), None)
        try:
            step04_lm = _first_existing(
                [
                    ws / "debug" / "step04_locator_landmarks.png",
                    ws / "workspace" / "debug" / "step04_locator_landmarks.png",
                ]
            )
            step04_loc_roi_crop = _first_existing(
                [
                    ws / "debug" / "step04_locator_roi_crop.png",
                    ws / "workspace" / "debug" / "step04_locator_roi_crop.png",
                ]
            )
            step03_loc_roi = _first_existing(
                [
                    ws / "debug" / "step03_locator_roi.png",
                    ws / "workspace" / "debug" / "step03_locator_roi.png",
                ]
            )
            if (
                mapping_method_vlm
                and not mapping_method_case10_layout
                and step04_lm is not None
                and step03_loc_roi is not None
            ):
                if step04_lm.stat().st_mtime + 1e-3 < step03_loc_roi.stat().st_mtime:
                    errors.append(
                        "step04_locator_landmarks.png must be newer than "
                        "step03_locator_roi.png (run Step4 after Step3A)."
                    )
            if (
                mapping_method_vlm
                and not mapping_method_case10_layout
                and step03_mapping is not None
                and step04_lm is not None
            ):
                if step03_mapping.stat().st_mtime + 1e-3 < step04_lm.stat().st_mtime:
                    errors.append(
                        "step03_mapping.json is older than step04_locator_landmarks.png. "
                        "Write prior/mapping only after Step4 locator landmarks."
                    )
            if (
                mapping_method_case10_layout
                and step03_mapping is not None
                and step04_loc_roi_crop is not None
            ):
                if step04_loc_roi_crop.stat().st_mtime + 1e-3 < step03_mapping.stat().st_mtime:
                    errors.append(
                        "step04_locator_roi_crop.png must be newer than step03_mapping.json "
                        "(take locator ROI after Step3 mapping / prior is fixed)."
                    )
            if step03_mapping is not None and step04_roi is not None:
                t3 = step03_mapping.stat().st_mtime
                t4 = step04_roi.stat().st_mtime
                if t4 + 1e-3 < t3:
                    errors.append(
                        "step04_roi_crop.png is older than step03_mapping.json. "
                        "Regenerate the board ROI crop after Step3 mapping in this run."
                    )
            if step04_roi is not None and step05_candidates_for_time is not None:
                t4 = step04_roi.stat().st_mtime
                t5 = step05_candidates_for_time.stat().st_mtime
                if t5 + 1e-3 < t4:
                    errors.append(
                        "step05_candidates.json is older than step04_roi_crop.png. "
                        "Step5/6/7 must run after Step4 ROI generation."
                    )
        except Exception as e:  # noqa: BLE001
            errors.append(f"Failed to validate Step3->Step4->Step5 artifact timeline: {e}")

        # Scheme C: Step5 candidates must provide stable global coordinates
        # + adaptive visualization radius, and Step8 should consume them.
        step05_candidates = next((p for p in [ws / "debug" / "step05_candidates.json",
                                              ws / "workspace" / "debug" / "step05_candidates.json"]
                                  if p.exists()), None)
        if step05_candidates is not None:
            try:
                cand_obj = json.loads(step05_candidates.read_text(encoding="utf-8"))
                if isinstance(cand_obj, dict):
                    cand_list = cand_obj.get("candidates", [])
                elif isinstance(cand_obj, list):
                    cand_list = cand_obj
                else:
                    cand_list = []
                valid_candidates: list[dict[str, Any]] = []
                for c in cand_list:
                    if not isinstance(c, dict):
                        continue
                    if not all(k in c for k in ("id", "gx", "gy", "r_vis")):
                        continue
                    try:
                        gx = float(c["gx"])
                        gy = float(c["gy"])
                        rv = float(c["r_vis"])
                    except Exception:
                        continue
                    if not (math.isfinite(gx) and math.isfinite(gy) and math.isfinite(rv) and rv > 0):
                        continue
                    valid_candidates.append(c)

                if not valid_candidates:
                    errors.append(
                        "step05_candidates.json must include candidate schema fields "
                        "`id`, `gx`, `gy`, `r_vis` (finite values) so Step8 can reuse "
                        "global coordinates and adaptive marker radius."
                    )

                step08_result = next((p for p in [ws / "debug" / "step08_result.json",
                                                  ws / "workspace" / "debug" / "step08_result.json"]
                                      if p.exists()), None)
                if step08_result is None:
                    errors.append(
                        "Missing debug/step08_result.json with selected candidate id and marker radius."
                    )
                elif valid_candidates:
                    try:
                        result_obj = json.loads(step08_result.read_text(encoding="utf-8"))
                        selected_id = result_obj.get("selected_id")
                        marker_radius = result_obj.get("marker_radius")
                        pixel = result_obj.get("pixel")

                        if selected_id is None:
                            errors.append(
                                "step08_result.json missing `selected_id` "
                                "(must reference a candidate id from step05_candidates.json)."
                            )
                            selected_candidate = None
                        else:
                            selected_candidate = next(
                                (c for c in valid_candidates if str(c.get("id")) == str(selected_id)),
                                None,
                            )
                            if selected_candidate is None:
                                errors.append(
                                    "step08_result.json selected_id does not exist in Step5 candidates."
                                )

                        if marker_radius is None:
                            errors.append(
                                "step08_result.json missing `marker_radius` "
                                "(must reuse candidate `r_vis`)."
                            )
                        elif selected_candidate is not None:
                            try:
                                mr = float(marker_radius)
                                rv = float(selected_candidate["r_vis"])
                                if not math.isfinite(mr):
                                    errors.append("step08_result.json marker_radius must be finite.")
                                elif abs(mr - rv) > 1.0:
                                    errors.append(
                                        "step08_result.json marker_radius must match selected candidate "
                                        "`r_vis` (±1px tolerance)."
                                    )
                            except Exception:
                                errors.append("step08_result.json marker_radius must be numeric.")

                        if not (isinstance(pixel, list) and len(pixel) == 2):
                            errors.append(
                                "step08_result.json missing/invalid `pixel`; expected [x, y]."
                            )
                        elif selected_candidate is not None:
                            try:
                                px, py = float(pixel[0]), float(pixel[1])
                                gx = float(selected_candidate["gx"])
                                gy = float(selected_candidate["gy"])
                                if abs(px - gx) > 1.0 or abs(py - gy) > 1.0:
                                    errors.append(
                                        "step08_result.json pixel must match selected candidate "
                                        "global coordinate (`gx`,`gy`) (±1px tolerance)."
                                    )
                            except Exception:
                                errors.append("step08_result.json pixel must contain numeric values.")

                        # Verify Step8 rendered marker radius on final image
                        # is consistent with the selected candidate r_vis.
                        if (
                            step08 is not None
                            and selected_candidate is not None
                            and isinstance(pixel, list)
                            and len(pixel) == 2
                            and marker_radius is not None
                        ):
                            try:
                                from PIL import Image

                                with Image.open(step08) as im8:
                                    img = im8.convert("RGB")
                                    w, h = img.size
                                    pix = img.load()

                                px = int(round(float(pixel[0])))
                                py = int(round(float(pixel[1])))
                                mr = float(marker_radius)
                                if not math.isfinite(mr) or mr <= 0:
                                    raise ValueError("invalid marker_radius for image check")

                                # Search around the selected TP for red ring pixels.
                                # Exclude near-axis pixels to reduce cross-hair influence.
                                search_r = int(max(20, min(120, round(mr * 8 + 16))))
                                x1 = max(0, px - search_r)
                                y1 = max(0, py - search_r)
                                x2 = min(w - 1, px + search_r)
                                y2 = min(h - 1, py + search_r)

                                dists: list[float] = []
                                for yy in range(y1, y2 + 1):
                                    for xx in range(x1, x2 + 1):
                                        dx = xx - px
                                        dy = yy - py
                                        if abs(dx) <= 2 or abs(dy) <= 2:
                                            continue
                                        r, g, b = pix[xx, yy]
                                        is_red = (r >= 150) and (g <= 120) and (b <= 120) and (r - max(g, b) >= 35)
                                        if not is_red:
                                            continue
                                        d = math.hypot(dx, dy)
                                        if 1.5 <= d <= search_r:
                                            dists.append(d)

                                if len(dists) < 16:
                                    errors.append(
                                        "Unable to verify Step8 rendered marker radius from step08_final_tp.png; "
                                        "insufficient red ring pixels around selected point."
                                    )
                                else:
                                    dists.sort()
                                    est_r = dists[len(dists) // 2]
                                    # Keep tolerance moderate because anti-aliasing/line thickness
                                    # can shift the observed ring by a few pixels.
                                    if abs(est_r - mr) > 4.0:
                                        errors.append(
                                            "step08_final_tp.png rendered marker radius does not match "
                                            "step08_result.json marker_radius / candidate r_vis "
                                            f"(observed~{est_r:.1f}px vs expected~{mr:.1f}px)."
                                        )
                            except Exception as e:  # noqa: BLE001
                                errors.append(f"Failed to validate rendered Step8 marker radius: {e}")
                    except Exception as e:  # noqa: BLE001
                        errors.append(f"Failed to validate step08_result.json: {e}")
            except Exception as e:  # noqa: BLE001
                errors.append(f"Failed to parse step05_candidates.json: {e}")
        return errors

    # --------------------------- logging ----------------------------- #

    def _prepare_run_dir(self, name: str | None) -> Path:
        stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        slug = f"{stamp}" + (f"-{name}" if name else "")
        run_dir = self.cfg.workspace_dir / "runs" / slug
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_dir

    def _log_jsonl(self, run_dir: Path, filename: str, records: list[Any]) -> None:
        with open(run_dir / filename, "w", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")

    def _persist_run(self, run_dir: Path, result: AgentRun,
                     messages: list[dict[str, Any]]) -> None:
        summary = {
            "task_question": result.task_question,
            "stopped_reason": result.stopped_reason,
            "final_answer": result.final_answer,
            "last_error": result.last_error,
            "steps": [
                {
                    "index": s.index,
                    "assistant_content": s.assistant_content,
                    "tool_calls": s.tool_calls,
                    "tool_results": s.tool_results,
                }
                for s in result.steps
            ],
        }
        (run_dir / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        self._log_jsonl(run_dir, "messages.final.jsonl", messages)

    # --------------------------- rendering --------------------------- #

    def _render_assistant(self, step: int, reply: AssistantReply, dt: float) -> None:
        body = reply.content.strip() or "(no text, only tool calls)"
        self.console.print(Panel(
            truncate(body, 1500),
            title=f"[cyan]assistant · step {step} · {dt:.2f}s · {len(reply.tool_calls)} tool-call(s)[/cyan]",
            border_style="cyan",
        ))

    def _render_tool(self, call: ToolInvocation, result: ToolResult) -> None:
        args_repr = truncate(json.dumps(call.arguments, ensure_ascii=False), 400)
        style = "green" if result.ok else "red"
        self.console.print(Panel(
            f"[bold]args[/bold]: {args_repr}\n\n"
            f"{truncate(result.text, 1500)}",
            title=f"[{style}]tool · {call.name}[/] {'[OK]' if result.ok else '[FAIL]'}"
                  + (" [final]" if result.is_final else ""),
            border_style=style,
        ))
