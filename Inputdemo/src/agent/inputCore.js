import { ValidationError } from "../domain/errors.js";

export function parseUserCommand(payload) {
  if (!payload || typeof payload !== "object") {
    throw new ValidationError("Request body must be a JSON object.");
  }

  const prompt = normalizePrompt(payload);
  const command = String(payload.Instruction || payload.instruction || payload.command || prompt.text || "").trim();
  if (!command) {
    throw new ValidationError("Field 'Instruction' is required.");
  }

  const cameraImage = normalizeOptionalFile(payload.Camera_image || payload.camera_image);
  const cameraImageBack = normalizeOptionalFile(
    payload.Camera_image_back || payload.camera_image_back || payload.cameraImageBack
  );
  const bitImages = normalizeNamedFiles(payload.Bit_image || payload.bit_image, "bit_image");
  const schematicDiagrams = normalizeNamedFiles(
    payload.Schematic_Diagram || payload.schematic_diagram,
    "schematic_diagram"
  );
  const legacyAttachments = normalizeAttachments(payload.modelAttachments);
  const modelAttachments = [...bitImages, ...schematicDiagrams, ...legacyAttachments];
  const context = normalizeContext(payload);
  const targetBoardSide = normalizeBoardSide(
    payload.Target_board_side || payload.target_board_side || payload.targetBoardSide
  );

  return {
    command,
    instruction: command,
    caseId: context.caseId,
    operator: context.operator,
    prompt,
    cameraImage,
    cameraImageBack,
    targetBoardSide,
    bitImages,
    schematicDiagrams,
    visualCapture: payload.visualCapture || (cameraImage ? {
      imageRef: cameraImage.name,
      image: cameraImage,
      target: "vlm"
    } : null),
    modelAttachments,
    context
  };
}

function normalizeBoardSide(value) {
  const side = String(value || "auto").trim().toLowerCase();
  if (side === "top") return "front";
  if (side === "bottom") return "back";
  return ["auto", "front", "back"].includes(side) ? side : "auto";
}

function normalizePrompt(payload) {
  const prompt = payload.prompt && typeof payload.prompt === "object" ? payload.prompt : {};
  const mode = prompt.mode === "voice" ? "voice" : "text";
  const text = String(prompt.text || payload.Instruction || payload.instruction || payload.command || "").trim();

  return {
    mode,
    text
  };
}

function normalizeContext(payload) {
  const context = payload.context && typeof payload.context === "object" ? payload.context : {};

  return {
    ...context,
    caseId: String(payload.Case_ID || payload.case_id || context.caseId || "").trim(),
    operator: String(payload.Operator || payload.operator || context.operator || "").trim()
  };
}

function normalizeOptionalFile(file) {
  if (!file || typeof file !== "object") {
    return null;
  }

  return normalizeFile(file, file.kind || "camera_image");
}

function normalizeNamedFiles(files, kind) {
  if (!files) {
    return [];
  }

  const list = Array.isArray(files) ? files : [files];
  return list
    .filter((item) => item && typeof item === "object")
    .map((item) => normalizeFile(item, kind));
}

function normalizeAttachments(attachments) {
  if (!Array.isArray(attachments)) {
    return [];
  }

  return attachments
    .filter((item) => item && typeof item === "object")
    .map((item, index) => normalizeFile(item, item.kind || `attachment-${index + 1}`));
}

function normalizeFile(file, fallbackKind) {
  return {
    kind: String(file.kind || fallbackKind),
    name: String(file.name || "unnamed"),
    type: String(file.type || "application/octet-stream"),
    size: Number(file.size || 0),
    dataUrl: typeof file.dataUrl === "string" ? file.dataUrl : undefined
  };
}
