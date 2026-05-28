export class ReportGenerator {
  create({ run, ragEvidence, vlmObservation, measurements }) {
    const failed = measurements.filter((item) => item.pass === false);
    const pose = run.modelOutput?.mg400Pose;
    const pixel = run.modelOutput?.pixel || vlmObservation?.pixel;
    const blockedArm = run.execution.arm.find((item) => item.status === "BLOCKED");
    const reachabilityFinding = blockedArm
      ? `MG400 reachability guard blocked the requested pose; fallback is ${blockedArm.fallbackPose ? `safe hover x=${blockedArm.fallbackPose.x}, y=${blockedArm.fallbackPose.y}, z=${blockedArm.fallbackPose.z}, r=${blockedArm.fallbackPose.r}` : "operator recalibration"}.`
      : "MG400 reachability guard allowed all requested arm poses.";

    return {
      title: "Debugging Bench Agent Test Report",
      summary: failed.length === 0
        ? "Bench procedure completed. Real VLM/model output was used."
        : "Bench procedure completed with failing measurements.",
      objective: run.input.command,
      findings: [
        `VLM detected ${vlmObservation.locations.length} actionable bench locations.`,
        `RAG is disabled; ${run.input.modelAttachments.length} schematic/layout files were passed as model inputs.`,
        pose
          ? `Real model returned MG400 pose x=${pose.x}, y=${pose.y}, z=${pose.z}, r=${pose.r}.`
          : `Real model returned pixel target ${pixel ? `x=${pixel.x}, y=${pixel.y}` : "without an MG400 pose"}.`,
        reachabilityFinding,
        `Execution completed ${run.execution.arm.length} arm actions and ${run.execution.equipment.length} equipment measurements.`
      ],
      measurements,
      nextActions: failed.length === 0
        ? [
            "Review real VLM summary evidence before closed-loop hardware execution.",
            pose
              ? "Replace the temporary pixel-to-pose mapping with calibrated camera-to-MG400 transform before real hardware contact."
              : "Add calibrated pixel-to-MG400 transform if final_answer has no pose."
          ]
        : [
            "Review failed measurement locations.",
            blockedArm?.fallbackAction || "Run retry flow with updated VLM capture."
          ]
    };
  }
}
