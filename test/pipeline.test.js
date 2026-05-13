import test from "node:test";
import assert from "node:assert/strict";
import { createDefaultAgent } from "../src/agent/factory.js";
import { parseUserCommand } from "../src/agent/inputCore.js";
import { AgentState } from "../src/domain/states.js";

test("bench agent runs the mocked VLM-to-report flow", async () => {
  const agent = createDefaultAgent();
  const input = parseUserCommand({
    prompt: {
      mode: "text",
      text: "Measure 5V rail ripple"
    },
    visualCapture: { imageRef: "Version 1.png" },
    modelAttachments: [
      { kind: "schematic", name: "voyah_hvac_v01_20240729_01.png", type: "image/png", size: 1024 },
      { kind: "layout", name: "位号图7.29_01(12).png", type: "image/png", size: 1024 }
    ]
  });

  const run = await agent.run(input);

  assert.equal(run.state, AgentState.REPORTING);
  assert.equal(run.vlmObservation.model, "mock-vlm-v0");
  assert.equal(run.ragEvidence.length, 0);
  assert.equal(run.vlmObservation.modelInputSummary.attachmentCount, 2);
  assert.equal(run.execution.equipment.length, 1);
  assert.equal(run.report.measurements[0].pass, true);
});
