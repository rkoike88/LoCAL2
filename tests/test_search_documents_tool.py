"""Unit tests for SearchLibraryTool — no live bus or ChromaDB required."""

from unittest.mock import MagicMock, patch

from local.tools.search_library_tool import SearchLibraryTool


def _make_tool() -> SearchLibraryTool:
    with patch("local.tools.base_tool.make_participant_bus") as mock_bus:
        mock_pub, mock_sub = MagicMock(), MagicMock()
        mock_bus.return_value = (mock_pub, mock_sub)
        mock_docs = MagicMock()
        tool = SearchLibraryTool(document_service=mock_docs)
        tool._pub = mock_pub
        tool._sub = mock_sub
        tool._docs = mock_docs
    return tool


class TestSearchLibraryTool:
    def test_announce_schema_publishes_tool_schema(self):
        tool = _make_tool()
        with patch("local.tools.search_library_tool.get_config", return_value={}):
            tool._announce_schema()
        env = tool._pub.publish.call_args.args[0]
        assert env.subject == "tool.schema"
        assert env.schema["function"]["name"] == "search_library"

    def test_handle_request_publishes_result_and_activity(self):
        tool = _make_tool()
        tool._docs.count.return_value = 1
        tool._docs.search.return_value = [{
            "content": "relevant passage", "source_file": "doc.pdf",
            "chunk_index": 0, "score": 0.9, "page": 1,
        }]
        envelope = MagicMock()
        envelope.correlation_id = "corr-1"
        envelope.payload = {"args": {"query": "test query"}}
        tool._handle_request(envelope)
        subjects = [c.args[0].subject for c in tool._pub.publish.call_args_list]
        assert "tool.result.search_library" in subjects
        assert "tool.activity.search_library" in subjects

    def test_empty_kb_returns_informative_message(self):
        tool = _make_tool()
        tool._docs.count.return_value = 0
        envelope = MagicMock()
        envelope.correlation_id = "corr-2"
        envelope.payload = {"args": {"query": "anything"}}
        tool._handle_request(envelope)
        result_env = next(
            c.args[0] for c in tool._pub.publish.call_args_list
            if c.args[0].subject == "tool.result.search_library"
        )
        assert "empty" in result_env.result.lower()

    def test_empty_query_returns_error_message(self):
        tool = _make_tool()
        tool._docs.count.return_value = 5
        envelope = MagicMock()
        envelope.correlation_id = "corr-3"
        envelope.payload = {"args": {"query": ""}}
        tool._handle_request(envelope)
        result_env = next(
            c.args[0] for c in tool._pub.publish.call_args_list
            if c.args[0].subject == "tool.result.search_library"
        )
        assert "required" in result_env.result.lower()
