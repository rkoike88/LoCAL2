"""WebSearchTool — executes web_search tool calls from GeneratorAgent."""
from __future__ import annotations

import logging
from datetime import date

from local.config_loader import get_config
from local.protocol.envelope import MessageEnvelope
from local.protocol.subjects import (
    TOOL_ACTIVITY_WEB_SEARCH,
    TOOL_CALL_WEB_SEARCH,
    TOOL_RESULT_WEB_SEARCH,
)
from local.tools.base_tool import BaseTool

logger = logging.getLogger(__name__)

CONFIG_NAME = "web_search"


class WebSearchTool(BaseTool):
    TOOL_NAME = "web_search"
    ACTIVITY_SUBJECT = TOOL_ACTIVITY_WEB_SEARCH
    RESULT_SUBJECT = TOOL_RESULT_WEB_SEARCH
    CONFIG_NAME = CONFIG_NAME

    def __init__(self) -> None:
        cfg = get_config(CONFIG_NAME)
        self._provider: str = cfg["provider"]
        self._searxng_url: str = cfg["searxng_url"]
        self._max_results: int = cfg["max_results"]
        self._timeout: float = cfg["timeout"]
        super().__init__(TOOL_CALL_WEB_SEARCH)
        logger.info("web_search_tool: provider=%s  max_results=%s", self._provider, self._max_results)

    def _build_schema(self) -> dict:
        cfg = get_config(CONFIG_NAME)
        description = (cfg.get("description") or "").strip()
        param_query = (cfg.get("param_query") or "").strip()
        return {
            "type": "function",
            "function": {
                "name": self.TOOL_NAME,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": param_query},
                    },
                    "required": ["query"],
                },
            },
        }

    def _handle_request(self, envelope: MessageEnvelope) -> None:
        args: dict = envelope.payload.get("args", {})
        query = args.get("query") or args.get("queries") or ""
        if isinstance(query, list):
            query = " ".join(str(q) for q in query if q)
        query = str(query).strip()
        correlation_id = envelope.correlation_id

        self._publish_activity("request", {"query": query}, correlation_id)

        try:
            result = self._search(query)
        except Exception as exc:
            logger.error("WebSearchTool: search failed for %r: %s", query, exc)
            result = f"[web_search error: {exc}]"

        self._publish_activity("result", {"result": result[:200]}, correlation_id)

        self._publish_result(result, correlation_id)

    def _search(self, query: str) -> str:
        if self._provider == "mock":
            return self._mock_search(query)
        if self._provider == "searxng":
            return self._searxng_search(query)
        raise ValueError(f"Unknown search provider: {self._provider!r}")

    def _searxng_search(self, query: str) -> str:
        import httpx
        resp = httpx.get(
            f"{self._searxng_url}/search",
            params={"q": query, "format": "json"},
            timeout=self._timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", [])[:self._max_results]
        if not results:
            return f"[{date.today()}] No results found for: {query}"
        today = date.today().isoformat()
        lines = [f"[{today}] Web search: {query}\n"]
        for i, r in enumerate(results, 1):
            title = r.get("title", "")
            url = r.get("url", "")
            content = r.get("content", "")
            lines.append(f"{i}. {title}\n   URL: {url}\n   {content}")
        return "\n\n".join(lines)

    def _mock_search(self, query: str) -> str:
        today = date.today().isoformat()
        return (
            f"[{today}] Mock web search: {query}\n\n"
            f"1. Mock Result: {query}\n"
            f"   URL: https://example.com/mock\n"
            f"   This is a mock search result for testing purposes."
        )
