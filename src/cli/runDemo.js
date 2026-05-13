import { createDefaultAgent } from "../agent/factory.js";
import { parseUserCommand } from "../agent/inputCore.js";

const payload = {
  prompt: {
    mode: "text",
    text: "Inspect CAN transceiver board and measure 5V rail ripple"
  },
  visualCapture: {
    imageRef: "C:/Users/FX506L/Desktop/KPIT Bench Agent/Version 1.png",
    cameraId: "bench-overview"
  },
  modelAttachments: [
    {
      kind: "schematic",
      name: "voyah_hvac_v01_20240729_01.png",
      type: "image/png"
    },
    {
      kind: "layout",
      name: "位号图7.29_01(12).png",
      type: "image/png"
    }
  ],
  context: {
    operator: "demo-user",
    benchId: "kpit-bench-01"
  }
};

const agent = createDefaultAgent();
const run = await agent.run(parseUserCommand(payload));

console.log(JSON.stringify({
  runId: run.runId,
  state: run.state,
  objective: run.plan.objective,
  vlmLocations: run.vlmObservation.locations,
  measurements: run.report.measurements,
  summary: run.report.summary,
  timeline: run.timeline
}, null, 2));
