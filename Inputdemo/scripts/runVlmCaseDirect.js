#!/usr/bin/env node
import { access, readFile, stat } from "node:fs/promises";
import path from "node:path";
import { defaultVlmAgentCasesDir, defaultVlmAgentRunsDir } from "../src/adapters/defaultPaths.js";
import { VlmAgentServiceRunner } from "../src/adapters/vlmAgentServiceRunner.js";

const args = parseArgs(process.argv.slice(2));

if (args.help) {
  printUsage();
  process.exit(0);
}

const caseId = args.case || args._[0] || "case-0526";
const taskFile = path.resolve(args.task || path.join(defaultVlmAgentCasesDir(), caseId, "task.yaml"));
const workspace = path.resolve(args.workspace || path.join(defaultVlmAgentRunsDir(), sanitizeSegment(caseId)));
const runId = args.runId || `direct_${sanitizeSegment(caseId)}_${timestampSlug()}`;
const runner = new VlmAgentServiceRunner({
  baseUrl: args.serviceUrl,
  envFile: args.envFile,
  maxSteps: numberArg(args.maxSteps, "max-steps") ?? 160,
  timeoutMs: numberArg(args.timeoutMs, "timeout-ms") ?? 60 * 60 * 1000,
  pollIntervalMs: numberArg(args.pollMs, "poll-ms") ?? 1000
});

await assertReadable(taskFile, "task file");

console.log("Direct VLM case run");
console.log(`  case      : ${caseId}`);
console.log(`  service   : ${runner.baseUrl}`);
console.log(`  task_file : ${taskFile}`);
console.log(`  workspace : ${workspace}`);
console.log(`  run_id    : ${runId}`);
console.log("");

let created;
try {
  const health = await runner.health();
  console.log(`service health: ${JSON.stringify(health)}`);

  created = await runner.createRun({
    run_id: runId,
    task_file: taskFile,
    name: runId,
    workspace,
    max_steps: runner.maxSteps,
    ...(runner.envFile ? { env_file: runner.envFile } : {}),
    ...(args.model ? { model: args.model } : {}),
    ...(args.baseUrl ? { base_url: args.baseUrl } : {}),
    ...(args.noNativeTools ? { no_native_tools: true } : {})
  });
} catch (error) {
  printError("failed to start direct VLM run", error);
  process.exit(1);
}

console.log(`created: status=${created.status} event_count=${created.event_count}`);

let seq = 0;
let latest = created;
const events = [];
try {
  latest = await waitForRunWithEvents(runner, runId, {
    since: seq,
    onEvent: (event) => {
      seq = Math.max(seq, Number(event.seq) || seq);
      events.push(event);
      printEvent(event);
    }
  });
} catch (error) {
  printError("direct VLM run polling failed", error);
  process.exit(1);
}

console.log("");
console.log("final status");
console.log(JSON.stringify({
  run_id: latest.run_id,
  status: latest.status,
  run_dir: latest.run_dir,
  summary_path: latest.summary_path,
  stopped_reason: latest.stopped_reason,
  error: latest.error,
  final_answer: latest.final_answer
}, null, 2));

const validation = await validateAgentOutputs({ latest, events, workspace });
console.log("");
console.log("agent output validation");
console.log(JSON.stringify(validation, null, 2));

process.exit(latest.status === "succeeded" && validation.ok ? 0 : 1);

async function waitForRunWithEvents(serviceRunner, id, { since = 0, onEvent } = {}) {
  const deadline = Date.now() + serviceRunner.timeoutMs;
  let eventSeq = since;
  while (Date.now() < deadline) {
    const eventPayload = await serviceRunner.getEvents(id, eventSeq);
    for (const event of eventPayload.events || []) {
      eventSeq = Math.max(eventSeq, Number(event.seq) || eventSeq);
      onEvent?.(event);
    }

    const run = await serviceRunner.getRun(id);
    if (["succeeded", "failed", "cancelled"].includes(run.status)) {
      const finalEvents = await serviceRunner.getEvents(id, eventSeq);
      for (const event of finalEvents.events || []) {
        eventSeq = Math.max(eventSeq, Number(event.seq) || eventSeq);
        onEvent?.(event);
      }
      return run;
    }
    await sleep(serviceRunner.pollIntervalMs);
  }

  const latestRun = await serviceRunner.getRun(id);
  if (["succeeded", "failed", "cancelled"].includes(latestRun.status)) return latestRun;
  throw new Error(`Timed out after ${serviceRunner.timeoutMs}ms waiting for ${id}; latest status=${latestRun.status}.`);
}

function printEvent(event) {
  const payload = event.payload || {};
  if (event.type === "agent.step") {
    const call = payload.tool_call || {};
    const result = payload.tool_result || {};
    const resultText = String(result.text || "").replace(/\s+/g, " ").trim();
    const clipped = resultText.length > 240 ? `${resultText.slice(0, 240)}...` : resultText;
    console.log(
      `[${String(event.seq).padStart(3, "0")}] agent.step ` +
      `step=${payload.step} tool=${call.name || "-"} ok=${result.ok} final=${Boolean(result.is_final)}` +
      (clipped ? ` result="${clipped}"` : "")
    );
    return;
  }
  const summary = {
    ...(payload.message ? { message: payload.message } : {}),
    ...(payload.tool ? { tool: payload.tool } : {}),
    ...(payload.error ? { error: payload.error } : {}),
    ...(payload.stopped_reason ? { stopped_reason: payload.stopped_reason } : {}),
    ...(payload.run_dir ? { run_dir: payload.run_dir } : {}),
    ...(payload.summary_path ? { summary_path: payload.summary_path } : {}),
    ...(payload.final_answer ? { final_answer: payload.final_answer } : {})
  };
  const detail = Object.keys(summary).length ? ` ${JSON.stringify(summary)}` : "";
  console.log(`[${String(event.seq).padStart(3, "0")}] ${event.type}${detail}`);
}

async function validateAgentOutputs({ latest, events, workspace }) {
  const checks = [];
  const fail = (name, detail = null) => checks.push({ name, ok: false, detail });
  const pass = (name, detail = null) => checks.push({ name, ok: true, detail });

  const stepEvents = events.filter((event) => event.type === "agent.step");
  if (stepEvents.length > 0) pass("service emitted agent.step events", { count: stepEvents.length });
  else fail("service emitted agent.step events", { count: 0 });

  for (const event of stepEvents) {
    const call = event.payload?.tool_call;
    const result = event.payload?.tool_result;
    const label = `event seq ${event.seq} step ${event.payload?.step}`;
    if (call?.name) pass(`${label} has real tool call`, { tool: call.name });
    else fail(`${label} has real tool call`, call || null);
    if (result && typeof result.ok === "boolean") pass(`${label} has real tool result`, { ok: result.ok });
    else fail(`${label} has real tool result`, result || null);
    if (result?.ok === false) fail(`${label} tool result is OK`, {
      tool: call?.name || null,
      text: truncateForReport(result.text)
    });
  }

  if (!latest.summary_path) {
    fail("summary.json path exists in service status");
  } else {
    pass("summary.json path exists in service status", latest.summary_path);
    try {
      const summary = JSON.parse(await readFile(latest.summary_path, "utf8"));
      validateSummary(summary, checks);
    } catch (error) {
      fail("summary.json is readable JSON", error.message);
    }
  }

  const requiredArtifacts = requiredCaseArtifacts();
  for (const item of requiredArtifacts) {
    const artifactPath = path.join(workspace, item);
    try {
      const info = await stat(artifactPath);
      pass(`artifact exists: ${item}`, { bytes: info.size });
    } catch {
      fail(`artifact exists: ${item}`, artifactPath);
    }
  }

  return {
    ok: checks.every((check) => check.ok),
    checks
  };
}

function validateSummary(summary, checks) {
  const pass = (name, detail = null) => checks.push({ name, ok: true, detail });
  const fail = (name, detail = null) => checks.push({ name, ok: false, detail });
  const steps = Array.isArray(summary.steps) ? summary.steps : [];
  if (steps.length > 0) pass("summary has steps", { count: steps.length });
  else fail("summary has steps", { count: 0 });

  for (const step of steps) {
    const calls = Array.isArray(step.tool_calls) ? step.tool_calls : [];
    const results = Array.isArray(step.tool_results) ? step.tool_results : [];
    const label = `summary step ${step.index}`;
    if (calls.length > 0) pass(`${label} has tool_calls`, calls.map((call) => call.name));
    else fail(`${label} has tool_calls`, step.assistant_content || null);
    if (results.length === calls.length) pass(`${label} has matching tool_results`, {
      calls: calls.length,
      results: results.length
    });
    else fail(`${label} has matching tool_results`, { calls: calls.length, results: results.length });
    for (let i = 0; i < results.length; i += 1) {
      const result = results[i];
      const call = calls[i];
      if (result.ok === true) pass(`${label} tool ${call?.name || i} result ok`);
      else fail(`${label} tool ${call?.name || i} result ok`, truncateForReport(result.text));
    }
  }

  const lastStep = steps.at(-1);
  const lastCalls = Array.isArray(lastStep?.tool_calls) ? lastStep.tool_calls : [];
  const hasFinish = lastCalls.some((call) => call.name === "finish")
    || stepHasFinalResult(lastStep);
  if (hasFinish) pass("summary ends with finish output");
  else fail("summary ends with finish output", {
    stopped_reason: summary.stopped_reason,
    last_step: lastStep?.index ?? null
  });

  if (summary.final_answer && typeof summary.final_answer === "object") {
    pass("summary has final_answer", summary.final_answer);
  } else {
    fail("summary has final_answer", summary.final_answer ?? null);
  }
}

function stepHasFinalResult(step) {
  const results = Array.isArray(step?.tool_results) ? step.tool_results : [];
  return results.some((result) => result.is_final);
}

function requiredCaseArtifacts() {
  if (args.requiredArtifact) {
    const list = Array.isArray(args.requiredArtifact) ? args.requiredArtifact : [args.requiredArtifact];
    return list.flatMap((item) => String(item).split(",")).map((item) => item.trim()).filter(Boolean);
  }
  if (args.noDefaultArtifacts) return [];
  return [
    "debug/case10_signal_to_tp.json",
    "debug/case10_target_tp_pdf_search.json",
    "debug/case10_assembly_drawing_tp_marked.png",
    "debug/case10_target_tp_work_roi.png",
    "debug/step03_mapping.json",
    "debug/step04_roi_crop.png",
    "debug/step08_result.json",
    "debug/step08_final_tp.png"
  ];
}

function truncateForReport(value, max = 500) {
  const text = String(value || "");
  return text.length > max ? `${text.slice(0, max)}...` : text;
}

async function assertReadable(filePath, label) {
  try {
    await access(filePath);
  } catch {
    throw new Error(`${label} is not readable: ${filePath}`);
  }
}

function parseArgs(argv) {
  const out = { _: [] };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (!arg.startsWith("--")) {
      out._.push(arg);
      continue;
    }
    const [rawKey, inlineValue] = arg.slice(2).split("=", 2);
    const key = camelCase(rawKey);
    if (["help", "noNativeTools", "noDefaultArtifacts"].includes(key)) {
      out[key] = true;
      continue;
    }
    const value = inlineValue ?? argv[++i];
    if (value === undefined) throw new Error(`Missing value for --${rawKey}`);
    if (key === "requiredArtifact" && out[key] !== undefined) {
      out[key] = Array.isArray(out[key]) ? [...out[key], value] : [out[key], value];
    } else {
      out[key] = value;
    }
  }
  return out;
}

function camelCase(value) {
  return value.replace(/-([a-z])/g, (_, char) => char.toUpperCase());
}

function numberArg(value, name) {
  if (value === undefined) return null;
  const number = Number(value);
  if (!Number.isFinite(number)) throw new Error(`--${name} must be a number.`);
  return number;
}

function sanitizeSegment(value) {
  return String(value || "")
    .trim()
    .replace(/[<>:"/\\|?*\x00-\x1F]/g, "_")
    .replace(/\s+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 80) || "case";
}

function timestampSlug() {
  const now = new Date();
  const pad = (value) => String(value).padStart(2, "0");
  return [
    now.getFullYear(),
    pad(now.getMonth() + 1),
    pad(now.getDate()),
    "-",
    pad(now.getHours()),
    pad(now.getMinutes()),
    pad(now.getSeconds())
  ].join("");
}

function printError(title, error) {
  console.error(`\n${title}: ${error.message}`);
  if (error.details) {
    console.error(JSON.stringify(error.details, null, 2));
  }
}

function printUsage() {
  console.log(`Usage:
  npm --prefix Inputdemo run vlm:case -- [case-id]
  npm --prefix Inputdemo run vlm:case -- --case case-0526
  npm --prefix Inputdemo run vlm:case -- --task "C:\\path\\to\\task.yaml"

Options:
  --case <id>             Case folder under Debugging-agent-v2/data/cases (default: case-0526)
  --task <path>           Task YAML path; overrides --case
  --workspace <path>      VLM output workspace (default: workspace/vlm-agent-runs/<case>)
  --service-url <url>     VLM service URL (default: VLM_AGENT_SERVICE_URL or http://127.0.0.1:8000)
  --env-file <path>       .env path passed to the VLM service
  --run-id <id>           Explicit service run id
  --max-steps <n>         Max VLM agent steps (default: 160)
  --timeout-ms <n>        Poll timeout (default: 3600000)
  --poll-ms <n>           Poll interval (default: 1000)
  --model <name>          Override VLM_MODEL for this run
  --base-url <url>        Override VLM_BASE_URL for this run
  --no-native-tools       Ask the service to disable native tool mode
  --required-artifact <p> Require an agent-created artifact under the workspace; repeat or comma-separate
  --no-default-artifacts  Only validate events/summary, not the default case10 artifact checklist
`);
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
