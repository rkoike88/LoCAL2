"""SearchMemoryTool — retrieves relevant past interactions from episodic memory by meaning.

Call this when the user asks about something from a prior session and you need
to search by semantic similarity rather than an exact key.
"""

from __future__ import annotations

import logging

from local.protocol.envelope import MessageEnvelope
from local.protocol.subjects import (
    TOOL_REQUEST_SEARCH_MEMORY,
    TOOL_RESULT_SEARCH_MEMORY,
    TOOL_SCHEMA,
)
from local.services.memory_service import MemoryService
from local.transport.bus_config import make_participant_bus

logger = logging.getLogger(__name__)

SCHEMA = {
    "type": "function",
    "function": {
        "name": "search_memory",
        "description": (
            "Search episodic memory from past sessions by meaning. "
            "Call this when the user asks about something you may have discussed before "
            "and you don't have an exact topic key — e.g. 'what did we talk about regarding X', "
            "'did I mention anything about Y', 'what was my take on Z last time'. "
            "Use recall_topic instead when you know the exact topic key."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural language description of what you are looking for.",
                },
            },
            "required": ["query"],
        },
    },
}


class SearchMemoryTool:
    TOOL_ID = "search_memory_tool"

    def __init__(self, memory_service: MemoryService | None = None) -> None:
        self._memory = memory_service or MemoryService()
        self._pub, self._sub = make_participant_bus([TOOL_REQUEST_SEARCH_MEMORY])

    def run(self) -> None:
        self._announce_schema()
        print("[search_memory_tool] ready")
        while True:
            try:
                envelope = self._sub.receive()
            except Exception as exc:
                logger.error("SearchMemoryTool: receive error: %s", exc)
                continue
            if envelope.subject == TOOL_REQUEST_SEARCH_MEMORY:
                self._handle_request(envelope)

    def _announce_schema(self) -> None:
        self._pub.publish(MessageEnvelope.create(
            message_type="tool_schema",
            subject=TOOL_SCHEMA,
            sender_id=self.TOOL_ID,
            payload={"schema": SCHEMA},
        ))

    def _handle_request(self, envelope: MessageEnvelope) -> None:
        args: dict = envelope.payload.get("args", {})
        query: str = args.get("query", "").strip()
        correlation_id = envelope.correlation_id

        try:
            result = self._search(query)
        except Exception as exc:
            logger.error("SearchMemoryTool: search failed: %s", exc)
            result = f"[search_memory error: {exc}]"

        self._pub.publish(MessageEnvelope.create(
            message_type="tool_result",
            subject=TOOL_RESULT_SEARCH_MEMORY,
            sender_id=self.TOOL_ID,
            payload={"result": result, "tool": "search_memory"},
            correlation_id=correlation_id,
            metadata={},
        ))

    def _search(self, query: str) -> str:
        if not query:
            return "[search_memory: query is required]"
        candidates = self._memory.search_episodic(query)
        if not candidates:
            return "[no relevant memories found]"
        lines = []
        for i, c in enumerate(candidates, 1):
            lines.append(f"{i}. {c['content']}")
        return "\n\n".join(lines)
