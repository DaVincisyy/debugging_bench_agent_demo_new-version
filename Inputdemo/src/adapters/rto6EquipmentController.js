import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { mkdir, readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const moduleDir = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(moduleDir, "..", "..");
const defaultConfigPath = path.join(projectRoot, "config", "rto6.json");
const bridgePath = path.join(projectRoot, "scripts", "rto6_bridge.py");
const excelBuilderPath = path.join(projectRoot, "scripts", "rto6_excel_report.mjs");

function pythonCommand() {
  const homeDir = process.env.USERPROFILE || "";
  const candidates = [
    process.env.PYTHON,
    process.env.PYTHON_EXE,
    path.join(homeDir, "AppData", "Local", "Programs", "Python", "Python312", "python.exe"),
    "python"
  ].filter(Boolean);
  return candidates.find((candidate) => (
    path.isAbsolute(candidate) ? existsSync(candidate) : true
  )) || "python";
}

function spreadsheetNodeCommand() {
  const homeDir = process.env.USERPROFILE || "";
  const bundled = path.join(
    homeDir,
    ".cache",
    "codex-runtimes",
    "codex-primary-runtime",
    "dependencies",
    "node",
    "bin",
    "node.exe"
  );
  return process.env.SPREADSHEET_NODE_EXE
    || (existsSync(bundled) ? bundled : process.execPath);
}

async function readConfig(configPath = defaultConfigPath) {
  return JSON.parse(await readFile(configPath, "utf8"));
}

function safeSegment(value, fallback) {
  const clean = String(value || "")
    .trim()
    .replace(/[^a-zA-Z0-9._-]+/g, "_")
    .replace(/^_+|_+$/g, "");
  return clean || fallback;
}

function timestampForFile(date = new Date()) {
  return date.toISOString().replace(/[-:]/g, "").replace(/\.\d{3}Z$/, "Z");
}

function runJsonProcess(command, args, payload, cwd = projectRoot) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      cwd,
      stdio: ["pipe", "pipe", "pipe"],
      windowsHide: true
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => { stdout += chunk.toString("utf8"); });
    child.stderr.on("data", (chunk) => { stderr += chunk.toString("utf8"); });
    child.on("error", reject);
    child.on("close", (code) => {
      let parsed;
      try {
        const jsonLine = stdout
          .split(/\r?\n/)
          .map((line) => line.trim())
          .filter(Boolean)
          .reverse()
          .find((line) => line.startsWith("{"));
        parsed = JSON.parse(jsonLine || "{}");
      } catch {
        reject(new Error(`Process returned invalid JSON: ${stdout || stderr}`));
        return;
      }
      if (code !== 0 || parsed.ok === false) {
        const error = new Error(parsed.error || stderr.trim() || `Process exited with code ${code}`);
        error.details = parsed;
        reject(error);
        return;
      }
      resolve(parsed);
    });
    child.stdin.end(JSON.stringify(payload));
  });
}

export class Rto6EquipmentController {
  constructor({ configPath = defaultConfigPath, bridgeRunner = null, excelRunner = null } = {}) {
    this.configPath = configPath;
    this.bridgeRunner = bridgeRunner;
    this.excelRunner = excelRunner;
  }

  async captureCurrentDisplayReport({
    runId,
    caseId,
    targetPoints = [],
    robotPose,
    capturedAt = new Date()
  }) {
    const config = await readConfig(this.configPath);
    const folder = path.resolve(
      projectRoot,
      config.outputDir,
      `${safeSegment(caseId, "case")}-${safeSegment(runId, "run")}`
    );
    await mkdir(folder, { recursive: true });
    const stamp = timestampForFile(capturedAt);
    const screenshotPath = path.join(folder, `RTO6_MEAN_${stamp}.jpg`);
    const outputPath = path.join(folder, `RTO6_MEAN_${stamp}.xlsx`);

    const capture = this.bridgeRunner
      ? await this.bridgeRunner({ config, screenshotPath })
      : await runJsonProcess(
          pythonCommand(),
          [bridgePath],
          { action: "capture_current_display", config, screenshotPath }
        );

    const workbookInput = {
      outputPath,
      screenshotPath: capture.screenshotPath,
      caseId,
      runId,
      capturedAt: capture.capturedAt,
      instrument: capture.instrument,
      scpiAddress: `${capture.host}:${capture.port}`,
      targetPoints,
      robotPose,
      measurementSlot: capture.measurementSlot,
      measurement: capture.measurement,
      source: capture.source,
      value: capture.value,
      unit: capture.unit,
      status: "COMPLETED"
    };
    const workbook = this.excelRunner
      ? await this.excelRunner(workbookInput)
      : await runJsonProcess(
          spreadsheetNodeCommand(),
          [excelBuilderPath],
          workbookInput
        );

    return {
      stepId: "rto6-current-display-capture",
      status: "COMPLETED",
      instrument: "RTO6",
      signal: `Current display: ${capture.measurement}`,
      measurementSlot: capture.measurementSlot,
      measurement: capture.measurement,
      source: capture.source,
      value: capture.value,
      unit: capture.unit,
      pass: null,
      capturedAt: capture.capturedAt,
      screenshotPath: capture.screenshotPath,
      workbookPath: workbook.outputPath,
      workbookPreviewPath: workbook.previewPath,
      durationMs: capture.durationMs
    };
  }
}
