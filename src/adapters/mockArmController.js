export class MockArmController {
  async execute(step) {
    return {
      stepId: step.id,
      status: "COMPLETED",
      motionCommand: step.command,
      targetLocationId: step.targetLocationId,
      precisionMm: 0.03,
      durationMs: 420
    };
  }
}
