"""HTTP service wrapper for the VLM agent (new agent core).

Supports the same API as the old service so webui works unchanged:
- POST /v1/runs/split  → planner + parallel child runs
- GET  /v1/runs/{id}   → run status
- GET  /v1/runs/{id}/events → SSE event stream
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import subprocess
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from .agent import Agent
from .case_builder import write_inputdemo_task_yaml
from .config import compose_agent_question, load_config, load_task
from .planner import PlannerResult, run_planner

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s - %(message)s")
log = logging.getLogger("vlm-agent-service")

TERMINAL_STATES = {"succeeded", "failed", "cancelled"}

UPLOAD_CASES_DIR = Path(os.environ.get("VLM_AGENT_CASES_DIR", "data/cases")).resolve()


def _final_answer_matches_target(final_answer: Any, target_point: str) -> bool:
    if not isinstance(final_answer, dict):
        return False
    # If the answer explicitly names a tp_id / test_point, it must match.
    expected = str(target_point).strip().upper()
    candidates: list[str] = []
    for key in ("tp_id", "test_point", "id", "label"):
        value = final_answer.get(key)
        if value:
            candidates.append(str(value))
    points = final_answer.get("points")
    if isinstance(points, list):
        for point in points:
            if not isinstance(point, dict):
                continue
            for key in ("tp_id", "test_point", "id", "label"):
                value = point.get(key)
                if value:
                    candidates.append(str(value))
    if candidates:
        return any(item.strip().upper() == expected for item in candidates)
    # New agent finish format: {"pixel": [x, y], ...} without explicit tp_id.
    # Since the child was told to localize exactly this TP, accept any valid pixel.
    pixel = final_answer.get("pixel")
    if isinstance(pixel, list) and len(pixel) == 2:
        if all(isinstance(v, (int, float)) and float(v) == float(v) for v in pixel):
            return True
    return False


class RunRequest(BaseModel):
    task_file: str = Field(..., description="Path to a task YAML file.")
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


class PrepareCaseRequest(BaseModel):
    """Inputdemo bench case — assets already saved under case_dir."""

    case_dir: str = Field(..., description="Absolute path to the case directory with asset files.")
    instruction: str = Field(..., description="User measurement question / task instruction.")
    case_id: str = Field(default="inputdemo-case")
    operator: str = Field(default="unknown")
    run_id: str | None = None
    assets: dict[str, str | None] = Field(
        default_factory=dict,
        description="Map of input keys to filenames inside case_dir (e.g. front_board_photo).",
    )


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
    vlm_split: dict[str, Any] | None = None


class RunManager:
    def __init__(self, max_workers: int = 4) -> None:
        self._runs: dict[str, ManagedRun] = {}
        self._lock = threading.RLock()
        self._executor = ThreadPoolExecutor(max_workers=max_workers)

    def create_queued(self, req: RunRequest) -> ManagedRun:
        run_id = req.run_id or f"run_{uuid.uuid4().hex[:12]}"
        with self._lock:
            if run_id in self._runs:
                raise ValueError(f"Run already exists: {run_id}")
            run = ManagedRun(run_id=run_id, request=req)
            self._runs[run_id] = run
            self._append_event(run, "agent.queued", {"task_file": req.task_file})
            log.info("Run queued run_id=%s task=%s", run_id, req.task_file)
            return run

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

    def events_since(self, run_id: str, seq: int = 0) -> list[dict[str, Any]]:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                return []
            return [event for event in run.events if int(event["seq"]) > seq]

    def snapshot(self, run: ManagedRun) -> dict[str, Any]:
        with self._lock:
            snap = {
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
            if run.vlm_split:
                snap["vlm_split"] = {
                    "enabled": run.vlm_split.get("enabled"),
                    "mode": run.vlm_split.get("mode"),
                    "target_points": run.vlm_split.get("target_points"),
                    "child_runs": [
                        {
                            "child_run_id": c.get("child_run_id"),
                            "target_point": c.get("target_point"),
                            "status": c.get("status"),
                        }
                        for c in (run.vlm_split.get("child_runs") or [])
                    ],
                }
            return snap

    # ------------------------------------------------------------------ #
    #  Single-agent execution (POST /v1/runs)
    # ------------------------------------------------------------------ #

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
            agent = Agent(cfg, event_sink=lambda event_type, payload: self._append_event_threadsafe(
                run_id, event_type, payload
            ))
            result = agent.run(
                question=compose_agent_question(task),
                inputs=task.get("inputs", {}),
                run_name=req.name or Path(req.task_file).stem,
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
        except BaseException as exc:  # noqa: BLE001
            with self._lock:
                run.status = "failed"
                run.error = str(exc)
                run.updated_at = time.time()
                self._append_event(run, "agent.failed", {"error": run.error})
            log.exception("Run crashed run_id=%s error=%s", run_id, str(exc))

    # ------------------------------------------------------------------ #
    #  Split execution (POST /v1/runs/split)
    # ------------------------------------------------------------------ #

    def _execute_split(self, run_id: str) -> None:
        """Split execution: planner → parallel child agent runs."""
        run = self.get(run_id)
        if run is None:
            return
        req = run.request
        with self._lock:
            run.status = "running"
            run.updated_at = time.time()
            self._append_event(run, "agent.split_started", {
                "task_file": req.task_file,
                "phase": "planner",
            })

        try:
            # --- Phase 1: Planner ---
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
            inputs = task.get("inputs", {})
            user_question = inputs.get("user_measurement_question", "")
            if isinstance(user_question, str):
                user_question = user_question.strip()
            if not user_question:
                user_question = (task.get("question") or "").strip()[:200]

            def _find_input_image(keys: list[str]) -> str | None:
                for key in keys:
                    val = inputs.get(key)
                    if isinstance(val, str) and Path(val).exists():
                        return val
                return None

            schematic_image = _find_input_image(["schematic_image", "schematic_pdf"])
            # 位号图 (assembly/bit drawing) is intentionally NOT sent to the
            # planner; it is consumed downstream by the per-TP localization
            # steps via `inputs`.
            assembly_image = _find_input_image(["assembly_drawing", "assembly_drawing_pdf",
                                                "bit_image", "bit_pdf"])

            self._append_event(run, "planner.started", {
                "phase": "planner",
                "question": user_question,
                "schematic_image": schematic_image,
                "assembly_image_downstream": assembly_image,
            })

            try:
                planner_result = run_planner(
                    user_question=user_question,
                    schematic_image_path=schematic_image,
                    cfg=cfg,
                    event_sink=lambda event_type, payload: self._append_event_threadsafe(
                        run_id, event_type, payload
                    ),
                )
            except Exception as planner_exc:
                log.exception("Planner crashed run_id=%s", run_id)
                self._append_event(run, "planner.failed", {"error": str(planner_exc)})
                planner_result = PlannerResult(target_points=[])
            target_points = planner_result.target_points

            self._append_event(run, "planner.finished", {
                "target_points": target_points,
                "count": len(target_points),
            })

            if not target_points:
                self._append_event(run, "planner.fallback", {
                    "reason": "No target points found; falling back to single-agent mode.",
                })
                agent = Agent(cfg, event_sink=lambda event_type, payload: self._append_event_threadsafe(
                    run_id, event_type, payload
                ))
                result = agent.run(
                    question=compose_agent_question(task),
                    inputs=inputs,
                    run_name=req.name or Path(req.task_file).stem,
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
                return

            # --- Phase 2: Parallel child runs ---
            run.vlm_split = {
                "enabled": True,
                "mode": "planner_then_child_runs",
                "planner_run_id": run_id,
                "target_points": target_points,
                "child_runs": [],
            }

            self._append_event(run, "split.children_starting", {
                "child_count": len(target_points),
                "target_points": target_points,
            })

            base_question = task.get("question", "") or compose_agent_question(task)

            def build_child_question(tp_id: str) -> str:
                return (
                    f"Localize test point {tp_id} on the board.\n\n"
                    f"Original task context: {base_question}\n\n"
                    f"[child-target-constraint]\n"
                    f"You are a child VLM run forked from an upstream multi-TP planner.\n"
                    f"Localize exactly one physical test point: {tp_id}.\n"
                    f"Do not add reference points, companion probe points, or any extra target_points in this child run.\n"
                    f"The only allowed target point for this child run is {tp_id}.\n"
                    f"In debug/case10_signal_to_tp.json, write target_points with exactly this one object.\n"
                    f"In final answer, return points[] with exactly one object for {tp_id}.\n"
                )

            def run_child(tp_id: str) -> dict[str, Any]:
                child_run_id = f"{run_id}__{tp_id}"
                child_workspace = Path(req.workspace) / tp_id
                with self._lock:
                    child_run = ManagedRun(
                        run_id=child_run_id,
                        request=RunRequest(
                            task_file=req.task_file,
                            name=f"{req.name}__{tp_id}" if req.name else child_run_id,
                            env_file=req.env_file,
                            workspace=str(child_workspace),
                            max_steps=req.max_steps,
                        ),
                        status="running",
                    )
                    self._runs[child_run_id] = child_run
                    self._append_event(child_run, "agent.queued", {"task_file": req.task_file})
                    self._append_event(child_run, "agent.started", {
                        "task_file": req.task_file,
                        "target_point": tp_id,
                        "parent_run_id": run_id,
                    })

                # Emit split.child_started FIRST so Node.js WebUI registers the child
                self._append_event_threadsafe(run_id, "split.child_started", {
                    "child_run_id": child_run_id,
                    "target_point": tp_id,
                })
                self._append_event_threadsafe(run_id, "child.agent.started", {
                    "child_run_id": child_run_id,
                    "target_point": tp_id,
                })

                try:
                    child_overrides = {**overrides, "workspace_dir": child_workspace}
                    child_cfg = load_config(env_path=req.env_file, overrides=child_overrides)
                    child_agent = Agent(child_cfg, event_sink=lambda event_type, payload: (
                        self._append_event_threadsafe(child_run_id, event_type, payload),
                        self._append_event_threadsafe(run_id, f"child.{event_type}", {
                            **payload,
                            "child_run_id": child_run_id,
                            "target_point": tp_id,
                        }),
                    ))
                    child_result = child_agent.run(
                        question=build_child_question(tp_id),
                        inputs=inputs,
                        run_name=child_run_id,
                    )
                    summary_path = str(child_result.run_dir / "summary.json") if child_result.run_dir else None
                    with self._lock:
                        child_run = self._runs.get(child_run_id)
                        if child_run:
                            answer_ok = _final_answer_matches_target(child_result.final_answer, tp_id)
                            child_run.run_dir = str(child_result.run_dir) if child_result.run_dir else None
                            child_run.summary_path = summary_path
                            child_run.final_answer = child_result.final_answer
                            child_run.stopped_reason = child_result.stopped_reason
                            child_run.status = "succeeded" if answer_ok else "failed"
                            child_run.error = child_result.last_error
                            if child_result.final_answer is not None and not answer_ok:
                                child_run.error = (
                                    f"Child final_answer does not match target {tp_id}: "
                                    f"{child_result.final_answer}"
                                )
                            child_run.updated_at = time.time()
                            event_type = "agent.final" if child_run.status == "succeeded" else "agent.failed"
                            final_payload = {
                                "final_answer": child_result.final_answer,
                                "stopped_reason": child_result.stopped_reason,
                                "summary_path": summary_path,
                                "run_dir": child_run.run_dir,
                                "error": child_run.error,
                            }
                            self._append_event(child_run, event_type, final_payload)
                            self._append_event_threadsafe(run_id, f"child.{event_type}", {
                                **final_payload,
                                "child_run_id": child_run_id,
                                "target_point": tp_id,
                            })
                    self._append_event_threadsafe(run_id, "split.child_finished", {
                        "child_run_id": child_run_id,
                        "target_point": tp_id,
                        "status": child_run.status if child_run else "failed",
                        "final_answer": child_result.final_answer,
                    })
                    return {
                        "child_run_id": child_run_id,
                        "target_point": tp_id,
                        "status": child_run.status if child_run else "failed",
                        "final_answer": child_result.final_answer,
                        **({"error": child_run.error} if child_run and child_run.error else {}),
                    }
                except BaseException as exc:  # noqa: BLE001
                    with self._lock:
                        child_run = self._runs.get(child_run_id)
                        if child_run:
                            child_run.status = "failed"
                            child_run.error = str(exc)
                    self._append_event_threadsafe(run_id, "child.agent.failed", {
                        "child_run_id": child_run_id,
                        "target_point": tp_id,
                        "error": str(exc),
                    })
                    self._append_event_threadsafe(run_id, "split.child_failed", {
                        "child_run_id": child_run_id,
                        "target_point": tp_id,
                        "error": str(exc),
                    })
                    return {
                        "child_run_id": child_run_id,
                        "target_point": tp_id,
                        "status": "failed",
                        "error": str(exc),
                    }

            child_workers = int(os.environ.get("VLM_AGENT_SPLIT_CHILD_WORKERS", str(len(target_points))))
            child_workers = max(1, min(child_workers, len(target_points)))
            with ThreadPoolExecutor(max_workers=child_workers) as pool:
                children = list(pool.map(run_child, target_points))

            run.vlm_split["child_runs"] = children
            all_ok = all(c["status"] == "succeeded" for c in children)

            with self._lock:
                run.status = "succeeded" if all_ok else "failed"
                run.final_answer = {
                    "split_multi_tp": True,
                    "target_points": target_points,
                    "children": children,
                }
                run.stopped_reason = "split-completed"
                run.updated_at = time.time()
                self._append_event(run, "agent.final" if all_ok else "agent.failed", {
                    "final_answer": run.final_answer,
                    "stopped_reason": run.stopped_reason,
                    "child_count": len(children),
                })

        except BaseException as exc:  # noqa: BLE001
            with self._lock:
                run.status = "failed"
                run.error = str(exc)
                run.updated_at = time.time()
                self._append_event(run, "agent.failed", {"error": run.error})
            log.exception("Split run crashed run_id=%s error=%s", run_id, str(exc))

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


# ------------------------------------------------------------------ #
#  FastAPI application
# ------------------------------------------------------------------ #

manager = RunManager()
app = FastAPI(title="Debugging Agent VLM Service (new core)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "service": "debugging-agent-v2", "case_prepare": True}


# ------------------------------------------------------------------ #
#  Case prepare — single source of truth for Inputdemo task.yaml
# ------------------------------------------------------------------ #


@app.post("/v1/cases/prepare", status_code=201)
def prepare_case(req: PrepareCaseRequest) -> dict[str, Any]:
    """Write task.yaml for an Inputdemo bench case (local shared filesystem)."""
    case_dir = Path(req.case_dir).resolve()
    if not case_dir.is_dir():
        raise HTTPException(status_code=400, detail=f"case_dir does not exist: {case_dir}")
    task_file = write_inputdemo_task_yaml(
        case_dir,
        instruction=req.instruction,
        case_id=req.case_id,
        operator=req.operator,
        assets=req.assets,
    )
    log.info("Prepared case case_dir=%s task=%s run_id=%s", case_dir, task_file, req.run_id)
    return {
        "ok": True,
        "format": "inputdemo-task",
        "case_dir": str(case_dir),
        "task_file": str(task_file),
        "run_id": req.run_id,
    }


@app.post("/v1/cases/prepare/upload", status_code=201)
async def prepare_case_upload(
    instruction: str = Form(...),
    case_id: str = Form("inputdemo-case"),
    operator: str = Form("unknown"),
    run_id: str | None = Form(None),
    files: list[UploadFile] = File(default=[], description="Case asset files (images/PDFs)"),
) -> dict[str, Any]:
    """Upload assets and write task.yaml on the VLM server (remote Inputdemo)."""
    safe_id = (case_id or run_id or f"case_{uuid.uuid4().hex[:12]}").strip()
    case_dir = UPLOAD_CASES_DIR / safe_id
    case_dir.mkdir(parents=True, exist_ok=True)

    assets: dict[str, str | None] = {}
    for upload in files or []:
        if not upload.filename:
            continue
        dest = case_dir / upload.filename
        dest.write_bytes(await upload.read())
        lower = upload.filename.lower()
        if lower.endswith(".pdf"):
            if "schematic" in lower and not assets.get("schematic_pdf"):
                assets["schematic_pdf"] = upload.filename
            elif not assets.get("assembly_drawing_pdf"):
                assets["assembly_drawing_pdf"] = upload.filename
        elif any(lower.endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".webp", ".bmp")):
            if "schematic" in lower and not assets.get("schematic_image"):
                assets["schematic_image"] = upload.filename
            elif "back" in lower or "rear" in lower:
                assets.setdefault("back_board_photo", upload.filename)
            elif "front" in lower or "board" in lower or "pcba" in lower:
                assets.setdefault("front_board_photo", upload.filename)
            elif not assets.get("assembly_drawing"):
                assets["assembly_drawing"] = upload.filename

    task_file = write_inputdemo_task_yaml(
        case_dir,
        instruction=instruction,
        case_id=case_id,
        operator=operator,
        assets=assets,
    )
    log.info("Prepared uploaded case case_id=%s dir=%s files=%d", safe_id, case_dir, len(files or []))
    return {
        "ok": True,
        "format": "inputdemo-task",
        "case_dir": str(case_dir),
        "task_file": str(task_file),
        "run_id": run_id or safe_id,
        "assets": assets,
    }


@app.get("/version")
def version() -> dict[str, Any]:
    return {
        "ok": True,
        "service": "debugging-agent-v2",
        "core": "new",
    }


# ------------------------------------------------------------------ #
#  Split endpoint — planner + parallel child runs
# ------------------------------------------------------------------ #


@app.post("/v1/runs/split", status_code=202)
def create_split_run(req: RunRequest) -> dict[str, Any]:
    """Create a split run: planner finds TPs, then each TP runs in parallel."""
    try:
        run = manager.create_queued(req)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    run.future = ThreadPoolExecutor(max_workers=1).submit(
        manager._execute_split, run.run_id
    )
    return manager.snapshot(run)


# ------------------------------------------------------------------ #
#  Standard endpoints
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
    run = manager.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    with manager._lock:
        manager._append_event(run, "observation.received", {
            "type": observation.type,
            "source": observation.source,
            "payload": observation.payload,
        })
        run.updated_at = time.time()
    return manager.snapshot(run)


@app.get("/v1/runs/{run_id}/events")
async def get_events(run_id: str, request: Request, since: int = 0) -> Any:
    """Get events for a run, with optional SSE streaming."""
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
                yield f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
            current = manager.get(run_id)
            if current is None or current.status in TERMINAL_STATES:
                break
            if await request.is_disconnected():
                break
            await asyncio.sleep(0.5)

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.get("/v1/runs/{run_id}/files/{file_path:path}")
async def download_run_file(run_id: str, file_path: str) -> Any:
    """Download a file generated inside a run's workspace."""
    run = manager.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    if not run.run_dir:
        raise HTTPException(status_code=404, detail="Run directory not available yet.")

    run_dir = Path(run.run_dir).resolve()
    requested = (run_dir / file_path).resolve()
    if run_dir not in requested.parents and requested != run_dir:
        raise HTTPException(status_code=403, detail="Path traversal not allowed.")
    if not requested.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {file_path}")
    if not requested.is_file():
        raise HTTPException(status_code=404, detail=f"Not a file: {file_path}")

    return FileResponse(path=str(requested), media_type="application/octet-stream", filename=requested.name)


# ------------------------------------------------------------------ #
#  Upload endpoint (remote Node.js orchestration)
# ------------------------------------------------------------------ #

@app.post("/v1/runs/upload", status_code=202)
async def create_run_with_files(
    task_yaml: UploadFile = File(..., description="The task.yaml file content"),
    files: list[UploadFile] = File(default=[], description="All image/PDF/data files"),
    run_id: str | None = Form(None),
    name: str | None = Form(None),
    workspace: str = Form("workspace"),
    max_steps: int | None = Form(None),
    env_file: str = Form(".env"),
    model: str | None = Form(None),
    base_url: str | None = Form(None),
    no_native_tools: bool = Form(False),
) -> dict[str, Any]:
    case_id = run_id or f"case_{uuid.uuid4().hex[:12]}"
    case_dir = UPLOAD_CASES_DIR / case_id
    case_dir.mkdir(parents=True, exist_ok=True)

    try:
        yaml_bytes = await task_yaml.read()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to read task_yaml: {e}") from e
    task_file = case_dir / "task.yaml"
    task_file.write_bytes(yaml_bytes)

    for f in (files or []):
        if not f.filename or f.filename == "task.yaml":
            continue
        try:
            content = await f.read()
        except Exception:
            continue
        (case_dir / f.filename).write_bytes(content)

    req = RunRequest(
        task_file=str(task_file), run_id=run_id, name=name,
        env_file=env_file, workspace=workspace, max_steps=max_steps or 25,
        model=model, base_url=base_url, no_native_tools=no_native_tools,
    )
    try:
        run = manager.create(req)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    snapshot = manager.snapshot(run)
    snapshot["case_dir"] = str(case_dir)
    return snapshot
