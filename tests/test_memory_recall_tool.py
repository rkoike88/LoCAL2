"""Unit tests for MemoryRecallTool — MemoryService and bus are fully mocked."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from local.tools.memory_recall_tool import MemoryRecallTool, SCHEMA


def _make_tool() -> tuple[MemoryRecallTool, MagicMock, MagicMock, MagicMock]:
    """Return (tool, mock_memory, mock_pub, mock_sub)."""
    mock_memory = MagicMock()
    mock_pub = MagicMock()
    mock_sub = MagicMock()
    with patch("local.tools.memory_recall_tool.make_participant_bus", return_value=(mock_pub, mock_sub)):
        tool = MemoryRecallTool(memory_service=mock_memory)
    return tool, mock_memory, mock_pub, mock_sub


def _make_envelope(args: dict, correlation_id: str = "corr-123") -> MagicMock:
    env = MagicMock()
    env.subject = "tool.request.recall_memory"
    env.payload = {"args": args}
    env.correlation_id = correlation_id
    return env


# ------------------------------------------------------------------
# Schema
# ------------------------------------------------------------------

class TestSchema:
    def test_tool_name_is_recall_memory(self):
        assert SCHEMA["function"]["name"] == "recall_memory"

    def test_schema_has_topic_and_query_params(self):
        props = SCHEMA["function"]["parameters"]["properties"]
        assert "topic" in props
        assert "query" in props

    def test_announce_publishes_to_tool_schema(self):
        tool, _, mock_pub, _ = _make_tool()
        tool._announce_schema()
        mock_pub.publish.assert_called_once()
        published = mock_pub.publish.call_args.args[0]
        assert published.subject == "tool.schema"
        assert published.payload["schema"]["function"]["name"] == "recall_memory"


# ------------------------------------------------------------------
# Topic mode
# ------------------------------------------------------------------

class TestTopicMode:
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

    def test_topic_takes_precedence_over_query(self):
        tool, mock_memory, _, _ = _make_tool()
        mock_memory.recall_topic.return_value = "value"
        tool._handle_request(_make_envelope({"topic": "user.x", "query": "something"}))
        mock_memory.recall_topic.assert_called_once()
        mock_memory.search_episodic.assert_not_called()


# ------------------------------------------------------------------
# Query mode
# ------------------------------------------------------------------

class TestQueryMode:
    def test_calls_search_episodic_with_query(self):
        tool, mock_memory, _, _ = _make_tool()
        mock_memory.search_episodic.return_value = []
        tool._handle_request(_make_envelope({"query": "what databases does user prefer"}))
        mock_memory.search_episodic.assert_called_once_with("what databases does user prefer")

    def test_formats_candidates_as_numbered_list(self):
        tool, mock_memory, mock_pub, _ = _make_tool()
        mock_memory.search_episodic.return_value = [
            {"content": "Q1\nA1", "metadata": {}, "score": 0.9},
            {"content": "Q2\nA2", "metadata": {}, "score": 0.8},
        ]
        tool._handle_request(_make_envelope({"query": "something"}))
        result = mock_pub.publish.call_args.args[0].payload["result"]
        assert "1." in result and "Q1" in result
        assert "2." in result and "Q2" in result

    def test_returns_no_memories_when_empty(self):
        tool, mock_memory, mock_pub, _ = _make_tool()
        mock_memory.search_episodic.return_value = []
        tool._handle_request(_make_envelope({"query": "anything"}))
        result = mock_pub.publish.call_args.args[0].payload["result"]
        assert "no relevant memories" in result


# ------------------------------------------------------------------
# Edge cases
# ------------------------------------------------------------------

class TestEdgeCases:
    def test_returns_guidance_when_no_args(self):
        tool, _, mock_pub, _ = _make_tool()
        tool._handle_request(_make_envelope({}))
        result = mock_pub.publish.call_args.args[0].payload["result"]
        assert "topic=" in result or "query=" in result

    def test_correlation_id_propagated(self):
        tool, mock_memory, mock_pub, _ = _make_tool()
        mock_memory.recall_topic.return_value = "x"
        tool._handle_request(_make_envelope({"topic": "user.x"}, "my-corr-id"))
        published = mock_pub.publish.call_args.args[0]
        assert published.correlation_id == "my-corr-id"

    def test_result_subject_is_tool_result(self):
        tool, mock_memory, mock_pub, _ = _make_tool()
        mock_memory.recall_topic.return_value = "x"
        tool._handle_request(_make_envelope({"topic": "user.x"}))
        published = mock_pub.publish.call_args.args[0]
        assert published.subject == "tool.result.recall_memory"

    def test_exception_returns_error_string(self):
        tool, mock_memory, mock_pub, _ = _make_tool()
        mock_memory.recall_topic.side_effect = RuntimeError("db down")
        tool._handle_request(_make_envelope({"topic": "user.x"}))
        result = mock_pub.publish.call_args.args[0].payload["result"]
        assert "recall_memory error" in result
