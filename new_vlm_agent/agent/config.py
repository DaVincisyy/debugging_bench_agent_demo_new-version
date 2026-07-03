"""Runtime configuration: loads from .env plus optional YAML overrides."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from textwrap import dedent
from typing import Any

import yaml
from dotenv import load_dotenv

from agent.prompts import WORKFLOW_PROMPT_HARD_CONSTRAINTS_ZH


@dataclass
class Config:
    base_url: str
    api_key: str
    model: str
    use_native_tools: bool = True
    temperature: float = 0.2
    max_tokens: int = 2048
    max_steps: int = 25
    workspace_dir: Path = field(default_factory=lambda: Path("workspace"))
    # `default` = STANDARD_WORKFLOW Part D OpenCV 两框对齐；`vlm_test` = CLI 实验：跳过 Part A 实物 IC 红框，纯 VLM case12 对应。
    workflow_mode: str = "default"

    # --- HTTP / resilience (OpenAI SDK → httpx) ---------------------- #
    # Longer timeouts help multimodal payloads; retries help transient
    # "Server disconnected without sending a response" from gateways.
    http_timeout_sec: float = 180.0
    http_max_retries: int = 4
    connect_retries: int = 3

    # --- chat context (multimodal size) ------------------------------ #
    # Do not drop inline images until estimated payload exceeds
    # ``context_image_max_bytes`` (data URLs only). Then strip from the
    # **earliest** image-bearing user messages first. The last
    # ``context_image_keep_last`` such messages are protected; if still
    # over budget, strip again keeping only the single newest image turn.
    # Set ``context_image_max_bytes`` to 0 to disable (not recommended for
    # long multimodal runs against ~50MB gateways).
    context_image_max_bytes: int = 40 * 1024 * 1024
    context_image_keep_last: int = 2
    context_keep_recent_turns: int = 18
    context_drop_initial_after_step: int = 0
    context_tool_text_max_chars: int = 700
    context_phase_summary_max_lines: int = 8
    hierarchical_agent_mode: bool = True
    # When True (default with hierarchical mode): each plan phase group (part0/partB/partA/partD)
    # starts a fresh messages[] for the same VLM — only system + handoff artifact paths +
    # compact inputs + full [planner-step]. Stops cross-phase context snowballing.
    phase_isolated_context: bool = True
    # After a normal run, one extra VLM call reviews step timing / tools for efficiency.
    post_run_reflect: bool = False

    # --- optional "thinking / reasoning" controls -------------------- #
    # Some providers (e.g. DeepSeek V4) run a hidden chain-of-thought
    # and expose `reasoning_content` on the assistant message. The two
    # fields below switch that on. They are ignored by providers that
    # do not understand them (since we only send them when truthy).
    enable_thinking: bool = False
    reasoning_effort: str | None = None   # "high" | "max" | ...
    # For Intern-S1 family (LMDeploy Chat API): controls deep-thinking mode.
    # None means "do not send this field"; bool sends extra_body.thinking_mode.
    thinking_mode: bool | None = None

    def ensure_workspace(self) -> Path:
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        return self.workspace_dir


def _as_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _as_optional_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    text = value.strip().lower()
    if text == "":
        return None
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"Invalid boolean string: {value!r}")


def load_config(env_path: str | os.PathLike[str] | None = None,
                overrides: dict[str, Any] | None = None) -> Config:
    """Load configuration from `.env` (and optional overrides dict).

    Raises RuntimeError if required keys are missing so the caller can
    fail fast at startup instead of deep in an API call.
    """
    if env_path is None:
        env_path = Path(".env")
    load_dotenv(dotenv_path=env_path, override=False)

    overrides = overrides or {}

    def pick(key: str, default: Any = None) -> Any:
        if key in overrides and overrides[key] is not None:
            return overrides[key]
        return os.getenv(key, default)

    api_key = pick("VLM_API_KEY")
    base_url = pick("VLM_BASE_URL")
    model = pick("VLM_MODEL")
    missing = [k for k, v in {
        "VLM_API_KEY": api_key,
        "VLM_BASE_URL": base_url,
        "VLM_MODEL": model,
    }.items() if not v]
    if missing:
        raise RuntimeError(
            f"Missing required env vars: {missing}. "
            f"Copy .env.example to .env and fill them in."
        )

    reasoning_effort = pick("VLM_REASONING_EFFORT")
    reasoning_effort = (str(reasoning_effort).strip() or None) if reasoning_effort else None
    thinking_mode = _as_optional_bool(pick("VLM_THINKING_MODE"))

    wf_mode = overrides.get("workflow_mode")
    if wf_mode is None:
        wf_mode = pick("VLM_AGENT_WORKFLOW_MODE", "default")
    wf_mode = str(wf_mode).strip() or "default"

    return Config(
        base_url=str(base_url),
        api_key=str(api_key),
        model=str(model),
        use_native_tools=_as_bool(pick("VLM_USE_NATIVE_TOOLS"), True),
        temperature=float(pick("VLM_TEMPERATURE", 0.2)),
        max_tokens=int(pick("VLM_MAX_TOKENS", 2048)),
        max_steps=int(overrides.get("max_steps", 25)),
        workspace_dir=Path(overrides.get("workspace_dir", "workspace")),
        workflow_mode=wf_mode,
        enable_thinking=_as_bool(pick("VLM_ENABLE_THINKING"), False),
        reasoning_effort=reasoning_effort,
        thinking_mode=thinking_mode,
        http_timeout_sec=float(pick("VLM_HTTP_TIMEOUT", 180)),
        http_max_retries=int(pick("VLM_HTTP_MAX_RETRIES", 4)),
        connect_retries=int(pick("VLM_CONNECT_RETRIES", 3)),
        context_image_max_bytes=int(
            pick("VLM_CONTEXT_IMAGE_MAX_BYTES", 40 * 1024 * 1024)
        ),
        context_image_keep_last=int(pick("VLM_CONTEXT_IMAGE_KEEP_LAST", 2)),
        context_keep_recent_turns=int(pick("VLM_CONTEXT_KEEP_RECENT_TURNS", 18)),
        context_drop_initial_after_step=int(
            pick("VLM_CONTEXT_DROP_INITIAL_AFTER_STEP", 0)
        ),
        context_tool_text_max_chars=int(
            pick("VLM_CONTEXT_TOOL_TEXT_MAX_CHARS", 700)
        ),
        context_phase_summary_max_lines=int(
            pick("VLM_CONTEXT_PHASE_SUMMARY_MAX_LINES", 8)
        ),
        hierarchical_agent_mode=_as_bool(pick("VLM_HIERARCHICAL_AGENT_MODE"), True),
        phase_isolated_context=_as_bool(pick("VLM_PHASE_ISOLATED_CONTEXT"), True),
        post_run_reflect=_as_bool(pick("VLM_POST_RUN_REFLECT"), False),
    )


def strip_workflow_doc_preamble(md: str) -> str:
    """Remove file-location blockquote after first H1 in STANDARD_WORKFLOW.md."""

    wf_lines = md.splitlines()
    body: list[str] = []
    i = 0
    if wf_lines and wf_lines[0].startswith("# "):
        body.append(wf_lines[0])
        i = 1
    while i < len(wf_lines) and wf_lines[i].strip() == "":
        i += 1
    while i < len(wf_lines) and wf_lines[i].startswith(">"):
        i += 1
    while i < len(wf_lines) and wf_lines[i].strip() == "":
        i += 1
    body.extend(wf_lines[i:])
    return "\n".join(body).strip()


PART3_QUICKSTART_ZH = dedent("""
## Step3+ 快速路径（已提供 step02 标注图；勿跑 Part 0/A/B）

1. **输入已就绪**：`debug/step02_locator_front_anchor.png` 与 `debug/step02_board_front_anchor.png` 已在启动时从 `INPUT_PATHS` 复制完成。
2. **Part D 仅允许 3 个工具**（按顺序，各调用一次）：
   - `case12_build_and_align_from_step02_anchors`（无参数）
   - `emit_step08_from_case12_aligned`（对齐成功后会自动链式执行，通常无需你再调）
   - `finish`（`pixel` 读 `debug/step08_result.json`）
3. **禁止**：`read_text_file`、`view_image`、`run_python`、`save_text_file`、重复对齐。
""").strip()


PART0_QUICKSTART_ZH = dedent("""
## Part 0 快速路径（workflow 未内嵌；勿反复 read_text_file 读 STANDARD_WORKFLOW.md）

1. **段 A**：`search_pdf_text`（`INPUT_PATHS["schematic_pdf"]`，query=`target_signal`）→ 必要时 `pdf_page_to_image` + `view_image` → `save_text_file` → `debug/case10_signal_to_tp.json`（含 `tp_id_or_ref`）。
2. **段 B**：`search_pdf_text`（`INPUT_PATHS["assembly_drawing_pdf"]`，query=JSON 内 `tp_id_or_ref` 全文）→ `out_json_path=debug/case10_target_tp_pdf_search.json`。
3. **段 C**：**必须**调用内置工具 **`mark_tp_on_assembly_from_pdf_hit`**（传入 search JSON 路径）→ 产出 `case10_assembly_drawing_tp_marked.png`。
   - 若 assembly PDF **0 文本命中**（graphic-only 丝印）：工具会栅格化 `case10_assembly_drawing.png` → **`view_image`** 目视定位 tp_id → **`run_python`** 画绿圈。
4. **禁止**：在 `run_python` 里 `import fitz/pypdf` 或自行探测 PDF 库；PDF 一律用 **`search_pdf_text` / `pdf_page_to_image` / `mark_tp_on_assembly_from_pdf_hit`**。
5. Part0 完成后进入 Part B（位号最大 IC），按 `workflow_doc` 仅在卡住时读一次（`read_text_file` 1-120 行即可）。
""").strip()


def compose_agent_question(task: dict[str, Any]) -> str:
    """Build the task text sent as ``## Task`` in the first user turn.

    When ``embed_workflow_in_prompt`` is not ``False`` and ``inputs.workflow_doc``
    points to an existing file, the **STANDARD_WORKFLOW** body is appended (with hard
    constraints) so the model follows the full procedure without relying on an extra
    ``read_text_file`` round trip.

    Set ``embed_workflow_in_prompt: false`` in the YAML root for short, custom runs.
    """
    base = (task.get("question") or "").strip()
    if task.get("embed_workflow_in_prompt") is False:
        quick = PART3_QUICKSTART_ZH if task.get("start_from_step3") else PART0_QUICKSTART_ZH
        inputs = task.get("inputs") or {}
        if str(inputs.get("_start_from_step3", "")).lower() == "true":
            quick = PART3_QUICKSTART_ZH
        return "\n\n".join(p for p in (base, quick) if p)

    inputs = task.get("inputs") or {}
    wd = inputs.get("workflow_doc")
    if not isinstance(wd, str) or not wd.strip():
        return base
    path = Path(wd.strip())
    if not path.is_file():
        return base

    body = strip_workflow_doc_preamble(path.read_text(encoding="utf-8"))
    # Short case question first, then binding constraints, then full workflow.
    return "\n\n".join(
        p for p in (base, WORKFLOW_PROMPT_HARD_CONSTRAINTS_ZH, body) if p
    )


def load_task(task_path: str | os.PathLike[str]) -> dict[str, Any]:
    """Load a YAML task spec describing inputs and the question.

    Relative paths inside `inputs` are resolved against the YAML file's
    own directory. This lets each test case live in its own folder
    (e.g. `data/cases/case_001/`) and reference its assets by bare
    filename (`locator.png`) without worrying about the CWD.

    A value is treated as a path when it is a string that either:

      * ends in a common asset extension (images + .txt / .csv /
        .json / .yaml / .yml), or
      * corresponds to an existing file relative to the YAML dir.

    Other strings are left untouched so free-form notes in YAML
    (`board_id: "XYZ123"`) still work.

    If `inputs.workflow_doc` / `inputs.skills_doc` are omitted or blank, and
    ``<task_dir>/../../skills/STANDARD_WORKFLOW.md`` and ``SKILL.md`` exist
    (normal layout: ``data/cases/<case>/task.yaml``), those paths are filled
    in automatically so case folders only need data assets + `question`.

    Optional YAML root key ``embed_workflow_in_prompt``: when ``false``, `compose_agent_question`
    leaves only ``question`` without inlining ``workflow_doc`` (see ``agent.cli``).
    """
    task_path = Path(task_path)
    with open(task_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if "question" not in data:
        raise ValueError(f"Task file {task_path} must define a 'question' field.")
    data.setdefault("inputs", {})

    base_dir = task_path.resolve().parent
    resolvable_ext = {
        ".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tiff",
        ".txt", ".csv", ".json", ".yaml", ".yml", ".md", ".log",
        ".pdf",
    }
    for key, value in list(data["inputs"].items()):
        if not isinstance(value, str):
            continue
        candidate = Path(value)
        if candidate.is_absolute():
            continue  # user wrote an absolute path — keep as-is.
        ext = candidate.suffix.lower()
        resolved = (base_dir / candidate).resolve()
        if ext in resolvable_ext or resolved.exists():
            data["inputs"][key] = str(resolved)

    # Default skill/workflow paths for cases under data/cases/<name>/ — no need
    # to list these in every task.yaml; they resolve via ../../skills/...
    skills_dir = (base_dir / "../../skills").resolve()
    wf = skills_dir / "STANDARD_WORKFLOW.md"
    sk = skills_dir / "SKILL.md"
    wd = data["inputs"].get("workflow_doc")
    sd = data["inputs"].get("skills_doc")
    if (not isinstance(wd, str) or not wd.strip()) and wf.is_file():
        data["inputs"]["workflow_doc"] = str(wf)
    if (not isinstance(sd, str) or not sd.strip()) and sk.is_file():
        data["inputs"]["skills_doc"] = str(sk)

    # Workflow expects assembly_drawing_pdf; cases may only define locator_pdf.
    inp = data["inputs"]
    if isinstance(inp.get("locator_pdf"), str) and not inp.get("assembly_drawing_pdf"):
        inp["assembly_drawing_pdf"] = inp["locator_pdf"]

    # Compact mode: quickstart in question instead of full workflow inline.
    if data.get("embed_workflow_in_prompt") is False:
        inp["_compact_workflow"] = "true"
    if data.get("start_from_step3") is True or str(data.get("start_from_step3", "")).lower() in {
        "true",
        "1",
        "yes",
    }:
        inp["_start_from_step3"] = "true"
        data["embed_workflow_in_prompt"] = False
        inp["_compact_workflow"] = "true"

    return data
