import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

export async function writeModelInputYamlFile({ runId, yaml }) {
  const outputDir = path.join(process.cwd(), "output", "yaml");
  await mkdir(outputDir, { recursive: true });

  const filePath = path.join(outputDir, `${runId}.yaml`);
  await writeFile(filePath, yaml, "utf8");

  return filePath;
}
