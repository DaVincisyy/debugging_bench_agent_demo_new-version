import { BenchAgent } from "./benchAgent.js";
import { MockRagRepository } from "../adapters/mockRagRepository.js";
import { Mg400ArmController } from "../adapters/mg400ArmController.js";
import { MockEquipmentController } from "../adapters/mockEquipmentController.js";
import { ReportGenerator } from "../adapters/reportGenerator.js";
import { VlmAgentCaseAdapter } from "../adapters/vlmAgentCaseAdapter.js";
import { RealVlmAgentClient, RealVlmAgentModelClient } from "../adapters/realVlmAgentClient.js";
import { RealVlmAgentRunner } from "../adapters/realVlmAgentRunner.js";
import { VlmAgentServiceRunner } from "../adapters/vlmAgentServiceRunner.js";

export function createDefaultAgent({ vlmRunner } = {}) {
  const realVlmRunner = vlmRunner || (
    process.env.VLM_AGENT_RUNNER === "cli"
      ? new RealVlmAgentRunner()
      : new VlmAgentServiceRunner({ fallbackRunner: new RealVlmAgentRunner() })
  );

  return new BenchAgent({
    vlmClient: new RealVlmAgentClient({ runner: realVlmRunner }),
    largeModelClient: new RealVlmAgentModelClient({ runner: realVlmRunner }),
    ragRepository: new MockRagRepository(),
    armController: new Mg400ArmController(),
    equipmentController: new MockEquipmentController(),
    reportGenerator: new ReportGenerator(),
    vlmAgentCaseAdapter: new VlmAgentCaseAdapter()
  });
}
