/**
 * Submit re-run case-110 VLM result (pixel [573, 1419]) to simulation arm.
 */
import { SimulationArmController } from "../src/adapters/simulationArmController.js";
import { readMg400Config } from "../src/adapters/mg400Config.js";
import { evaluateMg400PoseReachability } from "../src/domain/mg400Reachability.js";

const sleep = (ms) => new Promise(r => setTimeout(r, ms));

const CASE_LABEL = "case-110 (rerun)";
const PIXEL = { x: 573, y: 1419 };

async function main() {
  console.log(`=== ${CASE_LABEL} → 仿真机械臂 ===`);
  console.log(`VLM 结果: TP1 像素坐标 [${PIXEL.x}, ${PIXEL.y}]\n`);

  const rawPose = { x: PIXEL.x, y: PIXEL.y, z: 0, r: 0 };
  console.log(`原始像素位姿: x=${rawPose.x}, y=${rawPose.y}, z=${rawPose.z}, r=${rawPose.r}`);

  const reachability = evaluateMg400PoseReachability(rawPose);
  console.log(`\n可达性: ${reachability.reachable ? '✓ 可达' : '✗ 阻塞'}`);
  console.log(`已调整: ${reachability.adjusted}`);
  if (reachability.violations?.length) {
    console.log("越限项:");
    reachability.violations.forEach(v => console.log(`  - ${v}`));
  }
  const motionPose = reachability.pose;
  if (motionPose) {
    console.log(`\n执行 TCP: x=${motionPose.x}mm, y=${motionPose.y}mm, z=${motionPose.z}mm, r=${motionPose.r}°`);
    console.log(`         [${(motionPose.x/1000).toFixed(4)}, ${(motionPose.y/1000).toFixed(4)}, ${(motionPose.z/1000).toFixed(4)}]m`);
  }
  if (reachability.message) console.log(`\n提示: ${reachability.message}`);

  if (!reachability.reachable) {
    console.log(`\n⛔ 位姿被可达性守卫阻塞`);
    return;
  }

  console.log(`\n--- 启动仿真 & 执行运动 ---`);
  const config = await readMg400Config();
  const arm = new SimulationArmController();

  const step = {
    id: "case110-rerun-motion",
    kind: "ARM_MOTION",
    command: "MOVE_TO_MG400_POSE",
    targetLocationId: "TP1",
    targetPose: motionPose
  };

  const result = await arm.execute(step);
  console.log(`\n=== 运动结果 ===`);
  console.log(`状态: ${result.status}`);
  if (result.executedPose) {
    console.log(`执行 TCP: x=${result.executedPose.x}mm, y=${result.executedPose.y}mm, z=${result.executedPose.z}mm, r=${result.executedPose.r}°`);
  }
  if (result.durationMs) console.log(`命令耗时: ${result.durationMs}ms`);

  console.log(`\n--- 等待运动完成 (15s) ---`);
  for (let i = 15; i > 0; i--) {
    await sleep(1000);
    try {
      const status = await SimulationArmController.runCommand("status", { config });
      if (status.robot?.pose) {
        const p = status.robot.pose;
        console.log(`  t=${15-i+1}s  TCP: x=${p.x.toFixed(1)}, y=${p.y.toFixed(1)}, z=${p.z.toFixed(1)}, r=${p.r.toFixed(1)}`);
      }
    } catch { /* ignore */ }
  }

  try {
    const status = await SimulationArmController.runCommand("status", { config });
    if (status.robot?.pose) {
      const p = status.robot.pose;
      console.log(`\n最终 TCP: x=${p.x}mm, y=${p.y}mm, z=${p.z}mm, r=${p.r}°`);
    }
  } catch (e) {
    console.log(`\n获取最终位姿失败: ${e.message}`);
  }

  console.log(`\n✅ case-110 (rerun) 提交给仿真完成！`);
}

main().catch(err => {
  console.error("❌ 失败:", err.message);
  process.exit(1);
});
