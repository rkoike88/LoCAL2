"""WebSearchTool — executes web_search tool calls from GeneratorAgent."""
from __future__ import annotations

import logging
from datetime import date

from local.config_loader import ConfigManager, get_config
from local.protocol.envelope import MessageEnvelope
from local.protocol.subjects import (
    TOOL_ACTIVITY_WEB_SEARCH,
    TOOL_REQUEST_WEB_SEARCH,
    TOOL_RESULT_WEB_SEARCH,
    TOOL_SCHEMA,
    TOOL_SCHEMA_REQUEST,
)
from local.transport.bus_config import make_participant_bus

logger = logging.getLogger(__name__)

CONFIG_NAME = "web_search"


class WebSearchTool:
    TOOL_ID = "web_search_tool"

    def __init__(self) -> None:
        cfg = get_config(CONFIG_NAME)
        self._provider: str = cfg.get("provider", "searxng")
        self._searxng_url: str = cfg.get("searxng_url", "http://localhost:8080")
        self._max_results: int = cfg.get("max_results", 5)
        self._timeout: float = cfg.get("timeout", 10)
        self._pub, self._sub = make_participant_bus([TOOL_REQUEST_WEB_SEARCH, TOOL_SCHEMA_REQUEST])

    def run(self) -> None:
        self._announce_schema()
        print(f"[web_search_tool] provider={self._provider}  max_results={self._max_results}")
        while True:
            try:
                envelope = self._sub.receive()
            except Exception as exc:
                logger.error("WebSearchTool: receive error: %s", exc)
                continue
            if envelope.subject == TOOL_SCHEMA_REQUEST:
                ConfigManager.invalidate(CONFIG_NAME)
                self._announce_schema()
            elif envelope.subject == TOOL_REQUEST_WEB_SEARCH:
                self._handle_request(envelope)

    def _build_schema(self) -> dict:
        cfg = get_config(CONFIG_NAME)
        description = cfg.get("description", "Search the web for current information.").strip()
        param_query = cfg.get("param_query", "The search query string.").strip()
        return {
            "type": "function",
            "function": {
                "name": "web_search",
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

    def _announce_schema(self) -> None:
        self._pub.publish(MessageEnvelope.create(
            message_type="tool_schema",
            subject=TOOL_SCHEMA,
            sender_id=self.TOOL_ID,
            payload={"schema": self._build_schema()},
        ))

    def _handle_request(self, envelope: MessageEnvelope) -> None:
        args: dict = envelope.payload.get("args", {})
        query: str = args.get("query", "")
        correlation_id = envelope.correlation_id

        self._publish_activity("request", {"query": query}, correlation_id)

        try:
            result = self._search(query)
        except Exception as exc:
            logger.error("WebSearchTool: search failed for %r: %s", query, exc)
            result = f"[web_search error: {exc}]"

        self._publish_activity("result", {"result": result[:200]}, correlation_id)

        self._pub.publish(MessageEnvelope.create(
            message_type="tool_result",
            subject=TOOL_RESULT_WEB_SEARCH,
            sender_id=self.TOOL_ID,
            payload={"result": result, "tool": "web_search"},
            correlation_id=correlation_id,
            metadata={},
        ))

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

    def _publish_activity(self, event_type: str, data: dict, correlation_id: str | None) -> None:
        self._pub.publish(MessageEnvelope.create(
            message_type="tool_activity",
            subject=TOOL_ACTIVITY_WEB_SEARCH,
            sender_id=self.TOOL_ID,
            payload={"event": event_type, "tool": "web_search", **data},
            correlation_id=correlation_id or "",
            metadata={},
        ))
