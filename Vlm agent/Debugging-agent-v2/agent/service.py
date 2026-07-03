"""HTTP service wrapper for the VLM agent.

Supports both local and remote deployment:
- Local: task YAML + assets already on the server filesystem → POST /v1/runs
- Remote: Node.js uploads case files → POST /v1/runs/upload
"""

from __future__ import annotations

import asyncio
import json
import shutil
import threading
import time
import uuid
import zipfile
import io as io_mod
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from .agent import Agent
from .config import compose_agent_question, load_config, load_task
from .logging_setup import get_logger, setup_project_logging


TERMINAL_STATES = {"succeeded", "failed", "cancelled"}
setup_project_logging()
log = get_logger(__name__)

# Server-wide configurable base directory for uploaded cases
UPLOAD_CASES_DIR = Path(
    __import__("os").environ.get("VLM_AGENT_CASES_DIR", "data/cases")
).resolve()


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
            log.info("Run queued run_id=%s task=%s", run_id, req.task_file)
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
            log.info("Run started run_id=%s task=%s", run_id, req.task_file)

        try:
            overrides: dict[str, Any] = {"workspace_dir": Path(req.workspace)}
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
                event_sink=lambda event_type, payload: self._append_event_threadsafe(
                    run_id, event_type, payload
                ),
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
                log.info("Run finished run_id=%s status=%s stopped_reason=%s",
                         run_id, run.status, run.stopped_reason)
        except BaseException as exc:  # noqa: BLE001
            with self._lock:
                run.status = "failed"
                run.error = str(exc)
                run.updated_at = time.time()
                self._append_event(run, "agent.failed", {"error": run.error})
            log.exception("Run crashed run_id=%s error=%s", run_id, str(exc))

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

# CORS — allow the Node.js orchestrator to call from any origin
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "service": "debugging-agent-v2"}


# ------------------------------------------------------------------ #
#  Remote upload endpoint
# ------------------------------------------------------------------ #

@app.post("/v1/runs/upload", status_code=202)
async def create_run_with_files(
    task_yaml: UploadFile = File(..., description="The task.yaml file content"),
    files: list[UploadFile] = File(
        default=[],
        description="All image/PDF/data files referenced in the task YAML",
    ),
    run_id: str | None = Form(None),
    name: str | None = Form(None),
    workspace: str = Form("workspace"),
    max_steps: int | None = Form(None),
    env_file: str = Form(".env"),
    model: str | None = Form(None),
    base_url: str | None = Form(None),
    no_native_tools: bool = Form(False),
) -> dict[str, Any]:
    """Create a run by uploading task YAML + referenced asset files.

    The server writes everything into a per-case directory under
    ``VLM_AGENT_CASES_DIR`` (default: ``data/cases/<case_id>/``) and
    then executes the agent. This is the primary endpoint for remote
    Node.js orchestration where the case files originate on the client.
    """
    case_id = run_id or f"case_{uuid.uuid4().hex[:12]}"
    case_dir = UPLOAD_CASES_DIR / case_id
    case_dir.mkdir(parents=True, exist_ok=True)

    # 1. Save task YAML
    try:
        yaml_bytes = await task_yaml.read()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to read task_yaml: {e}") from e
    task_file = case_dir / "task.yaml"
    task_file.write_bytes(yaml_bytes)
    log.info("Upload: wrote task YAML → %s (%d bytes)", task_file, len(yaml_bytes))

    # 2. Save all uploaded asset files
    saved_count = 0
    for f in (files or []):
        if not f.filename or f.filename == "task.yaml":
            continue
        try:
            content = await f.read()
        except Exception as e:
            log.warning("Upload: skip file %s — read error: %s", f.filename, e)
            continue
        dest = case_dir / f.filename
        dest.write_bytes(content)
        saved_count += 1
        log.info("Upload: saved %s → %s (%d bytes)", f.filename, dest, len(content))

    log.info("Upload: case ready case_id=%s dir=%s files=%d",
             case_id, str(case_dir), saved_count + 1)

    # 3. Create and execute the run
    req = RunRequest(
        task_file=str(task_file),
        run_id=run_id,
        name=name,
        env_file=env_file,
        workspace=workspace,
        max_steps=max_steps or 25,
        model=model,
        base_url=base_url,
        no_native_tools=no_native_tools,
    )
    try:
        run = manager.create(req)
    except ValueError as exc:
        # Clean up on conflict
        shutil.rmtree(case_dir, ignore_errors=True)
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    snapshot = manager.snapshot(run)
    snapshot["case_dir"] = str(case_dir)
    return snapshot


# ------------------------------------------------------------------ #
#  Download generated workspace files
# ------------------------------------------------------------------ #

@app.get("/v1/runs/{run_id}/files/{file_path:path}")
async def download_run_file(run_id: str, file_path: str) -> Any:
    """Download a file generated inside a run's workspace.

    Common paths: ``summary.json``, ``debug/step08_final_tp.png``,
    ``debug/case10_assembly_drawing_tp_marked.png``, etc.
    """
    run = manager.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    if not run.run_dir:
        raise HTTPException(status_code=404, detail="Run directory not available yet.")

    # Safety: normalize and reject path traversal
    run_dir = Path(run.run_dir).resolve()
    requested = (run_dir / file_path).resolve()
    if run_dir not in requested.parents and requested != run_dir:
        raise HTTPException(status_code=403, detail="Path traversal not allowed.")

    if not requested.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {file_path}")
    if not requested.is_file():
        raise HTTPException(status_code=404, detail=f"Not a file: {file_path}")

    return FileResponse(
        path=str(requested),
        media_type="application/octet-stream",
        filename=requested.name,
    )


# ------------------------------------------------------------------ #
#  Standard endpoints (unchanged)
# ------------------------------------------------------------------ #


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
