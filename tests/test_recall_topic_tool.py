"""Unit tests for RecallTopicTool — MemoryService and bus are fully mocked."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from local.tools.recall_topic_tool import RecallTopicTool, SCHEMA


def _make_tool() -> tuple[RecallTopicTool, MagicMock, MagicMock, MagicMock]:
    mock_memory = MagicMock()
    mock_pub = MagicMock()
    mock_sub = MagicMock()
    with patch("local.tools.recall_topic_tool.make_participant_bus", return_value=(mock_pub, mock_sub)):
        tool = RecallTopicTool(memory_service=mock_memory)
    return tool, mock_memory, mock_pub, mock_sub


def _make_envelope(args: dict, correlation_id: str = "corr-1") -> MagicMock:
    env = MagicMock()
    env.subject = "tool.request.recall_topic"
    env.payload = {"args": args}
    env.correlation_id = correlation_id
    return env


class TestSchema:
    def test_tool_name_is_recall_topic(self):
        assert SCHEMA["function"]["name"] == "recall_topic"

    def test_schema_requires_topic(self):
        required = SCHEMA["function"]["parameters"]["required"]
        assert "topic" in required

    def test_announce_publishes_to_tool_schema(self):
        tool, _, mock_pub, _ = _make_tool()
        tool._announce_schema()
        mock_pub.publish.assert_called_once()
        published = mock_pub.publish.call_args.args[0]
        assert published.subject == "tool.schema"
        assert published.payload["schema"]["function"]["name"] == "recall_topic"


class TestRecallBehaviour:
    def test_calls_recall_topic_with_key(self):
        tool, mock_memory, _, _ = _make_tool()
        mock_memory.recall_topic.return_value = "Python"
        tool._handle_request(_make_envelope({"topic": "user.language"}))
        mock_memory.recall_topic.assert_called_once_with("user.language")

    def test_returns_value_when_found(self):
        tool, mock_memory, mock_pub, _ = _make_tool()
        mock_memory.recall_topic.return_value = "Python"
        tool._handle_request(_make_envelope({"topic": "user.language"}, "corr-1"))
        result = mock_pub.publish.call_args.args[0].payload["result"]
        assert result == "Python"

    def test_returns_not_found_message_when_missing(self):
        tool, mock_memory, mock_pub, _ = _make_tool()
        mock_memory.recall_topic.return_value = None
        tool._handle_request(_make_envelope({"topic": "user.missing"}))
        result = mock_pub.publish.call_args.args[0].payload["result"]
        assert "no memory found" in result
        assert "user.missing" in result

    def test_missing_topic_returns_error(self):
        tool, mock_memory, mock_pub, _ = _make_tool()
        tool._handle_request(_make_envelope({}))
        result = mock_pub.publish.call_args.args[0].payload["result"]
        assert "topic" in result
        mock_memory.recall_topic.assert_not_called()


class TestBusWiring:
    def test_result_subject_is_tool_result_recall_topic(self):
        tool, mock_memory, mock_pub, _ = _make_tool()
        mock_memory.recall_topic.return_value = "x"
        tool._handle_request(_make_envelope({"topic": "user.x"}))
        published = mock_pub.publish.call_args.args[0]
        assert published.subject == "tool.result.recall_topic"

    def test_correlation_id_propagated(self):
        tool, mock_memory, mock_pub, _ = _make_tool()
        mock_memory.recall_topic.return_value = "x"
        tool._handle_request(_make_envelope({"topic": "user.x"}, "my-corr-id"))
        published = mock_pub.publish.call_args.args[0]
        assert published.correlation_id == "my-corr-id"

    def test_exception_returns_error_string(self):
        tool, mock_memory, mock_pub, _ = _make_tool()
        mock_memory.recall_topic.side_effect = RuntimeError("db down")
        tool._handle_request(_make_envelope({"topic": "user.x"}))
        result = mock_pub.publish.call_args.args[0].payload["result"]
        assert "recall_topic error" in result
