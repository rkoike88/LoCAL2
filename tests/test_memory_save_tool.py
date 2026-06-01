"""Unit tests for MemorySaveTool — MemoryService and bus are fully mocked."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from local.tools.memory_save_tool import MemorySaveTool, SCHEMA


def _make_tool() -> tuple[MemorySaveTool, MagicMock, MagicMock, MagicMock]:
    mock_memory = MagicMock()
    mock_pub = MagicMock()
    mock_sub = MagicMock()
    with patch("local.tools.memory_save_tool.make_participant_bus", return_value=(mock_pub, mock_sub)):
        tool = MemorySaveTool(memory_service=mock_memory)
    return tool, mock_memory, mock_pub, mock_sub


def _make_envelope(args: dict, correlation_id: str = "corr-1") -> MagicMock:
    env = MagicMock()
    env.subject = "tool.request.save_memory"
    env.payload = {"args": args}
    env.correlation_id = correlation_id
    return env


# ------------------------------------------------------------------
# Schema
# ------------------------------------------------------------------

class TestSchema:
    def test_tool_name_is_save_memory(self):
        assert SCHEMA["function"]["name"] == "save_memory"

    def test_schema_requires_topic_and_value(self):
        required = SCHEMA["function"]["parameters"]["required"]
        assert "topic" in required
        assert "value" in required

    def test_announce_publishes_to_tool_schema(self):
        tool, _, mock_pub, _ = _make_tool()
        tool._announce_schema()
        mock_pub.publish.assert_called_once()
        published = mock_pub.publish.call_args.args[0]
        assert published.subject == "tool.schema"
        assert published.payload["schema"]["function"]["name"] == "save_memory"


# ------------------------------------------------------------------
# Save behaviour
# ------------------------------------------------------------------

class TestSaveBehaviour:
    def test_calls_write_topic_with_correct_args(self):
        tool, mock_memory, _, _ = _make_tool()
        tool._handle_request(_make_envelope({"topic": "user.language", "value": "Python"}))
        mock_memory.write_topic.assert_called_once_with("user.language", "Python")

    def test_returns_confirmation_on_success(self):
        tool, mock_memory, mock_pub, _ = _make_tool()
        tool._handle_request(_make_envelope({"topic": "project.stack", "value": "FastAPI"}))
        result = mock_pub.publish.call_args.args[0].payload["result"]
        assert "saved" in result
        assert "project.stack" in result

    def test_accepts_any_topic_prefix(self):
        tool, mock_memory, _, _ = _make_tool()
        for topic in ["user.x", "project.y", "constraint.z", "custom.key"]:
            tool._handle_request(_make_envelope({"topic": topic, "value": "v"}))
        assert mock_memory.write_topic.call_count == 4

    def test_missing_topic_returns_error(self):
        tool, mock_memory, mock_pub, _ = _make_tool()
        tool._handle_request(_make_envelope({"value": "Python"}))
        result = mock_pub.publish.call_args.args[0].payload["result"]
        assert "topic" in result
        mock_memory.write_topic.assert_not_called()

    def test_missing_value_returns_error(self):
        tool, mock_memory, mock_pub, _ = _make_tool()
        tool._handle_request(_make_envelope({"topic": "user.language"}))
        result = mock_pub.publish.call_args.args[0].payload["result"]
        assert "value" in result
        mock_memory.write_topic.assert_not_called()

    def test_empty_topic_returns_error(self):
        tool, mock_memory, mock_pub, _ = _make_tool()
        tool._handle_request(_make_envelope({"topic": "  ", "value": "Python"}))
        result = mock_pub.publish.call_args.args[0].payload["result"]
        assert "topic" in result
        mock_memory.write_topic.assert_not_called()


# ------------------------------------------------------------------
# Bus wiring
# ------------------------------------------------------------------

class TestBusWiring:
    def test_result_subject_is_tool_result_save_memory(self):
        tool, _, mock_pub, _ = _make_tool()
        tool._handle_request(_make_envelope({"topic": "user.x", "value": "v"}))
        published = mock_pub.publish.call_args.args[0]
        assert published.subject == "tool.result.save_memory"

    def test_correlation_id_propagated(self):
        tool, _, mock_pub, _ = _make_tool()
        tool._handle_request(_make_envelope({"topic": "user.x", "value": "v"}, "my-corr"))
        published = mock_pub.publish.call_args.args[0]
        assert published.correlation_id == "my-corr"

    def test_exception_returns_error_string(self):
        tool, mock_memory, mock_pub, _ = _make_tool()
        mock_memory.write_topic.side_effect = RuntimeError("db error")
        tool._handle_request(_make_envelope({"topic": "user.x", "value": "v"}))
        result = mock_pub.publish.call_args.args[0].payload["result"]
        assert "save_memory error" in result
