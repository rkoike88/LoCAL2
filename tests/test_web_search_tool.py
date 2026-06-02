"""Unit tests for WebSearchTool — no live bus or SearXNG required."""

from unittest.mock import MagicMock, patch
import pytest

from local.tools.web_search_tool import WebSearchTool
from local.protocol.subjects import TOOL_RESULT_WEB_SEARCH, TOOL_SCHEMA


def _make_tool(provider="mock") -> WebSearchTool:
    """Create a WebSearchTool with patched bus connections."""
    with patch("local.tools.web_search_tool.make_participant_bus") as mock_bus, \
         patch("local.tools.web_search_tool.get_config") as mock_cfg:
        mock_cfg.return_value = {
            "provider": provider,
            "searxng_url": "http://localhost:8080",
            "max_results": 3,
            "timeout": 5,
        }
        mock_pub = MagicMock()
        mock_sub = MagicMock()
        mock_bus.return_value = (mock_pub, mock_sub)
        tool = WebSearchTool()
        tool._pub = mock_pub
        tool._sub = mock_sub
    return tool


def _make_request_envelope(query: str, correlation_id: str = "test-corr-123"):
    from local.protocol.envelope import MessageEnvelope
    return MessageEnvelope.create(
        message_type="tool_request",
        subject="tool.request.web_search",
        sender_id="generator",
        payload={"args": {"query": query}, "tool": "web_search"},
        correlation_id=correlation_id,
        metadata={},
    )


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

class TestSchema:
    def test_schema_has_required_fields(self):
        tool = _make_tool()
        fn = tool._build_schema()["function"]
        assert fn["name"] == "web_search"
        assert "description" in fn
        assert fn["parameters"]["required"] == ["query"]

    def test_announce_schema_publishes_to_tool_schema_subject(self):
        tool = _make_tool()
        tool._announce_schema()
        call_args = tool._pub.publish.call_args
        envelope = call_args[0][0]
        assert envelope.subject == TOOL_SCHEMA
        assert envelope.payload["schema"]["function"]["name"] == "web_search"


# ---------------------------------------------------------------------------
# Mock provider
# ---------------------------------------------------------------------------

class TestMockProvider:
    def test_mock_search_returns_nonempty_string(self):
        tool = _make_tool(provider="mock")
        result = tool._mock_search("test query")
        assert isinstance(result, str)
        assert len(result) > 0
        assert "test query" in result

    def test_mock_search_includes_date_prefix(self):
        tool = _make_tool(provider="mock")
        result = tool._mock_search("anything")
        from datetime import date
        assert date.today().isoformat() in result

    def test_mock_search_includes_url(self):
        tool = _make_tool(provider="mock")
        result = tool._mock_search("anything")
        assert "URL:" in result


# ---------------------------------------------------------------------------
# SearXNG provider
# ---------------------------------------------------------------------------

class TestSearXNGProvider:
    def _make_searxng_response(self, results: list[dict]):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"results": results}
        mock_resp.raise_for_status.return_value = None
        return mock_resp

    def test_formats_results_with_title_url_content(self):
        tool = _make_tool(provider="searxng")
        results = [
            {"title": "Test Page", "url": "https://test.com", "content": "Some snippet."},
        ]
        with patch("httpx.get") as mock_get:
            mock_get.return_value = self._make_searxng_response(results)
            result = tool._searxng_search("test query")
        assert "Test Page" in result
        assert "https://test.com" in result
        assert "Some snippet." in result

    def test_respects_max_results(self):
        tool = _make_tool(provider="searxng")
        results = [{"title": f"R{i}", "url": f"https://r{i}.com", "content": f"c{i}"} for i in range(10)]
        with patch("httpx.get") as mock_get:
            mock_get.return_value = self._make_searxng_response(results)
            result = tool._searxng_search("query")
        # max_results=3, so only 3 numbered entries
        assert "1." in result
        assert "3." in result
        assert "4." not in result

    def test_no_results_returns_error_string(self):
        tool = _make_tool(provider="searxng")
        with patch("httpx.get") as mock_get:
            mock_get.return_value = self._make_searxng_response([])
            result = tool._searxng_search("obscure query")
        assert "No results" in result

    def test_includes_date_prefix(self):
        tool = _make_tool(provider="searxng")
        from datetime import date
        results = [{"title": "T", "url": "https://t.com", "content": "c"}]
        with patch("httpx.get") as mock_get:
            mock_get.return_value = self._make_searxng_response(results)
            result = tool._searxng_search("query")
        assert date.today().isoformat() in result


# ---------------------------------------------------------------------------
# handle_request — correlation_id propagation
# ---------------------------------------------------------------------------

class TestHandleRequest:
    def test_result_published_to_correct_subject(self):
        tool = _make_tool()
        envelope = _make_request_envelope("test", correlation_id="abc-123")
        tool._handle_request(envelope)
        call_args = tool._pub.publish.call_args
        result_env = call_args[0][0]
        assert result_env.subject == TOOL_RESULT_WEB_SEARCH

    def test_correlation_id_preserved(self):
        tool = _make_tool()
        envelope = _make_request_envelope("test", correlation_id="abc-123")
        tool._handle_request(envelope)
        result_env = tool._pub.publish.call_args[0][0]
        assert result_env.correlation_id == "abc-123"

    def test_result_payload_contains_result_string(self):
        tool = _make_tool()
        envelope = _make_request_envelope("hello")
        tool._handle_request(envelope)
        result_env = tool._pub.publish.call_args[0][0]
        assert isinstance(result_env.payload["result"], str)
        assert len(result_env.payload["result"]) > 0

    def test_search_exception_returns_error_string(self):
        tool = _make_tool(provider="searxng")
        with patch.object(tool, "_searxng_search", side_effect=Exception("connection refused")):
            envelope = _make_request_envelope("test")
            tool._handle_request(envelope)
        result_env = tool._pub.publish.call_args[0][0]
        assert "web_search error" in result_env.payload["result"]

    def test_unknown_provider_raises(self):
        tool = _make_tool()
        tool._provider = "unknown_provider"
        with pytest.raises(ValueError, match="Unknown search provider"):
            tool._search("query")
