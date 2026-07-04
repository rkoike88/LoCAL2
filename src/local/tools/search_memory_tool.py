"""SearchMemoryTool — retrieves relevant past interactions from episodic memory by meaning."""

from __future__ import annotations

import logging

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

    def _handle_request(self, envelope: MessageEnvelope) -> None:
        args: dict = envelope.payload.get("args", {})
        query: str = args.get("query", "").strip()
        correlation_id = envelope.correlation_id

        self._publish_activity("request", {"query": query}, correlation_id)

        try:
            result, sources = self._search_with_sources(query)
        except Exception as exc:
            logger.error("SearchMemoryTool: search failed: %s", exc)
            result, sources = f"[search_memory error: {exc}]", []

        self._publish_activity("result", {"result": result, "sources": sources}, correlation_id)
        self._publish_result(result, correlation_id, sources=sources)

    def _search(self, query: str) -> str:
        result, _ = self._search_with_sources(query)
        return result

    def _search_with_sources(self, query: str) -> tuple[str, list[dict]]:
        if not query:
            return "[search_memory: query is required]", []
        candidates = self._memory.search_episodic(query)
        if not candidates:
            return "[no relevant memories found]", []
        sources = [
            {
                "type": "memory",
                "id": c.get("id", ""),
                "score": round(float(c.get("score") or 0), 3),
                "snippet": (c["content"] or "")[:80],
                "query": (c["metadata"].get("query") or ""),
            }
            for c in candidates
        ]
        lines = []
        for i, c in enumerate(candidates, 1):
            critic_score = c["metadata"].get("critic_score")
            critic_feedback = c["metadata"].get("critic_feedback", "")
            if critic_score is not None:
                score_int = int(critic_score)
                if score_int >= 4:
                    label = f"[GOOD EXAMPLE — rated {score_int}/5]"
                elif score_int <= 2:
                    label = f"[AVOID — rated {score_int}/5 — do not repeat this approach]"
                else:
                    label = f"[MIXED — rated {score_int}/5]"
                suffix = f" {label}"
            else:
                suffix = ""
            entry = f"{i}.{suffix} {c['content']}"
            if critic_feedback:
                entry += f"\n   [critique: {critic_feedback}]"
            lines.append(entry)
        return "\n\n".join(lines), sources
