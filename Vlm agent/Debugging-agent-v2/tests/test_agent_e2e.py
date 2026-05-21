"""End-to-end sanity test with a mocked VLM backend.

We DO NOT hit a real API; instead we replace `LLMClient.chat` with a
scripted sequence that exercises every built-in tool the framework
ships with. This verifies:

  * ToolRegistry dispatch & JSON-schema advertisement.
  * Image attachment round-trip (tool produces an image → next user turn
    carries it as image_url data URL).
  * run_python shared-state preservation across steps.
  * Trace files (summary.json, messages.final.jsonl, messages.init.jsonl)
    are written.
  * finish() returns structured answer and stops the loop.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw

# Make the package importable when running directly.
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

from agent import Agent, Config  # noqa: E402
from agent.llm_client import AssistantReply, ToolInvocation  # noqa: E402


# --------------------------------------------------------------------------- #
# Fixtures: build a tiny fake dataset
# --------------------------------------------------------------------------- #

def _make_fixture_dir(base: Path) -> dict[str, str]:
    base.mkdir(parents=True, exist_ok=True)

    # Locator drawing: plain white board with a red dot at (200, 150)
    locator = Image.new("RGB", (400, 300), "white")
    ImageDraw.Draw(locator).ellipse((195, 145, 205, 155), fill="red")
    locator_path = base / "locator.png"
    locator.save(locator_path)

    # Front camera photo: same board but bigger, dot at (800, 600)
    front = Image.new("RGB", (1600, 1200), "lightgray")
    ImageDraw.Draw(front).ellipse((795, 595, 805, 605), fill="red")
    front_path = base / "front.jpg"
    front.save(front_path)

    # Fake schematic netlist
    schematic_path = base / "schematic.txt"
    schematic_path.write_text(
        "NET TP12 -> R32.2 -> U4.14\n"
        "NET TP13 -> R33.1 -> U4.15\n",
        encoding="utf-8",
    )

    return {
        "locator_image": str(locator_path),
        "front_photo": str(front_path),
        "schematic_text": str(schematic_path),
    }


# --------------------------------------------------------------------------- #
# Scripted VLM
# --------------------------------------------------------------------------- #

class ScriptedClient:
    """Stand-in for LLMClient.chat that returns a fixed list of replies.

    We still instantiate a real LLMClient (so the message builders work)
    and then monkey-patch `chat`.
    """

    def __init__(self, script: list[AssistantReply]):
        self._script = list(script)
        self.calls_seen: list[list[dict]] = []

    def __call__(self, messages, tools_schema=None):
        self.calls_seen.append(list(messages))
        if not self._script:
            raise RuntimeError("ScriptedClient ran out of replies")
        return self._script.pop(0)


def _tc(name: str, args: dict, call_id: str | None = None) -> ToolInvocation:
    return ToolInvocation(id=call_id or f"call_{name}", name=name, arguments=args)


def _reply(content: str, tool_calls: list[ToolInvocation]) -> AssistantReply:
    raw = {"role": "assistant", "content": content}
    if tool_calls:
        raw["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.name,
                    "arguments": json.dumps(tc.arguments),
                },
            }
            for tc in tool_calls
        ]
    return AssistantReply(content=content, tool_calls=tool_calls, raw_message=raw)


# --------------------------------------------------------------------------- #
# Test body
# --------------------------------------------------------------------------- #

def run_test() -> int:
    tmp_root = Path(".pytest_tmp")
    fixture_dir = tmp_root / "fixtures"
    workspace = tmp_root / "workspace"
    if tmp_root.exists():
        import shutil
        shutil.rmtree(tmp_root)
    inputs = _make_fixture_dir(fixture_dir)

    cfg = Config(
        base_url="http://unused.local",
        api_key="sk-unused",
        model="mock-vlm",
        use_native_tools=True,
        temperature=0.0,
        max_tokens=256,
        max_steps=12,
        workspace_dir=workspace,
    )
    agent = Agent(cfg)

    script = [
        _reply(
            "I'll start by listing the input files.",
            [_tc("list_files", {"path": str(fixture_dir)})],
        ),
        _reply(
            "Now reading the schematic.",
            [_tc("read_text_file", {"path": inputs["schematic_text"]})],
        ),
        _reply(
            "Let's look at the locator drawing.",
            [_tc("view_image", {"path": inputs["locator_image"],
                                "note": "find the red dot (TP12)"})],
        ),
        _reply(
            "Crop the camera photo around where I expect TP12.",
            [_tc("crop_image", {
                "path": inputs["front_photo"],
                "bbox": [700, 500, 900, 700],
                "out_path": "crops/tp12_zoom.png",
            })],
        ),
        _reply(
            "Use run_python to locate the red blob precisely in the crop.",
            [_tc("run_python", {"code":
                "import numpy as np, cv2\n"
                "from pathlib import Path\n"
                "img = cv2.imread(str(Path('crops') / 'tp12_zoom.png'))\n"
                "hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)\n"
                "mask = cv2.inRange(hsv, (0, 100, 100), (10, 255, 255))\n"
                "ys, xs = np.where(mask > 0)\n"
                "cx_crop, cy_crop = int(xs.mean()), int(ys.mean())\n"
                "cx, cy = cx_crop + 700, cy_crop + 500\n"
                "print(f'pixel={cx},{cy}')\n"
            })],
        ),
        _reply(
            "Annotate the full camera photo to verify.",
            [_tc("annotate_image", {
                "path": inputs["front_photo"],
                "points": [{"x": 800, "y": 600, "label": "TP12", "color": "red"}],
                "out_path": "verify/tp12_marked.png",
            })],
        ),
        _reply(
            "Looks good. Submitting.",
            [_tc("finish", {"answer": {
                "tp_id": "TP12",
                "camera_view": "front",
                "pixel": [800, 600],
                "confidence": 0.95,
                "needs_user_help": False,
                "user_message": None,
                "reasoning": "Red locator dot matched via HSV mask in cropped front photo.",
            }})],
        ),
    ]
    scripted = ScriptedClient(script)
    agent.client.chat = scripted  # type: ignore[assignment]

    result = agent.run(
        question="Locate TP12 in the front camera photo.",
        inputs=inputs,
        run_name="e2e-mock",
    )

    assert result.stopped_reason == "finish-tool-called", result.stopped_reason
    assert result.final_answer is not None
    assert result.final_answer["pixel"] == [800, 600]
    assert result.final_answer["tp_id"] == "TP12"

    assert result.run_dir is not None
    summary_path = result.run_dir / "summary.json"
    assert summary_path.exists()
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["final_answer"]["tp_id"] == "TP12"
    assert len(summary["steps"]) == len(script)

    assert (result.run_dir / "messages.init.jsonl").exists()
    assert (result.run_dir / "messages.final.jsonl").exists()

    crop_file = workspace / "crops" / "tp12_zoom.png"
    mark_file = workspace / "verify" / "tp12_marked.png"
    assert crop_file.exists(), f"missing {crop_file}"
    assert mark_file.exists(), f"missing {mark_file}"

    py_step = summary["steps"][4]
    py_result_text = py_step["tool_results"][0]["text"]
    import re
    m = re.search(r"pixel=(\d+),(\d+)", py_result_text)
    assert m, py_result_text
    px, py = int(m.group(1)), int(m.group(2))
    # HSV centroid of the 10px red dot should land within ±2px of (800,600)
    assert abs(px - 800) <= 2 and abs(py - 600) <= 2, (px, py)

    image_attach_turns = 0
    for msg in scripted.calls_seen[-1]:
        if msg.get("role") == "user" and isinstance(msg.get("content"), list):
            for part in msg["content"]:
                if isinstance(part, dict) and part.get("type") == "image_url":
                    image_attach_turns += 1
    assert image_attach_turns >= 3, (
        f"expected ≥3 attached images across the run, got {image_attach_turns}"
    )

    print("\nE2E TEST PASSED")
    print(f"  steps            = {len(result.steps)}")
    print(f"  final pixel      = {result.final_answer['pixel']}")
    print(f"  run_dir          = {result.run_dir}")
    print(f"  image attach n   = {image_attach_turns}")
    return 0


if __name__ == "__main__":
    sys.exit(run_test())
