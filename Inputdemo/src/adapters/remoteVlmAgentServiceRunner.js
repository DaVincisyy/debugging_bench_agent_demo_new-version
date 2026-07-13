/**
 * RemoteVlmAgentServiceRunner — calls a remote VLM Agent Service via HTTP.
 *
 * Use this when the Debugging-agent-v2 Python service runs on a separate
 * server (managed by the AI/model team). Case files (images, PDFs, YAML)
 * are uploaded via multipart form to ``POST /v1/runs/upload``.
 *
 * Environment:
 *   VLM_AGENT_SERVICE_URL   — remote service base URL (e.g. http://10.0.0.5:8000)
 *   VLM_AGENT_MAX_STEPS     — max agent tool-use steps (default 80)
 *   VLM_AGENT_TIMEOUT_MS    — total wait timeout (default 60 min)
 *   VLM_AGENT_SERVICE_POLL_INTERVAL_MS — poll interval (default 1000 ms)
 */

import { readFile, readdir } from "node:fs/promises";
import path from "node:path";
import { defaultVlmAgentRunsDir } from "./defaultPaths.js";

const DEFAULT_SERVICE_URL = "http://127.0.0.1:8000";

export class RemoteVlmAgentServiceRunner {
  constructor({
    baseUrl = process.env.VLM_AGENT_SERVICE_URL || DEFAULT_SERVICE_URL,
    maxSteps = Number(process.env.VLM_AGENT_MAX_STEPS || 80),
    timeoutMs = Number(process.env.VLM_AGENT_TIMEOUT_MS || 60 * 60 * 1000),
    pollIntervalMs = Number(process.env.VLM_AGENT_SERVICE_POLL_INTERVAL_MS || 1000),
    runsRoot = defaultVlmAgentRunsDir(),
    skipFileUpload = false,
    onProgress = null,
  } = {}) {
    this.baseUrl = baseUrl.replace(/\/+$/, "");
    this.maxSteps = maxSteps;
    this.timeoutMs = timeoutMs;
    this.pollIntervalMs = pollIntervalMs;
    this.runsRoot = runsRoot;
    // For same-machine use: when set, only the task_file path is sent
    // (the service finds it locally — compatible with the original /v1/runs endpoint).
    this.skipFileUpload = skipFileUpload;
    this.onProgress = onProgress;
  }

  /** One-liner connectivity check. */
  async health() {
    try {
      const resp = await fetch(`${this.baseUrl}/health`);
      return await resp.json();
    } catch (cause) {
      throw Object.assign(
        new Error(`VLM Agent Service unreachable at ${this.baseUrl}: ${cause.message}`),
        { details: { category: "vlm-agent-service-unavailable", serviceUrl: this.baseUrl } }
      );
    }
  }

  /**
   * Main entry — matches the interface expected by RealVlmAgentClient.
   */
  async run({ input, vlmAgentCase }) {
    if (!vlmAgentCase?.taskFile) {
      throw new Error("VLM agent task file is missing.");
    }

    // Same-machine shortcut: just POST /v1/runs with a local path
    if (this.skipFileUpload) {
      return this.runWithLocalPath({ input, vlmAgentCase });
    }

    // Remote mode: upload all case files
    const caseDir = vlmAgentCase.caseDir || path.dirname(vlmAgentCase.taskFile);
    return this.runWithUpload({ input, vlmAgentCase, caseDir });
  }

  // ------------------------------------------------------------------ //
  //  Remote-specific: upload + create (used by server.js)
  // ------------------------------------------------------------------ //

  /**
   * Upload case files and create a run on the remote server.
   * Returns the run snapshot immediately (status: "queued").
   * The caller then uses `waitForRun` to poll for completion.
   */
  async createRunRemote({ vlmAgentCase, input, runId, workspace }) {
    const caseDir = vlmAgentCase.caseDir || path.dirname(vlmAgentCase.taskFile);

    // Build multipart form
    const form = new FormData();

    // 1. Attach task YAML
    const taskYamlContent = await readFile(vlmAgentCase.taskFile);
    form.append("task_yaml", new Blob([taskYamlContent], { type: "application/x-yaml" }), "task.yaml");

    // 2. Attach all other files in the case directory
    const entries = await readdir(caseDir, { withFileTypes: true });
    let fileCount = 0;
    for (const entry of entries) {
      if (!entry.isFile()) continue;
      if (entry.name === "task.yaml") continue;
      const filePath = path.join(caseDir, entry.name);
      const content = await readFile(filePath);
      form.append("files", new Blob([content]), entry.name);
      fileCount += 1;
    }

    // 3. Form fields
    const caseId = (input && input.caseId) ? String(input.caseId).trim() : "";
    form.append("run_id", runId);
    if (caseId) form.append("case_id", caseId);
    form.append("name", runId);
    form.append("workspace", workspace);
    form.append("max_steps", String(this.maxSteps));

    if (typeof this.onProgress === "function") {
      try { this.onProgress({ stage: "uploading", caseDir, fileCount: fileCount + 1 }); } catch {}
    }

    // 4. POST to server
    const response = await fetch(`${this.baseUrl}/v1/runs/upload`, {
      method: "POST",
      body: form,
    });

    if (!response.ok) {
      const body = await response.text();
      throw Object.assign(
        new Error(`VLM Agent Service upload failed: HTTP ${response.status} — ${body}`),
        { details: { category: "vlm-agent-service", status: response.status, body } }
      );
    }

    const runSnapshot = await response.json();
    return {
      run_id: runSnapshot.run_id,
      status: runSnapshot.status,
      run_dir: runSnapshot.run_dir,
      summary_path: runSnapshot.summary_path,
    };
  }

  /**
   * Create a split (planner + parallel children) run on the remote server.
   * Mirrors VlmAgentServiceRunner.createSplitRun for interface compatibility.
   */
  async createSplitRun(payload) {
    try {
      const resp = await this._fetchWithRetry(() =>
        fetch(`${this.baseUrl}/v1/runs/split`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        })
      );
      if (!resp.ok) {
        const body = await resp.text();
        throw Object.assign(
          new Error(`Failed to create VLM agent split run: HTTP ${resp.status} — ${body}`),
          { details: { category: "vlm-agent-service", status: resp.status, body } }
        );
      }
      return resp.json();
    } catch (cause) {
      if (cause.details) throw cause;
      throw Object.assign(
        new Error(`Failed to create VLM agent split run: ${cause.message}`),
        {
          details: {
            category: "vlm-agent-service-unavailable",
            action: "Start the VLM agent service or set VLM_AGENT_SERVICE_URL to the server-hosted service.",
            serviceUrl: this.baseUrl,
          },
        }
      );
    }
  }

  // ------------------------------------------------------------------ //
  //  waitForRun — matches VlmAgentServiceRunner interface (server.js)
  // ------------------------------------------------------------------ //

  /**
   * Poll the remote server until the run completes, optionally streaming
   * events via ``onEvent``. Called by server.js after createRunRemote.
   */
  async _fetchWithRetry(fn, maxRetries = 5, baseDelayMs = 500) {
    let lastError;
    for (let attempt = 0; attempt < maxRetries; attempt++) {
      try {
        return await fn();
      } catch (error) {
        lastError = error;
        const isNetworkError = /fetch failed|ECONNREFUSED|ECONNRESET|ETIMEDOUT|ENOTFOUND|network/i.test(
          error.message || ""
        );
        if (!isNetworkError || attempt >= maxRetries - 1) throw error;
        const delay = baseDelayMs * Math.pow(2, attempt); // exponential backoff
        console.warn(`[remote-vlm] fetch attempt ${attempt + 1}/${maxRetries} failed: ${error.message}. Retrying in ${delay}ms...`);
        await sleep(delay);
      }
    }
    throw lastError;
  }

  async cancelRun(runId) {
    try {
      const resp = await fetch(
        `${this.baseUrl}/v1/runs/${encodeURIComponent(runId)}/cancel`,
        { method: "POST" }
      );
      return resp.ok;
    } catch {
      return false;
    }
  }

  async waitForRun(runId, { onEvent } = {}) {
    const deadline = Date.now() + this.timeoutMs;
    let eventSeq = 0;

    while (Date.now() < deadline) {
      try {
        if (onEvent) {
          const eventsPayload = await this._fetchWithRetry(() => this.getEvents(runId, eventSeq));
          for (const event of eventsPayload.events || []) {
            eventSeq = Math.max(eventSeq, Number(event.seq) || eventSeq);
            try { onEvent(event); } catch { /* ignore */ }
          }
        }
        const run = await this._fetchWithRetry(() => this.getRun(runId));
        if (["succeeded", "failed", "cancelled"].includes(run.status)) {
          if (onEvent) {
            const eventsPayload = await this._fetchWithRetry(() => this.getEvents(runId, eventSeq));
            for (const event of eventsPayload.events || []) {
              try { onEvent(event); } catch { /* ignore */ }
            }
          }
          return run;
        }
      } catch (error) {
        // Non-network errors or all retries exhausted — propagate
        const isNetworkError = /fetch failed|ECONNREFUSED|ECONNRESET|ETIMEDOUT|ENOTFOUND|network/i.test(
          error.message || ""
        );
        if (!isNetworkError) throw error;
        // For network errors after retries exhausted, log and continue polling
        console.warn(`[remote-vlm] polling retries exhausted for ${runId}: ${error.message}. Will retry next cycle.`);
      }
      await sleep(this.pollIntervalMs);
    }
    const latest = await this._fetchWithRetry(() => this.getRun(runId));
    if (["succeeded", "failed", "cancelled"].includes(latest.status)) return latest;
    throw Object.assign(
      new Error(`VLM Agent Service timed out after ${this.timeoutMs}ms.`),
      { details: { category: "vlm-agent-service-timeout", serviceUrl: this.baseUrl, runId } }
    );
  }

  /** Send an observation (e.g. robot action result) to the server. */
  async postObservation(runId, observation) {
    const resp = await fetch(
      `${this.baseUrl}/v1/runs/${encodeURIComponent(runId)}/observations`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(observation),
      }
    );
    if (!resp.ok) {
      console.warn(`postObservation failed: HTTP ${resp.status}`);
    }
  }

  // ------------------------------------------------------------------ //
  //  Same-machine path (backward compatible)
  // ------------------------------------------------------------------ //

  async runWithLocalPath({ input, vlmAgentCase }) {
    const runPayload = {
      task_file: vlmAgentCase.taskFile,
      run_id: vlmAgentCase.runId,
      name: sanitizeSegment(vlmAgentCase.runId || "inputdemo-run"),
      workspace: path.join(this.runsRoot, sanitizeSegment(input.caseId || "inputdemo-case")),
      max_steps: this.maxSteps,
    };
    const serviceRun = await this.createRun(runPayload);
    return this.waitAndCollect(serviceRun.run_id);
  }

  // ------------------------------------------------------------------ //
  //  Full remote upload flow
  // ------------------------------------------------------------------ //

  async runWithUpload({ input, vlmAgentCase, caseDir }) {
    this._notify("uploading", { caseDir });

    // Build multipart form with task YAML + all asset files
    const form = new FormData();

    // 1. Attach task YAML
    const taskYamlContent = await readFile(vlmAgentCase.taskFile);
    const taskYamlBlob = new Blob([taskYamlContent], { type: "application/x-yaml" });
    form.append("task_yaml", taskYamlBlob, "task.yaml");

    // 2. Attach all other files in the case directory
    const entries = await readdir(caseDir, { withFileTypes: true });
    let fileCount = 0;
    for (const entry of entries) {
      if (!entry.isFile()) continue;
      if (entry.name === "task.yaml") continue; // already attached
      const filePath = path.join(caseDir, entry.name);
      const content = await readFile(filePath);
      form.append("files", new Blob([content]), entry.name);
      fileCount += 1;
    }

    // 3. Form fields
    const runId = vlmAgentCase.runId || `run_${Date.now()}`;
    form.append("run_id", runId);
    if (vlmAgentCase.runId) form.append("name", sanitizeSegment(vlmAgentCase.runId));
    form.append("workspace", path.join(this.runsRoot, sanitizeSegment(input.caseId || "inputdemo-case")));
    form.append("max_steps", String(this.maxSteps));

    this._notify("uploading_files", { caseDir, fileCount: fileCount + 1 });

    // 4. POST to server
    let response;
    try {
      response = await fetch(`${this.baseUrl}/v1/runs/upload`, {
        method: "POST",
        body: form,
        // No Content-Type header — fetch sets it with boundary for multipart
      });
    } catch (cause) {
      throw Object.assign(
        new Error(`Failed to upload case to VLM Agent Service: ${cause.message}`),
        {
          details: {
            category: "vlm-agent-service-unavailable",
            serviceUrl: this.baseUrl,
            action: "Verify the VLM Agent Service is running on the target server.",
          },
        }
      );
    }

    if (!response.ok) {
      const body = await response.text();
      throw Object.assign(
        new Error(`VLM Agent Service upload returned HTTP ${response.status}: ${body}`),
        { details: { category: "vlm-agent-service", status: response.status, body } }
      );
    }

    const { run_id } = await response.json();
    this._notify("upload_complete", { run_id });

    return this.waitAndCollect(run_id);
  }

  // ------------------------------------------------------------------ //
  //  Polling
  // ------------------------------------------------------------------ //

  async waitAndCollect(runId) {
    const deadline = Date.now() + this.timeoutMs;

    while (Date.now() < deadline) {
      const run = await this.getRun(runId);
      this._notify("poll", { run_id: runId, status: run.status });

      if (["succeeded", "failed", "cancelled"].includes(run.status)) {
        return this.buildResult(run);
      }

      await sleep(this.pollIntervalMs);
    }

    // Timeout — check one more time
    const final = await this.getRun(runId);
    if (["succeeded", "failed", "cancelled"].includes(final.status)) {
      return this.buildResult(final);
    }

    throw Object.assign(
      new Error(`VLM Agent Service timed out after ${this.timeoutMs}ms.`),
      { details: { category: "vlm-agent-service-timeout", serviceUrl: this.baseUrl, runId } }
    );
  }

  async buildResult(run) {
    const finalAnswer = run.final_answer || {};
    const pixel = normalizePixel(finalAnswer?.pixel);

    return {
      ok: run.status === "succeeded",
      model: process.env.VLM_MODEL || "vlm-agent-remote",
      agentDir: null,
      serviceUrl: this.baseUrl,
      workspace: null,
      runDir: run.run_dir,
      summaryPath: run.summary_path,
      precheck: { ok: true, checked: false, serviceUrl: this.baseUrl },
      attempts: [],
      stdout: "",
      stderr: "",
      summary: run.final_answer ? { final_answer: run.final_answer } : null,
      finalAnswer,
      pixel,
      error: run.error || null,
      stoppedReason: run.stopped_reason || null,
    };
  }

  // ------------------------------------------------------------------ //
  //  Low-level HTTP helpers
  // ------------------------------------------------------------------ //

  async createRun(payload) {
    const resp = await fetch(`${this.baseUrl}/v1/runs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!resp.ok) {
      const detail = await resp.text();
      throw new Error(`Failed to create VLM run: HTTP ${resp.status} — ${detail}`);
    }
    return resp.json();
  }

  async getRun(runId) {
    const resp = await fetch(`${this.baseUrl}/v1/runs/${encodeURIComponent(runId)}`);
    if (!resp.ok) {
      const detail = await resp.text();
      throw new Error(`Failed to get VLM run ${runId}: HTTP ${resp.status} — ${detail}`);
    }
    return resp.json();
  }

  async getEvents(runId, since = 0) {
    const resp = await fetch(
      `${this.baseUrl}/v1/runs/${encodeURIComponent(runId)}/events?since=${encodeURIComponent(since)}`
    );
    if (!resp.ok) {
      const detail = await resp.text();
      throw new Error(`Failed to get VLM events ${runId}: HTTP ${resp.status} — ${detail}`);
    }
    return resp.json();
  }

  async downloadFile(runId, filePath) {
    const resp = await fetch(
      `${this.baseUrl}/v1/runs/${encodeURIComponent(runId)}/files/${encodeURIComponent(filePath)}`
    );
    if (!resp.ok) return null;
    return Buffer.from(await resp.arrayBuffer());
  }

  // ------------------------------------------------------------------ //
  //  Progress callback
  // ------------------------------------------------------------------ //

  _notify(stage, data) {
    if (typeof this.onProgress === "function") {
      try { this.onProgress({ stage, ...data }); } catch { /* ignore */ }
    }
  }
}

// ------------------------------------------------------------------ //
//  Helpers
// ------------------------------------------------------------------ //

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function sanitizeSegment(value) {
  return String(value || "")
    .trim()
    .replace(/[<>:"/\\|?*\x00-\x1F]/g, "_")
    .replace(/\s+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 120);
}

function normalizePixel(value) {
  if (!Array.isArray(value) || value.length !== 2) return null;
  const x = Number(value[0]);
  const y = Number(value[1]);
  if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
  return [Math.round(x), Math.round(y)];
}
