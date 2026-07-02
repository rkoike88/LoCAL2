"""WebFetchTool — executes web_fetch tool calls from GeneratorAgent."""
from __future__ import annotations

import logging

from local.config_loader import get_config
from local.protocol.envelope import MessageEnvelope
from local.protocol.subjects import (
    TOOL_ACTIVITY_WEB_FETCH,
    TOOL_CALL_WEB_FETCH,
    TOOL_RESULT_WEB_FETCH,
)
from local.tools.base_tool import BaseTool

logger = logging.getLogger(__name__)

CONFIG_NAME = "web_fetch"

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


class WebFetchTool(BaseTool):
    TOOL_NAME = "web_fetch"
    ACTIVITY_SUBJECT = TOOL_ACTIVITY_WEB_FETCH
    RESULT_SUBJECT = TOOL_RESULT_WEB_FETCH
    CONFIG_NAME = CONFIG_NAME

    def __init__(self) -> None:
        cfg = get_config(CONFIG_NAME)
        self._max_chars: int = cfg["max_chars"]
        self._timeout: float = cfg["timeout"]
        super().__init__(TOOL_CALL_WEB_FETCH)
        logger.info("web_fetch_tool: max_chars=%s  timeout=%s", self._max_chars, self._timeout)

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

        self._publish_result(result, correlation_id)

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
