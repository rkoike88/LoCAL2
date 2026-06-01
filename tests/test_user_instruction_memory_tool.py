"""Unit tests for UserInstructionMemoryTool — MemoryService and bus are fully mocked."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from local.tools.user_instruction_memory_tool import UserInstructionMemoryTool, SCHEMA


def _make_tool() -> tuple[UserInstructionMemoryTool, MagicMock, MagicMock, MagicMock]:
    mock_memory = MagicMock()
    mock_pub = MagicMock()
    mock_sub = MagicMock()
    with patch(
        "local.tools.user_instruction_memory_tool.make_participant_bus",
        return_value=(mock_pub, mock_sub),
    ):
        tool = UserInstructionMemoryTool(memory_service=mock_memory)
    return tool, mock_memory, mock_pub, mock_sub


def _make_envelope(args: dict, correlation_id: str = "corr-1") -> MagicMock:
    env = MagicMock()
    env.subject = "tool.request.user_instruction_memory"
    env.payload = {"args": args}
    env.correlation_id = correlation_id
    return env


class TestSchema:
    def test_tool_name_is_user_instruction_memory(self):
        assert SCHEMA["function"]["name"] == "user_instruction_memory"

    def test_schema_requires_note(self):
        required = SCHEMA["function"]["parameters"]["required"]
        assert "note" in required

    def test_announce_publishes_to_tool_schema(self):
        tool, _, mock_pub, _ = _make_tool()
        tool._announce_schema()
        mock_pub.publish.assert_called_once()
        published = mock_pub.publish.call_args.args[0]
        assert published.subject == "tool.schema"
        assert published.payload["schema"]["function"]["name"] == "user_instruction_memory"


class TestSaveBehaviour:
    def test_calls_write_episodic_with_note(self):
        tool, mock_memory, _, _ = _make_tool()
        tool._handle_request(_make_envelope({"note": "User prefers concise answers"}))
        mock_memory.write_episodic.assert_called_once()
        call_kwargs = mock_memory.write_episodic.call_args
        assert "User prefers concise answers" in str(call_kwargs)

    def test_returns_confirmation_on_success(self):
        tool, _, mock_pub, _ = _make_tool()
        tool._handle_request(_make_envelope({"note": "Remember this fact"}))
        result = mock_pub.publish.call_args.args[0].payload["result"]
        assert "noted" in result
        assert "Remember this fact" in result

    def test_missing_note_returns_error(self):
        tool, mock_memory, mock_pub, _ = _make_tool()
        tool._handle_request(_make_envelope({}))
        result = mock_pub.publish.call_args.args[0].payload["result"]
        assert "note" in result
        mock_memory.write_episodic.assert_not_called()

    def test_empty_note_returns_error(self):
        tool, mock_memory, mock_pub, _ = _make_tool()
        tool._handle_request(_make_envelope({"note": "   "}))
        result = mock_pub.publish.call_args.args[0].payload["result"]
        assert "note" in result
        mock_memory.write_episodic.assert_not_called()


class TestBusWiring:
    def test_result_subject_is_tool_result_user_instruction_memory(self):
        tool, _, mock_pub, _ = _make_tool()
        tool._handle_request(_make_envelope({"note": "something"}))
        published = mock_pub.publish.call_args.args[0]
        assert published.subject == "tool.result.user_instruction_memory"

    def test_correlation_id_propagated(self):
        tool, _, mock_pub, _ = _make_tool()
        tool._handle_request(_make_envelope({"note": "something"}, "my-corr"))
        published = mock_pub.publish.call_args.args[0]
        assert published.correlation_id == "my-corr"

    def test_exception_returns_error_string(self):
        tool, mock_memory, mock_pub, _ = _make_tool()
        mock_memory.write_episodic.side_effect = RuntimeError("db error")
        tool._handle_request(_make_envelope({"note": "something"}))
        result = mock_pub.publish.call_args.args[0].payload["result"]
        assert "user_instruction_memory error" in result
