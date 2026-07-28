import test from "node:test";
import assert from "node:assert/strict";
import path from "node:path";
import {
  defaultVlmAgentRunsDir,
  isRemoteServiceUrl,
  resolveDebuggingAgentDir,
} from "../src/adapters/defaultPaths.js";

function withVlmEnvironment(overrides, callback) {
  const keys = [
    "VLM_AGENT_DIR",
    "VLM_AGENT_RUNNER",
    "VLM_AGENT_SERVICE_URL",
    "VLM_AGENT_REMOTE_WORKSPACE",
  ];
  const previous = Object.fromEntries(keys.map((key) => [key, process.env[key]]));
  try {
    for (const key of keys) delete process.env[key];
    Object.assign(process.env, overrides);
    return callback();
  } finally {
    for (const key of keys) {
      if (previous[key] === undefined) delete process.env[key];
      else process.env[key] = previous[key];
    }
  }
}

test("local VLM service URL uses the local agent workspace", () => {
  withVlmEnvironment(
    { VLM_AGENT_SERVICE_URL: "http://127.0.0.1:8000" },
    () => assert.equal(
      defaultVlmAgentRunsDir(),
      path.join(resolveDebuggingAgentDir(), "workspace", "vlm-agent-runs"),
    ),
  );
});

test("remote VLM service URL uses the remote workspace", () => {
  withVlmEnvironment(
    { VLM_AGENT_SERVICE_URL: "http://10.0.0.5:8000" },
    () => assert.equal(
      defaultVlmAgentRunsDir(),
      "/opt/vlm-agent/Debugging-agent-v2/workspace/vlm-agent-runs",
    ),
  );
});

test("explicit remote runner honors a configured remote workspace", () => {
  withVlmEnvironment(
    {
      VLM_AGENT_RUNNER: "remote-svc",
      VLM_AGENT_SERVICE_URL: "http://127.0.0.1:8000",
      VLM_AGENT_REMOTE_WORKSPACE: "/srv/vlm/runs",
    },
    () => assert.equal(defaultVlmAgentRunsDir(), "/srv/vlm/runs"),
  );
});

test("remote URL detection excludes loopback hosts", () => {
  assert.equal(isRemoteServiceUrl("http://localhost:8000"), false);
  assert.equal(isRemoteServiceUrl("http://127.0.0.1:8000"), false);
  assert.equal(isRemoteServiceUrl("http://[::1]:8000"), false);
  assert.equal(isRemoteServiceUrl("http://vlm.internal:8000"), true);
});
