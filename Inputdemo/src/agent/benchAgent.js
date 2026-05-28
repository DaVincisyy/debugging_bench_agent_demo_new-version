import { AgentState, StepKind } from "../domain/states.js";
import { createBenchRun, transition } from "../domain/run.js";
import { mapVlmTargetToExecution } from "./vlmTargetExecutionMapper.js";

export class BenchAgent {
  constructor({ vlmClient, largeModelClient, ragRepository, armController, equipmentController, reportGenerator, vlmAgentCaseAdapter = null }) {
    this.vlmClient = vlmClient;
    this.largeModelClient = largeModelClient;
    this.ragRepository = ragRepository;
    this.armController = armController;
    this.equipmentController = equipmentController;
    this.reportGenerator = reportGenerator;
    this.vlmAgentCaseAdapter = vlmAgentCaseAdapter;
  }

  async run(input) {
    const run = createBenchRun(input);

    transition(run, AgentState.PREPARING, "Parsed 6-field input; building Debugging-agent-v2 task and running VLM without RAG.");
    if (this.vlmAgentCaseAdapter) {
      run.vlmAgentCase = await this.vlmAgentCaseAdapter.adapt({
        runId: run.runId,
        input
      });
    }
    const ragEvidence = [];
    const [vlmObservation, modelOutput] = await Promise.all([
      this.vlmClient.analyzeBench({ input, vlmAgentCase: run.vlmAgentCase }),
      this.largeModelClient.generateMg400Pose({
        input,
        vlmAgentCase: run.vlmAgentCase
      })
    ]);

    run.ragEvidence = ragEvidence;
    run.vlmObservation = vlmObservation;
    run.modelOutput = modelOutput;
    run.plan = mapVlmTargetToExecution({ input, ragEvidence, vlmObservation, modelOutput });

    transition(run, AgentState.EXECUTING, "VLM target execution mapping created; executing hardware flow.");
    const blockedLocations = new Map();
    for (const step of run.plan.steps) {
      if (step.kind === StepKind.ARM_MOTION || step.kind === StepKind.VISUAL_CAPTURE) {
        try {
          const armResult = await this.armController.execute(step);
          run.execution.arm.push(armResult);
          if (armResult.status === "BLOCKED" && step.targetLocationId) {
            blockedLocations.set(step.targetLocationId, armResult);
          }
        } catch (error) {
          const armResult = {
            stepId: step.id,
            status: "FAILED",
            motionCommand: step.command,
            targetLocationId: step.targetLocationId,
            targetPose: step.targetPose || null,
            tcpCommand: null,
            error: error.message,
            details: error.details || null
          };
          run.execution.arm.push(armResult);
          if (step.targetLocationId) blockedLocations.set(step.targetLocationId, armResult);
        }
      }

      if (step.kind === StepKind.EQUIPMENT_MEASUREMENT) {
        const blockedArm = blockedLocations.get(step.locationId);
        if (blockedArm) {
          run.execution.equipment.push({
            stepId: step.id,
            status: "SKIPPED",
            instrument: step.instrument,
            signal: step.signal,
            locationId: step.locationId,
            pass: false,
            reason: blockedArm.message || blockedArm.error || "Arm motion did not reach the requested measurement point.",
            fallbackPose: blockedArm.fallbackPose || null,
            fallbackAction: blockedArm.fallbackAction || "Request a recalibrated measurement target inside the MG400 workspace."
          });
          continue;
        }
        run.execution.equipment.push(await this.equipmentController.measure(step));
      }
    }

    transition(run, AgentState.REPORTING, "Execution completed; generating report.");
    run.report = this.reportGenerator.create({
      run,
      ragEvidence,
      vlmObservation,
      measurements: run.execution.equipment
    });

    return run;
  }
}
