"""Small unit-level checks that don't need a VLM backend."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))


def test_missing_env_raises():
    from agent.config import load_config

    # Make sure relevant env vars are absent.
    for k in ("VLM_API_KEY", "VLM_BASE_URL", "VLM_MODEL"):
        os.environ.pop(k, None)

    try:
        load_config(env_path=Path("non-existent.env"))
    except RuntimeError as e:
        assert "Missing required env vars" in str(e)
        print("OK  missing_env_raises")
        return
    raise AssertionError("Expected RuntimeError for missing env vars")


def test_tool_registry_openai_schema():
    from agent.builtin_tools import build_default_registry

    reg = build_default_registry(Path("."))
    schema = reg.openai_schema()
    names = {s["function"]["name"] for s in schema}
    required = {
        "list_files", "read_text_file", "save_text_file", "image_info",
        "view_image", "crop_image", "annotate_image", "run_python",
        "run_shell", "finish",
    }
    missing = required - names
    assert not missing, f"missing tools: {missing}"
    for s in schema:
        assert s["type"] == "function"
        assert "name" in s["function"]
        assert "description" in s["function"]
        assert "parameters" in s["function"]
    print(f"OK  tool_registry_openai_schema ({len(schema)} tools)")


def test_fallback_tool_call_parsing():
    """When use_native_tools=False we parse <tool_call>{...}</tool_call>."""
    from openai.types.chat import ChatCompletionMessage  # noqa: F401 (availability check)
    from agent.config import Config
    from agent.llm_client import LLMClient

    cfg = Config(
        base_url="http://localhost:0",  # never actually reached
        api_key="sk-unused",
        model="mock",
        use_native_tools=False,
    )
    client = LLMClient(cfg)

    # Manually feed a fake OpenAI-style response through the regex path
    # by calling the private helper logic via a monkey-patched create().
    class _Msg:
        def __init__(self, content):
            self.content = content
            self.tool_calls = None
    class _Choice:
        def __init__(self, content):
            self.message = _Msg(content)
    class _Resp:
        def __init__(self, content):
            self.choices = [_Choice(content)]

    def fake_create(**kwargs):
        return _Resp(
            'Let me look at the file.\n'
            '<tool_call>{"name": "list_files", "arguments": {"path": "."}}</tool_call>'
        )

    client._client.chat.completions.create = fake_create  # type: ignore[assignment]
    reply = client.chat(messages=[], tools_schema=None)

    assert len(reply.tool_calls) == 1, reply.tool_calls
    tc = reply.tool_calls[0]
    assert tc.name == "list_files"
    assert tc.arguments == {"path": "."}
    print("OK  fallback_tool_call_parsing")


def test_fallback_tool_call_nested_finish_json():
    """Tag parser must not truncate at the first ``}`` inside nested arguments."""
    from agent.config import Config
    from agent.llm_client import LLMClient

    cfg = Config(
        base_url="http://localhost:0",
        api_key="sk-unused",
        model="mock",
        use_native_tools=False,
    )
    client = LLMClient(cfg)

    class _Msg:
        def __init__(self, content):
            self.content = content
            self.tool_calls = None

    class _Choice:
        def __init__(self, content):
            self.message = _Msg(content)

    class _Resp:
        def __init__(self, content):
            self.choices = [_Choice(content)]

    payload = (
        '{"name": "finish", "arguments": {"answer": {'
        '"tp_id": "TP1", "camera_view": "front", "pixel": [233, 1255], '
        '"confidence": 0.75, "needs_user_help": false, '
        '"reasoning": "nested"}}}'
    )

    def fake_create(**kwargs):
        return _Resp(
            "Done.\n"
            f"<tool_call>{payload}</tool_call>"
        )

    client._client.chat.completions.create = fake_create  # type: ignore[assignment]
    reply = client.chat(messages=[], tools_schema=None)

    assert len(reply.tool_calls) == 1, reply.tool_calls
    assert reply.tool_calls[0].name == "finish"
    assert reply.tool_calls[0].arguments["answer"]["pixel"] == [233, 1255]
    print("OK  fallback_tool_call_nested_finish_json")


def test_fallback_tool_call_dedupes_multiple_finish_tags():
    from agent.config import Config
    from agent.llm_client import LLMClient

    cfg = Config(
        base_url="http://localhost:0",
        api_key="sk-unused",
        model="mock",
        use_native_tools=False,
    )
    client = LLMClient(cfg)

    class _Msg:
        def __init__(self, content):
            self.content = content
            self.tool_calls = None

    class _Choice:
        def __init__(self, content):
            self.message = _Msg(content)

    class _Resp:
        def __init__(self, content):
            self.choices = [_Choice(content)]

    def fake_create(**kwargs):
        return _Resp(
            "<tool_call>{\"name\": \"finish\", \"arguments\": {\"answer\": {\"pixel\": [1, 1]}}}"
            "</tool_call>\n"
            "<tool_call>{\"name\": \"finish\", \"arguments\": {\"answer\": {\"pixel\": [9, 9]}}}"
            "</tool_call>"
        )

    client._client.chat.completions.create = fake_create  # type: ignore[assignment]
    reply = client.chat(messages=[], tools_schema=None)

    assert len(reply.tool_calls) == 1
    assert reply.tool_calls[0].arguments["answer"]["pixel"] == [9, 9]
    print("OK  fallback_tool_call_dedupes_multiple_finish_tags")


def test_normalize_finish_arguments_flattened():
    from agent.tools import normalize_finish_arguments

    flat = {
        "tp_id": "TP1",
        "camera_view": "front",
        "pixel": [1, 2],
        "confidence": 0.9,
        "needs_user_help": False,
        "reasoning": "x",
    }
    norm = normalize_finish_arguments(flat)
    assert norm == {"answer": flat}

    wrapped = {"answer": {"pixel": [3, 4]}}
    assert normalize_finish_arguments(wrapped) == wrapped
    print("OK  normalize_finish_arguments_flattened")


def test_thinking_mode_payload():
    """Verify enable_thinking + reasoning_effort reach the API call,
    and that reasoning_content on the response is round-tripped back
    into the raw assistant message (DeepSeek requirement)."""
    from agent.config import Config
    from agent.llm_client import LLMClient

    cfg = Config(
        base_url="http://localhost:0",
        api_key="sk-unused",
        model="deepseek-v4-pro",
        use_native_tools=True,
        enable_thinking=True,
        reasoning_effort="high",
    )
    client = LLMClient(cfg)

    captured: dict = {}

    class _Msg:
        def __init__(self, content, reasoning, tool_calls=None):
            self.content = content
            self.reasoning_content = reasoning
            self.tool_calls = tool_calls
    class _Choice:
        def __init__(self, msg):
            self.message = msg
    class _Resp:
        def __init__(self, msg):
            self.choices = [_Choice(msg)]

    def fake_create(**kwargs):
        captured.update(kwargs)
        return _Resp(_Msg("done.", "I was thinking step by step..."))

    client._client.chat.completions.create = fake_create  # type: ignore[assignment]
    reply = client.chat(messages=[], tools_schema=[{
        "type": "function",
        "function": {"name": "noop", "description": "x",
                     "parameters": {"type": "object", "properties": {}}},
    }])

    assert captured.get("reasoning_effort") == "high", captured
    assert captured.get("extra_body") == {"thinking": {"type": "enabled"}}, captured
    assert captured.get("tools"), "tools_schema should have been forwarded"
    assert reply.raw_message.get("reasoning_content") == "I was thinking step by step..."
    print("OK  thinking_mode_payload")


def test_thinking_mode_disabled_does_not_leak():
    """When enable_thinking=false & no effort, neither field should be sent."""
    from agent.config import Config
    from agent.llm_client import LLMClient

    cfg = Config(
        base_url="http://localhost:0",
        api_key="sk-unused",
        model="gpt-4o",
        enable_thinking=False,
        reasoning_effort=None,
    )
    client = LLMClient(cfg)
    captured: dict = {}

    class _Msg: content = ""; reasoning_content = None; tool_calls = None
    class _Choice: message = _Msg()
    class _Resp: choices = [_Choice()]

    def fake_create(**kwargs):
        captured.update(kwargs)
        return _Resp()

    client._client.chat.completions.create = fake_create  # type: ignore[assignment]
    client.chat(messages=[], tools_schema=None)
    assert "extra_body" not in captured, captured
    assert "reasoning_effort" not in captured, captured
    print("OK  thinking_mode_disabled_does_not_leak")


def test_intern_s1_thinking_mode_payload():
    """Intern-S1 family should use extra_body.thinking_mode(boolean)."""
    from agent.config import Config
    from agent.llm_client import LLMClient

    cfg = Config(
        base_url="http://localhost:23333/v1",
        api_key="sk-unused",
        model="intern-s1-pro",
        thinking_mode=True,
        enable_thinking=False,
        reasoning_effort="high",  # should be suppressed for intern-s1 family
    )
    client = LLMClient(cfg)
    captured: dict = {}

    class _Msg: content = ""; reasoning_content = None; tool_calls = None
    class _Choice: message = _Msg()
    class _Resp: choices = [_Choice()]

    def fake_create(**kwargs):
        captured.update(kwargs)
        return _Resp()

    client._client.chat.completions.create = fake_create  # type: ignore[assignment]
    client.chat(messages=[], tools_schema=None)
    assert captured.get("extra_body") == {"thinking_mode": True}, captured
    assert "reasoning_effort" not in captured, captured
    print("OK  intern_s1_thinking_mode_payload")


def test_load_task_resolves_relative_paths():
    """YAML 'inputs' relative paths must resolve against the YAML's dir."""
    import tempfile
    import textwrap
    from agent.config import load_task

    with tempfile.TemporaryDirectory() as tmp:
        case_dir = Path(tmp) / "case_xyz"
        case_dir.mkdir()
        (case_dir / "locator.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        (case_dir / "schematic.txt").write_text("net tp1\n", encoding="utf-8")
        yaml_path = case_dir / "task.yaml"
        yaml_path.write_text(textwrap.dedent("""
            question: test
            inputs:
              locator_image: locator.png
              schematic_text: schematic.txt
              future_photo: front.jpg      # doesn't exist yet but known ext -> resolve
              board_id: XYZ
              absolute_path: C:/stay/as-is.txt
        """), encoding="utf-8")

        data = load_task(yaml_path)

        assert Path(data["inputs"]["locator_image"]) == (case_dir / "locator.png").resolve()
        assert Path(data["inputs"]["schematic_text"]) == (case_dir / "schematic.txt").resolve()
        # Non-existent but image extension: still resolved against yaml dir.
        assert data["inputs"]["future_photo"].endswith("front.jpg")
        assert Path(data["inputs"]["future_photo"]).parent == case_dir.resolve()
        # Free-form strings untouched.
        assert data["inputs"]["board_id"] == "XYZ"
        # Absolute path untouched.
        assert data["inputs"]["absolute_path"] == "C:/stay/as-is.txt"
    print("OK  load_task_resolves_relative_paths")


def test_load_task_injects_default_skill_docs_when_layout_matches():
    """Under data/cases/<name>/, missing workflow_doc/skills_doc get defaults."""
    import tempfile
    import textwrap
    from agent.config import load_task

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        skills = root / "data" / "skills"
        skills.mkdir(parents=True)
        (skills / "STANDARD_WORKFLOW.md").write_text("# wf\n", encoding="utf-8")
        (skills / "SKILL.md").write_text("# sk\n", encoding="utf-8")
        case_dir = root / "data" / "cases" / "case_demo"
        case_dir.mkdir(parents=True)
        yaml_path = case_dir / "task.yaml"
        yaml_path.write_text(textwrap.dedent("""
            question: go
            inputs:
              board_id: X
        """), encoding="utf-8")

        data = load_task(yaml_path)

        assert Path(data["inputs"]["workflow_doc"]) == (skills / "STANDARD_WORKFLOW.md").resolve()
        assert Path(data["inputs"]["skills_doc"]) == (skills / "SKILL.md").resolve()
        assert data["inputs"]["board_id"] == "X"
    print("OK  load_task_injects_default_skill_docs_when_layout_matches")


def test_compose_agent_question_embeds_workflow():
    """compose_agent_question should inline STANDARD_WORKFLOW when enabled."""
    import tempfile
    import textwrap
    from pathlib import Path

    from agent.config import compose_agent_question, load_task

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        skills = root / "data" / "skills"
        skills.mkdir(parents=True)
        (skills / "STANDARD_WORKFLOW.md").write_text(
            "# Title\n\n> drop\n> me\n\nBody unique marker xyzzy.\n",
            encoding="utf-8",
        )
        case_dir = root / "data" / "cases" / "case_demo"
        case_dir.mkdir(parents=True)
        yaml_path = case_dir / "task.yaml"
        yaml_path.write_text(textwrap.dedent("""
            question: Short ask
            inputs:
              board_id: X
        """), encoding="utf-8")

        task = load_task(yaml_path)
        text = compose_agent_question(task)

        assert "Short ask" in text
        assert "Body unique marker xyzzy." in text
        assert "drop" not in text
        assert "首轮必须遵守" in text

        task["embed_workflow_in_prompt"] = False
        assert compose_agent_question(task).strip() == "Short ask"
    print("OK  compose_agent_question_embeds_workflow")


def test_write_path_sandboxing():
    from agent.builtin_tools import build_default_registry

    tmp_ws = Path(".pytest_tmp") / "sandbox_ws"
    tmp_ws.mkdir(parents=True, exist_ok=True)
    reg = build_default_registry(tmp_ws)

    ok = reg.run("save_text_file", {"path": "hello.txt", "content": "hi"})
    assert ok.ok, ok.text
    assert (tmp_ws / "hello.txt").read_text(encoding="utf-8") == "hi"

    # Attempt to escape workspace — should fail gracefully via ToolResult.ok=False.
    escape = reg.run("save_text_file",
                     {"path": "../escape.txt", "content": "no"})
    assert not escape.ok, f"expected sandbox to block this write: {escape.text}"
    assert "outside workspace" in escape.text.lower()
    print("OK  write_path_sandboxing")


def test_context_compaction_skips_when_under_byte_budget():
    from rich.console import Console

    from agent.agent import Agent
    from agent.config import Config

    tmp_ws = Path(".pytest_tmp") / "compact_ws2"
    tmp_ws.mkdir(parents=True, exist_ok=True)
    tiny = "data:image/png;base64," + "x" * 50
    img = {"type": "image_url", "image_url": {"url": tiny}}
    cfg = Config(
        base_url="http://localhost:0",
        api_key="sk",
        model="gpt-4o",
        workspace_dir=tmp_ws,
        context_image_max_bytes=5000,
        context_image_keep_last=1,
    )
    agent = Agent(cfg, console=Console(force_terminal=False, width=120))
    messages: list = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": [{"type": "text", "text": "first"}, img]},
        {"role": "user", "content": [{"type": "text", "text": "second"}, img]},
    ]
    assert agent._inline_image_url_byte_estimate(messages) <= 5000
    agent._compact_old_inline_images(messages)
    assert any(
        isinstance(p, dict) and p.get("type") == "image_url"
        for p in messages[1]["content"]
    )
    assert any(
        isinstance(p, dict) and p.get("type") == "image_url"
        for p in messages[2]["content"]
    )


def test_context_compaction_strips_oldest_when_over_byte_budget():
    from rich.console import Console

    from agent.agent import Agent
    from agent.config import Config

    tmp_ws = Path(".pytest_tmp") / "compact_ws3"
    tmp_ws.mkdir(parents=True, exist_ok=True)
    tiny = "data:image/png;base64," + "x" * 200
    img = {"type": "image_url", "image_url": {"url": tiny}}
    cfg = Config(
        base_url="http://localhost:0",
        api_key="sk",
        model="gpt-4o",
        workspace_dir=tmp_ws,
        context_image_max_bytes=250,
        context_image_keep_last=1,
    )
    agent = Agent(cfg, console=Console(force_terminal=False, width=120))
    messages: list = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": [{"type": "text", "text": "first"}, img]},
        {"role": "user", "content": [{"type": "text", "text": "second"}, img]},
    ]
    assert agent._inline_image_url_byte_estimate(messages) > 250
    agent._compact_old_inline_images(messages)
    assert not any(
        isinstance(p, dict) and p.get("type") == "image_url"
        for p in messages[1]["content"]
    )
    assert any(
        isinstance(p, dict) and p.get("type") == "image_url"
        for p in messages[2]["content"]
    )
    assert agent._inline_image_url_byte_estimate(messages) <= 250


def test_build_pairwise_roi_complete_graph():
    from case10_dual_roi_refine import build_pairwise_roi

    centers = {"tp": (0.0, 0.0), "ref_1": (3.0, 4.0), "ref_2": (0.0, 5.0)}
    p = build_pairwise_roi(centers)
    assert len(p) == 3
    by_pair = {(e["a"], e["b"]): e for e in p}
    assert ("tp", "ref_1") in by_pair
    assert ("tp", "ref_2") in by_pair
    assert ("ref_1", "ref_2") in by_pair
    e01 = by_pair[("tp", "ref_1")]
    assert e01["dist_px"] == 5.0
    assert e01["dx_a_to_b"] == 3.0
    assert e01["dy_a_to_b"] == 4.0
    print("OK  build_pairwise_roi_complete_graph")


def test_compute_locator_to_board_scale_ref_ref_median():
    from case10_dual_roi_refine import compute_locator_to_board_scale

    locator_obj: dict = {
        "pairwise_roi": [
            {"a": "ref_1", "b": "ref_2", "dist_px": 10.0},
            {"a": "tp", "b": "ref_1", "dist_px": 5.0},
        ],
        "graph_edges": [],
    }
    board_refs = {"ref_1": (0.0, 0.0), "ref_2": (20.0, 0.0)}
    scale, mode = compute_locator_to_board_scale(locator_obj, board_refs, (100.0, 100.0), (0.0, 0.0))
    assert mode == "ref_ref_median"
    assert abs(float(scale) - 2.0) < 1e-6
    print("OK  compute_locator_to_board_scale_ref_ref_median")


def test_compute_locator_to_board_scale_tp_ref_fallback():
    from case10_dual_roi_refine import compute_locator_to_board_scale

    locator_obj: dict = {
        "pairwise_roi": [],
        "graph_edges": [{"from": "tp", "to": "ref_1", "dist_px": 10.0}],
    }
    board_refs = {"ref_1": (130.0, 100.0)}
    scale, mode = compute_locator_to_board_scale(locator_obj, board_refs, (100.0, 100.0), (0.0, 0.0))
    assert mode == "tp_ref_median"
    assert abs(float(scale) - 3.0) < 1e-6
    print("OK  compute_locator_to_board_scale_tp_ref_fallback")


def test_compute_locator_to_board_scale_no_scale():
    from case10_dual_roi_refine import compute_locator_to_board_scale

    scale, mode = compute_locator_to_board_scale({"pairwise_roi": [], "graph_edges": []}, {}, (0.0, 0.0), (0.0, 0.0))
    assert scale is None
    assert mode == "no_scale"
    print("OK  compute_locator_to_board_scale_no_scale")


def main() -> int:
    test_missing_env_raises()
    test_tool_registry_openai_schema()
    test_fallback_tool_call_parsing()
    test_fallback_tool_call_nested_finish_json()
    test_fallback_tool_call_dedupes_multiple_finish_tags()
    test_normalize_finish_arguments_flattened()
    test_thinking_mode_payload()
    test_thinking_mode_disabled_does_not_leak()
    test_intern_s1_thinking_mode_payload()
    test_load_task_resolves_relative_paths()
    test_write_path_sandboxing()
    test_build_pairwise_roi_complete_graph()
    test_compute_locator_to_board_scale_ref_ref_median()
    test_compute_locator_to_board_scale_tp_ref_fallback()
    test_compute_locator_to_board_scale_no_scale()
    print("\nALL UNIT TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
