"""SearchMemoryTool — retrieves relevant past interactions from episodic memory by meaning."""

from __future__ import annotations

import logging

from local.config_loader import get_config
from local.protocol.envelope import MessageEnvelope
from local.protocol.subjects import (
    TOOL_ACTIVITY_SEARCH_MEMORY,
    TOOL_CALL_SEARCH_MEMORY,
    TOOL_RESULT_SEARCH_MEMORY,
)
from local.services.memory_service import MemoryService
from local.tools.base_tool import BaseTool

logger = logging.getLogger(__name__)

CONFIG_NAME = "search_memory"


class SearchMemoryTool(BaseTool):
    TOOL_NAME = "search_memory"
    ACTIVITY_SUBJECT = TOOL_ACTIVITY_SEARCH_MEMORY
    RESULT_SUBJECT = TOOL_RESULT_SEARCH_MEMORY
    CONFIG_NAME = CONFIG_NAME

    def __init__(self, memory_service: MemoryService | None = None) -> None:
        self._memory = memory_service or MemoryService()
        super().__init__(TOOL_CALL_SEARCH_MEMORY)

    def _build_schema(self) -> dict:
        cfg = get_config(CONFIG_NAME)
        description = cfg.get("description", "Search episodic memory from past sessions by meaning.").strip()
        param_query = cfg.get("param_query", "Natural language description of what you are looking for.").strip()
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
        query: str = args.get("query", "").strip()
        correlation_id = envelope.correlation_id

        self._publish_activity("request", {"query": query}, correlation_id)

        try:
            result = self._search(query)
        except Exception as exc:
            logger.error("SearchMemoryTool: search failed: %s", exc)
            result = f"[search_memory error: {exc}]"

        self._publish_activity("result", {"result": result}, correlation_id)

        self._publish_result(result, correlation_id)

    def _search(self, query: str) -> str:
        if not query:
            return "[search_memory: query is required]"
        candidates = self._memory.search_episodic(query)
        if not candidates:
            return "[no relevant memories found]"
        lines = []
        for i, c in enumerate(candidates, 1):
            critic_score = c["metadata"].get("critic_score")
            suffix = f" [quality: {critic_score}/5]" if critic_score is not None else ""
            lines.append(f"{i}.{suffix} {c['content']}")
        return "\n\n".join(lines)
