"""Build Inputdemo bench task.yaml — single source of truth for VLM case content."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = (_REPO_ROOT / "data" / "skills").resolve()
STANDARD_WORKFLOW = SKILLS_DIR / "STANDARD_WORKFLOW.md"
SKILL_MD = SKILLS_DIR / "SKILL.md"

INPUTDEMO_BENCH_QUESTION = """\
按仓库默认「标准测点定位流程」全量执行：根据 `user_measurement_question` 与原理图确定目标 TP，在位号图 PDF 检索、整页栅格、OpenCV 绿圈与后续 Part B-D（双 ROI / `case10_dual_roi_layout`）直至 `finish`，并产出规程所列全部 `debug/case10_*`、`step*`、`progress/*` 中间文件。

**【本用例 Part B 强制：网格法】** 在写出 **`debug/case10_assembly_drawing_tp_marked.png`** 之后、**首次**为最大 IC 估 **`vlm_roi`** 之前：**必须** 用 **`run_python`** 复制该图并叠 **浅色稀疏网格**（推荐 **`grid_step_px=128`**，半透明浅灰线，勿盖住 TP 绿圈），**`cv2.imwrite` -> `debug/case10_assembly_drawing_tp_marked_grid.png`**（**W×H 与 `tp_marked` 完全一致**）。**估框用的 `view_image` 必须以带网格版为准**；写入 **`case10_assembly_vlm_hints.json`** 时附带 **`grid_step_px`** 与 **`vlm_visual_aid`**: `debug/case10_assembly_drawing_tp_marked_grid.png`。**StepB2 分割与 OpenCV 只准读无网格的 `case10_assembly_drawing_tp_marked.png`**，禁止用 `_grid` 图做阈值。"""


def _first_by_kind(assets: dict[str, str | None], prefix: str, kind: str) -> str | None:
    for key, name in assets.items():
        if not name or not key.startswith(prefix):
            continue
        lower = name.lower()
        if kind == "pdf" and lower.endswith(".pdf"):
            return name
        if kind == "image" and any(lower.endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".webp", ".bmp")):
            return name
    return None


def build_inputdemo_task_dict(
    *,
    instruction: str,
    case_id: str,
    operator: str,
    assets: dict[str, str | None],
) -> dict[str, Any]:
    """Return a task.yaml-compatible dict for Inputdemo bench runs."""
    schematic_image = assets.get("schematic_image") or _first_by_kind(assets, "schematic", "image")
    schematic_pdf = assets.get("schematic_pdf") or _first_by_kind(assets, "schematic", "pdf")
    assembly_drawing = assets.get("assembly_drawing") or _first_by_kind(assets, "bit", "image")
    assembly_drawing_pdf = assets.get("assembly_drawing_pdf") or _first_by_kind(assets, "bit", "pdf")
    front_board_photo = assets.get("front_board_photo")
    back_board_photo = assets.get("back_board_photo")

    inputs: dict[str, Any] = {
        "user_measurement_question": (instruction or "").strip(),
        "board_id": (case_id or "inputdemo-case").strip() or "inputdemo-case",
        "engineer_note": (
            f"Operator: {operator or 'unknown'}\n"
            "Prepared by VLM service case_builder.py. See STANDARD_WORKFLOW Part B."
        ),
    }
    if schematic_image:
        inputs["schematic_image"] = schematic_image
    if schematic_pdf:
        inputs["schematic_pdf"] = schematic_pdf
    if assembly_drawing:
        inputs["assembly_drawing"] = assembly_drawing
    if assembly_drawing_pdf:
        inputs["assembly_drawing_pdf"] = assembly_drawing_pdf
    if front_board_photo:
        inputs["front_board_photo"] = front_board_photo
    if back_board_photo:
        inputs["back_board_photo"] = back_board_photo
    if STANDARD_WORKFLOW.is_file():
        inputs["workflow_doc"] = str(STANDARD_WORKFLOW)
    if SKILL_MD.is_file():
        inputs["skills_doc"] = str(SKILL_MD)

    return {
        "embed_workflow_in_prompt": False,
        "question": INPUTDEMO_BENCH_QUESTION,
        "inputs": inputs,
    }


def write_inputdemo_task_yaml(
    case_dir: str | Path,
    *,
    instruction: str,
    case_id: str,
    operator: str,
    assets: dict[str, str | None],
) -> Path:
    """Write task.yaml into ``case_dir`` and return its path."""
    directory = Path(case_dir).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    task = build_inputdemo_task_dict(
        instruction=instruction,
        case_id=case_id,
        operator=operator,
        assets=assets,
    )
    task_file = directory / "task.yaml"
    task_file.write_text(
        yaml.safe_dump(task, allow_unicode=True, sort_keys=False, width=120),
        encoding="utf-8",
    )
    return task_file
