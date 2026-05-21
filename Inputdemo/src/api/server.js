import http from "node:http";
import { createDefaultAgent } from "../agent/factory.js";
import { buildModelInputYaml } from "../agent/modelInputYaml.js";
import { buildPlan } from "../agent/planner.js";
import { parseUserCommand } from "../agent/inputCore.js";
import { createBenchRun, transition } from "../domain/run.js";
import { AgentState, StepKind } from "../domain/states.js";
import { getRun, saveRun, updateRun, appendRunEvent } from "./runStore.js";
import { readJsonBody, sendJson } from "../utils/http.js";
import { webPage } from "./webPage.js";
import { Mg400ArmController } from "../adapters/mg400ArmController.js";
import { readMg400Config, writeMg400Config } from "../adapters/mg400Config.js";
import { VlmAgentCaseAdapter } from "../adapters/vlmAgentCaseAdapter.js";
import { VlmAgentServiceRunner } from "../adapters/vlmAgentServiceRunner.js";
import { RealVlmAgentRunner } from "../adapters/realVlmAgentRunner.js";
import { writeModelInputYamlFile } from "../adapters/modelInputYamlFileWriter.js";
import { MockEquipmentController } from "../adapters/mockEquipmentController.js";
import { ReportGenerator } from "../adapters/reportGenerator.js";
import { getEthernetInfo } from "../adapters/ethernetConfig.js";

const port = Number(process.env.PORT || 3000);
const cliFallbackRunner = new RealVlmAgentRunner();
const agent = createDefaultAgent({ vlmRunner: cliFallbackRunner });
const serviceRunner = process.env.VLM_AGENT_RUNNER === "cli" ? null : new VlmAgentServiceRunner();
const serviceCaseAdapter = serviceRunner ? new VlmAgentCaseAdapter() : null;
const serviceArmController = new Mg400ArmController();
const serviceEquipmentController = new MockEquipmentController();
const serviceReportGenerator = new ReportGenerator();

async function route(request, response) {
  const url = new URL(request.url, `http://${request.headers.host}`);

  if (request.method === "GET" && url.pathname === "/health") {
    sendJson(response, 200, { ok: true, service: "debugging-bench-agent" });
    return;
  }

  if (request.method === "GET" && url.pathname === "/") {
    response.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
    response.end(webPage);
    return;
  }

  if (request.method === "POST" && url.pathname === "/api/runs") {
    const payload = await readJsonBody(request);
    const input = parseUserCommand(payload);
    if (serviceRunner) {
      try {
        const run = await startServiceBackedRun(input);
        sendJson(response, 202, run);
      } catch (error) {
        if (!isServiceUnavailable(error)) throw error;
        const run = await startCliFallbackRun(input, error);
        sendJson(response, 201, run);
      }
      return;
    }
    const run = await agent.run(input);
    saveRun(run);
    sendJson(response, 201, run);
    return;
  }

  if (request.method === "GET" && url.pathname === "/api/mg400/config") {
    sendJson(response, 200, await readMg400Config());
    return;
  }

  if (request.method === "POST" && url.pathname === "/api/mg400/config") {
    const payload = await readJsonBody(request);
    sendJson(response, 200, await writeMg400Config(payload));
    return;
  }

  if (request.method === "POST" && url.pathname === "/api/mg400/test") {
    const config = await readMg400Config();
    sendJson(response, 200, await Mg400ArmController.runCommand("test", { config }));
    return;
  }

  if (request.method === "GET" && url.pathname === "/api/mg400/status") {
    const config = await readMg400Config();
    sendJson(response, 200, await Mg400ArmController.runCommand("status", { config }));
    return;
  }

  if (request.method === "POST" && url.pathname === "/api/mg400/command") {
    const command = await readJsonBody(request);
    const config = await readMg400Config();
    sendJson(response, 200, await Mg400ArmController.runCommand("command", { config, command }));
    return;
  }

  if (request.method === "GET" && url.pathname === "/api/ethernet/info") {
    const name = url.searchParams.get("name") || "以太网";
    sendJson(response, 200, { ok: true, adapter: await getEthernetInfo(name) });
    return;
  }

  const runMatch = url.pathname.match(/^\/api\/runs\/([^/]+)$/);
  if (request.method === "GET" && runMatch) {
    const run = getRun(runMatch[1]);
    if (!run) {
      sendJson(response, 404, { error: "Run not found." });
      return;
    }

    sendJson(response, 200, run);
    return;
  }

  sendJson(response, 404, { error: "Route not found." });
}

const server = http.createServer((request, response) => {
  route(request, response).catch((error) => {
    const statusCode = error.statusCode || 500;
    sendJson(response, statusCode, {
      ok: false,
      error: error.message,
      details: error.details
    });
  });
});

server.listen(port, () => {
  console.log(`Debugging Bench Agent listening on http://localhost:${port}`);
});

async function startServiceBackedRun(input) {
  const run = createBenchRun(input);
  run.serviceMode = true;
  transition(run, AgentState.PREPARING, "Parsed input; creating VLM service task.");
  run.modelInputYaml = buildModelInputYaml(input);
  run.modelInputYamlFile = await writeModelInputYamlFile({
    runId: run.runId,
    yaml: run.modelInputYaml
  });
  run.vlmAgentCase = await serviceCaseAdapter.adapt({ runId: run.runId, input });
  run.vlmService = await serviceRunner.createRun({
    run_id: run.runId,
    task_file: run.vlmAgentCase.taskFile,
    name: run.runId,
    workspace: serviceWorkspace(input),
    max_steps: serviceRunner.maxSteps,
    ...(serviceRunner.envFile ? { env_file: serviceRunner.envFile } : {})
  });
  appendRunEvent(run.runId, "node.vlm_forwarded", {
    serviceUrl: serviceRunner.baseUrl,
    serviceStatus: run.vlmService.status,
    taskFile: run.vlmAgentCase.taskFile
  });
  saveRun(run);
  finalizeServiceBackedRun(run.runId).catch((error) => {
    const stored = getRun(run.runId);
    if (stored) {
      stored.error = error.message;
      transition(stored, AgentState.REPORTING, `Service-backed run failed: ${error.message}`);
      appendRunEvent(run.runId, "node.failed", {
        error: error.message,
        details: error.details || null
      });
    }
  });
  return run;
}

async function startCliFallbackRun(input, serviceError) {
  const run = await agent.run(input);
  run.serviceMode = false;
  run.vlmServiceFallback = {
    attempted: true,
    serviceUrl: serviceRunner?.baseUrl || null,
    reason: serviceError.message,
    details: serviceError.details || null
  };
  saveRun(run);
  appendRunEvent(run.runId, "node.vlm_service_fallback", run.vlmServiceFallback);
  return run;
}

async function finalizeServiceBackedRun(runId) {
  const run = getRun(runId);
  if (!run) return;
  const completed = await serviceRunner.waitForRun(runId);
  run.vlmService = completed;
  if (completed.status !== "succeeded") {
    run.error = completed.error || `VLM service status: ${completed.status}`;
    transition(run, AgentState.REPORTING, run.error);
    appendRunEvent(runId, "node.failed", { error: run.error, service: completed });
    return;
  }

  const finalAnswer = completed.final_answer || null;
  const pixel = normalizePixel(finalAnswer?.pixel);
  run.vlmObservation = buildVlmObservation({ input: run.input, service: completed, finalAnswer, pixel });
  run.modelOutput = buildModelOutput({ service: completed, finalAnswer, pixel });
  run.plan = buildPlan({
    input: run.input,
    ragEvidence: run.ragEvidence || [],
    vlmObservation: run.vlmObservation,
    modelOutput: run.modelOutput
  });
  appendRunEvent(runId, "node.plan_created", { plan: run.plan });

  transition(run, AgentState.EXECUTING, "VLM service completed; executing MG400 flow.");
  const blockedLocations = new Map();
  for (const step of run.plan.steps) {
    if (step.kind === StepKind.ARM_MOTION || step.kind === StepKind.VISUAL_CAPTURE) {
      const armResult = await serviceArmController.execute(step);
      run.execution.arm.push(armResult);
      appendRunEvent(runId, "robot.action_finished", { step, result: armResult });
      await serviceRunner.postObservation(runId, {
        type: "robot.observation",
        source: "node-mg400-gateway",
        payload: armResult
      });
      if (armResult.status === "BLOCKED" && step.targetLocationId) {
        blockedLocations.set(step.targetLocationId, armResult);
      }
    }
    if (step.kind === StepKind.EQUIPMENT_MEASUREMENT) {
      const blockedArm = blockedLocations.get(step.locationId);
      if (blockedArm) {
        run.execution.equipment.push({
          stepId: step.id,
          status: "SKIPPED",
          instrument: step.instrument,
          signal: step.signal,
          locationId: step.locationId,
          pass: false,
          reason: blockedArm.message || blockedArm.error || "Arm motion did not reach the requested measurement point."
        });
      } else {
        run.execution.equipment.push(await serviceEquipmentController.measure(step));
      }
      appendRunEvent(runId, "equipment.measurement_finished", {
        step,
        result: run.execution.equipment.at(-1)
      });
    }
  }

  transition(run, AgentState.REPORTING, "Execution completed; generating report.");
  run.report = serviceReportGenerator.create({
    run,
    ragEvidence: run.ragEvidence || [],
    vlmObservation: run.vlmObservation,
    measurements: run.execution.equipment
  });
  appendRunEvent(runId, "node.completed", { run });
  updateRun(runId, run);
}

function serviceWorkspace(input) {
  const caseId = sanitizeSegment(input.caseId) || sanitizeSegment(input.command) || "inputdemo-case";
  return `${process.cwd()}\\output\\vlm-agent-runs\\${caseId}`;
}

function buildVlmObservation({ input, service, finalAnswer, pixel }) {
  const locationId = finalAnswer?.tp_id || finalAnswer?.test_point || "vlm-target";
  return {
    model: process.env.VLM_MODEL || "vlm-agent-service",
    provider: "debugging-agent-v2-service",
    realModelService: true,
    imageRef: input.cameraImage?.name || input.visualCapture?.imageRef || null,
    promptMode: input.prompt?.mode || "text",
    workspace: serviceWorkspace(input),
    runDir: service.run_dir,
    summaryPath: service.summary_path,
    stoppedReason: service.stopped_reason,
    finalAnswer,
    pixel: pixel ? { x: pixel[0], y: pixel[1] } : null,
    confidence: Number.isFinite(Number(finalAnswer?.confidence)) ? Number(finalAnswer.confidence) : null,
    modelInputSummary: {
      attachmentCount: input.modelAttachments.length,
      schematicCount: input.schematicDiagrams.length,
      bitImageCount: input.bitImages.length,
      attachmentNames: input.modelAttachments.map((item) => item.name)
    },
    benchOverview: {
      boardDetected: Boolean(pixel),
      instrumentsDetected: [],
      armReachableZones: pixel ? ["vlm-localized-test-point"] : []
    },
    locations: [
      {
        id: locationId,
        label: finalAnswer?.tp_id || finalAnswer?.label || "VLM localized test point",
        pixel: pixel ? { x: pixel[0], y: pixel[1] } : null,
        cameraView: finalAnswer?.camera_view || null
      }
    ],
    recommendedMeasurements: [
      {
        signal: finalAnswer?.signal || finalAnswer?.tp_id || "TARGET_SIGNAL",
        locationId,
        instrument: "oscilloscope",
        expectedRange: finalAnswer?.expected_range || "See real VLM final_answer.",
        reason: finalAnswer?.reason || `Real VLM agent result for: ${input.command}`
      }
    ]
  };
}

function buildModelOutput({ service, finalAnswer, pixel }) {
  return {
    model: process.env.VLM_MODEL || "vlm-agent-service",
    provider: "debugging-agent-v2-service",
    realModelService: true,
    inputFormat: "debugging-agent-v2-task",
    testPoint: finalAnswer?.tp_id || finalAnswer?.test_point || null,
    confidence: Number.isFinite(Number(finalAnswer?.confidence)) ? Number(finalAnswer.confidence) : null,
    pixel: pixel ? { x: pixel[0], y: pixel[1] } : null,
    mg400Pose: normalizePose(finalAnswer?.mg400Pose || finalAnswer?.mg400_pose || finalAnswer?.pose),
    finalAnswer,
    summaryPath: service.summary_path,
    runDir: service.run_dir,
    workspace: service.run_dir ? service.run_dir.replace(/\\runs\\.*$/, "") : null,
    reason: "VLM service returned localization output."
  };
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

function sanitizeSegment(value) {
  return String(value || "")
    .trim()
    .replace(/[<>:"/\\|?*\x00-\x1F]/g, "_")
    .replace(/\s+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 120);
}

function isServiceUnavailable(error) {
  const category = error.details?.category;
  if (category === "vlm-agent-service-unavailable") return true;
  return /fetch failed|ECONNREFUSED|ECONNRESET|ENOTFOUND|ETIMEDOUT|network/i.test([
    error.message,
    error.cause?.message,
    error.cause?.code
  ].filter(Boolean).join("\n"));
}
