"""Per-step idle guard, view_image fallback, and phase fuse for Agent.run()."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml

logger = logging.getLogger(__name__)

_IDLE_SYSTEM_MSG = (
    "[SYSTEM] 连续空转超限，请立即调用当前阶段允许的视觉检测或坐标标记工具推进任务。"
)

_DEFAULT_PHASE_RULES: dict[str, Any] = {
    "phases": {
        "partb": {"max_idle_steps": 3},
        "partb_locator_largest_ic": {"max_idle_steps": 3},
    }
}


def load_phase_rules() -> dict[str, Any]:
    path = Path(__file__).resolve().parent.parent / "config" / "phase_rules.yaml"
    if not path.is_file():
        return dict(_DEFAULT_PHASE_RULES)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return data if isinstance(data, dict) else dict(_DEFAULT_PHASE_RULES)
    except Exception:  # noqa: BLE001
        return dict(_DEFAULT_PHASE_RULES)


def max_idle_steps_for_phase(phase_rules: dict[str, Any], phase: str) -> int | None:
    phases = phase_rules.get("phases") or {}
    if not isinstance(phases, dict):
        return None
    key = (phase or "").strip()
    for candidate in (key, key.lower()):
        entry = phases.get(candidate)
        if isinstance(entry, dict) and "max_idle_steps" in entry:
            try:
                return int(entry["max_idle_steps"])
            except (TypeError, ValueError):
                return None
    return None


def pick_view_image_fallback_path(
    step_id: str,
    run_inputs: dict[str, Any],
) -> str | None:
    sid = (step_id or "").lower()
    if sid.startswith("partb"):
        return "debug/case10_assembly_drawing_tp_marked.png"
    if sid.startswith("parta"):
        fb = run_inputs.get("front_board_photo")
        if isinstance(fb, str) and fb.strip():
            return fb.strip()
        return "debug/case10_board_landscape.png"
    if sid.startswith("part0"):
        for key in ("schematic_image", "assembly_drawing_page1_png"):
            val = run_inputs.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
        return "debug/case10_assembly_drawing_tp_marked.png"
    if sid.startswith("partd"):
        return "debug/case12_board_approx_overlay_opencv.png"
    return None


@dataclass
class IdleStepOutcome:
    consecutive_no_tool_steps: int
    injected_system: bool = False
    injected_view_image: bool = False
    skip_to_next_phase: bool = False
    view_image_path: str | None = None


@dataclass
class AgentLoop:
    """Idle-step counter + partB fuse; used from Agent.run() each step."""

    phase_rules: dict[str, Any] = field(default_factory=load_phase_rules)
    consecutive_no_tool_steps: int = 0
    _view_image_fallback_used: bool = False

    def reset_on_tool_calls(self) -> None:
        self.consecutive_no_tool_steps = 0
        self._view_image_fallback_used = False

    def on_empty_tool_response(
        self,
        *,
        current_step_id: str,
        plan_idx: int,
        plan_len: int,
    ) -> IdleStepOutcome:
        self.consecutive_no_tool_steps += 1
        outcome = IdleStepOutcome(consecutive_no_tool_steps=self.consecutive_no_tool_steps)

        if self.consecutive_no_tool_steps > 3:
            outcome.injected_system = True

        fuse_limit = max_idle_steps_for_phase(self.phase_rules, current_step_id)
        if fuse_limit is None and current_step_id.lower().startswith("partb"):
            fuse_limit = max_idle_steps_for_phase(self.phase_rules, "partb")
        if (
            fuse_limit is not None
            and self.consecutive_no_tool_steps > fuse_limit
            and self._view_image_fallback_used
            and current_step_id.lower().startswith("partb")
            and plan_idx < plan_len - 1
        ):
            outcome.skip_to_next_phase = True
            logger.warning(
                "partb idle fuse: consecutive_no_tool_steps=%s > max_idle_steps=%s; skip phase",
                self.consecutive_no_tool_steps,
                fuse_limit,
            )

        return outcome

    def should_inject_view_image_fallback(
        self,
        *,
        current_step_id: str,
        run_inputs: dict[str, Any],
    ) -> str | None:
        if self.consecutive_no_tool_steps <= 3:
            return None
        if self._view_image_fallback_used:
            return None
        path = pick_view_image_fallback_path(current_step_id, run_inputs)
        if path:
            self._view_image_fallback_used = True
        return path

    @staticmethod
    def idle_system_message() -> str:
        return _IDLE_SYSTEM_MSG

    def run_step_log_idle(self, step_idx: int, outcome: IdleStepOutcome) -> None:
        logger.info(
            "agent_loop idle step=%s consecutive=%s injected_system=%s skip_phase=%s",
            step_idx,
            outcome.consecutive_no_tool_steps,
            outcome.injected_system,
            outcome.skip_to_next_phase,
        )


def build_validator_rejection_result(error_json: str) -> dict[str, Any]:
    """Standard tool-result payload when validate_tool_call fails."""
    try:
        detail = json.loads(error_json)
    except json.JSONDecodeError:
        detail = {"message": error_json}
    return {
        "ok": False,
        "text": f"[tool-validator] {error_json}",
        "detail": detail,
    }
