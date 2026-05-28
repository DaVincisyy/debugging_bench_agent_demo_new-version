import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";

const configPath = path.resolve("config", "mg400.json");

const defaultConfig = Object.freeze({
  mode: process.env.MG400_MODE || "simulation",
  ip: process.env.MG400_IP || "127.0.0.1",
  dashboardPort: Number(process.env.MG400_DASHBOARD_PORT || 29999),
  motionPort: Number(process.env.MG400_MOTION_PORT || 30003),
  feedbackPort: Number(process.env.MG400_FEEDBACK_PORT || 30004),
  simHost: process.env.MG400_SIM_HOST || "127.0.0.1",
  simDashboardPort: Number(process.env.MG400_SIM_DASHBOARD_PORT || process.env.MG400_SIM_PORT || 29999),
  simFeedbackPort: Number(process.env.MG400_SIM_FEEDBACK_PORT || 30004),
  speed: Number(process.env.MG400_SPEED || 30),
  load: Number(process.env.MG400_LOAD || 0.5),
  timeoutMs: Number(process.env.MG400_TIMEOUT_MS || 5000),
  autoEnable: process.env.MG400_AUTO_ENABLE !== "false",
  motionCommand: process.env.MG400_MOTION_COMMAND || "MovJ",
  returnHome: process.env.MG400_RETURN_HOME === "true",
  homePose: { x: 350, y: 0, z: 0, r: 0 }
});

function normalizeNumber(value, fallback) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function normalizePose(value, fallback) {
  const source = value && typeof value === "object" ? value : {};
  return {
    x: normalizeNumber(source.x, fallback.x),
    y: normalizeNumber(source.y, fallback.y),
    z: normalizeNumber(source.z, fallback.z),
    r: normalizeNumber(source.r, fallback.r)
  };
}

export function normalizeMg400Config(value = {}) {
  const merged = { ...defaultConfig, ...value };
  const mode = merged.mode === "mg400" || merged.mode === "real" ? "mg400" : "simulation";
  const motionCommand = merged.motionCommand === "MovL" ? "MovL" : "MovJ";

  return {
    mode,
    ip: String(merged.ip || defaultConfig.ip).trim(),
    dashboardPort: normalizeNumber(merged.dashboardPort, defaultConfig.dashboardPort),
    motionPort: normalizeNumber(merged.motionPort, defaultConfig.motionPort),
    feedbackPort: normalizeNumber(merged.feedbackPort, defaultConfig.feedbackPort),
    simHost: String(merged.simHost || defaultConfig.simHost).trim(),
    simDashboardPort: normalizeNumber(merged.simDashboardPort, defaultConfig.simDashboardPort),
    simFeedbackPort: normalizeNumber(merged.simFeedbackPort, defaultConfig.simFeedbackPort),
    speed: normalizeNumber(merged.speed, defaultConfig.speed),
    load: normalizeNumber(merged.load, defaultConfig.load),
    timeoutMs: normalizeNumber(merged.timeoutMs, defaultConfig.timeoutMs),
    autoEnable: Boolean(merged.autoEnable),
    motionCommand,
    returnHome: Boolean(merged.returnHome),
    homePose: normalizePose(merged.homePose, defaultConfig.homePose)
  };
}

export async function readMg400Config() {
  try {
    const raw = await readFile(configPath, "utf8");
    return normalizeMg400Config(JSON.parse(raw));
  } catch (error) {
    if (error.code !== "ENOENT") {
      throw error;
    }
    return normalizeMg400Config();
  }
}

export async function writeMg400Config(update) {
  const next = normalizeMg400Config({ ...(await readMg400Config()), ...update });
  await mkdir(path.dirname(configPath), { recursive: true });
  await writeFile(configPath, `${JSON.stringify(next, null, 2)}\n`, "utf8");
  return next;
}

export { configPath as mg400ConfigPath };
