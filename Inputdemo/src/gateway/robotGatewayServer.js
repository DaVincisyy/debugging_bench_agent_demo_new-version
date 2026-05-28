import http from "node:http";
import { Mg400ArmController } from "../adapters/mg400ArmController.js";
import { readJsonBody, sendJson } from "../utils/http.js";

const port = Number(process.env.ROBOT_GATEWAY_PORT || 8010);

async function route(request, response) {
  const url = new URL(request.url, `http://${request.headers.host}`);

  if (request.method === "GET" && url.pathname === "/health") {
    sendJson(response, 200, {
      ok: true,
      service: "robot-gateway",
      protocols: ["http-json", "mg400-tcp", "simulation-dashboard-tcp"]
    });
    return;
  }

  if (request.method === "POST" && url.pathname === "/v1/robot/execute") {
    const payload = await readJsonBody(request);
    const controller = new Mg400ArmController({ config: payload.config });
    sendJson(response, 200, await controller.execute(payload.step || {}));
    return;
  }

  const actionMatch = url.pathname.match(/^\/v1\/robot\/([^/]+)$/);
  if (request.method === "POST" && actionMatch) {
    const payload = await readJsonBody(request);
    const action = decodeURIComponent(actionMatch[1]);
    sendJson(response, 200, await Mg400ArmController.runCommand(action, payload));
    return;
  }

  sendJson(response, 404, { ok: false, error: "Route not found." });
}

const server = http.createServer((request, response) => {
  route(request, response).catch((error) => {
    sendJson(response, error.statusCode || 500, {
      ok: false,
      error: error.message,
      details: error.details || null
    });
  });
});

server.listen(port, () => {
  console.log(`Robot Gateway listening on http://localhost:${port}`);
});
