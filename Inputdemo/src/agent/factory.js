import { BenchAgent } from "./benchAgent.js";
import { MockRagRepository } from "../adapters/mockRagRepository.js";
import { RobotGatewayClient } from "../adapters/robotGatewayClient.js";
import { MockEquipmentController } from "../adapters/mockEquipmentController.js";
import { ReportGenerator } from "../adapters/reportGenerator.js";
import { VlmAgentCaseAdapter } from "../adapters/vlmAgentCaseAdapter.js";
import { RealVlmAgentClient, RealVlmAgentModelClient } from "../adapters/realVlmAgentClient.js";
import { RealVlmAgentRunner } from "../adapters/realVlmAgentRunner.js";
import { VlmAgentServiceRunner } from "../adapters/vlmAgentServiceRunner.js";
import { RemoteVlmAgentServiceRunner } from "../adapters/remoteVlmAgentServiceRunner.js";

/**
 * Decide which VLM runner to use based on environment:
 *
 *   VLM_AGENT_RUNNER=cli        → local CLI spawn (RealVlmAgentRunner)
 *   VLM_AGENT_RUNNER=local-svc  → local FastAPI service (VlmAgentServiceRunner + CLI fallback)
 *   VLM_AGENT_RUNNER=remote-svc → remote FastAPI service (RemoteVlmAgentServiceRunner)
 *
 *   (default / unset)
 *   ─ If VLM_AGENT_SERVICE_URL points to a non-localhost address
 *     → RemoteVlmAgentServiceRunner (remote server)
 *   ─ Else → VlmAgentServiceRunner with CLI fallback (local dev)
 */
export function createDefaultAgent({ vlmRunner } = {}) {
  const runnerMode = process.env.VLM_AGENT_RUNNER || "";

  let realVlmRunner = vlmRunner;

  if (!realVlmRunner) {
    if (runnerMode === "cli") {
      // Force local CLI (spawns python process)
      realVlmRunner = new RealVlmAgentRunner();
    } else if (runnerMode === "local-svc") {
      // Force local service + CLI fallback
      realVlmRunner = new VlmAgentServiceRunner({
        fallbackRunner: new RealVlmAgentRunner(),
      });
    } else if (runnerMode === "remote-svc") {
      // Force remote service (no fallback)
      realVlmRunner = new RemoteVlmAgentServiceRunner();
    } else {
      // Auto-detect: remote if VLM_AGENT_SERVICE_URL is non-localhost
      const serviceUrl = (process.env.VLM_AGENT_SERVICE_URL || "").trim();
      if (serviceUrl && !isLocalhostUrl(serviceUrl)) {
        realVlmRunner = new RemoteVlmAgentServiceRunner();
      } else {
        realVlmRunner = new VlmAgentServiceRunner({
          fallbackRunner: new RealVlmAgentRunner(),
        });
      }
    }
  }

  return new BenchAgent({
    vlmClient: new RealVlmAgentClient({ runner: realVlmRunner }),
    largeModelClient: new RealVlmAgentModelClient({ runner: realVlmRunner }),
    ragRepository: new MockRagRepository(),
    armController: new RobotGatewayClient(),
    equipmentController: new MockEquipmentController(),
    reportGenerator: new ReportGenerator(),
    vlmAgentCaseAdapter: new VlmAgentCaseAdapter(),
  });
}

function isLocalhostUrl(url) {
  try {
    const hostname = new URL(url).hostname.toLowerCase();
    return (
      hostname === "localhost" ||
      hostname === "127.0.0.1" ||
      hostname === "::1" ||
      hostname === "0.0.0.0"
    );
  } catch {
    return true; // treat unparsable as localhost for safety
  }
}
