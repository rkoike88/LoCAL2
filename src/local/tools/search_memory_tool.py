"""SearchMemoryTool — retrieves relevant past interactions from episodic memory by meaning."""

from __future__ import annotations

import logging

from local.config_loader import ConfigManager, get_config
from local.protocol.envelope import MessageEnvelope
from local.protocol.subjects import (
    TOOL_ACTIVITY_SEARCH_MEMORY,
    TOOL_REQUEST_SEARCH_MEMORY,
    TOOL_RESULT_SEARCH_MEMORY,
    TOOL_SCHEMA,
    TOOL_SCHEMA_REQUEST,
)
from local.services.memory_service import MemoryService
from local.transport.bus_config import make_participant_bus

logger = logging.getLogger(__name__)

CONFIG_NAME = "search_memory"


class SearchMemoryTool:
    TOOL_ID = "search_memory_tool"

    def __init__(self, memory_service: MemoryService | None = None) -> None:
        self._memory = memory_service or MemoryService()
        self._pub, self._sub = make_participant_bus([TOOL_REQUEST_SEARCH_MEMORY, TOOL_SCHEMA_REQUEST])

    def run(self) -> None:
        self._announce_schema()
        print("[search_memory_tool] ready")
        while True:
            try:
                envelope = self._sub.receive()
            except Exception as exc:
                logger.error("SearchMemoryTool: receive error: %s", exc)
                continue
            if envelope.subject == TOOL_SCHEMA_REQUEST:
                ConfigManager.invalidate(CONFIG_NAME)
                self._announce_schema()
            elif envelope.subject == TOOL_REQUEST_SEARCH_MEMORY:
                self._handle_request(envelope)

    def _build_schema(self) -> dict:
        cfg = get_config(CONFIG_NAME)
        description = cfg.get("description", "Search episodic memory from past sessions by meaning.").strip()
        param_query = cfg.get("param_query", "Natural language description of what you are looking for.").strip()
        return {
            "type": "function",
            "function": {
                "name": "search_memory",
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
        query: str = args.get("query", "").strip()
        correlation_id = envelope.correlation_id

        self._publish_activity("request", {"query": query}, correlation_id)

        try:
            result = self._search(query)
        except Exception as exc:
            logger.error("SearchMemoryTool: search failed: %s", exc)
            result = f"[search_memory error: {exc}]"

        self._publish_activity("result", {"result": result}, correlation_id)

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

    def _publish_activity(self, event_type: str, data: dict, correlation_id: str | None) -> None:
        self._pub.publish(MessageEnvelope.create(
            message_type="tool_activity",
            subject=TOOL_ACTIVITY_SEARCH_MEMORY,
            sender_id=self.TOOL_ID,
            payload={"event": event_type, "tool": "search_memory", **data},
            correlation_id=correlation_id or "",
            metadata={},
        ))
