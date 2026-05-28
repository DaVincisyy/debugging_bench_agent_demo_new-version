import { StepKind } from "../domain/states.js";
import { evaluateMg400PoseReachability } from "../domain/mg400Reachability.js";

export function mapVlmTargetToExecution({ input, vlmObservation, ragEvidence, modelOutput }) {
  const measurement = vlmObservation.recommendedMeasurements[0];
  const targetPose = modelOutput?.mg400Pose || null;
  const targetLocationId = measurement?.locationId || "vlm-target";
  const reachabilityPrecheck = targetPose ? evaluateMg400PoseReachability(targetPose) : null;

  return {
    objective: input.command,
    mapper: "VLM Target Execution Mapper",
    evidenceIds: ragEvidence.map((item) => item.id),
    steps: [
      {
        id: "step-001",
        kind: StepKind.ARM_MOTION,
        command: targetPose ? "MOVE_TO_MG400_POSE" : "MOVE_PROBE_TO_LOCATION",
        targetLocationId,
        targetPose,
        reachabilityPrecheck
      },
      {
        id: "step-002",
        kind: StepKind.EQUIPMENT_MEASUREMENT,
        instrument: measurement?.instrument || "oscilloscope",
        signal: measurement?.signal || "TARGET_SIGNAL",
        locationId: targetLocationId,
        expectedRange: measurement?.expectedRange || "See VLM final_answer"
      },
      {
        id: "step-003",
        kind: StepKind.VISUAL_CAPTURE,
        command: "CAPTURE_POST_MEASUREMENT_FRAME",
        targetLocationId
      }
    ]
  };
}
