export class ReportGenerator {
  create({ run, ragEvidence, vlmObservation, measurements }) {
    const failed = measurements.filter((item) => item.pass === false);

    return {
      title: "Debugging Bench Agent Test Report",
      summary: failed.length === 0
        ? "Bench procedure completed. Mock measurements are within expected limits."
        : "Bench procedure completed with failing measurements.",
      objective: run.input.command,
      findings: [
        `VLM detected ${vlmObservation.locations.length} actionable bench locations.`,
        `RAG is disabled; ${run.input.modelAttachments.length} schematic/layout files were passed as model inputs.`,
        `Execution completed ${run.execution.arm.length} arm actions and ${run.execution.equipment.length} equipment measurements.`
      ],
      measurements,
      nextActions: failed.length === 0
        ? ["Replace mock adapters with real VLM/model/hardware clients.", "Add async job queue before closed-loop hardware execution."]
        : ["Review failed measurement locations.", "Run retry flow with updated VLM capture."]
    };
  }
}
