import { readMg400Config } from "./mg400Config.js";

const DEFAULT_GATEWAY_URL = "http://127.0.0.1:8010";

export class RobotGatewayClient {
  constructor({
    baseUrl = process.env.ROBOT_GATEWAY_URL || DEFAULT_GATEWAY_URL,
    timeoutMs = Number(process.env.ROBOT_GATEWAY_TIMEOUT_MS || 30000)
  } = {}) {
    this.baseUrl = baseUrl.replace(/\/+$/, "");
    this.timeoutMs = timeoutMs;
  }

  async execute(step) {
    const config = await readMg400Config();
    return this.request("/v1/robot/execute", {
      step,
      config
    }, "execute robot step");
  }

  async runCommand(action, payload = {}) {
    const config = payload.config || await readMg400Config();
    return this.request(`/v1/robot/${encodeURIComponent(action)}`, {
      ...payload,
      config
    }, `run robot ${action}`);
  }

  async health() {
    return this.get("/health", "read robot gateway health");
  }

  async get(pathname, action) {
    return this.requestJson(`${this.baseUrl}${pathname}`, {}, action);
  }

  async request(pathname, payload, action) {
    return this.requestJson(`${this.baseUrl}${pathname}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }, action);
  }

  async requestJson(url, options, action) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);
    let response;
    try {
      response = await fetch(url, {
        ...options,
        signal: controller.signal
      });
    } catch (cause) {
      const timeout = cause.name === "AbortError";
      const error = new Error(
        timeout
          ? `Robot gateway timed out while trying to ${action}.`
          : `Failed to ${action} through robot gateway: ${cause.message}`
      );
      error.details = {
        category: timeout ? "robot-gateway-timeout" : "robot-gateway-unavailable",
        action: "Start the Robot Gateway service or set ROBOT_GATEWAY_URL to the gateway host.",
        serviceUrl: this.baseUrl,
        cause: cause.message
      };
      throw error;
    } finally {
      clearTimeout(timer);
    }

    let body;
    try {
      body = await response.clone().json();
    } catch {
      body = { detail: await response.text() };
    }
    if (!response.ok || body?.ok === false) {
      const error = new Error(body?.error || `Robot gateway returned HTTP ${response.status}.`);
      error.details = {
        category: "robot-gateway",
        status: response.status,
        serviceUrl: this.baseUrl,
        response: body
      };
      throw error;
    }
    return body;
  }
}
