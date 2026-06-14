"""Unit tests for SemanticScholarTool — no live network required."""

from unittest.mock import MagicMock, patch

import pytest

import local.tools.semantic_scholar_tool as ss_module
from local.tools.semantic_scholar_tool import SemanticScholarTool, _search_papers


# ---------------------------------------------------------------------------
# _search_papers — mocked httpx
# ---------------------------------------------------------------------------

def _mock_response(papers: list) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = {"data": papers}
    resp.raise_for_status = MagicMock()
    return resp


_PAPER_1 = {
    "title": "Attention Is All You Need",
    "year": 2017,
    "authors": [{"name": "Vaswani"}, {"name": "Shazeer"}],
    "citationCount": 98432,
    "url": "https://semanticscholar.org/paper/abc",
    "abstract": "We propose the Transformer, a model architecture eschewing recurrence.",
}

_PAPER_MANY_AUTHORS = {
    "title": "Large Language Models",
    "year": 2023,
    "authors": [{"name": "A"}, {"name": "B"}, {"name": "C"}, {"name": "D"}],
    "citationCount": 500,
    "url": "https://semanticscholar.org/paper/xyz",
    "abstract": "A survey of large language models.",
}


class TestSearchPapers:
    def setup_method(self):
        # Reset rate-limiter state so tests don't sleep waiting for the gap
        ss_module._last_request_at = 0.0

    def _patch(self, papers):
        return patch("httpx.get", return_value=_mock_response(papers))

    def _patch_cfg(self):
        return patch("local.tools.semantic_scholar_tool.get_config", return_value={
            "max_results": 5, "timeout": 15, "abstract_max_chars": 300, "min_request_gap": 1.2,
            "fields": "title,authors,year,abstract,citationCount,url",
        })

    def test_returns_formatted_paper(self):
        with self._patch_cfg(), self._patch([_PAPER_1]):
            result = _search_papers("transformers", 5)
        assert "Attention Is All You Need" in result
        assert "2017" in result
        assert "Vaswani" in result
        assert "98,432" in result

    def test_et_al_for_many_authors(self):
        with self._patch_cfg(), self._patch([_PAPER_MANY_AUTHORS]):
            result = _search_papers("llm", 5)
        assert "et al." in result

    def test_no_results_message(self):
        with self._patch_cfg(), self._patch([]):
            result = _search_papers("xyzzy nonsense", 5)
        assert "No papers found" in result

    def test_abstract_truncated(self):
        long_abstract = "x" * 500
        paper = {**_PAPER_1, "abstract": long_abstract}
        with self._patch_cfg(), self._patch([paper]):
            result = _search_papers("transformers", 5)
        assert "…" in result
        assert "x" * 500 not in result

    def test_limit_capped_at_max_results(self):
        with self._patch_cfg(), self._patch([_PAPER_1]) as mock_get:
            _search_papers("test", 100)
        call_params = mock_get.call_args.kwargs["params"]
        assert call_params["limit"] == 5  # capped to max_results

    def test_api_key_added_to_headers_when_set(self):
        with self._patch_cfg(), self._patch([_PAPER_1]) as mock_get:
            with patch.dict("os.environ", {"SEMANTIC_SCHOLAR_API_KEY": "test-key"}):
                _search_papers("test", 5)
        headers = mock_get.call_args.kwargs["headers"]
        assert headers.get("x-api-key") == "test-key"

    def test_no_api_key_header_when_env_unset(self):
        with self._patch_cfg(), self._patch([_PAPER_1]) as mock_get:
            with patch.dict("os.environ", {}, clear=True):
                _search_papers("test", 5)
        headers = mock_get.call_args.kwargs["headers"]
        assert "x-api-key" not in headers

    def test_raises_on_network_error(self):
        with self._patch_cfg():
            with patch("httpx.get", side_effect=Exception("timeout")):
                with pytest.raises(Exception):
                    _search_papers("test", 5)

    def test_date_prefix_in_result(self):
        with self._patch_cfg(), self._patch([_PAPER_1]):
            result = _search_papers("transformers", 5)
        assert "Papers:" in result


# ---------------------------------------------------------------------------
# SemanticScholarTool bus behaviour
# ---------------------------------------------------------------------------

class TestSemanticScholarToolBus:
    def _make_tool(self):
        with patch("local.tools.base_tool.make_participant_bus") as mock_bus:
            mock_pub, mock_sub = MagicMock(), MagicMock()
            mock_bus.return_value = (mock_pub, mock_sub)
            tool = SemanticScholarTool()
            tool._pub = mock_pub
            tool._sub = mock_sub
        return tool

    def test_announce_schema_publishes_tool_schema(self):
        tool = self._make_tool()
        tool._announce_schema()
        envelope = tool._pub.publish.call_args.args[0]
        assert envelope.subject == "tool.schema"
        assert envelope.schema["function"]["name"] == "search_papers"

    def test_handle_request_publishes_result_and_activity(self):
        tool = self._make_tool()
        envelope = MagicMock()
        envelope.correlation_id = "corr-1"
        envelope.payload = {"args": {"query": "transformers", "limit": 3}}
        with patch("local.tools.semantic_scholar_tool._search_papers", return_value="paper results"):
            tool._handle_request(envelope)
        subjects = [c.args[0].subject for c in tool._pub.publish.call_args_list]
        assert "tool.result.search_papers" in subjects
        assert "tool.activity.search_papers" in subjects

    def test_handle_request_graceful_on_error(self):
        tool = self._make_tool()
        envelope = MagicMock()
        envelope.correlation_id = "corr-2"
        envelope.payload = {"args": {"query": "test"}}
        with patch("local.tools.semantic_scholar_tool._search_papers", side_effect=Exception("API down")):
            tool._handle_request(envelope)
        result_env = next(
            c.args[0] for c in tool._pub.publish.call_args_list
            if c.args[0].subject == "tool.result.search_papers"
        )
        assert "Search failed" in result_env.result
