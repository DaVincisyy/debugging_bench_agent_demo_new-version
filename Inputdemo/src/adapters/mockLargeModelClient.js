export class MockLargeModelClient {
  async generateMg400Pose({ vlmAgentCase }) {
    return {
      model: "mock-large-model-v0",
      inputFormat: "debugging-agent-v2-task",
      taskFile: vlmAgentCase?.taskFile || null,
      testPoint: "TP_VCP",
      confidence: 0.88,
      mg400Pose: {
        x: 245.6,
        y: -32.4,
        z: 78.2,
        r: 91.5
      },
      reason: "Mock output for the requested instruction and uploaded schematic/bit images."
    };
  }
}
