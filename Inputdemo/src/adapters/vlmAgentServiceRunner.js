import { readFile } from "node:fs/promises";
import path from "node:path";
import { defaultVlmAgentRunsDir } from "./defaultPaths.js";

const DEFAULT_SERVICE_URL = "http://127.0.0.1:8000";

export class VlmAgentServiceRunner {
  constructor({
    baseUrl = process.env.VLM_AGENT_SERVICE_URL || DEFAULT_SERVICE_URL,
    envFile = process.env.VLM_ENV_FILE,
    runsRoot = defaultVlmAgentRunsDir(),
    maxSteps = Number(process.env.VLM_AGENT_MAX_STEPS || 80),
    timeoutMs = Number(process.env.VLM_AGENT_TIMEOUT_MS || 60 * 60 * 1000),
    pollIntervalMs = Number(process.env.VLM_AGENT_SERVICE_POLL_INTERVAL_MS || 1000),
    fallbackRunner = null
  } = {}) {
    this.baseUrl = baseUrl.replace(/\/+$/, "");
    this.envFile = envFile;
    this.runsRoot = runsRoot;
    this.maxSteps = maxSteps;
    this.timeoutMs = timeoutMs;
    this.pollIntervalMs = pollIntervalMs;
    this.fallbackRunner = fallbackRunner;
  }

  async run({ input, vlmAgentCase }) {
    try {
      return await this.runOnce({ input, vlmAgentCase });
    } catch (error) {
      if (!this.fallbackRunner || !isServiceUnavailable(error)) throw error;
      const fallbackResult = await this.fallbackRunner.run({ input, vlmAgentCase });
      return {
        ...fallbackResult,
        serviceFallback: {
          attempted: true,
          serviceUrl: this.baseUrl,
          reason: error.message,
          details: error.details || null
        }
      };
    }
  }

  async health() {
    return requestServiceJson(`${this.baseUrl}/health`, {}, "read VLM agent service health", this.baseUrl);
  }

  async runOnce({ input, vlmAgentCase }) {
    if (!vlmAgentCase?.taskFile) {
      throw new Error("VLM agent task file is missing; cannot call the VLM agent service.");
    }

    const caseId = sanitizeSegment(input.caseId) || sanitizeSegment(input.command) || "inputdemo-case";
    const workspace = path.join(this.runsRoot, caseId);
    const serviceRun = await this.createRun({
      run_id: vlmAgentCase.runId,
      task_file: vlmAgentCase.taskFile,
      name: sanitizeSegment(vlmAgentCase.runId || "inputdemo-run"),
      workspace,
      max_steps: this.maxSteps,
      ...(this.envFile ? { env_file: this.envFile } : {})
    });
    const completed = await this.waitForRun(serviceRun.run_id);
    if (completed.status !== "succeeded") {
      const error = new Error(`VLM agent service run ${completed.run_id} ended with status ${completed.status}.`);
      error.details = {
        category: "vlm-agent-service",
        action: "Inspect the Python VLM service run status/events and fix the reported agent error.",
        serviceUrl: this.baseUrl,
        runId: completed.run_id,
        runDir: completed.run_dir,
        summaryPath: completed.summary_path,
        error: completed.error,
        stoppedReason: completed.stopped_reason
      };
      throw error;
    }

    let summary = null;
    if (completed.summary_path) {
      summary = JSON.parse(await readFile(completed.summary_path, "utf8"));
    }
    const finalAnswer = completed.final_answer || summary?.final_answer || null;
    const pixel = normalizePixel(finalAnswer?.pixel);

    return {
      ok: Boolean(finalAnswer),
      model: process.env.VLM_MODEL || "vlm-agent-service",
      agentDir: null,
      serviceUrl: this.baseUrl,
      workspace,
      runDir: completed.run_dir,
      summaryPath: completed.summary_path,
      precheck: { ok: true, checked: false, serviceUrl: this.baseUrl },
      attempts: [],
      stdout: "",
      stderr: "",
      summary,
      finalAnswer,
      pixel
    };
  }

  async createRun(payload) {
    return requestServiceJson(`${this.baseUrl}/v1/runs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }, "create VLM agent service run", this.baseUrl);
  }

  async getRun(runId) {
    return requestServiceJson(
      `${this.baseUrl}/v1/runs/${encodeURIComponent(runId)}`,
      {},
      "read VLM agent service run",
      this.baseUrl
    );
  }

  async getEvents(runId, since = 0) {
    return requestServiceJson(
      `${this.baseUrl}/v1/runs/${encodeURIComponent(runId)}/events?since=${encodeURIComponent(since)}`,
      {},
      "read VLM agent service events",
      this.baseUrl
    );
  }

  async postObservation(runId, observation) {
    return requestServiceJson(`${this.baseUrl}/v1/runs/${encodeURIComponent(runId)}/observations`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(observation)
    }, "post VLM agent service observation", this.baseUrl);
  }

  async waitForRun(runId, { onEvent } = {}) {
    const deadline = Date.now() + this.timeoutMs;
    let eventSeq = 0;
    while (Date.now() < deadline) {
      if (onEvent) {
        const eventPayload = await this.getEvents(runId, eventSeq);
        for (const event of eventPayload.events || []) {
          eventSeq = Math.max(eventSeq, Number(event.seq) || eventSeq);
          onEvent(event);
        }
      }
      const run = await this.getRun(runId);
      if (["succeeded", "failed", "cancelled"].includes(run.status)) {
        if (onEvent) {
          const eventPayload = await this.getEvents(runId, eventSeq);
          for (const event of eventPayload.events || []) {
            eventSeq = Math.max(eventSeq, Number(event.seq) || eventSeq);
            onEvent(event);
          }
        }
        return run;
      }
      await sleep(this.pollIntervalMs);
    }
    const latest = await this.getRun(runId);
    if (["succeeded", "failed", "cancelled"].includes(latest.status)) return latest;
    const error = new Error(`VLM agent service timed out after ${this.timeoutMs}ms.`);
    error.details = {
      category: "vlm-agent-service-timeout",
      action: "Inspect the Python service logs/events, or increase VLM_AGENT_TIMEOUT_MS.",
      serviceUrl: this.baseUrl,
      runId
    };
    throw error;
  }
}

async function requestServiceJson(url, options, action, serviceUrl) {
  let response;
  try {
    response = await fetch(url, options);
  } catch (cause) {
    const error = new Error(`Failed to ${action}: ${cause.message}`);
    error.details = {
      category: "vlm-agent-service-unavailable",
      action: "Start Vlm agent/Debugging-agent-v2/agent/service.py with uvicorn, or allow Inputdemo to use the CLI fallback.",
      serviceUrl,
      cause: cause.message,
      code: cause.code || cause.cause?.code || null
    };
    error.cause = cause;
    throw error;
  }
  return readServiceJson(response, action);
}

async function readServiceJson(response, action) {
  let body = null;
  try {
    body = await response.clone().json();
  } catch {
    body = { detail: await response.text() };
  }
  if (!response.ok) {
    const error = new Error(`Failed to ${action}: HTTP ${response.status}.`);
    error.details = {
      category: "vlm-agent-service",
      status: response.status,
      response: body
    };
    throw error;
  }
  return body;
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

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function normalizePixel(value) {
  if (!Array.isArray(value) || value.length !== 2) return null;
  const x = Number(value[0]);
  const y = Number(value[1]);
  if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
  return [Math.round(x), Math.round(y)];
}

function sanitizeSegment(value) {
  return String(value || "")
    .trim()
    .replace(/[<>:"/\\|?*\x00-\x1F]/g, "_")
    .replace(/\s+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 120);
}
