import { appendFile, mkdir } from "node:fs/promises";
import path from "node:path";

export class PixelTargetDispatcher {
  constructor({ outputDir = path.join(process.cwd(), "output", "pixel-targets") } = {}) {
    this.outputDir = outputDir;
  }

  async dispatch(payload) {
    const normalized = normalizePayload(payload);
    await mkdir(this.outputDir, { recursive: true });
    const record = {
      ...normalized,
      acceptedAt: new Date().toISOString(),
      status: "ACCEPTED"
    };
    await appendFile(
      path.join(this.outputDir, "targets.jsonl"),
      `${JSON.stringify(record)}\n`,
      "utf8"
    );
    return {
      ok: true,
      message: `Pixel target accepted: (${record.pixel.x}, ${record.pixel.y}).`,
      record
    };
  }
}

function normalizePayload(payload) {
  const pixel = payload?.pixel || {};
  const x = Number(pixel.x);
  const y = Number(pixel.y);
  if (!Number.isFinite(x) || !Number.isFinite(y)) {
    const error = new Error("payload.pixel.x and payload.pixel.y must be finite numbers.");
    error.statusCode = 400;
    throw error;
  }
  return {
    type: payload?.type || "vlm_pixel_target",
    caseId: String(payload?.caseId || ""),
    pixel: { x: Math.round(x), y: Math.round(y) },
    tpId: payload?.tpId || null,
    cameraView: payload?.cameraView || null,
    confidence: Number.isFinite(Number(payload?.confidence)) ? Number(payload.confidence) : null,
    summaryPath: payload?.summaryPath || null
  };
}
