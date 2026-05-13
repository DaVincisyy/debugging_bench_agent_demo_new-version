export class MockVlmClient {
  async analyzeBench({ command, visualCapture, modelAttachments = [], prompt }) {
    const imageRef = visualCapture?.imageRef || "mock-bench-frame";
    const schematicInputs = modelAttachments.filter((item) => item.kind === "schematic");
    const layoutInputs = modelAttachments.filter((item) => item.kind === "layout");

    return {
      model: "mock-vlm-v0",
      imageRef,
      promptMode: prompt?.mode || "text",
      modelInputSummary: {
        attachmentCount: modelAttachments.length,
        schematicCount: schematicInputs.length,
        layoutCount: layoutInputs.length,
        attachmentNames: modelAttachments.map((item) => item.name)
      },
      confidence: 0.86,
      benchOverview: {
        boardDetected: true,
        instrumentsDetected: ["oscilloscope", "programmable-power-supply"],
        armReachableZones: ["top-left", "center", "probe-pad-j3"]
      },
      locations: [
        {
          id: "probe-pad-j3",
          label: "J3 5V rail probe pad",
          normalizedBox: { x: 0.57, y: 0.42, width: 0.08, height: 0.06 }
        },
        {
          id: "can-transceiver-u7",
          label: "CAN transceiver U7",
          normalizedBox: { x: 0.36, y: 0.48, width: 0.12, height: 0.1 }
        }
      ],
      recommendedMeasurements: [
        {
          signal: "5V_RAIL",
          locationId: "probe-pad-j3",
          instrument: "oscilloscope",
          expectedRange: "4.85V..5.15V",
          reason: `Command requests bench inspection: ${command}`
        }
      ]
    };
  }
}
