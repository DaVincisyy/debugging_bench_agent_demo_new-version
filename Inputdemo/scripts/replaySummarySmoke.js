import net from "node:net";
import { spawn } from "node:child_process";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { mapVlmTargetToExecution } from "../src/agent/vlmTargetExecutionMapper.js";
import { createBenchRun, transition } from "../src/domain/run.js";
import { AgentState, StepKind } from "../src/domain/states.js";
import { MockEquipmentController } from "../src/adapters/mockEquipmentController.js";
import { ReportGenerator } from "../src/adapters/reportGenerator.js";

const summaryPath = process.argv[2];
if (!summaryPath) {
  console.error("Usage: node Inputdemo/scripts/replaySummarySmoke.js <summary.json>");
  process.exit(2);
}

const summary = JSON.parse(await readFile(summaryPath, "utf8"));
const finalAnswer = summary.final_answer || {};
const pixel = normalizePixel(finalAnswer.pixel);

const simRequests = [];
const here = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(here, "..", "..");
const simServer = net.createServer((socket) => {
  let buffer = "";
  socket.setEncoding("utf8");
  socket.on("data", (chunk) => {
    buffer += chunk;
    while (buffer.includes("\n")) {
      const newline = buffer.indexOf("\n");
      const line = buffer.slice(0, newline).trim();
      buffer = buffer.slice(newline + 1);
      simRequests.push(line);
      if (line === "GetPose()") {
        socket.write("0,{245.6000,-32.4000,78.2000,0,0,91.5000}\n");
      } else {
        socket.write("0,{1}\n");
      }
    }
  });
});

await new Promise((resolve) => simServer.listen(0, "127.0.0.1", resolve));
const simPort = simServer.address().port;
const gatewayPort = 18010 + Math.floor(Math.random() * 1000);
const gateway = spawn(process.execPath, [path.join(repoRoot, "Inputdemo", "src", "gateway", "robotGatewayServer.js")], {
  cwd: repoRoot,
  env: { ...process.env, ROBOT_GATEWAY_PORT: String(gatewayPort) },
  stdio: ["ignore", "ignore", "ignore"]
});

try {
  await waitForGateway(gatewayPort);
  const run = buildReplayRun({ summaryPath, summary, finalAnswer, pixel });
  const config = {
    mode: "simulation",
    simHost: "127.0.0.1",
    simDashboardPort: simPort,
    simFeedbackPort: 30004,
    timeoutMs: 1000,
    speed: 30,
    motionCommand: "MovJ"
  };
  const equipmentController = new MockEquipmentController();

  transition(run, AgentState.EXECUTING, "Replayed existing VLM summary; executing robot gateway path.");
  for (const step of run.plan.steps) {
    if (step.kind === StepKind.ARM_MOTION || step.kind === StepKind.VISUAL_CAPTURE) {
      run.execution.arm.push(await executeRobotStep({ gatewayPort, step, config }));
    }
    if (step.kind === StepKind.EQUIPMENT_MEASUREMENT) {
      run.execution.equipment.push(await equipmentController.measure(step));
    }
  }

  transition(run, AgentState.REPORTING, "Summary replay completed; generating report.");
  run.report = new ReportGenerator().create({
    run,
    ragEvidence: [],
    vlmObservation: run.vlmObservation,
    measurements: run.execution.equipment
  });

  console.log(JSON.stringify({
    ok: true,
    summaryPath,
    finalAnswer,
    plan: run.plan,
    robotResults: run.execution.arm,
    equipment: run.execution.equipment,
    report: run.report,
    simRequests
  }, null, 2));
} finally {
  gateway.kill();
  await new Promise((resolve) => simServer.close(resolve));
}

function buildReplayRun({ summaryPath, summary, finalAnswer, pixel }) {
  const input = {
    command: "Replay generated VLM summary without rerunning VLM",
    instruction: "Replay generated VLM summary without rerunning VLM",
    caseId: "summary-replay",
    operator: "codex",
    cameraImage: { name: "PCBA_IMG.jpg" },
    prompt: { mode: "text" },
    modelAttachments: [],
    schematicDiagrams: [],
    bitImages: []
  };
  const locationId = finalAnswer.tp_id || finalAnswer.test_point || "vlm-target";
  const run = createBenchRun(input);
  run.ragEvidence = [];
  run.vlmObservation = {
    model: "summary-replay",
    provider: "debugging-agent-v2-summary",
    realModelService: true,
    imageRef: input.cameraImage.name,
    promptMode: "text",
    workspace: path.dirname(path.dirname(summaryPath)),
    runDir: path.dirname(summaryPath),
    summaryPath,
    stoppedReason: summary.stopped_reason,
    finalAnswer,
    pixel: pixel ? { x: pixel[0], y: pixel[1] } : null,
    confidence: Number.isFinite(Number(finalAnswer.confidence)) ? Number(finalAnswer.confidence) : null,
    modelInputSummary: { attachmentCount: 0, schematicCount: 0, bitImageCount: 0, attachmentNames: [] },
    benchOverview: {
      boardDetected: Boolean(pixel),
      instrumentsDetected: [],
      armReachableZones: pixel ? ["vlm-localized-test-point"] : []
    },
    locations: [{
      id: locationId,
      label: finalAnswer.tp_id || finalAnswer.label || "VLM localized test point",
      pixel: pixel ? { x: pixel[0], y: pixel[1] } : null,
      cameraView: finalAnswer.camera_view || null
    }],
    recommendedMeasurements: [{
      signal: finalAnswer.signal || finalAnswer.tp_id || "TARGET_SIGNAL",
      locationId,
      instrument: "oscilloscope",
      expectedRange: finalAnswer.expected_range || "See summary final_answer.",
      reason: finalAnswer.reasoning || finalAnswer.reason || "Summary replay."
    }]
  };
  run.modelOutput = {
    model: "summary-replay",
    provider: "debugging-agent-v2-summary",
    realModelService: true,
    inputFormat: "debugging-agent-v2-summary",
    testPoint: finalAnswer.tp_id || null,
    confidence: run.vlmObservation.confidence,
    pixel: run.vlmObservation.pixel,
    mg400Pose: normalizePose(finalAnswer.mg400Pose || finalAnswer.mg400_pose || finalAnswer.pose) || poseFromPixel(pixel),
    finalAnswer,
    summaryPath,
    runDir: path.dirname(summaryPath),
    workspace: path.dirname(path.dirname(summaryPath)),
    reason: "Replayed existing summary."
  };
  run.plan = mapVlmTargetToExecution({
    input,
    ragEvidence: [],
    vlmObservation: run.vlmObservation,
    modelOutput: run.modelOutput
  });
  return run;
}

async function executeRobotStep({ gatewayPort, step, config }) {
  const response = await fetch(`http://127.0.0.1:${gatewayPort}/v1/robot/execute`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ step, config })
  });
  const body = await response.json();
  if (!response.ok || body.ok === false) {
    throw new Error(body.error || `Robot gateway HTTP ${response.status}`);
  }
  return body;
}

async function waitForGateway(port) {
  const deadline = Date.now() + 5000;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`http://127.0.0.1:${port}/health`);
      if (response.ok) return;
    } catch {
      // Service is still starting.
    }
    await sleep(100);
  }
  throw new Error("Robot gateway did not start.");
}

function normalizePixel(value) {
  if (!Array.isArray(value) || value.length !== 2) return null;
  const x = Number(value[0]);
  const y = Number(value[1]);
  if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
  return [Math.round(x), Math.round(y)];
}

function normalizePose(value) {
  if (!value || typeof value !== "object") return null;
  const pose = {
    x: Number(value.x),
    y: Number(value.y),
    z: Number(value.z),
    r: Number(value.r)
  };
  if (Object.values(pose).some((item) => !Number.isFinite(item))) return null;
  return pose;
}

function poseFromPixel(value) {
  if (!Array.isArray(value) || value.length !== 2) return null;
  const x = Number(value[0]);
  const y = Number(value[1]);
  if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
  return { x: Math.round(x), y: Math.round(y), z: 0, r: 0 };
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
