import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(here, "..", "..", "..");

export function resolveDebuggingAgentDir() {
  if (process.env.VLM_AGENT_DIR) return path.resolve(process.env.VLM_AGENT_DIR);
  return path.join(projectRoot, "Vlm agent", "Debugging-agent-v2");
}

export function defaultVlmAgentCasesDir() {
  return path.join(resolveDebuggingAgentDir(), "data", "cases");
}

export function defaultVlmAgentRunsDir() {
  return path.join(resolveDebuggingAgentDir(), "workspace", "vlm-agent-runs");
}
