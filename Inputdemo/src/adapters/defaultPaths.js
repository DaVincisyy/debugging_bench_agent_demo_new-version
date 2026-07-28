import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(here, "..", "..", "..");

export function resolveDebuggingAgentDir() {
  if (process.env.VLM_AGENT_DIR) return path.resolve(process.env.VLM_AGENT_DIR);
  return path.join(projectRoot, "new_vlm_agent");
}

export function defaultVlmAgentCasesDir() {
  // Cases are prepared locally under Inputdemo/ (independent of Vlm agent/ folder).
  const here = path.dirname(fileURLToPath(import.meta.url));
  const inputdemoDir = path.resolve(here, "..", "..", "..");
  return path.join(inputdemoDir, "data", "cases");
}

export function defaultVlmAgentRunsDir() {
  // When running in remote mode, use the server's workspace path.
  // Must use forward slashes — path.join on Windows produces backslashes.
  if (
    process.env.VLM_AGENT_RUNNER === "remote-svc"
    || isRemoteServiceUrl(process.env.VLM_AGENT_SERVICE_URL)
  ) {
    return process.env.VLM_AGENT_REMOTE_WORKSPACE || "/opt/vlm-agent/Debugging-agent-v2/workspace/vlm-agent-runs";
  }
  return path.join(resolveDebuggingAgentDir(), "workspace", "vlm-agent-runs");
}

export function isRemoteServiceUrl(serviceUrl) {
  if (!serviceUrl) return false;
  try {
    const hostname = new URL(serviceUrl).hostname.toLowerCase().replace(/^\[|\]$/g, "");
    return !["localhost", "127.0.0.1", "::1", "0.0.0.0"].includes(hostname);
  } catch {
    return false;
  }
}
