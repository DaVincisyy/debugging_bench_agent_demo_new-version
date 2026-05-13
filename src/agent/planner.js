import { StepKind } from "../domain/states.js";

export function buildPlan({ input, vlmObservation, ragEvidence }) {
  const measurement = vlmObservation.recommendedMeasurements[0];

  return {
    objective: input.command,
    evidenceIds: ragEvidence.map((item) => item.id),
    steps: [
      {
        id: "step-001",
        kind: StepKind.ARM_MOTION,
        command: "MOVE_PROBE_TO_LOCATION",
        targetLocationId: measurement.locationId
      },
      {
        id: "step-002",
        kind: StepKind.EQUIPMENT_MEASUREMENT,
        instrument: measurement.instrument,
        signal: measurement.signal,
        locationId: measurement.locationId,
        expectedRange: measurement.expectedRange
      },
      {
        id: "step-003",
        kind: StepKind.VISUAL_CAPTURE,
        command: "CAPTURE_POST_MEASUREMENT_FRAME",
        targetLocationId: measurement.locationId
      }
    ]
  };
}
