"""HTTP service wrapper for the VLM agent.

This keeps the existing CLI and Agent core intact while exposing a long-running
FastAPI process that Node.js can talk to over a structured protocol.
"""

from __future__ import annotations

import json
import asyncio
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .agent import Agent
from .config import compose_agent_question, load_config, load_task


TERMINAL_STATES = {"succeeded", "failed", "cancelled"}


class RunRequest(BaseModel):
    task_file: str = Field(..., description="Path to a Debugging-agent-v2 task YAML file.")
    run_id: str | None = None
    name: str | None = None
    env_file: str = ".env"
    workspace: str = "workspace"
    max_steps: int | None = None
    model: str | None = None
    base_url: str | None = None
    no_native_tools: bool = False


class ObservationRequest(BaseModel):
    type: str = "observation"
    source: str = "node"
    payload: dict[str, Any] = Field(default_factory=dict)


@dataclass
class ManagedRun:
    run_id: str
    request: RunRequest
    status: str = "queued"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    run_dir: str | None = None
    summary_path: str | None = None
    final_answer: Any = None
    stopped_reason: str | None = None
    error: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    future: Future[Any] | None = None
    cancel_requested: bool = False


class RunManager:
    def __init__(self, max_workers: int = 2) -> None:
        self._runs: dict[str, ManagedRun] = {}
        self._lock = threading.RLock()
        self._executor = ThreadPoolExecutor(max_workers=max_workers)

    def create(self, req: RunRequest) -> ManagedRun:
        run_id = req.run_id or f"run_{uuid.uuid4().hex[:12]}"
        with self._lock:
            if run_id in self._runs:
                raise ValueError(f"Run already exists: {run_id}")
            run = ManagedRun(run_id=run_id, request=req)
            self._runs[run_id] = run
            self._append_event(run, "agent.queued", {"task_file": req.task_file})
            run.future = self._executor.submit(self._execute, run_id)
            return run

    def get(self, run_id: str) -> ManagedRun | None:
        with self._lock:
            return self._runs.get(run_id)

    def cancel(self, run_id: str) -> ManagedRun | None:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                return None
            run.cancel_requested = True
            if run.status == "queued" and run.future is not None and run.future.cancel():
                run.status = "cancelled"
                run.updated_at = time.time()
                self._append_event(run, "agent.cancelled", {"reason": "cancelled-before-start"})
            else:
                self._append_event(run, "agent.cancel_requested", {})
            return run

    def add_observation(self, run_id: str, observation: ObservationRequest) -> ManagedRun | None:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                return None
            self._append_event(run, "observation.received", {
                "type": observation.type,
                "source": observation.source,
                "payload": observation.payload,
            })
            run.updated_at = time.time()
            return run

    def events_since(self, run_id: str, seq: int = 0) -> list[dict[str, Any]]:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                return []
            return [event for event in run.events if int(event["seq"]) > seq]

    def snapshot(self, run: ManagedRun) -> dict[str, Any]:
        with self._lock:
            return {
                "run_id": run.run_id,
                "status": run.status,
                "created_at": run.created_at,
                "updated_at": run.updated_at,
                "run_dir": run.run_dir,
                "summary_path": run.summary_path,
                "final_answer": run.final_answer,
                "stopped_reason": run.stopped_reason,
                "error": run.error,
                "cancel_requested": run.cancel_requested,
                "event_count": len(run.events),
            }

    def _execute(self, run_id: str) -> None:
        run = self.get(run_id)
        if run is None:
            return
        req = run.request
        with self._lock:
            if run.cancel_requested:
                run.status = "cancelled"
                self._append_event(run, "agent.cancelled", {"reason": "cancelled-before-start"})
                return
            run.status = "running"
            run.updated_at = time.time()
            self._append_event(run, "agent.started", {"task_file": req.task_file})

        try:
            overrides: dict[str, Any] = {
                "workspace_dir": Path(req.workspace),
            }
            if req.max_steps is not None:
                overrides["max_steps"] = req.max_steps
            if req.model:
                overrides["VLM_MODEL"] = req.model
            if req.base_url:
                overrides["VLM_BASE_URL"] = req.base_url
            if req.no_native_tools:
                overrides["VLM_USE_NATIVE_TOOLS"] = "false"

            cfg = load_config(env_path=req.env_file, overrides=overrides)
            task = load_task(req.task_file)
            agent = Agent(cfg)
            result = agent.run(
                question=compose_agent_question(task),
                inputs=task.get("inputs", {}),
                run_name=req.name or Path(req.task_file).stem,
                event_sink=lambda event_type, payload: self._append_event_threadsafe(run_id, event_type, payload),
            )
            summary_path = result.run_dir / "summary.json" if result.run_dir else None
            with self._lock:
                run.run_dir = str(result.run_dir) if result.run_dir else None
                run.summary_path = str(summary_path) if summary_path else None
                run.final_answer = result.final_answer
                run.stopped_reason = result.stopped_reason
                run.status = "succeeded" if result.final_answer is not None else "failed"
                run.error = result.last_error
                run.updated_at = time.time()
                event_type = "agent.final" if run.status == "succeeded" else "agent.failed"
                self._append_event(run, event_type, {
                    "final_answer": result.final_answer,
                    "stopped_reason": result.stopped_reason,
                    "summary_path": run.summary_path,
                    "run_dir": run.run_dir,
                    "error": run.error,
                })
                action = self._build_robot_action(result.final_answer)
                if action is not None:
                    self._append_event(run, "robot.action_proposed", action)
        except BaseException as exc:  # noqa: BLE001
            with self._lock:
                run.status = "failed"
                run.error = str(exc)
                run.updated_at = time.time()
                self._append_event(run, "agent.failed", {"error": run.error})

    def _build_robot_action(self, final_answer: Any) -> dict[str, Any] | None:
        if not isinstance(final_answer, dict):
            return None
        pose = final_answer.get("mg400Pose") or final_answer.get("mg400_pose") or final_answer.get("pose")
        pixel = final_answer.get("pixel")
        if pose is None and pixel is None:
            return None
        return {
            "action": {
                "kind": "move_to_vlm_target",
                "pose": pose,
                "pixel": pixel,
                "test_point": final_answer.get("tp_id") or final_answer.get("test_point"),
                "confidence": final_answer.get("confidence"),
            },
            "requires_confirmation": True,
        }

    def _append_event(self, run: ManagedRun, event_type: str, payload: dict[str, Any]) -> None:
        event = {
            "event_id": f"evt_{uuid.uuid4().hex[:12]}",
            "run_id": run.run_id,
            "seq": len(run.events) + 1,
            "type": event_type,
            "timestamp": time.time(),
            "payload": payload,
        }
        run.events.append(event)

    def _append_event_threadsafe(self, run_id: str, event_type: str, payload: dict[str, Any]) -> None:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                return
            self._append_event(run, event_type, payload)
            run.updated_at = time.time()


manager = RunManager()
app = FastAPI(title="Debugging Agent VLM Service")


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "service": "debugging-agent-v2"}


@app.post("/v1/runs", status_code=202)
def create_run(req: RunRequest) -> dict[str, Any]:
    try:
        run = manager.create(req)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return manager.snapshot(run)


@app.get("/v1/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    run = manager.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    return manager.snapshot(run)


@app.post("/v1/runs/{run_id}/cancel")
def cancel_run(run_id: str) -> dict[str, Any]:
    run = manager.cancel(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    return manager.snapshot(run)


@app.post("/v1/runs/{run_id}/observations")
def add_observation(run_id: str, observation: ObservationRequest) -> dict[str, Any]:
    run = manager.add_observation(run_id, observation)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    return manager.snapshot(run)


@app.get("/v1/runs/{run_id}/events")
async def get_events(run_id: str, request: Request, since: int = 0) -> Any:
    run = manager.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found.")

    wants_sse = "text/event-stream" in request.headers.get("accept", "")
    if not wants_sse:
        return {"run_id": run_id, "events": manager.events_since(run_id, since)}

    async def stream():
        seq = since
        while True:
            events = manager.events_since(run_id, seq)
            for event in events:
                seq = int(event["seq"])
                yield f"event: {event['type']}\n"
                yield f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
            current = manager.get(run_id)
            if current is None or current.status in TERMINAL_STATES:
                break
            if await request.is_disconnected():
                break
            await asyncio.sleep(0.5)

    return StreamingResponse(stream(), media_type="text/event-stream")
