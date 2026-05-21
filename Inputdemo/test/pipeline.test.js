import test from "node:test";
import assert from "node:assert/strict";
import net from "node:net";
import http from "node:http";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { BenchAgent } from "../src/agent/benchAgent.js";
import { parseUserCommand } from "../src/agent/inputCore.js";
import { AgentState } from "../src/domain/states.js";
import { SimulationArmController } from "../src/adapters/simulationArmController.js";
import { MockEquipmentController } from "../src/adapters/mockEquipmentController.js";
import { MockLargeModelClient } from "../src/adapters/mockLargeModelClient.js";
import { MockRagRepository } from "../src/adapters/mockRagRepository.js";
import { MockVlmClient } from "../src/adapters/mockVlmClient.js";
import { ReportGenerator } from "../src/adapters/reportGenerator.js";
import { VlmAgentCaseAdapter } from "../src/adapters/vlmAgentCaseAdapter.js";
import { RealVlmAgentRunner } from "../src/adapters/realVlmAgentRunner.js";
import { VlmAgentServiceRunner } from "../src/adapters/vlmAgentServiceRunner.js";
import { evaluateMg400PoseReachability } from "../src/domain/mg400Reachability.js";

test("bench agent runs the mocked VLM-to-report flow with image and PDF model inputs", async () => {
  const simulationRequests = [];
  const simulationServer = net.createServer((socket) => {
    let buffer = "";
    socket.setEncoding("utf8");
    socket.on("data", (chunk) => {
      buffer += chunk;
      while (buffer.includes("\n")) {
        const newline = buffer.indexOf("\n");
        const line = buffer.slice(0, newline).trim();
        buffer = buffer.slice(newline + 1);
        simulationRequests.push(line);
        if (line === "GetPose()") {
          socket.write("0,{245.6000,-32.4000,78.2000,0,0,91.5000}\n");
          continue;
        }
        socket.write("0,{1}\n");
      }
    });
  });
  await new Promise((resolve) => simulationServer.listen(0, "127.0.0.1", resolve));
  const { port } = simulationServer.address();

  const agent = new BenchAgent({
    vlmClient: new MockVlmClient(),
    largeModelClient: new MockLargeModelClient(),
    ragRepository: new MockRagRepository(),
    armController: new SimulationArmController({ port }),
    equipmentController: new MockEquipmentController(),
    reportGenerator: new ReportGenerator(),
    vlmAgentCaseAdapter: new VlmAgentCaseAdapter({
      outputDir: path.join(tmpdir(), "inputdemo-vlm-agent-cases-test")
    })
  });
  try {
    const tinyPng = "data:image/png;base64,iVBORw0KGgo=";
    const tinyPdf = "data:application/pdf;base64,JVBERi0xLjQK";
    const input = parseUserCommand({
      Instruction: "Measure 5V rail ripple",
      Case_ID: "case-001",
      Operator: "demo-user",
      Camera_image: {
        name: "PCBA_IMG.jpg",
        type: "image/jpeg",
        size: 2048,
        dataUrl: tinyPng
      },
      Bit_image: [
        { name: "bit-map.png", type: "image/png", size: 1024, dataUrl: tinyPng },
        { name: "bit-map.pdf", type: "application/pdf", size: 4096, dataUrl: tinyPdf }
      ],
      Schematic_Diagram: [
        { name: "schematic.png", type: "image/png", size: 1024, dataUrl: tinyPng },
        { name: "schematic.pdf", type: "application/pdf", size: 8192, dataUrl: tinyPdf }
      ]
    });

    const run = await agent.run(input);

    assert.equal(run.input.instruction, "Measure 5V rail ripple");
    assert.equal(run.input.caseId, "case-001");
    assert.equal(run.input.operator, "demo-user");
    assert.equal(run.input.cameraImage.name, "PCBA_IMG.jpg");
    assert.equal(run.state, AgentState.REPORTING);
    assert.equal(run.vlmObservation.model, "mock-vlm-v0");
    assert.match(run.modelInputYaml, /Instruction: "Measure 5V rail ripple"/);
    assert.match(run.modelInputYaml, /Input_Assets:/);
    assert.match(run.modelInputYaml, /Model_Attachments:/);
    assert.match(run.modelInputYaml, /bit_pdf_count: 1/);
    assert.match(run.modelInputYaml, /schematic_pdf_count: 1/);
    assert.match(run.modelInputYaml, /media_kind: "pdf"/);
    assert.match(run.modelInputYaml, /Output_Requirement:/);
    assert.match(run.modelInputYamlFile, /output[\\/]yaml[\\/]run_/);
    assert.equal(await readFile(run.modelInputYamlFile, "utf8"), run.modelInputYaml);
    assert.match(run.vlmAgentCase.taskFile, /task\.yaml$/);
    assert.match(run.vlmAgentCase.caseDir, /case-001$/);
    assert.match(run.vlmAgentCase.taskYaml, /user_measurement_question:/);
    assert.match(run.vlmAgentCase.taskYaml, /front_board_photo: "PCBA_IMG.jpg"/);
    assert.match(run.vlmAgentCase.taskYaml, /assembly_drawing_pdf: "bit-map.pdf"/);
    assert.match(run.vlmAgentCase.taskYaml, /assembly_drawing: "bit-map.png"/);
    assert.match(run.vlmAgentCase.taskYaml, /schematic_pdf: "schematic.pdf"/);
    assert.match(run.vlmAgentCase.taskYaml, /schematic_image: "schematic.png"/);
    assert.equal(await readFile(run.vlmAgentCase.taskFile, "utf8"), run.vlmAgentCase.taskYaml);
    assert.equal(await readFile(path.join(run.vlmAgentCase.caseDir, "bit-map.pdf"), "utf8"), "%PDF-1.4\n");
    assert.equal(run.modelOutput.model, "mock-large-model-v0");
    assert.deepEqual(run.modelOutput.mg400Pose, {
      x: 245.6,
      y: -32.4,
      z: 78.2,
      r: 91.5
    });
    assert.deepEqual(run.plan.steps[0].targetPose, run.modelOutput.mg400Pose);
    assert.equal(run.plan.steps[0].reachabilityPrecheck.reachable, false);
    assert.equal(run.execution.arm[0].controller, "simulation");
    assert.equal(run.execution.arm[0].status, "BLOCKED");
    assert.equal(run.execution.arm[0].tcpCommand, null);
    assert.deepEqual(run.execution.arm[0].fallbackPose, {
      x: 245.6,
      y: -32.4,
      z: 95,
      r: 91.5
    });
    assert.deepEqual(simulationRequests, ["GetPose()"]);
    assert.equal(run.ragEvidence.length, 0);
    assert.equal(run.vlmObservation.modelInputSummary.attachmentCount, 4);
    assert.equal(run.vlmObservation.modelInputSummary.bitImageCount, 2);
    assert.equal(run.vlmObservation.modelInputSummary.schematicCount, 2);
    assert.equal(run.execution.equipment.length, 1);
    assert.equal(run.execution.equipment[0].status, "SKIPPED");
    assert.equal(run.report.measurements[0].pass, false);
    assert.match(run.report.findings.join("\n"), /reachability guard blocked/);
  } finally {
    await new Promise((resolve) => simulationServer.close(resolve));
  }
});

test("MG400 reachability guard adjusts workspace envelope violations", () => {
  const result = evaluateMg400PoseReachability({
    x: 500,
    y: 0,
    z: 100,
    r: 0
  });

  assert.equal(result.reachable, true);
  assert.equal(result.adjusted, true);
  assert.ok(result.pose.x < 500);
  assert.match(result.message, /Adjusted MG400 pose/);
});

test("real VLM runner prechecks Debugging-agent-v2 .env before launching", async () => {
  const root = path.join(tmpdir(), "inputdemo-vlm-precheck-missing-env");
  const agentDir = path.join(root, "Debugging-agent-v2");
  await mkdir(path.join(agentDir, "agent"), { recursive: true });
  await writeFile(path.join(agentDir, "agent", "cli.py"), "");
  await writeFile(path.join(agentDir, ".env"), "VLM_BASE_URL=https://example.test/v1\nVLM_MODEL=test-model\n");

  const runner = new RealVlmAgentRunner({
    agentDir,
    runsRoot: path.join(root, "runs"),
    connectivityPrecheck: false
  });

  await assert.rejects(
    () => runner.runOnce({
      input: { caseId: "case-001", command: "measure" },
      vlmAgentCase: { taskFile: path.join(root, "task.yaml"), runId: "run-001" }
    }),
    (error) => {
      assert.match(error.message, /Missing VLM_API_KEY/);
      assert.equal(error.details.category, "vlm-env");
      assert.equal(error.details.envFile, path.join(agentDir, ".env"));
      return true;
    }
  );
});

test("real VLM runner reports incomplete case-001 run directory when summary is missing", async () => {
  const root = path.join(tmpdir(), "inputdemo-vlm-summary-missing");
  const agentDir = path.join(root, "Debugging-agent-v2");
  const runsRoot = path.join(root, "vlm-agent-runs");
  const latestRunDir = "20260519-132412-run_mpc6u7gu_yya8q992";
  await mkdir(path.join(agentDir, "agent"), { recursive: true });
  await writeFile(path.join(agentDir, "agent", "cli.py"), "");
  await writeFile(
    path.join(agentDir, ".env"),
    "VLM_BASE_URL=https://example.test/v1\nVLM_API_KEY=test-key\nVLM_MODEL=test-model\n"
  );
  await mkdir(path.join(runsRoot, "case-001", "runs", latestRunDir), { recursive: true });
  await writeFile(path.join(runsRoot, "case-001", "runs", latestRunDir, "messages.init.jsonl"), "{}\n");

  class MissingSummaryRunner extends RealVlmAgentRunner {
    spawnAgent() {
      return Promise.resolve({
        latestRunDir,
        stdout: `[bold]run_dir[/bold] = ${path.join(runsRoot, "case-001", "runs", latestRunDir)}\n[bold]model[/bold] = test-model\n`,
        stderr: ""
      });
    }
  }

  const runner = new MissingSummaryRunner({
    agentDir,
    runsRoot,
    connectivityPrecheck: false
  });

  await assert.rejects(
    () => runner.runOnce({
      input: { caseId: "case-001", command: "measure" },
      vlmAgentCase: { taskFile: path.join(root, "task.yaml"), runId: "run-001" }
    }),
    (error) => {
      assert.match(error.message, /summary\.json was not readable/);
      assert.equal(error.details.category, "vlm-summary-missing");
      assert.match(error.details.runDir, /20260519-132412-run_mpc6u7gu_yya8q992$/);
      assert.match(error.details.action, /Inspect the real VLM run directory/);
      return true;
    }
  );
});

test("real VLM runner rebuilds the agent process after retryable network failures", async () => {
  class FlakyNetworkRunner extends RealVlmAgentRunner {
    constructor() {
      super({
        agentDir: "unused",
        connectivityPrecheck: false,
        networkRetries: 2,
        networkRetryBaseDelayMs: 0
      });
      this.calls = 0;
    }

    spawnAgent() {
      this.calls += 1;
      if (this.calls === 1) {
        const error = new Error("Server disconnected without sending a response.");
        error.details = {
          category: "vlm-api",
          stderr: "httpx.RemoteProtocolError: Server disconnected without sending a response."
        };
        return Promise.reject(error);
      }
      return Promise.resolve({
        latestRunDir: "retry-succeeded",
        stdout: "[bold]run_dir[/bold] = retry-succeeded\n",
        stderr: ""
      });
    }
  }

  const runner = new FlakyNetworkRunner();
  const result = await runner.spawnAgentWithNetworkRetries([], { workspace: "workspace" });

  assert.equal(runner.calls, 2);
  assert.equal(result.latestRunDir, "retry-succeeded");
  assert.equal(result.attempts.length, 1);
  assert.equal(result.attempts[0].category, "vlm-api");
});

test("VLM agent service runner calls the Python service protocol and reads summary output", async () => {
  const root = path.join(tmpdir(), "inputdemo-vlm-service-runner");
  const summaryPath = path.join(root, "runs", "summary.json");
  await mkdir(path.dirname(summaryPath), { recursive: true });
  await writeFile(summaryPath, JSON.stringify({
    final_answer: {
      tp_id: "TP12",
      pixel: [100.4, 205.6],
      confidence: 0.88
    },
    stopped_reason: "finish-tool-called"
  }), "utf8");

  const requests = [];
  const server = http.createServer((request, response) => {
    requests.push({ method: request.method, url: request.url });
    if (request.method === "POST" && request.url === "/v1/runs") {
      let body = "";
      request.setEncoding("utf8");
      request.on("data", (chunk) => {
        body += chunk;
      });
      request.on("end", () => {
        const payload = JSON.parse(body);
        response.writeHead(202, { "Content-Type": "application/json" });
        response.end(JSON.stringify({
          run_id: payload.run_id,
          status: "queued",
          event_count: 1
        }));
      });
      return;
    }
    if (request.method === "GET" && request.url === "/v1/runs/run-service-001") {
      response.writeHead(200, { "Content-Type": "application/json" });
      response.end(JSON.stringify({
        run_id: "run-service-001",
        status: "succeeded",
        run_dir: path.join(root, "runs"),
        summary_path: summaryPath,
        final_answer: {
          tp_id: "TP12",
          pixel: [100.4, 205.6],
          confidence: 0.88
        },
        stopped_reason: "finish-tool-called"
      }));
      return;
    }
    response.writeHead(404, { "Content-Type": "application/json" });
    response.end(JSON.stringify({ detail: "not found" }));
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address();

  try {
    const runner = new VlmAgentServiceRunner({
      baseUrl: `http://127.0.0.1:${port}`,
      runsRoot: path.join(root, "vlm-agent-runs"),
      pollIntervalMs: 1,
      timeoutMs: 1000
    });
    const result = await runner.runOnce({
      input: { caseId: "case-001", command: "measure" },
      vlmAgentCase: {
        runId: "run-service-001",
        taskFile: path.join(root, "task.yaml")
      }
    });

    assert.equal(result.ok, true);
    assert.equal(result.serviceUrl, `http://127.0.0.1:${port}`);
    assert.deepEqual(result.pixel, [100, 206]);
    assert.equal(result.finalAnswer.tp_id, "TP12");
    assert.equal(result.summary.stopped_reason, "finish-tool-called");
    assert.deepEqual(requests.map((item) => `${item.method} ${item.url}`), [
      "POST /v1/runs",
      "GET /v1/runs/run-service-001"
    ]);
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
});

test("VLM agent service runner falls back to the CLI runner when the service is unavailable", async () => {
  const fallbackCalls = [];
  const fallbackRunner = {
    run(payload) {
      fallbackCalls.push(payload);
      return Promise.resolve({
        ok: true,
        model: "cli-fallback",
        agentDir: "Debugging-agent-v2",
        workspace: "workspace",
        runDir: "workspace/runs/fallback",
        summaryPath: "workspace/runs/fallback/summary.json",
        precheck: { ok: true },
        attempts: [],
        summary: { final_answer: { tp_id: "TP12", pixel: [11, 22] } },
        finalAnswer: { tp_id: "TP12", pixel: [11, 22] },
        pixel: [11, 22]
      });
    }
  };

  const runner = new VlmAgentServiceRunner({
    baseUrl: "http://127.0.0.1:1",
    fallbackRunner,
    timeoutMs: 100,
    pollIntervalMs: 1
  });
  const payload = {
    input: { caseId: "case-001", command: "measure" },
    vlmAgentCase: {
      runId: "run-service-fallback",
      taskFile: path.join(tmpdir(), "task.yaml")
    }
  };

  const result = await runner.run(payload);

  assert.equal(result.ok, true);
  assert.equal(result.model, "cli-fallback");
  assert.equal(result.serviceFallback.attempted, true);
  assert.equal(result.serviceFallback.serviceUrl, "http://127.0.0.1:1");
  assert.equal(fallbackCalls.length, 1);
  assert.deepEqual(fallbackCalls[0], payload);
});
