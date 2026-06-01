"""MemoryRecallTool — executes recall_memory tool calls from GeneratorAgent.

Dual mode dispatch:
  - topic=<key>  → exact lookup from topic store (user.*, project.*, constraint.*)
  - query=<text> → embedding similarity search over episodic store

Announces JSON schema to tool.schema on startup so GeneratorAgent registers it
dynamically. No static schema in generator.yaml.
"""

from __future__ import annotations

import logging

from local.protocol.envelope import MessageEnvelope
from local.protocol.subjects import (
    TOOL_REQUEST_RECALL_MEMORY,
    TOOL_RESULT_RECALL_MEMORY,
    TOOL_SCHEMA,
)
from local.services.memory_service import MemoryService
from local.transport.bus_config import make_participant_bus

logger = logging.getLogger(__name__)

SCHEMA = {
    "type": "function",
    "function": {
        "name": "recall_memory",
        "description": (
            "Retrieve information from persistent memory saved in previous sessions. "
            "Call this tool when the user asks about something they may have told you before, "
            "or when you need a preference, fact, or constraint that might have been saved. "
            "Use topic= to look up a specific key (e.g. topic='user.language_preference'). "
            "Use query= to search by meaning when you don't know the exact key "
            "(e.g. query='what UI preference did the user mention'). "
            "Provide topic OR query, not both."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": (
                        "Exact topic key to retrieve a standing fact. "
                        "Use dot-notation prefix: user.*, project.*, constraint.*"
                    ),
                },
                "query": {
                    "type": "string",
                    "description": (
                        "Natural language query to search episodic memory by similarity."
                    ),
                },
            },
        },
    },
}


class MemoryRecallTool:
    TOOL_ID = "memory_recall_tool"

    def __init__(self, memory_service: MemoryService | None = None) -> None:
        self._memory = memory_service or MemoryService()
        self._pub, self._sub = make_participant_bus([TOOL_REQUEST_RECALL_MEMORY])

    def run(self) -> None:
        self._announce_schema()
        print(f"[memory_recall_tool] ready")
        while True:
            try:
                envelope = self._sub.receive()
            except Exception as exc:
                logger.error("MemoryRecallTool: receive error: %s", exc)
                continue
            if envelope.subject == TOOL_REQUEST_RECALL_MEMORY:
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
        topic: str = args.get("topic", "").strip()
        query: str = args.get("query", "").strip()
        correlation_id = envelope.correlation_id

        try:
            result = self._recall(topic, query)
        except Exception as exc:
            logger.error("MemoryRecallTool: recall failed: %s", exc)
            result = f"[recall_memory error: {exc}]"

        self._pub.publish(MessageEnvelope.create(
            message_type="tool_result",
            subject=TOOL_RESULT_RECALL_MEMORY,
            sender_id=self.TOOL_ID,
            payload={"result": result, "tool": "recall_memory"},
            correlation_id=correlation_id,
            metadata={},
        ))

    def _recall(self, topic: str, query: str) -> str:
        if topic:
            value = self._memory.recall_topic(topic)
            if value is None:
                return f"[no memory found for topic: {topic!r}]"
            return value

        if query:
            candidates = self._memory.search_episodic(query)
            if not candidates:
                return "[no relevant memories found]"
            lines = []
            for i, c in enumerate(candidates, 1):
                lines.append(f"{i}. {c['content']}")
            return "\n\n".join(lines)

        return "[recall_memory: provide either topic= or query=]"
