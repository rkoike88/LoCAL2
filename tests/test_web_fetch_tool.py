"""Unit tests for WebFetchTool — no live bus or network required."""

from unittest.mock import MagicMock, patch
import pytest

from local.tools.web_fetch_tool import WebFetchTool
from local.protocol.subjects import TOOL_RESULT_WEB_FETCH, TOOL_SCHEMA


def _make_tool(max_chars=200) -> WebFetchTool:
    with patch("local.tools.web_fetch_tool.make_participant_bus") as mock_bus, \
         patch("local.tools.web_fetch_tool.get_config") as mock_cfg:
        mock_cfg.return_value = {"max_chars": max_chars, "timeout": 5}
        mock_pub = MagicMock()
        mock_sub = MagicMock()
        mock_bus.return_value = (mock_pub, mock_sub)
        tool = WebFetchTool()
        tool._pub = mock_pub
        tool._sub = mock_sub
    return tool


def _make_request_envelope(url: str, correlation_id: str = "corr-xyz"):
    from local.protocol.envelope import MessageEnvelope
    return MessageEnvelope.create(
        message_type="tool_request",
        subject="tool.request.web_fetch",
        sender_id="generator",
        payload={"args": {"url": url}, "tool": "web_fetch"},
        correlation_id=correlation_id,
        metadata={},
    )


def _make_http_response(html: str, status_code: int = 200):
    mock_resp = MagicMock()
    mock_resp.text = html
    mock_resp.status_code = status_code
    mock_resp.raise_for_status.return_value = None
    return mock_resp


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

class TestSchema:
    def test_schema_has_required_fields(self):
        tool = _make_tool()
        fn = tool._build_schema()["function"]
        assert fn["name"] == "web_fetch"
        assert "description" in fn
        assert fn["parameters"]["required"] == ["url"]

    def test_announce_schema_publishes_to_tool_schema_subject(self):
        tool = _make_tool()
        tool._announce_schema()
        envelope = tool._pub.publish.call_args[0][0]
        assert envelope.subject == TOOL_SCHEMA
        assert envelope.payload["schema"]["function"]["name"] == "web_fetch"


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------

class TestFetch:
    def test_extracts_body_text(self):
        tool = _make_tool(max_chars=1000)
        html = "<html><body><p>Hello world</p><p>Second paragraph.</p></body></html>"
        with patch("httpx.get") as mock_get:
            mock_get.return_value = _make_http_response(html)
            result = tool._fetch("https://example.com")
        assert "Hello world" in result
        assert "Second paragraph" in result

    def test_strips_script_and_style_tags(self):
        tool = _make_tool(max_chars=1000)
        html = "<html><body><script>alert('x')</script><p>Content</p><style>.x{}</style></body></html>"
        with patch("httpx.get") as mock_get:
            mock_get.return_value = _make_http_response(html)
            result = tool._fetch("https://example.com")
        assert "alert" not in result
        assert "Content" in result

    def test_truncates_to_max_chars(self):
        tool = _make_tool(max_chars=50)
        long_text = "word " * 500
        html = f"<html><body><p>{long_text}</p></body></html>"
        with patch("httpx.get") as mock_get:
            mock_get.return_value = _make_http_response(html)
            result = tool._fetch("https://example.com")
        assert len(result) <= 50 + len("\n[truncated at 50 chars]")
        assert "truncated" in result

    def test_no_truncation_when_within_limit(self):
        tool = _make_tool(max_chars=1000)
        html = "<html><body><p>Short content.</p></body></html>"
        with patch("httpx.get") as mock_get:
            mock_get.return_value = _make_http_response(html)
            result = tool._fetch("https://example.com")
        assert "truncated" not in result

    def test_http_error_raises(self):
        tool = _make_tool()
        with patch("httpx.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.raise_for_status.side_effect = Exception("404 Not Found")
            mock_get.return_value = mock_resp
            with pytest.raises(Exception, match="404"):
                tool._fetch("https://example.com/missing")


# ---------------------------------------------------------------------------
# handle_request — correlation_id and error path
# ---------------------------------------------------------------------------

class TestHandleRequest:
    def test_result_published_to_correct_subject(self):
        tool = _make_tool()
        html = "<html><body><p>Test content</p></body></html>"
        with patch("httpx.get") as mock_get:
            mock_get.return_value = _make_http_response(html)
            tool._handle_request(_make_request_envelope("https://example.com", "cid-1"))
        result_env = tool._pub.publish.call_args[0][0]
        assert result_env.subject == TOOL_RESULT_WEB_FETCH

    def test_correlation_id_preserved(self):
        tool = _make_tool()
        html = "<html><body><p>x</p></body></html>"
        with patch("httpx.get") as mock_get:
            mock_get.return_value = _make_http_response(html)
            tool._handle_request(_make_request_envelope("https://example.com", "cid-99"))
        result_env = tool._pub.publish.call_args[0][0]
        assert result_env.correlation_id == "cid-99"

    def test_fetch_exception_returns_error_string(self):
        tool = _make_tool()
        with patch("httpx.get", side_effect=Exception("connection timeout")):
            tool._handle_request(_make_request_envelope("https://bad.url"))
        result_env = tool._pub.publish.call_args[0][0]
        assert "web_fetch error" in result_env.payload["result"]
