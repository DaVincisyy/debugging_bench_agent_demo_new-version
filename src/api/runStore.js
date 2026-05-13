const runs = new Map();

export function saveRun(run) {
  runs.set(run.runId, run);
  return run;
}

export function getRun(runId) {
  return runs.get(runId) || null;
}
