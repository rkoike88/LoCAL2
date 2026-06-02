"""Unit tests for SearchMemoryTool — MemoryService and bus are fully mocked."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from local.tools.search_memory_tool import SearchMemoryTool


def _make_tool() -> tuple[SearchMemoryTool, MagicMock, MagicMock, MagicMock]:
    mock_memory = MagicMock()
    mock_pub = MagicMock()
    mock_sub = MagicMock()
    with patch("local.tools.search_memory_tool.make_participant_bus", return_value=(mock_pub, mock_sub)):
        tool = SearchMemoryTool(memory_service=mock_memory)
    return tool, mock_memory, mock_pub, mock_sub


def _make_envelope(args: dict, correlation_id: str = "corr-1") -> MagicMock:
    env = MagicMock()
    env.subject = "tool.request.search_memory"
    env.payload = {"args": args}
    env.correlation_id = correlation_id
    return env


class TestSchema:
    def test_tool_name_is_search_memory(self):
        tool, _, _, _ = _make_tool()
        assert tool._build_schema()["function"]["name"] == "search_memory"

    def test_schema_requires_query(self):
        tool, _, _, _ = _make_tool()
        required = tool._build_schema()["function"]["parameters"]["required"]
        assert "query" in required

    def test_announce_publishes_to_tool_schema(self):
        tool, _, mock_pub, _ = _make_tool()
        tool._announce_schema()
        mock_pub.publish.assert_called_once()
        published = mock_pub.publish.call_args.args[0]
        assert published.subject == "tool.schema"
        assert published.payload["schema"]["function"]["name"] == "search_memory"


class TestSearchBehaviour:
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

    def test_missing_query_returns_error(self):
        tool, mock_memory, mock_pub, _ = _make_tool()
        tool._handle_request(_make_envelope({}))
        result = mock_pub.publish.call_args.args[0].payload["result"]
        assert "query" in result
        mock_memory.search_episodic.assert_not_called()


class TestBusWiring:
    def test_result_subject_is_tool_result_search_memory(self):
        tool, mock_memory, mock_pub, _ = _make_tool()
        mock_memory.search_episodic.return_value = []
        tool._handle_request(_make_envelope({"query": "something"}))
        published = mock_pub.publish.call_args.args[0]
        assert published.subject == "tool.result.search_memory"

    def test_correlation_id_propagated(self):
        tool, mock_memory, mock_pub, _ = _make_tool()
        mock_memory.search_episodic.return_value = []
        tool._handle_request(_make_envelope({"query": "something"}, "my-corr"))
        published = mock_pub.publish.call_args.args[0]
        assert published.correlation_id == "my-corr"

    def test_exception_returns_error_string(self):
        tool, mock_memory, mock_pub, _ = _make_tool()
        mock_memory.search_episodic.side_effect = RuntimeError("db down")
        tool._handle_request(_make_envelope({"query": "something"}))
        result = mock_pub.publish.call_args.args[0].payload["result"]
        assert "search_memory error" in result
