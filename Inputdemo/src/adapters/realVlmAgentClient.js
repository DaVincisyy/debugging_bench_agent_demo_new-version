export class RealVlmAgentClient {
  constructor({ runner }) {
    this.runner = runner;
  }

  async analyzeBench({ input, vlmAgentCase }) {
    const result = await this.runner.run({ input, vlmAgentCase });
    const finalAnswer = result.finalAnswer || {};
    const pixel = result.pixel;
    const locationId = finalAnswer.tp_id || finalAnswer.test_point || "vlm-target";

    return {
      model: result.model,
      provider: "debugging-agent-v2",
      realModelService: true,
      imageRef: input.cameraImage?.name || input.visualCapture?.imageRef || null,
      promptMode: input.prompt?.mode || "text",
      agentDir: result.agentDir,
      workspace: result.workspace,
      runDir: result.runDir,
      summaryPath: result.summaryPath,
      precheck: result.precheck,
      attempts: result.attempts || [],
      stoppedReason: result.summary?.stopped_reason || null,
      finalAnswer,
      pixel: pixel ? { x: pixel[0], y: pixel[1] } : null,
      confidence: Number.isFinite(Number(finalAnswer.confidence)) ? Number(finalAnswer.confidence) : null,
      modelInputSummary: {
        attachmentCount: input.modelAttachments.length,
        schematicCount: input.schematicDiagrams.length,
        bitImageCount: input.bitImages.length,
        attachmentNames: input.modelAttachments.map((item) => item.name)
      },
      benchOverview: {
        boardDetected: Boolean(pixel),
        instrumentsDetected: [],
        armReachableZones: pixel ? ["vlm-localized-test-point"] : []
      },
      locations: [
        {
          id: locationId,
          label: finalAnswer.tp_id || finalAnswer.label || "VLM localized test point",
          pixel: pixel ? { x: pixel[0], y: pixel[1] } : null,
          cameraView: finalAnswer.camera_view || null
        }
      ],
      recommendedMeasurements: [
        {
          signal: finalAnswer.signal || finalAnswer.tp_id || "TARGET_SIGNAL",
          locationId,
          instrument: "oscilloscope",
          expectedRange: finalAnswer.expected_range || "See real VLM final_answer.",
          reason: finalAnswer.reason || `Real VLM agent result for: ${input.command}`
        }
      ]
    };
  }
}

export class RealVlmAgentModelClient {
  constructor({ runner }) {
    this.runner = runner;
  }

  async generateMg400Pose({ input, vlmAgentCase }) {
    const result = await this.runner.run({ input, vlmAgentCase });
    const finalAnswer = result.finalAnswer || {};
    const pose = normalizePose(finalAnswer.mg400Pose || finalAnswer.mg400_pose || finalAnswer.pose)
      || poseFromPixel(result.pixel || finalAnswer.pixel);

    return {
      model: result.model,
      provider: "debugging-agent-v2",
      realModelService: true,
      inputFormat: "debugging-agent-v2-task",
      testPoint: finalAnswer.tp_id || finalAnswer.test_point || null,
      confidence: Number.isFinite(Number(finalAnswer.confidence)) ? Number(finalAnswer.confidence) : null,
      pixel: result.pixel ? { x: result.pixel[0], y: result.pixel[1] } : null,
      mg400Pose: pose,
      finalAnswer,
      summaryPath: result.summaryPath,
      runDir: result.runDir,
      workspace: result.workspace,
      precheck: result.precheck,
      attempts: result.attempts || [],
      reason: pose
        ? "Real VLM agent returned or derived an MG400 pose."
        : "Real VLM agent returned localization output; MG400 pose was not present in final_answer."
    };
  }
}

function normalizePose(value) {
  if (!value || typeof value !== "object") return null;
  const pose = {
    x: Number(value.x),
    y: Number(value.y),
    z: Number(value.z),
    r: Number(value.r)
  };
  if (Object.values(pose).some((item) => !Number.isFinite(item))) return null;
  return pose;
}

function poseFromPixel(value) {
  if (!Array.isArray(value) || value.length !== 2) return null;
  const x = Number(value[0]);
  const y = Number(value[1]);
  if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
  return { x: Math.round(x), y: Math.round(y), z: 0, r: 0 };
}
