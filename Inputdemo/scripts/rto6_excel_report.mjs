import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const rawInput = await readStdin();
const input = JSON.parse(rawInput || "{}");
const outputPath = path.resolve(input.outputPath);
const screenshotPath = path.resolve(input.screenshotPath);
const screenshotBytes = await fs.readFile(screenshotPath);
const screenshotMime = screenshotPath.toLowerCase().endsWith(".png")
  ? "image/png"
  : "image/jpeg";
const screenshotDataUrl = `data:${screenshotMime};base64,${screenshotBytes.toString("base64")}`;

const workbook = Workbook.create();
const sheet = workbook.worksheets.add("RTO6 Measurement");
sheet.showGridLines = false;
sheet.freezePanes.freezeRows(2);

sheet.getRange("A1:H1").merge();
const measurementLabel = String(input.measurement || "Current").toUpperCase();
sheet.getRange("A1").values = [[`RTO6 ${measurementLabel} Voltage Capture`]];
sheet.getRange("A1:H1").format = {
  fill: "#17365D",
  font: { bold: true, color: "#FFFFFF", size: 16 },
  horizontalAlignment: "center",
  verticalAlignment: "center"
};
sheet.getRange("A1:H1").format.rowHeight = 30;

sheet.getRange("A3:F6").values = [
  ["Case ID", input.caseId || "", "Run ID", input.runId || "", "Captured At", input.capturedAt || ""],
  ["Instrument", input.instrument || "", "SCPI Address", input.scpiAddress || "", "Target Points", (input.targetPoints || []).join(", ")],
  ["Measurement Slot", input.measurementSlot ?? "", "Measurement", input.measurement || "", "Source", input.source || ""],
  ["Fixed MG400 Pose", `X=${input.robotPose?.x}, Y=${input.robotPose?.y}`, "Z / R", `Z=${input.robotPose?.z}, R=${input.robotPose?.r}`, "Status", input.status || "COMPLETED"]
];
sheet.getRange("A3:F6").format = {
  verticalAlignment: "center",
  wrapText: true,
  borders: {
    insideHorizontal: { style: "thin", color: "#D9E2F3" },
    bottom: { style: "thin", color: "#B4C6E7" }
  }
};
sheet.getRange("A3:A6").format = { fill: "#D9EAF7", font: { bold: true, color: "#17365D" } };
sheet.getRange("C3:C6").format = { fill: "#D9EAF7", font: { bold: true, color: "#17365D" } };
sheet.getRange("E3:E6").format = { fill: "#D9EAF7", font: { bold: true, color: "#17365D" } };

sheet.getRange("A8:B8").merge();
sheet.getRange("A8").values = [[`${measurementLabel} Result (${input.unit || ""})`]];
sheet.getRange("A9:B10").merge();
sheet.getRange("A9").values = [[Number(input.value)]];
sheet.getRange("A8:B10").format = {
  horizontalAlignment: "center",
  verticalAlignment: "center",
  borders: { preset: "outside", style: "medium", color: "#4472C4" }
};
sheet.getRange("A8:B8").format = {
  fill: "#4472C4",
  font: { bold: true, color: "#FFFFFF" }
};
sheet.getRange("A9:B10").format = {
  fill: "#EAF2F8",
  font: { bold: true, color: "#17365D", size: 20 },
  numberFormat: `0.000000 "${String(input.unit || "").replace(/"/g, "")}"`
};

sheet.getRange("A12:H12").merge();
sheet.getRange("A12").values = [["RTO6 Screen Capture"]];
sheet.getRange("A12:H12").format = {
  fill: "#D9EAF7",
  font: { bold: true, color: "#17365D" },
  horizontalAlignment: "left"
};
sheet.images.add({
  dataUrl: screenshotDataUrl,
  anchor: {
    from: { row: 12, col: 0 },
    extent: { widthPx: 960, heightPx: 540 }
  }
});

sheet.getRange("A3:H40").format.font = { name: "Aptos", size: 10 };
sheet.getRange("A1:H1").format.font = { name: "Aptos Display", bold: true, color: "#FFFFFF", size: 16 };
sheet.getRange("A9:B10").format.font = { name: "Aptos Display", bold: true, color: "#17365D", size: 20 };
sheet.getRange("A:H").format.columnWidth = 18;
sheet.getRange("B:B").format.columnWidth = 25;
sheet.getRange("D:D").format.columnWidth = 25;
sheet.getRange("F:F").format.columnWidth = 24;
sheet.getRange("A3:H6").format.rowHeight = 28;
sheet.getRange("A13:H35").format.rowHeight = 20;

await fs.mkdir(path.dirname(outputPath), { recursive: true });
const exported = await SpreadsheetFile.exportXlsx(workbook);
await exported.save(outputPath);

const inspect = await workbook.inspect({
  kind: "table",
  range: "RTO6 Measurement!A1:F10",
  include: "values,formulas",
  tableMaxRows: 12,
  tableMaxCols: 8
});
const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 50 },
  summary: "formula error scan"
});
const preview = await workbook.render({
  sheetName: "RTO6 Measurement",
  range: "A1:H39",
  scale: 1,
  format: "png"
});
const previewPath = outputPath.replace(/\.xlsx$/i, ".preview.png");
await fs.writeFile(previewPath, new Uint8Array(await preview.arrayBuffer()));

process.stdout.write(JSON.stringify({
  ok: true,
  outputPath,
  previewPath,
  inspect: inspect.ndjson,
  errors: errors.ndjson
}));

async function readStdin() {
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  return Buffer.concat(chunks).toString("utf8");
}
