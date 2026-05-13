import { ValidationError } from "../domain/errors.js";

export function parseUserCommand(payload) {
  if (!payload || typeof payload !== "object") {
    throw new ValidationError("Request body must be a JSON object.");
  }

  const prompt = normalizePrompt(payload);
  const command = String(payload.command || prompt.text || "").trim();
  if (!command) {
    throw new ValidationError("A text prompt or voice prompt file is required.");
  }

  return {
    command,
    prompt,
    visualCapture: payload.visualCapture || null,
    modelAttachments: normalizeAttachments(payload.modelAttachments),
    context: payload.context || {}
  };
}

function normalizePrompt(payload) {
  const prompt = payload.prompt && typeof payload.prompt === "object" ? payload.prompt : {};
  const mode = prompt.mode === "voice" ? "voice" : "text";
  const text = String(prompt.text || payload.command || "").trim();

  return {
    mode,
    text
  };
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
