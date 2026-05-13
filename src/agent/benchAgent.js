import { AgentState, StepKind } from "../domain/states.js";
import { createBenchRun, transition } from "../domain/run.js";
import { buildPlan } from "./planner.js";

export class BenchAgent {
  constructor({ vlmClient, ragRepository, armController, equipmentController, reportGenerator }) {
    this.vlmClient = vlmClient;
    this.ragRepository = ragRepository;
    this.armController = armController;
    this.equipmentController = equipmentController;
    this.reportGenerator = reportGenerator;
  }

  async run(input) {
    const run = createBenchRun(input);

    transition(run, AgentState.PREPARING, "Parsed prompt and uploaded files; running VLM without RAG.");
    const ragEvidence = [];
    const vlmObservation = await this.vlmClient.analyzeBench(input);

    run.ragEvidence = ragEvidence;
    run.vlmObservation = vlmObservation;
    run.plan = buildPlan({ input, ragEvidence, vlmObservation });

    transition(run, AgentState.EXECUTING, "Plan generated; executing mock hardware flow.");
    for (const step of run.plan.steps) {
      if (step.kind === StepKind.ARM_MOTION || step.kind === StepKind.VISUAL_CAPTURE) {
        run.execution.arm.push(await this.armController.execute(step));
      }

      if (step.kind === StepKind.EQUIPMENT_MEASUREMENT) {
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
