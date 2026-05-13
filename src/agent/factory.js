import { BenchAgent } from "./benchAgent.js";
import { MockVlmClient } from "../adapters/mockVlmClient.js";
import { MockRagRepository } from "../adapters/mockRagRepository.js";
import { MockArmController } from "../adapters/mockArmController.js";
import { MockEquipmentController } from "../adapters/mockEquipmentController.js";
import { ReportGenerator } from "../adapters/reportGenerator.js";

export function createDefaultAgent() {
  return new BenchAgent({
    vlmClient: new MockVlmClient(),
    ragRepository: new MockRagRepository(),
    armController: new MockArmController(),
    equipmentController: new MockEquipmentController(),
    reportGenerator: new ReportGenerator()
  });
}
