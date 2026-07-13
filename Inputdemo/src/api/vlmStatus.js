const DEFAULT_WORKER_COUNT = 2;
const TERMINAL = new Set(["succeeded", "failed", "cancelled", "completed"]);

export class VlmStatusMonitor {
  constructor({ workerCount = DEFAULT_WORKER_COUNT } = {}) {
    this.workers = Array.from({ length: workerCount }, (_, index) => ({
      id: `vlm-${index + 1}`,
      runId: null
    }));
    this.runs = new Map();
    this.mg400 = {
      status: "idle",
      runId: null,
      stepId: null,
      action: "MG400 空闲",
      updatedAt: Date.now()
    };
  }

  registerRun(run, { maxSteps = null, serviceUrl = null, runnerMode = null } = {}) {
    const now = Date.now();
    const existing = this.runs.get(run.runId) || {};
    this.runs.set(run.runId, {
      ...existing,
      runId: run.runId,
      caseId: run.input?.caseId || null,
      instruction: run.input?.instruction || run.input?.command || "",
      status: existing.status || "preparing",
      phase: existing.phase || "preparing_task",
      action: existing.action || "正在准备 task.yaml 和 VLM 输入",
      serviceUrl,
      runnerMode,
      maxSteps,
      step: null,
      lastTool: null,
      lastEventType: null,
      lastEventAt: now,
      createdAt: existing.createdAt || now,
      startedAt: existing.startedAt || null,
      finishedAt: existing.finishedAt || null,
      workerId: existing.workerId || null,
      error: null
    });
    return this.runs.get(run.runId);
  }

  markSubmitted(runId, details = {}) {
    this.patchRun(runId, {
      status: "queued",
      phase: "queued",
      action: "已提交 VLM 服务，等待 worker 开始",
      serviceStatus: details.serviceStatus || null,
      serviceUrl: details.serviceUrl || null,
      taskFile: details.taskFile || null,
      lastEventType: "node.vlm_forwarded"
    });
  }

  handleEvent(runId, type, payload = {}) {
    const existingRun = this.runs.get(runId);
    const isTerminalEvent = type === "agent.final"
      || type === "agent.failed"
      || type === "node.failed"
      || type === "node.completed";
    if (existingRun && TERMINAL.has(existingRun.status) && !isTerminalEvent) {
      this.patchRun(runId, {
        lastEventType: type,
        lastEventAt: Date.now()
      });
      return;
    }

    const patch = {
      lastEventType: type,
      lastEventAt: Date.now()
    };

    if (type === "planner.progress") {
      patch.status = "running";
      patch.phase = "planner_progress";
      patch.action = payload.message || "Planner 执行中...";
    } else if (type === "agent.split_started") {
      const worker = this.assignWorker(runId);
      patch.status = "running";
      patch.phase = "planner";
      patch.action = "🔍 Planner 正在分析任务，准备拆分 TP";
      patch.startedAt = this.runs.get(runId)?.startedAt || Date.now();
      patch.workerId = worker?.id || this.runs.get(runId)?.workerId || null;
    } else if (type === "planner.started") {
      patch.status = "running";
      patch.phase = "planner_calling";
      patch.action = "🧠 Planner 正在看图 + 分析目标 TP...";
    } else if (type === "planner.finished") {
      const points = payload.target_points || [];
      const count = payload.count || points.length;
      patch.status = "running";
      patch.phase = "planner_done";
      patch.action = count > 0
        ? `✅ Planner 发现 ${count} 个目标点：${points.join(", ")}`
        : "⚠️ Planner 未发现目标点，回退到单 Agent 模式";
    } else if (type === "split.children_starting") {
      patch.phase = "splitting";
      patch.action = `🚀 启动 ${payload.child_count || 0} 个子 VLM 任务并行执行`;
    } else if (type === "split.child_started") {
      patch.phase = "child_running";
      patch.action = `📌 子任务 ${payload.target_point} 已启动`;
      // Release planner's worker so children can use it
      this.releaseWorker(runId);
    } else if (type === "split.child_finished") {
      patch.phase = "child_running";
      patch.action = `✅ 子任务 ${payload.target_point} 完成`;
    } else if (type === "split.child_failed") {
      patch.phase = "child_failed";
      patch.action = `❌ 子任务 ${payload.target_point} 失败: ${payload.error || ""}`;
    } else if (type === "agent.started") {
      const worker = this.assignWorker(runId);
      patch.status = "running";
      patch.phase = "started";
      patch.action = "VLM agent 已开始执行任务";
      patch.startedAt = this.runs.get(runId)?.startedAt || Date.now();
      patch.workerId = worker?.id || this.runs.get(runId)?.workerId || null;
    } else if (type === "agent.waiting") {
      // Child runs may never emit agent.started; assign worker on first activity
      if (!this.runs.get(runId)?.workerId) {
        const worker = this.assignWorker(runId);
        if (worker) patch.workerId = worker.id;
      }
      patch.status = "running";
      patch.phase = "llm_call";
      patch.step = displayStep(payload);
      patch.action = "正在调用大模型 / 等待模型返回";
    } else if (type === "tool.started") {
      const toolName = payload.tool_call?.name || payload.name || null;
      patch.status = "running";
      patch.phase = "tool_running";
      patch.step = displayStep(payload);
      patch.lastTool = toolName || this.runs.get(runId)?.lastTool || null;
      patch.action = toolName ? actionForTool(toolName) : "VLM tool is running";
    } else if (type === "tool.finished") {
      const toolName = payload.tool_call?.name || payload.name || null;
      const failed = payload.tool_result?.ok === false || payload.tool_result?.status === "failed";
      patch.status = "running";
      patch.phase = failed ? "tool_failed" : "tool_finished";
      patch.step = displayStep(payload);
      patch.lastTool = toolName || this.runs.get(runId)?.lastTool || null;
      patch.action = toolName
        ? `${actionForTool(toolName)} ${failed ? "failed" : "done"}`
        : "VLM tool finished";
    } else if (type === "agent.step") {
      const toolNames = toolNamesFromPayload(payload);
      patch.status = "running";
      patch.phase = toolNames.length ? "tool_finished" : "step_finished";
      patch.step = displayStep(payload);
      patch.lastTool = toolNames[0] || this.runs.get(runId)?.lastTool || null;
      patch.action = toolNames.length
        ? actionForTool(toolNames[0])
        : "模型已返回，正在进入下一步";
    } else if (type === "agent.run_dir") {
      patch.phase = "workspace_ready";
      patch.action = "VLM 工作目录已创建";
      patch.runDir = payload.run_dir || payload.workspace || null;
    } else if (type === "agent.final") {
      patch.status = "succeeded";
      patch.phase = "vlm_finished";
      patch.action = "VLM 已生成 final_answer";
      patch.finishedAt = Date.now();
      this.releaseWorker(runId);
    } else if (type === "agent.failed" || type === "node.failed") {
      patch.status = "failed";
      patch.phase = "failed";
      patch.action = "VLM 运行失败";
      patch.error = payload.error || null;
      patch.finishedAt = Date.now();
      this.releaseWorker(runId);
    } else if (type === "node.execution_mapping_created") {
      patch.phase = "mapping_execution";
      patch.action = "VLM 输出已转成 MG400 执行计划";
    } else if (type === "robot.action_finished") {
      patch.phase = "robot_action_finished";
      patch.action = "MG400 动作已返回结果";
    } else if (type === "node.completed") {
      patch.status = "completed";
      patch.phase = "completed";
      patch.action = "报告已生成";
      patch.finishedAt = Date.now();
      this.releaseWorker(runId);
    }

    this.patchRun(runId, patch);
  }

  markRunFailed(runId, error) {
    this.patchRun(runId, {
      status: "failed",
      phase: "failed",
      action: "运行失败",
      error: error?.message || String(error || ""),
      finishedAt: Date.now(),
      lastEventType: "node.failed",
      lastEventAt: Date.now()
    });
    this.releaseWorker(runId);
  }

  markMg400(status, details = {}) {
    this.mg400 = {
      status,
      runId: details.runId || null,
      stepId: details.stepId || null,
      action: details.action || (status === "executing" ? "MG400 执行中" : "MG400 空闲"),
      updatedAt: Date.now(),
      result: details.result || null
    };
  }

  snapshot() {
    const now = Date.now();
    const runs = [...this.runs.values()];
    const workers = this.workers.map((worker) => {
      const run = worker.runId ? this.runs.get(worker.runId) : null;
      if (!run || TERMINAL.has(run.status)) {
        return {
          id: worker.id,
          status: "idle",
          runId: null,
          action: "等待新任务",
          phase: "idle",
          elapsedMs: 0
        };
      }
      return formatRunForWorker(worker.id, run, now);
    });
    const assigned = new Set(workers.map((worker) => worker.runId).filter(Boolean));
    const queue = runs
      .filter((run) => !assigned.has(run.runId) && !TERMINAL.has(run.status) && run.status !== "completed")
      .sort((a, b) => a.createdAt - b.createdAt)
      .map((run) => formatQueuedRun(run, now));
    const recent = runs
      .filter((run) => TERMINAL.has(run.status) || run.status === "completed")
      .sort((a, b) => (b.finishedAt || b.lastEventAt || 0) - (a.finishedAt || a.lastEventAt || 0))
      .slice(0, 6)
      .map((run) => formatQueuedRun(run, now));

    return {
      ok: true,
      workers,
      queue,
      recent,
      mg400: this.mg400,
      activeCount: workers.filter((worker) => worker.status !== "idle").length,
      queueCount: queue.length,
      updatedAt: now
    };
  }

  patchRun(runId, patch) {
    const existing = this.runs.get(runId);
    if (!existing) return null;
    const next = {
      ...existing,
      ...patch,
      lastEventAt: patch.lastEventAt || Date.now()
    };
    this.runs.set(runId, next);
    return next;
  }

  assignWorker(runId) {
    const existingRun = this.runs.get(runId);
    if (existingRun?.workerId) {
      return this.workers.find((worker) => worker.id === existingRun.workerId) || null;
    }
    const worker = this.workers.find((item) => item.runId === null);
    if (!worker) return null;
    worker.runId = runId;
    this.patchRun(runId, { workerId: worker.id });
    return worker;
  }

  releaseWorker(runId) {
    const worker = this.workers.find((item) => item.runId === runId);
    if (worker) worker.runId = null;
  }
}

function displayStep(payload) {
  const raw = payload.index ?? payload.step;
  const value = Number(raw);
  return Number.isFinite(value) ? value + 1 : null;
}

function toolNamesFromPayload(payload) {
  const calls = payload.tool_calls || (payload.tool_call ? [payload.tool_call] : []);
  return calls
    .map((item) => item?.name)
    .filter(Boolean);
}

function actionForTool(name) {
  const map = {
    view_image: "正在查看图片 / 视觉证据",
    vlm_roi: "正在请求 VLM 检查局部区域",
    search_pdf_text: "正在搜索 PDF 文本",
    read_text_file: "正在读取工作流或中间文件",
    write_text_file: "正在写入中间分析文件",
    run_python: "正在运行视觉处理 / 坐标计算脚本",
    finish: "正在生成最终答案"
  };
  return map[name] || `正在执行工具：${name}`;
}

function formatRunForWorker(workerId, run, now) {
  return {
    id: workerId,
    status: run.status || "running",
    runId: run.runId,
    caseId: run.caseId,
    instruction: run.instruction,
    phase: run.phase,
    action: run.action,
    step: run.step,
    maxSteps: run.maxSteps,
    lastTool: run.lastTool,
    lastEventType: run.lastEventType,
    elapsedMs: now - (run.startedAt || run.createdAt || now),
    serviceUrl: run.serviceUrl,
    error: run.error
  };
}

function formatQueuedRun(run, now) {
  const isTerminal = TERMINAL.has(run.status) || run.status === "completed";
  const end = isTerminal ? (run.finishedAt || run.lastEventAt || now) : now;
  return {
    runId: run.runId,
    caseId: run.caseId,
    status: run.status,
    phase: run.phase,
    action: run.action,
    step: run.step,
    maxSteps: run.maxSteps,
    lastTool: run.lastTool,
    lastEventType: run.lastEventType,
    elapsedMs: end - (run.startedAt || run.createdAt || end),
    workerId: run.workerId,
    error: run.error
  };
}
