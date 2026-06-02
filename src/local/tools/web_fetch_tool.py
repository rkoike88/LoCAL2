"""WebFetchTool — executes web_fetch tool calls from GeneratorAgent."""
from __future__ import annotations

import logging

from local.config_loader import ConfigManager, get_config
from local.protocol.envelope import MessageEnvelope
from local.protocol.subjects import (
    TOOL_ACTIVITY_WEB_FETCH,
    TOOL_REQUEST_WEB_FETCH,
    TOOL_RESULT_WEB_FETCH,
    TOOL_SCHEMA,
    TOOL_SCHEMA_REQUEST,
)
from local.transport.bus_config import make_participant_bus

logger = logging.getLogger(__name__)

CONFIG_NAME = "web_fetch"

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


class WebFetchTool:
    TOOL_ID = "web_fetch_tool"

    def __init__(self) -> None:
        cfg = get_config(CONFIG_NAME)
        self._max_chars: int = cfg.get("max_chars", 3000)
        self._timeout: float = cfg.get("timeout", 15)
        self._pub, self._sub = make_participant_bus([TOOL_REQUEST_WEB_FETCH, TOOL_SCHEMA_REQUEST])

    def run(self) -> None:
        self._announce_schema()
        print(f"[web_fetch_tool] max_chars={self._max_chars}  timeout={self._timeout}")
        while True:
            try:
                envelope = self._sub.receive()
            except Exception as exc:
                logger.error("WebFetchTool: receive error: %s", exc)
                continue
            if envelope.subject == TOOL_SCHEMA_REQUEST:
                ConfigManager.invalidate(CONFIG_NAME)
                self._announce_schema()
            elif envelope.subject == TOOL_REQUEST_WEB_FETCH:
                self._handle_request(envelope)

    def _build_schema(self) -> dict:
        cfg = get_config(CONFIG_NAME)
        description = cfg.get("description", "Fetch the full text content of a specific URL.").strip()
        param_url = cfg.get("param_url", "The full URL to fetch.").strip()
        return {
            "type": "function",
            "function": {
                "name": "web_fetch",
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": param_url},
                    },
                    "required": ["url"],
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
        url: str = args.get("url", "")
        correlation_id = envelope.correlation_id

        self._publish_activity("request", {"url": url}, correlation_id)

        try:
            result = self._fetch(url)
        except Exception as exc:
            logger.error("WebFetchTool: fetch failed for %r: %s", url, exc)
            result = f"[web_fetch error: {exc}]"

        self._publish_activity("result", {"result": result[:200]}, correlation_id)

        self._pub.publish(MessageEnvelope.create(
            message_type="tool_result",
            subject=TOOL_RESULT_WEB_FETCH,
            sender_id=self.TOOL_ID,
            payload={"result": result, "tool": "web_fetch"},
            correlation_id=correlation_id,
            metadata={},
        ))

    def _fetch(self, url: str) -> str:
        import httpx
        from bs4 import BeautifulSoup

        resp = httpx.get(url, timeout=self._timeout, headers={"User-Agent": _USER_AGENT},
                         follow_redirects=True)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()

        text = soup.get_text(separator="\n", strip=True)
        lines = [line for line in text.splitlines() if line.strip()]
        content = "\n".join(lines)

        if len(content) > self._max_chars:
            content = content[:self._max_chars] + f"\n[truncated at {self._max_chars} chars]"
        return content

    def _publish_activity(self, event_type: str, data: dict, correlation_id: str | None) -> None:
        self._pub.publish(MessageEnvelope.create(
            message_type="tool_activity",
            subject=TOOL_ACTIVITY_WEB_FETCH,
            sender_id=self.TOOL_ID,
            payload={"event": event_type, "tool": "web_fetch", **data},
            correlation_id=correlation_id or "",
            metadata={},
        ))
