import http from "node:http";
import { createDefaultAgent } from "../agent/factory.js";
import { parseUserCommand } from "../agent/inputCore.js";
import { getRun, saveRun } from "./runStore.js";
import { readJsonBody, sendJson } from "../utils/http.js";
import { webPage } from "./webPage.js";

const port = Number(process.env.PORT || 3000);
const agent = createDefaultAgent();

async function route(request, response) {
  const url = new URL(request.url, `http://${request.headers.host}`);

  if (request.method === "GET" && url.pathname === "/health") {
    sendJson(response, 200, { ok: true, service: "debugging-bench-agent" });
    return;
  }

  if (request.method === "GET" && url.pathname === "/") {
    response.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
    response.end(webPage);
    return;
  }

  if (request.method === "POST" && url.pathname === "/api/runs") {
    const payload = await readJsonBody(request);
    const input = parseUserCommand(payload);
    const run = await agent.run(input);
    saveRun(run);
    sendJson(response, 201, run);
    return;
  }

  const runMatch = url.pathname.match(/^\/api\/runs\/([^/]+)$/);
  if (request.method === "GET" && runMatch) {
    const run = getRun(runMatch[1]);
    if (!run) {
      sendJson(response, 404, { error: "Run not found." });
      return;
    }

    sendJson(response, 200, run);
    return;
  }

  sendJson(response, 404, { error: "Route not found." });
}

const server = http.createServer((request, response) => {
  route(request, response).catch((error) => {
    const statusCode = error.statusCode || 500;
    sendJson(response, statusCode, {
      error: error.message,
      details: error.details
    });
  });
});

server.listen(port, () => {
  console.log(`Debugging Bench Agent listening on http://localhost:${port}`);
});
