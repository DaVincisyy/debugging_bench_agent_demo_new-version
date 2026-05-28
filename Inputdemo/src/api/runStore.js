const runs = new Map();

export function saveRun(run) {
  runs.set(run.runId, run);
  return run;
}

export function getRun(runId) {
  return runs.get(runId) || null;
}

export function updateRun(runId, patch) {
  const run = getRun(runId);
  if (!run) return null;
  Object.assign(run, patch);
  return run;
}

export function appendRunEvent(runId, type, payload = {}) {
  const run = getRun(runId);
  if (!run) return null;
  if (!run.nodeEvents) run.nodeEvents = [];
  const event = {
    event_id: `node_evt_${run.nodeEvents.length + 1}`,
    run_id: runId,
    seq: run.nodeEvents.length + 1,
    type,
    timestamp: Date.now(),
    payload
  };
  run.nodeEvents.push(event);
  return event;
}

export function getRunEvents(runId, since = 0) {
  const run = getRun(runId);
  if (!run) return null;
  return (run.nodeEvents || []).filter((event) => Number(event.seq) > Number(since || 0));
}
