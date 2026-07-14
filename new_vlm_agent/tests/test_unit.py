"""Small unit-level checks that don't need a VLM backend."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import base64
import io
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))


def test_oversized_image_data_url_is_compressed_without_touching_source(tmp_path, monkeypatch):
    from PIL import Image
    from agent.utils import encode_image_data_url

    source = tmp_path / "large.png"
    Image.effect_noise((1800, 1200), 100).convert("RGB").save(source, format="PNG")
    original = source.read_bytes()
    monkeypatch.setenv("VLM_IMAGE_DATA_URI_MAX_BYTES", str(300 * 1024))

    data_url = encode_image_data_url(source)

    assert data_url.startswith("data:image/jpeg;base64,")
    assert len(data_url.encode("utf-8")) <= 300 * 1024
    assert source.read_bytes() == original
    encoded = data_url.split(",", 1)[1]
    with Image.open(io.BytesIO(base64.b64decode(encoded))) as attached:
        attached.verify()


def test_resolved_side_plans_are_physically_isolated():
    from agent.agent import Agent

    front = Agent._workflow_plan_for_resolved_side("front")
    back = Agent._workflow_plan_for_resolved_side("back")
    front_tools = {tool for step in front for tool in step.allowed_tools}
    back_tools = {tool for step in back for tool in step.allowed_tools}

    assert "case12_build_and_align_from_step02_anchors" in front_tools
    assert "register_back_board_from_outline_and_holes" not in front_tools
    assert "detect_largest_ic_on_board_full" not in back_tools
    assert "case12_build_and_align_from_step02_anchors" not in back_tools
    assert "register_back_board_from_outline_and_holes" in back_tools
    assert "prepare_back_board_landmark_candidates" in back_tools


def test_explicit_board_side_bypasses_auto_even_when_both_photos_exist(tmp_path):
    from rich.console import Console

    from agent.agent import Agent
    from agent.config import Config

    agent = Agent(
        Config(
            base_url="http://localhost:0",
            api_key="sk",
            model="mock",
            workspace_dir=tmp_path,
        ),
        console=Console(force_terminal=False, width=120),
    )
    common = {"front_board_photo": "front.jpg", "back_board_photo": "back.jpg"}
    front = agent._build_workflow_plan({**common, "target_board_side": "front"})
    back = agent._build_workflow_plan({**common, "target_board_side": "back"})

    assert all(step.step_id != "partside_locator_decision" for step in front)
    assert all(step.step_id != "partside_locator_decision" for step in back)
    assert any(step.step_id == "parta_board_largest_ic" for step in front)
    assert all(step.step_id != "parta_board_largest_ic" for step in back)


def test_side_guard_blocks_opposite_geometry_tools(tmp_path):
    from rich.console import Console

    from agent.agent import Agent
    from agent.config import Config
    from agent.llm_client import ToolInvocation

    debug = tmp_path / "debug"
    debug.mkdir()
    cfg = Config(
        base_url="http://localhost:0",
        api_key="sk",
        model="mock",
        workspace_dir=tmp_path,
    )
    agent = Agent(cfg, console=Console(force_terminal=False, width=120))
    mixed = agent._auto_side_workflow_plan()
    partd_idx = next(i for i, step in enumerate(mixed) if step.step_id == "partd_case12_align_and_finish")

    (debug / "board_side_decision.json").write_text(
        json.dumps({"side": "front"}), encoding="utf-8"
    )
    back_call = ToolInvocation(
        id="back", name="register_back_board_from_outline_and_holes", arguments={}
    )
    assert "side=front" in agent._is_call_blocked_by_plan(back_call, mixed, partd_idx)
    assert "side=front" in agent._is_call_blocked_by_plan(
        back_call, mixed, len(mixed)
    )

    (debug / "board_side_decision.json").write_text(
        json.dumps({"side": "back"}), encoding="utf-8"
    )
    front_call = ToolInvocation(
        id="front", name="case12_build_and_align_from_step02_anchors", arguments={}
    )
    assert "side=back" in agent._is_call_blocked_by_plan(front_call, mixed, partd_idx)
    assert "side=back" in agent._is_call_blocked_by_plan(
        front_call, mixed, len(mixed)
    )


def test_finish_rejects_camera_view_opposite_to_locked_side(tmp_path):
    from rich.console import Console

    from agent.agent import Agent
    from agent.config import Config

    debug = tmp_path / "debug"
    debug.mkdir()
    (debug / "board_side_decision.json").write_text(
        json.dumps({"side": "front"}), encoding="utf-8"
    )
    agent = Agent(
        Config(
            base_url="http://localhost:0",
            api_key="sk",
            model="mock",
            workspace_dir=tmp_path,
        ),
        console=Console(force_terminal=False, width=120),
    )

    errors = agent._validate_finish_answer({
        "camera_view": "back", "pixel": [10, 20], "needs_user_help": False,
    })
    assert any("locked board side" in error for error in errors)


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
        "list_files", "read_text_file", "save_text_file",
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


def test_fallback_tool_call_recovers_missing_trailing_brace():
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

    # Missing one trailing `}` before </tool_call> (real-world malformed output).
    malformed = (
        '<tool_call>{"name": "finish", "arguments": {"answer": {"pixel": [2, 3], '
        '"needs_user_help": false, "reasoning": "ok"}}</tool_call>'
    )

    def fake_create(**kwargs):
        return _Resp(malformed)

    client._client.chat.completions.create = fake_create  # type: ignore[assignment]
    reply = client.chat(messages=[], tools_schema=None)
    assert len(reply.tool_calls) == 1
    assert reply.tool_calls[0].name == "finish"
    assert reply.tool_calls[0].arguments["answer"]["pixel"] == [2, 3]
    print("OK  fallback_tool_call_recovers_missing_trailing_brace")


def test_fallback_tool_call_recovers_missing_close_tag():
    """Models often emit <tool_call>{...} without </tool_call>."""
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
            '<tool_call>{"name": "list_files", "arguments": {"path": "data/cases/case_001"}}'
        )

    client._client.chat.completions.create = fake_create  # type: ignore[assignment]
    reply = client.chat(messages=[], tools_schema=None)

    assert len(reply.tool_calls) == 1
    assert reply.tool_calls[0].name == "list_files"
    assert reply.tool_calls[0].arguments == {"path": "data/cases/case_001"}
    print("OK  fallback_tool_call_recovers_missing_close_tag")


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
        compact = compose_agent_question(task)
        assert "Short ask" in compact
        assert "Part 0 快速路径" in compact
        assert "Body unique marker xyzzy." not in compact
    print("OK  compose_agent_question_embeds_workflow")


def test_load_task_aliases_locator_pdf_and_compact_flag():
    import tempfile
    import textwrap
    from agent.config import load_task

    with tempfile.TemporaryDirectory() as tmp:
        case_dir = Path(tmp) / "case"
        case_dir.mkdir()
        (case_dir / "assy.pdf").write_bytes(b"%PDF-1.4\n")
        yaml_path = case_dir / "task.yaml"
        yaml_path.write_text(textwrap.dedent("""
            embed_workflow_in_prompt: false
            question: go
            inputs:
              locator_pdf: assy.pdf
        """), encoding="utf-8")
        data = load_task(yaml_path)
        assert data["inputs"]["assembly_drawing_pdf"] == data["inputs"]["locator_pdf"]
        assert data["inputs"]["_compact_workflow"] == "true"
    print("OK  load_task_aliases_locator_pdf_and_compact_flag")


def test_load_task_start_from_step3_flag():
    import tempfile
    import textwrap
    from agent.config import load_task

    with tempfile.TemporaryDirectory() as tmp:
        case_dir = Path(tmp) / "case"
        case_dir.mkdir()
        yaml_path = case_dir / "task.yaml"
        yaml_path.write_text(textwrap.dedent("""
            start_from_step3: true
            question: step3 only
            inputs:
              front_locator_marked: loc.png
              front_board_marked: brd.png
        """), encoding="utf-8")
        data = load_task(yaml_path)
        assert data["inputs"]["_start_from_step3"] == "true"
        assert data["embed_workflow_in_prompt"] is False
    print("OK  load_task_start_from_step3_flag")


def test_clamp_board_pixel():
    from case12_step02_graph import clamp_board_pixel

    assert clamp_board_pixel(1061, 1220, 1024, 768) == (1023, 767)
    assert clamp_board_pixel(10.4, 20.6, 100, 80) == (10, 21)
    print("OK  clamp_board_pixel")


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


def test_build_pairwise_roi_includes_ref_ic_order():
    from case10_dual_roi_refine import build_pairwise_roi

    centers = {
        "tp": (10.0, 10.0),
        "ref_ic": (100.0, 10.0),
        "ref_1": (10.0, 80.0),
    }
    p = build_pairwise_roi(centers)
    nodes_in_edges = set()
    for e in p:
        nodes_in_edges.add(e["a"])
        nodes_in_edges.add(e["b"])
    assert nodes_in_edges == {"tp", "ref_ic", "ref_1"}
    assert len(p) == 3  # C(3,2)
    print("OK  build_pairwise_roi_includes_ref_ic_order")


def test_case12_ic_bbox_isotropic_preserves_interpoint_ratios():
    from case12_step02_graph import _fit_isotropic_scale_translate_ic_boxes

    bbox_l = (100, 80, 200, 160)  # 100 x 80
    bbox_b = (300, 200, 500, 360)  # 200 x 160 => s_w=s_h=2, perfect
    s, tx, ty, meta = _fit_isotropic_scale_translate_ic_boxes(bbox_l, bbox_b)
    assert abs(s - 2.0) < 1e-6
    assert abs(tx - 100.0) < 1e-3 and abs(ty - 40.0) < 1e-3
    assert meta["ic_corner_residual_max_px"] < 0.02

    p1 = (10.0, 20.0)
    p2 = (40.0, 50.0)
    d_before = ((p2[0] - p1[0]) ** 2 + (p2[1] - p1[1]) ** 2) ** 0.5
    q1 = (s * p1[0] + tx, s * p1[1] + ty)
    q2 = (s * p2[0] + tx, s * p2[1] + ty)
    d_after = ((q2[0] - q1[0]) ** 2 + (q2[1] - q1[1]) ** 2) ** 0.5
    assert abs(d_after - s * d_before) < 1e-6
    print("OK  case12_ic_bbox_isotropic_preserves_interpoint_ratios")


def test_parse_vlm_board_largest_ic_bbox():
    from case12_step02_graph import _parse_vlm_board_largest_ic_bbox

    bb = _parse_vlm_board_largest_ic_bbox(
        {"board_largest_ic_bbox_xyxy": [10, 20, 100, 200]},
        (512, 384),
    )
    assert bb == (10, 20, 100, 200)
    bb2 = _parse_vlm_board_largest_ic_bbox(
        {"bbox_board_ic_xyxy": [500, 400, 10, 5]},
        (512, 384),
    )
    assert bb2[0] < bb2[2] and bb2[1] < bb2[3]
    print("OK  parse_vlm_board_largest_ic_bbox")


def test_case12_vlm_correspondence_explicit_s():
    from case12_step02_graph import _compute_st_from_vlm_ic_correspondence

    bbox_loc = (100, 100, 200, 150)
    ref_ic_loc = (150.0, 125.0)
    obj = {
        "ref_ic_center_board_px": [300.0, 250.0],
        "isotropic_scale_locator_to_board": 2.0,
    }
    s, tx, ty, meta, bb = _compute_st_from_vlm_ic_correspondence(obj, (400, 300), bbox_loc, ref_ic_loc)
    assert abs(s - 2.0) < 1e-6
    assert abs(tx - 0.0) < 1e-6
    assert abs(ty - 0.0) < 1e-6
    assert bb is None
    assert meta["align_mode"] == "vlm_ref_ic_center_explicit_s"
    print("OK  case12_vlm_correspondence_explicit_s")


def test_case12_vlm_correspondence_bbox_derives_s():
    from case12_step02_graph import _compute_st_from_vlm_ic_correspondence

    bbox_loc = (0, 0, 100, 100)
    ref_ic_loc = (50.0, 50.0)
    obj = {
        "ref_ic_center_board_px": [150.0, 110.0],
        "board_largest_ic_bbox_xyxy": [100, 80, 200, 140],
    }
    s, tx, ty, meta, bb = _compute_st_from_vlm_ic_correspondence(obj, (500, 400), bbox_loc, ref_ic_loc)
    assert abs(s - 0.8) < 1e-5
    assert abs((s * ref_ic_loc[0] + tx) - 150.0) < 1e-3
    assert abs((s * ref_ic_loc[1] + ty) - 110.0) < 1e-3
    assert meta["align_mode"] == "vlm_ref_ic_center_bbox_derived_s"
    assert bb is not None
    print("OK  case12_vlm_correspondence_bbox_derives_s")


def test_case12_refinement_validates_small_nudge():
    from case12_step02_graph import validate_refinement_against_base

    base = {"tp": (100.0, 100.0), "ref_ic": (200.0, 100.0), "ref_1": (100.0, 200.0)}
    refined = {"tp": (102.0, 99.0), "ref_ic": (201.0, 101.0), "ref_1": (99.0, 201.0)}
    stats = validate_refinement_against_base(
        base, refined, max_delta_px=10.0, max_relative_pairwise_dist_change=0.15
    )
    assert stats["max_delta_px"] <= 10.0
    print("OK  case12_refinement_validates_small_nudge")


def test_case12_refinement_rejects_large_delta():
    from case12_step02_graph import validate_refinement_against_base

    base = {"tp": (0.0, 0.0), "ref_ic": (10.0, 0.0)}
    refined = {"tp": (50.0, 0.0), "ref_ic": (10.0, 0.0)}
    try:
        validate_refinement_against_base(
            base, refined, max_delta_px=5.0, max_relative_pairwise_dist_change=None
        )
    except ValueError as e:
        assert "max_delta_px" in str(e)
        print("OK  case12_refinement_rejects_large_delta")
        return
    raise AssertionError("expected ValueError")


def test_case12_refinement_rejects_topology_stretch():
    from case12_step02_graph import validate_refinement_against_base

    base = {"tp": (0.0, 0.0), "ref_ic": (100.0, 0.0), "ref_1": (0.0, 100.0)}
    refined = {"tp": (0.0, 0.0), "ref_ic": (130.0, 0.0), "ref_1": (0.0, 100.0)}
    try:
        validate_refinement_against_base(
            base, refined, max_delta_px=50.0, max_relative_pairwise_dist_change=0.12
        )
    except ValueError as e:
        assert "relative dist change" in str(e) or "pair" in str(e)
        print("OK  case12_refinement_rejects_topology_stretch")
        return
    raise AssertionError("expected ValueError")


def test_detect_green_tp_center_hsv_ring():
    import cv2
    import numpy as np
    from case10_dual_roi_refine import _detect_green_tp_center

    img = np.full((400, 400, 3), 255, dtype=np.uint8)
    cv2.circle(img, (200, 180), 14, (0, 255, 0), 2)
    cv2.circle(img, (200, 180), 8, (255, 255, 255), -1)
    tp = _detect_green_tp_center(img)
    assert tp is not None
    cx, cy, rad = tp
    assert abs(cx - 200) < 15 and abs(cy - 180) < 15
    assert rad >= 4
    print("OK  detect_green_tp_center_hsv_ring")


def test_detect_green_tp_on_workspace_locator_if_present():
    from pathlib import Path

    p = Path("workspace/debug/step02_locator_front_anchor.png")
    if not p.is_file():
        print("SKIP  detect_green_tp_on_workspace_locator_if_present (no png)")
        return
    import cv2
    from case10_dual_roi_refine import _detect_green_tp_center

    bgr = cv2.imread(str(p))
    tp = _detect_green_tp_center(bgr)
    assert tp is not None, "green TP must be detected on step02_locator_front_anchor"
    print(f"OK  detect_green_tp_on_workspace_locator tp={tp}")


def test_vlm_test_blocks_case12_legacy_align_env():
    from case12_step02_graph import run_align_locator_graph_to_board_ic_bbox

    prev = os.environ.get("VLM_AGENT_WORKFLOW_MODE")
    os.environ["VLM_AGENT_WORKFLOW_MODE"] = "vlm_test"
    try:
        try:
            run_align_locator_graph_to_board_ic_bbox(workspace="__no_such__/ws")
        except RuntimeError as e:
            assert "vlm_test" in str(e).replace(" ", "").lower() or "forbidden" in str(
                e
            ).lower()
            print("OK  vlm_test_blocks_case12_legacy_align_env")
            return
        raise AssertionError("expected RuntimeError from vlm_test guard")
    finally:
        if prev is None:
            os.environ.pop("VLM_AGENT_WORKFLOW_MODE", None)
        else:
            os.environ["VLM_AGENT_WORKFLOW_MODE"] = prev


def test_vlm_test_run_python_regex_allows_only_vlm_symbol():
    from agent.builtin_tools import build_default_registry, set_runtime_context

    root = _HERE.parent
    with tempfile.TemporaryDirectory() as td:
        ws = Path(td)
        reg = build_default_registry(ws)
        set_runtime_context(root, ws, {}, workflow_mode="vlm_test")
        r = reg.run(
            "run_python",
            {"code": "pass\n# run_align_locator_graph_to_board_ic_bbox()\n"},
        )
        assert r.ok is False
        assert "vlm_test-guard" in (r.text or "")

        r_ok = reg.run(
            "run_python",
            {
                "code": (
                    "run_align_locator_graph_to_board_ic_bbox_vlm = lambda: None\n"
                    "del run_align_locator_graph_to_board_ic_bbox_vlm\n"
                ),
            },
        )
        assert r_ok.ok is True

        rs = reg.run(
            "save_text_file",
            {"path": "debug/case10_largest_ic.json", "content": "{}"},
        )
        assert rs.ok is False
        assert "vlm_test-guard" in (rs.text or "")
    set_runtime_context(root, root / "workspace", {}, workflow_mode="default")


def test_vlm_test_run_python_physical_board_geometry_guard():
    from agent.builtin_tools import build_default_registry, set_runtime_context
    from PIL import Image

    root = _HERE.parent
    with tempfile.TemporaryDirectory() as td:
        ws = Path(td)
        dbg = ws / "debug"
        dbg.mkdir(parents=True)
        Image.new("RGB", (32, 24)).save(dbg / "case10_board_landscape.png")
        reg = build_default_registry(ws)
        set_runtime_context(root, ws, {}, workflow_mode="vlm_test")

        benign = """import cv2
img = cv2.imread("debug/case10_board_landscape.png")
print(img.shape)
"""
        r_ok = reg.run("run_python", {"code": benign})
        assert r_ok.ok is True

        bad_rect = benign + '\ncv2.rectangle(img, (0,0), (10,10), (0,0,255), 3)\n'
        r_bad = reg.run("run_python", {"code": bad_rect})
        assert r_bad.ok is False
        assert "classic CV" in (r_bad.text or "") or "vlm_test-guard" in (r_bad.text or "")

        contours = benign + '\ncv2.findContours(img, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)\n'
        r_cnt = reg.run("run_python", {"code": contours})
        assert r_cnt.ok is False

    set_runtime_context(root, root / "workspace", {}, workflow_mode="default")


def test_vlm_test_annotate_physical_board_bbox_forbidden_circle_ok():
    from agent.builtin_tools import build_default_registry, set_runtime_context
    from PIL import Image

    root = _HERE.parent
    with tempfile.TemporaryDirectory() as td:
        ws = Path(td)
        dbg = ws / "debug"
        dbg.mkdir(parents=True)
        Image.new("RGB", (320, 240)).save(dbg / "case10_board_landscape.png")
        reg = build_default_registry(ws)
        set_runtime_context(root, ws, {}, workflow_mode="vlm_test")

        bbox_res = reg.run(
            "annotate_image",
            {
                "path": "debug/case10_board_landscape.png",
                "points": [{"bbox": [12, 12, 40, 40], "color": "red"}],
                "out_path": "debug/_tmp_physical_board_bbox.png",
            },
        )
        assert bbox_res.ok is False
        assert "bbox" in (bbox_res.text or "").lower()

        circle_res = reg.run(
            "annotate_image",
            {
                "path": "debug/case10_board_landscape.png",
                "points": [{"x": 160, "y": 120, "color": "blue", "radius": 8}],
                "out_path": "debug/_tmp_physical_board_dot.png",
            },
        )
        assert circle_res.ok is True

    set_runtime_context(root, root / "workspace", {}, workflow_mode="default")


def test_document_ref_message_format():
    from agent.llm_client import LLMClient

    msg = LLMClient.document_ref_message("file-fe-abc123")
    assert msg == {"role": "system", "content": "fileid://file-fe-abc123"}
    print("OK  document_ref_message_format")


def test_upload_extract_file_polls_until_processed():
    from agent.config import Config
    from agent.llm_client import LLMClient

    cfg = Config(
        base_url="http://localhost:0",
        api_key="sk-unused",
        model="mock",
    )
    client = LLMClient(cfg)

    class _FileObj:
        def __init__(self, file_id: str, status: str):
            self.id = file_id
            self.status = status

    states = iter(["processing", "processed"])

    def fake_create(**kwargs):
        assert kwargs.get("purpose") == "file-extract"
        return _FileObj("file-fe-test", next(states))

    def fake_retrieve(file_id: str):
        assert file_id == "file-fe-test"
        return _FileObj(file_id, next(states, "processed"))

    client._client.files.create = fake_create  # type: ignore[assignment]
    client._client.files.retrieve = fake_retrieve  # type: ignore[assignment]

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(b"%PDF-1.4\n")
        tmp_path = tmp.name

    try:
        file_id = client.upload_extract_file(tmp_path, poll_interval_sec=0.01)
        assert file_id == "file-fe-test"
    finally:
        Path(tmp_path).unlink(missing_ok=True)
    print("OK  upload_extract_file_polls_until_processed")


def main() -> int:
    test_missing_env_raises()
    test_tool_registry_openai_schema()
    test_fallback_tool_call_parsing()
    test_fallback_tool_call_nested_finish_json()
    test_fallback_tool_call_dedupes_multiple_finish_tags()
    test_fallback_tool_call_recovers_missing_trailing_brace()
    test_fallback_tool_call_recovers_missing_close_tag()
    test_normalize_finish_arguments_flattened()
    test_thinking_mode_payload()
    test_thinking_mode_disabled_does_not_leak()
    test_intern_s1_thinking_mode_payload()
    test_load_task_resolves_relative_paths()
    test_load_task_injects_default_skill_docs_when_layout_matches()
    test_load_task_aliases_locator_pdf_and_compact_flag()
    test_load_task_start_from_step3_flag()
    test_clamp_board_pixel()
    test_compose_agent_question_embeds_workflow()
    test_write_path_sandboxing()
    test_build_pairwise_roi_complete_graph()
    test_compute_locator_to_board_scale_ref_ref_median()
    test_compute_locator_to_board_scale_tp_ref_fallback()
    test_compute_locator_to_board_scale_no_scale()
    test_build_pairwise_roi_includes_ref_ic_order()
    test_case12_ic_bbox_isotropic_preserves_interpoint_ratios()
    test_parse_vlm_board_largest_ic_bbox()
    test_case12_vlm_correspondence_explicit_s()
    test_case12_vlm_correspondence_bbox_derives_s()
    test_case12_refinement_validates_small_nudge()
    test_case12_refinement_rejects_large_delta()
    test_case12_refinement_rejects_topology_stretch()
    test_detect_green_tp_center_hsv_ring()
    test_detect_green_tp_on_workspace_locator_if_present()
    test_vlm_test_blocks_case12_legacy_align_env()
    test_vlm_test_run_python_regex_allows_only_vlm_symbol()
    test_vlm_test_run_python_physical_board_geometry_guard()
    test_vlm_test_annotate_physical_board_bbox_forbidden_circle_ok()
    test_document_ref_message_format()
    test_upload_extract_file_polls_until_processed()
    print("\nALL UNIT TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
