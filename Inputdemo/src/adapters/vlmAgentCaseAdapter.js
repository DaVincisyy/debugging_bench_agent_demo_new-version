import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { buildVlmAgentTaskYaml } from "../agent/vlmAgentTaskYaml.js";

export class VlmAgentCaseAdapter {
  constructor({ outputDir = path.join(process.cwd(), "output", "vlm-agent-cases") } = {}) {
    this.outputDir = outputDir;
  }

  async adapt({ runId, input }) {
    const caseName = buildCaseDirName(input.caseId, runId);
    const caseDir = path.join(this.outputDir, caseName);
    await mkdir(caseDir, { recursive: true });

    const savedAssets = {
      cameraImage: await this.saveFile(caseDir, input.cameraImage, "front_board_photo"),
      bitImages: await this.saveFiles(caseDir, input.bitImages, "bit"),
      schematicDiagrams: await this.saveFiles(caseDir, input.schematicDiagrams, "schematic")
    };
    const taskYaml = buildVlmAgentTaskYaml(input, savedAssets);
    const taskFile = path.join(caseDir, "task.yaml");
    await writeFile(taskFile, taskYaml, "utf8");

    return {
      format: "debugging-agent-v2-task",
      runId,
      caseDir,
      taskFile,
      taskYaml,
      savedAssets,
      command: `python -m agent run "${taskFile}"`
    };
  }

  async saveFiles(caseDir, files, prefix) {
    const out = [];
    for (let index = 0; index < files.length; index += 1) {
      out.push(await this.saveFile(caseDir, files[index], `${prefix}_${index + 1}`));
    }
    return out.filter(Boolean);
  }

  async saveFile(caseDir, file, fallbackName) {
    if (!file) return null;

    const mediaKind = getMediaKind(file);
    const fileName = buildFileName(file, fallbackName);
    const filePath = path.join(caseDir, fileName);

    if (file.dataUrl) {
      const buffer = decodeDataUrl(file.dataUrl);
      await writeFile(filePath, buffer);
    }

    return {
      sourceName: file.name,
      fileName,
      filePath,
      type: file.type,
      mediaKind,
      saved: Boolean(file.dataUrl)
    };
  }
}

function getMediaKind(file) {
  const type = String(file?.type || "").toLowerCase();
  const name = String(file?.name || "").toLowerCase();
  if (type.includes("pdf") || name.endsWith(".pdf")) return "pdf";
  if (type.startsWith("image/") || /\.(png|jpe?g|bmp|webp|tiff?)$/i.test(name)) return "image";
  return "file";
}

function buildFileName(file, fallbackName) {
  const original = String(file?.name || "").trim();
  const ext = extensionFromFile(file);
  const base = sanitizeBase(original ? original.replace(/\.[^.]+$/, "") : fallbackName);
  return `${base || fallbackName}${ext}`;
}

function extensionFromFile(file) {
  const original = String(file?.name || "").trim();
  const ext = path.extname(original);
  if (ext) return ext;
  const type = String(file?.type || "").toLowerCase();
  if (type.includes("pdf")) return ".pdf";
  if (type.includes("png")) return ".png";
  if (type.includes("jpeg") || type.includes("jpg")) return ".jpg";
  if (type.includes("webp")) return ".webp";
  return "";
}

function sanitizeBase(value) {
  return String(value || "")
    .replace(/[<>:"/\\|?*\x00-\x1F]/g, "_")
    .replace(/\s+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 120);
}

function buildCaseDirName(caseId, runId) {
  const base = sanitizeBase(caseId);
  if (base) return base;
  return sanitizeBase(runId) || "inputdemo-case";
}

function decodeDataUrl(dataUrl) {
  const match = String(dataUrl).match(/^data:([^;,]+)?(;base64)?,(.*)$/);
  if (!match) {
    throw new Error("Invalid file dataUrl.");
  }
  const isBase64 = Boolean(match[2]);
  const data = match[3] || "";
  return isBase64
    ? Buffer.from(data, "base64")
    : Buffer.from(decodeURIComponent(data), "utf8");
}
