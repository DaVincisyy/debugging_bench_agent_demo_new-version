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
      "Mapping: use PCB outline plus mounting/tooling-hole registration on the photo selected by target_board_side.",
      "IC detection/IC-anchor code is retained for compatibility but must not be called.",
      "Gen by Inputdemo. PDF->asm_pdf, IMG->asm_fb."
    ].join("\n"))
  };

  return toYaml({
    question: block(
      "执行统一的板框/孔位测点定位流程：根据 `user_measurement_question` 与原理图确定目标 TP，并严格按 `target_board_side` 选择位号图页面和实物照片。`front` 只使用 Top/Front 位号图与 `front_board_photo`；`back` 只使用 Bottom/Back 位号图与 `back_board_photo`；`auto` 先判断板面再锁定对应照片。随后统一使用 PCB 板框和安装孔/工艺孔配准，将目标映射到所选实物照片并生成 Step08。最大 IC 与 IC-anchor 代码只保留兼容性，本流程禁止调用。"
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
