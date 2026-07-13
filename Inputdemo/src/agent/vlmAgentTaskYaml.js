export function buildVlmAgentTaskYaml(input, savedAssets) {
  const bitPdf = firstByKind(savedAssets.bitImages, "pdf");
  const bitImage = firstByKind(savedAssets.bitImages, "image");
  const schematicPdf = firstByKind(savedAssets.schematicDiagrams, "pdf");
  const schematicImage = firstByKind(savedAssets.schematicDiagrams, "image");

  const inputs = {
    user_measurement_question: block(input.instruction),
    schematic_image: schematicImage?.fileName,
    schematic_pdf: schematicPdf?.fileName,
    assembly_drawing_pdf: bitPdf?.fileName,
    assembly_drawing: bitImage?.fileName,
    front_board_photo: savedAssets.cameraImage?.fileName,
    back_board_photo: savedAssets.cameraImageBack?.fileName,
    target_board_side: input.targetBoardSide || "auto",
    board_id: input.caseId || "inputdemo-case",
    engineer_note: block([
      `Operator: ${input.operator || "unknown"}`,
      `Target board side: ${input.targetBoardSide || "auto"}`,
      "Part B: grid=tp_marked->grid.png->vlm_roi; OpenCV ungridded tp_marked. See STANDARD_WORKFLOW Part B.",
      "Gen by Inputdemo. PDF->asm_pdf, IMG->asm_fb."
    ].join("\n"))
  };

  return toYaml({
    question: block(
      "按仓库默认「标准测点定位流程」全量执行：根据 `user_measurement_question` 与原理图确定目标 TP，在位号图 PDF 检索、整页栅格、OpenCV 绿圈与后续 Part B-D（双 ROI / `case10_dual_roi_layout`）直至 `finish`，并产出规程所列全部 `debug/case10_*`、`step*`、`progress/*` 中间文件。\n\n" +
      "**【本用例 Part B 强制：网格法】** 在写出 **`debug/case10_assembly_drawing_tp_marked.png`** 之后、**首次**为最大 IC 估 **`vlm_roi`** 之前：**必须** 用 **`run_python`** 复制该图并叠 **浅色稀疏网格**（推荐 **`grid_step_px=128`**，半透明浅灰线，勿盖住 TP 绿圈），**`cv2.imwrite` -> `debug/case10_assembly_drawing_tp_marked_grid.png`**（**W×H 与 `tp_marked` 完全一致**）。**估框用的 `view_image` 必须以带网格版为准**；写入 **`case10_assembly_vlm_hints.json`** 时附带 **`grid_step_px`** 与 **`vlm_visual_aid`**: `debug/case10_assembly_drawing_tp_marked_grid.png`。**StepB2 分割与 OpenCV 只准读无网格的 `case10_assembly_drawing_tp_marked.png`**，禁止用 `_grid` 图做阈值。"
    ),
    inputs: compactObject(inputs)
  });
}

function firstByKind(files, mediaKind) {
  return files.find((file) => file.mediaKind === mediaKind) || null;
}

function compactObject(value) {
  return Object.fromEntries(
    Object.entries(value).filter(([, item]) => item !== undefined && item !== null && item !== "")
  );
}

function block(text) {
  return { __yamlBlock: String(text || "").trim() };
}

function toYaml(value, indent = 0) {
  if (Array.isArray(value)) {
    if (value.length === 0) return "[]";
    return value.map((item) => {
      if (isScalar(item)) return `${spaces(indent)}- ${formatScalar(item)}`;
      return `${spaces(indent)}-\n${toYaml(item, indent + 2)}`;
    }).join("\n");
  }

  if (value && typeof value === "object" && !value.__yamlBlock) {
    return Object.entries(value).map(([key, item]) => {
      if (item && typeof item === "object" && item.__yamlBlock) {
        const lines = item.__yamlBlock.split(/\r?\n/);
        return `${spaces(indent)}${key}: |\n${lines.map((line) => `${spaces(indent + 2)}${line}`).join("\n")}`;
      }
      if (isScalar(item)) return `${spaces(indent)}${key}: ${formatScalar(item)}`;
      return `${spaces(indent)}${key}:\n${toYaml(item, indent + 2)}`;
    }).join("\n");
  }

  return formatScalar(value);
}

function isScalar(value) {
  return value === null || ["string", "number", "boolean"].includes(typeof value);
}

function formatScalar(value) {
  if (value === null || value === undefined) return "null";
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(String(value));
}

function spaces(count) {
  return " ".repeat(count);
}
