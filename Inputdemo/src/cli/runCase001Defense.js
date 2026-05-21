import { readFile, stat } from "node:fs/promises";
import path from "node:path";
import { createDefaultAgent } from "../agent/factory.js";
import { parseUserCommand } from "../agent/inputCore.js";

const sourceCaseDir = path.resolve(
  "..",
  "output",
  "vlm-agent-cases",
  "case-001-sim-real-vlm-1779168249558"
);

async function dataUrl(filePath, mime) {
  const data = await readFile(filePath);
  return `data:${mime};base64,${data.toString("base64")}`;
}

async function filePayload(name, mime) {
  const filePath = path.join(sourceCaseDir, name);
  const info = await stat(filePath);
  return {
    name,
    type: mime,
    size: info.size,
    dataUrl: await dataUrl(filePath, mime)
  };
}

const suffix = process.env.CASE001_DEFENSE_SUFFIX || Date.now().toString();
const payload = {
  Instruction: "请定位 C_MOTOR_STEP 信号对应的测试点，并返回 MG400 可用的物理板图像素坐标。",
  Case_ID: `case-001-defense-real-vlm-${suffix}`,
  Operator: "codex-network-defense",
  Camera_image: await filePayload("PCBA_IMG.jpg", "image/jpeg"),
  Bit_image: [
    await filePayload("位号图7.29.pdf", "application/pdf")
  ],
  Schematic_Diagram: [
    await filePayload("voyah_hvac_v01_20240729.pdf", "application/pdf")
  ]
};

console.log(JSON.stringify({
  event: "case001-defense-start",
  caseId: payload.Case_ID,
  sourceCaseDir,
  assets: {
    camera: payload.Camera_image.size,
    bitPdf: payload.Bit_image[0].size,
    schematicPdf: payload.Schematic_Diagram[0].size
  },
  env: {
    VLM_AGENT_NETWORK_RETRIES: process.env.VLM_AGENT_NETWORK_RETRIES || null,
    VLM_AGENT_FORCE_CONNECTION_CLOSE: process.env.VLM_AGENT_FORCE_CONNECTION_CLOSE || null,
    VLM_HTTP_MAX_RETRIES: process.env.VLM_HTTP_MAX_RETRIES || null,
    VLM_CONNECT_RETRIES: process.env.VLM_CONNECT_RETRIES || null
  }
}, null, 2));

const agent = createDefaultAgent();
const started = Date.now();
const run = await agent.run(parseUserCommand(payload));
const elapsedMs = Date.now() - started;

console.log(JSON.stringify({
  event: "case001-defense-finish",
  elapsedMs,
  runId: run.runId,
  state: run.state,
  caseId: run.input.caseId,
  vlmAgentCase: run.vlmAgentCase,
  vlmObservation: {
    model: run.vlmObservation?.model,
    runDir: run.vlmObservation?.runDir,
    summaryPath: run.vlmObservation?.summaryPath,
    attempts: run.vlmObservation?.attempts,
    stoppedReason: run.vlmObservation?.stoppedReason,
    pixel: run.vlmObservation?.pixel,
    confidence: run.vlmObservation?.confidence
  },
  modelOutput: {
    runDir: run.modelOutput?.runDir,
    summaryPath: run.modelOutput?.summaryPath,
    attempts: run.modelOutput?.attempts,
    pixel: run.modelOutput?.pixel,
    mg400Pose: run.modelOutput?.mg400Pose
  },
  timeline: run.timeline,
  report: run.report
}, null, 2));
