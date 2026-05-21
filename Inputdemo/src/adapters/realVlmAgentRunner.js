import { spawn } from "node:child_process";
import { existsSync, readdirSync, statSync } from "node:fs";
import { readFile } from "node:fs/promises";
import path from "node:path";

const DEFAULT_AGENT_DIR = path.resolve("..", "Vlm agent", "Debugging-agent-v2");
const FALLBACK_AGENT_DIR = path.resolve("Vlm agent", "Debugging-agent-v2");

function getPythonCommand() {
  return process.env.PYTHON || process.env.PYTHON_EXE || "python";
}

function resolveAgentDir() {
  const configured = process.env.VLM_AGENT_DIR;
  if (configured) return path.resolve(configured);
  if (existsSync(path.join(DEFAULT_AGENT_DIR, "agent", "cli.py"))) return DEFAULT_AGENT_DIR;
  return FALLBACK_AGENT_DIR;
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

function extractModelName(stdout) {
  const match = String(stdout || "").match(/model(?:\]\s*=\s*|[^\S\r\n]*=[^\S\r\n]*)([^\r\n]+)/i);
  return match?.[1]?.replace(/\[[^\]]+\]/g, "").trim() || process.env.VLM_MODEL || "real-vlm-agent";
}

export class RealVlmAgentRunner {
  constructor({
    agentDir = resolveAgentDir(),
    runsRoot = path.join(process.cwd(), "output", "vlm-agent-runs"),
    maxSteps = Number(process.env.VLM_AGENT_MAX_STEPS || 80),
    timeoutMs = Number(process.env.VLM_AGENT_TIMEOUT_MS || 15 * 60 * 1000),
    connectivityPrecheck = process.env.VLM_AGENT_CONNECTIVITY_PRECHECK !== "false",
    precheckTimeoutMs = Number(process.env.VLM_AGENT_PRECHECK_TIMEOUT_MS || 8000),
    networkRetries = Number(process.env.VLM_AGENT_NETWORK_RETRIES || 3),
    networkRetryBaseDelayMs = Number(process.env.VLM_AGENT_NETWORK_RETRY_BASE_DELAY_MS || 2000),
    forceConnectionClose = process.env.VLM_AGENT_FORCE_CONNECTION_CLOSE === "true"
  } = {}) {
    this.agentDir = agentDir;
    this.runsRoot = runsRoot;
    this.maxSteps = maxSteps;
    this.timeoutMs = timeoutMs;
    this.connectivityPrecheck = connectivityPrecheck;
    this.precheckTimeoutMs = precheckTimeoutMs;
    this.networkRetries = Math.max(0, networkRetries);
    this.networkRetryBaseDelayMs = Math.max(0, networkRetryBaseDelayMs);
    this.forceConnectionClose = forceConnectionClose;
    this.pending = new Map();
  }

  run({ input, vlmAgentCase }) {
    if (!vlmAgentCase?.taskFile) {
      throw new Error("VLM agent task file is missing; cannot run the real VLM agent.");
    }

    const key = vlmAgentCase.taskFile;
    if (!this.pending.has(key)) {
      this.pending.set(key, this.runOnce({ input, vlmAgentCase }).finally(() => {
        this.pending.delete(key);
      }));
    }
    return this.pending.get(key);
  }

  async runOnce({ input, vlmAgentCase }) {
    const agentMain = path.join(this.agentDir, "agent", "cli.py");
    if (!existsSync(agentMain)) {
      throw new Error(`Real VLM agent was not found at ${this.agentDir}. Set VLM_AGENT_DIR to Debugging-agent-v2.`);
    }

    const caseId = sanitizeSegment(input.caseId) || sanitizeSegment(input.command) || sanitizeSegment(vlmAgentCase.runId) || "inputdemo-case";
    const workspace = path.join(this.runsRoot, caseId);
    const runName = sanitizeSegment(vlmAgentCase.runId || "inputdemo-run");
    const envFile = process.env.VLM_ENV_FILE || path.join(this.agentDir, ".env");
    const precheck = await this.precheck({ envFile });
    const args = [
      "-m",
      "agent",
      "--env",
      envFile,
      "--workspace",
      workspace,
      "--max-steps",
      String(this.maxSteps),
      "run",
      vlmAgentCase.taskFile,
      "--name",
      runName
    ];

    const result = await this.spawnAgentWithNetworkRetries(args, { workspace });
    const summaryPath = path.join(workspace, "runs", result.latestRunDir, "summary.json");
    let summary;
    try {
      summary = JSON.parse(await readFile(summaryPath, "utf8"));
    } catch (cause) {
      const error = new Error(`Real VLM agent completed but summary.json was not readable at ${summaryPath}.`);
      error.details = {
        category: "vlm-summary-missing",
        action: "Inspect the real VLM run directory for messages.final.jsonl or rerun after resolving any model/API error.",
        agentDir: this.agentDir,
        workspace,
        runDir: path.join(workspace, "runs", result.latestRunDir),
        summaryPath,
        stdout: result.stdout,
        stderr: result.stderr,
        cause: cause.message
      };
      throw error;
    }
    const finalAnswer = summary.final_answer || null;
    const pixel = normalizePixel(finalAnswer?.pixel);
    const model = extractModelName(result.stdout);

    return {
      ok: Boolean(finalAnswer),
      model,
      agentDir: this.agentDir,
      workspace,
      runDir: path.join(workspace, "runs", result.latestRunDir),
      summaryPath,
      precheck,
      attempts: result.attempts || [],
      stdout: result.stdout,
      stderr: result.stderr,
      summary,
      finalAnswer,
      pixel
    };
  }

  async spawnAgentWithNetworkRetries(args, { workspace } = {}) {
    const maxAttempts = this.networkRetries + 1;
    const attempts = [];
    let lastError = null;
    for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
      try {
        const result = await this.spawnAgent(args, { workspace, attempt });
        return {
          ...result,
          attempts
        };
      } catch (error) {
        lastError = error;
        attempts.push({
          attempt,
          category: error.details?.category || "unknown",
          message: error.message,
          runDir: error.details?.runDir || null
        });
        if (attempt >= maxAttempts || !isRetryableNetworkFailure(error)) {
          if (error.details) error.details.attempts = attempts;
          throw error;
        }
        const delayMs = retryDelayMs(attempt, this.networkRetryBaseDelayMs);
        console.warn(
          `[Network Retry] 侦测到链路断开，正在进行第 ${attempt + 1}/${maxAttempts} 次重建连接重试... ` +
          `category=${error.details?.category || "unknown"} delay=${delayMs}ms`
        );
        await sleep(delayMs);
      }
    }
    throw lastError;
  }

  async precheck({ envFile }) {
    const agentMain = path.join(this.agentDir, "agent", "cli.py");
    if (!existsSync(agentMain)) {
      const error = new Error(`Real VLM agent was not found at ${this.agentDir}. Set VLM_AGENT_DIR to Debugging-agent-v2.`);
      error.details = {
        category: "vlm-agent-dir",
        action: "Set VLM_AGENT_DIR to the Debugging-agent-v2 directory that contains agent/cli.py.",
        agentDir: this.agentDir
      };
      throw error;
    }

    const envConfig = await loadVlmEnv(envFile);
    if (envConfig.missing.length > 0) {
      const error = new Error(
        `Real VLM service is not configured. Missing ${envConfig.missing.join(", ")} in ${envFile}.`
      );
      error.details = {
        category: "vlm-env",
        action: "Fill VLM_API_KEY, VLM_BASE_URL, and VLM_MODEL in the Debugging-agent-v2 .env file or set VLM_ENV_FILE.",
        agentDir: this.agentDir,
        envFile,
        missing: envConfig.missing
      };
      throw error;
    }

    const result = {
      ok: true,
      envFile,
      baseUrl: envConfig.baseUrl,
      model: envConfig.model,
      connectivity: { checked: false }
    };

    if (!this.connectivityPrecheck) return result;
    result.connectivity = await checkModelConnectivity({
      baseUrl: envConfig.baseUrl,
      apiKey: envConfig.apiKey,
      timeoutMs: this.precheckTimeoutMs
    });
    return result;
  }

  spawnAgent(args, { workspace } = {}) {
    return new Promise((resolve, reject) => {
      const child = spawn(getPythonCommand(), args, {
        cwd: this.agentDir,
        env: {
          ...process.env,
          PYTHONIOENCODING: "utf-8",
          VLM_FORCE_CONNECTION_CLOSE: this.forceConnectionClose ? "true" : "false"
        },
        stdio: ["ignore", "pipe", "pipe"]
      });
      let stdout = "";
      let stderr = "";
      let settled = false;
      const timer = setTimeout(() => {
        if (settled) return;
        settled = true;
        const latestRunDir = findLatestRunDir(stdout) || findLatestRunDirFromWorkspace(workspace);
        const runDir = latestRunDir && workspace ? path.join(workspace, "runs", latestRunDir) : null;
        child.kill();
        const error = new Error(`Real VLM agent timed out after ${this.timeoutMs}ms.`);
        error.details = {
          category: "vlm-timeout",
          action: "Check the run directory for an incomplete trace, then increase VLM_AGENT_TIMEOUT_MS or fix the upstream model/API latency before retrying.",
          agentDir: this.agentDir,
          workspace,
          runDir,
          stdout,
          stderr,
          args
        };
        reject(error);
      }, this.timeoutMs);

      child.stdout.on("data", (chunk) => {
        stdout += chunk.toString("utf8");
      });
      child.stderr.on("data", (chunk) => {
        stderr += chunk.toString("utf8");
      });
      child.on("error", (error) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        reject(error);
      });
      child.on("close", (code) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        const latestRunDir = findLatestRunDir(stdout);
        if (code !== 0 || !latestRunDir) {
          const error = new Error(buildAgentErrorMessage(code, stdout, stderr));
          error.details = {
            category: classifyAgentFailure(stdout, stderr),
            action: suggestAgentAction(stdout, stderr),
            code,
            agentDir: this.agentDir,
            workspace,
            runDir: latestRunDir && workspace ? path.join(workspace, "runs", latestRunDir) : null,
            stdout,
            stderr,
            args
          };
          reject(error);
          return;
        }
        resolve({ stdout, stderr, latestRunDir });
      });
    });
  }
}

async function loadVlmEnv(envFile) {
  let parsed = {};
  try {
    parsed = parseDotEnv(await readFile(envFile, "utf8"));
  } catch (cause) {
    const error = new Error(`VLM .env file was not readable at ${envFile}.`);
    error.details = {
      category: "vlm-env",
      action: "Create the Debugging-agent-v2 .env file from .env.example or set VLM_ENV_FILE to a readable file.",
      envFile,
      cause: cause.message
    };
    throw error;
  }

  const env = {
    VLM_API_KEY: process.env.VLM_API_KEY || parsed.VLM_API_KEY,
    VLM_BASE_URL: process.env.VLM_BASE_URL || parsed.VLM_BASE_URL,
    VLM_MODEL: process.env.VLM_MODEL || parsed.VLM_MODEL
  };
  const missing = Object.entries(env).filter(([, value]) => !value).map(([key]) => key);
  return {
    missing,
    apiKey: env.VLM_API_KEY,
    baseUrl: env.VLM_BASE_URL,
    model: env.VLM_MODEL
  };
}

function parseDotEnv(text) {
  const out = {};
  for (const line of String(text || "").split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const match = trimmed.match(/^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$/);
    if (!match) continue;
    let value = match[2].trim();
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    }
    out[match[1]] = value;
  }
  return out;
}

async function checkModelConnectivity({ baseUrl, apiKey, timeoutMs }) {
  if (typeof fetch !== "function") {
    return { checked: false, warning: "Node fetch is unavailable; skipped /models connectivity precheck." };
  }

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  const modelsUrl = new URL("models", baseUrl.endsWith("/") ? baseUrl : `${baseUrl}/`);
  try {
    const response = await fetch(modelsUrl, {
      method: "GET",
      headers: {
        Authorization: `Bearer ${apiKey}`,
        Connection: "close"
      },
      signal: controller.signal
    });
    if (response.status === 404 || response.status === 405) {
      return {
        checked: true,
        ok: true,
        warning: `VLM endpoint is reachable, but ${modelsUrl} returned HTTP ${response.status}; continuing because some OpenAI-compatible gateways do not expose /models.`
      };
    }
    if (!response.ok) {
      const error = new Error(`VLM model connectivity precheck failed: GET ${modelsUrl} returned HTTP ${response.status}.`);
      error.details = {
        category: "vlm-api",
        action: "Verify VLM_BASE_URL, VLM_API_KEY permissions, and whether the selected gateway exposes the configured model.",
        baseUrl,
        status: response.status
      };
      throw error;
    }
    return { checked: true, ok: true, status: response.status };
  } catch (cause) {
    if (cause.details) throw cause;
    const timeout = cause.name === "AbortError";
    const error = new Error(
      timeout
        ? `VLM model connectivity precheck timed out after ${timeoutMs}ms.`
        : `VLM model connectivity precheck failed: ${cause.message}`
    );
    error.details = {
      category: timeout ? "vlm-api-timeout" : "vlm-api",
      action: "Check network access, VLM_BASE_URL, proxy/VPN settings, and provider availability before starting another run.",
      baseUrl,
      timeoutMs
    };
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function retryDelayMs(attempt, baseDelayMs) {
  if (baseDelayMs <= 0) return 0;
  return Math.min(baseDelayMs * (2 ** Math.max(0, attempt - 1)), 30000);
}

function findLatestRunDir(stdout) {
  const matches = [...String(stdout || "").matchAll(/run_dir(?:\]\s*=\s*|[^\S\r\n]*=[^\S\r\n]*)([^\r\n]+)/gi)];
  const raw = matches.at(-1)?.[1];
  if (!raw) return null;
  return path.basename(raw.replace(/\[[^\]]+\]/g, "").trim());
}

function findLatestRunDirFromWorkspace(workspace) {
  if (!workspace) return null;
  try {
    const runsDir = path.join(workspace, "runs");
    const candidates = readdirSync(runsDir, { withFileTypes: true })
      .filter((entry) => entry.isDirectory())
      .map((entry) => {
        const fullPath = path.join(runsDir, entry.name);
        return { name: entry.name, mtimeMs: statSync(fullPath).mtimeMs };
      })
      .sort((a, b) => b.mtimeMs - a.mtimeMs);
    return candidates[0]?.name || null;
  } catch {
    return null;
  }
}

function buildAgentErrorMessage(code, stdout, stderr) {
  const text = `${stderr}\n${stdout}`;
  if (/Missing required env vars/i.test(text)) {
    return "Real VLM service is not configured. Fill VLM_API_KEY, VLM_BASE_URL, and VLM_MODEL in Vlm agent/Debugging-agent-v2/.env or set VLM_ENV_FILE.";
  }
  return `Real VLM agent failed with code ${code}. ${stderr.trim() || stdout.trim()}`.trim();
}

function classifyAgentFailure(stdout, stderr) {
  const text = `${stderr}\n${stdout}`;
  if (/timeout|APITimeoutError|ReadTimeout|Timed out/i.test(text)) return "vlm-api-timeout";
  if (/APIConnectionError|RemoteProtocolError|Server disconnected|ECONN|ENOTFOUND|ETIMEDOUT|ECONNRESET|EPIPE|10054|UNEXPECTED_EOF|SSL.*EOF|connection reset|forcibly closed/i.test(text)) return "vlm-api";
  if (/401|403|unauthorized|forbidden|api[_ -]?key/i.test(text)) return "vlm-api-auth";
  if (/Missing required env vars/i.test(text)) return "vlm-env";
  return "vlm-agent";
}

function isRetryableNetworkFailure(error) {
  const category = error.details?.category;
  const text = [
    error.message,
    error.code,
    error.errno,
    error.details?.stdout,
    error.details?.stderr
  ].filter(Boolean).join("\n");
  if (category === "vlm-api" || category === "vlm-api-timeout" || category === "vlm-timeout") return true;
  return /ECONNRESET|EPIPE|ETIMEDOUT|ENOTFOUND|10054|RemoteProtocolError|Server disconnected|APIConnectionError|APITimeoutError|UNEXPECTED_EOF|SSL.*EOF|connection reset|forcibly closed/i.test(text);
}

function suggestAgentAction(stdout, stderr) {
  const category = classifyAgentFailure(stdout, stderr);
  if (category === "vlm-api-timeout") {
    return "Increase VLM_HTTP_TIMEOUT or VLM_AGENT_TIMEOUT_MS, and verify provider latency before retrying.";
  }
  if (category === "vlm-api-auth") {
    return "Verify VLM_API_KEY permissions and VLM_BASE_URL for the selected model.";
  }
  if (category === "vlm-api") {
    return "Check network access, VLM_BASE_URL, proxy/VPN settings, and provider availability.";
  }
  if (category === "vlm-env") {
    return "Fill VLM_API_KEY, VLM_BASE_URL, and VLM_MODEL in .env or set VLM_ENV_FILE.";
  }
  return "Inspect stderr/stdout and the run directory, then retry after fixing the reported agent error.";
}
