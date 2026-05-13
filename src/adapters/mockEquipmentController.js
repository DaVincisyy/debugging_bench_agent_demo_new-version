export class MockEquipmentController {
  async measure(step) {
    return {
      stepId: step.id,
      status: "COMPLETED",
      instrument: step.instrument,
      signal: step.signal,
      value: 4.98,
      unit: "V",
      rippleMvpp: 18.7,
      pass: true,
      durationMs: 280
    };
  }
}
