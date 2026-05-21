import { readdir, readFile, stat } from "node:fs/promises";
import path from "node:path";

export class VlmAgentSummaryAdapter {
  constructor({ runsDir = path.join(process.cwd(), "output", "vlm-agent-runs") } = {}) {
    this.runsDir = runsDir;
  }

  async readLatestByCaseId(caseId) {
    const safeCaseId = sanitizeSegment(caseId);
    if (!safeCaseId) {
      const error = new Error("caseId is required.");
      error.statusCode = 400;
      throw error;
    }

    const caseRunsDir = path.join(this.runsDir, safeCaseId, "runs");
    const summaryPath = await findLatestSummary(caseRunsDir);
    if (!summaryPath) {
      return {
        ok: false,
        caseId: safeCaseId,
        found: false,
        message: `No summary.json found under ${caseRunsDir}.`
      };
    }

    const raw = await readJson(summaryPath);
    const finalAnswer = raw.final_answer || null;
    const pixel = normalizePixel(finalAnswer?.pixel);
    const interfacePayload = pixel ? buildInterfacePayload({ caseId: safeCaseId, summaryPath, finalAnswer, pixel }) : null;

    return {
      ok: Boolean(pixel),
      caseId: safeCaseId,
      found: true,
      summaryPath,
      stoppedReason: raw.stopped_reason || null,
      finalAnswer,
      pixel,
      message: pixel
        ? `VLM agent returned pixel [${pixel[0]}, ${pixel[1]}].`
        : "summary.json was found, but final_answer.pixel is missing or invalid.",
      interfacePayload
    };
  }
}

async function findLatestSummary(caseRunsDir) {
  let entries = [];
  try {
    entries = await readdir(caseRunsDir, { withFileTypes: true });
  } catch {
    return null;
  }

  const candidates = [];
  for (const entry of entries) {
    if (!entry.isDirectory()) continue;
    const summaryPath = path.join(caseRunsDir, entry.name, "summary.json");
    try {
      const info = await stat(summaryPath);
      candidates.push({ summaryPath, mtimeMs: info.mtimeMs });
    } catch {
      // Ignore incomplete run directories.
    }
  }

  candidates.sort((a, b) => b.mtimeMs - a.mtimeMs);
  return candidates[0]?.summaryPath || null;
}

async function readJson(filePath) {
  const text = await readFile(filePath, "utf8");
  return JSON.parse(text);
}

function normalizePixel(value) {
  if (!Array.isArray(value) || value.length !== 2) return null;
  const x = Number(value[0]);
  const y = Number(value[1]);
  if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
  return [Math.round(x), Math.round(y)];
}

function buildInterfacePayload({ caseId, summaryPath, finalAnswer, pixel }) {
  return {
    type: "vlm_pixel_target",
    caseId,
    pixel: { x: pixel[0], y: pixel[1] },
    tpId: finalAnswer?.tp_id || null,
    cameraView: finalAnswer?.camera_view || null,
    confidence: Number.isFinite(Number(finalAnswer?.confidence)) ? Number(finalAnswer.confidence) : null,
    summaryPath
  };
}

function sanitizeSegment(value) {
  return String(value || "")
    .trim()
    .replace(/[<>:"/\\|?*\x00-\x1F]/g, "_")
    .replace(/\s+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 120);
}
