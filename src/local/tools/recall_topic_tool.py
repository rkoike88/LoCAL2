"""RecallTopicTool — retrieves a standing fact from the topic store by exact key.

Call this when you need to look up a specific preference, rule, or fact that
was previously saved with save_topic.
"""

from __future__ import annotations

import logging

from local.protocol.envelope import MessageEnvelope
from local.protocol.subjects import (
    TOOL_REQUEST_RECALL_TOPIC,
    TOOL_RESULT_RECALL_TOPIC,
    TOOL_SCHEMA,
)
from local.services.memory_service import MemoryService
from local.transport.bus_config import make_participant_bus

logger = logging.getLogger(__name__)

SCHEMA = {
    "type": "function",
    "function": {
        "name": "recall_topic",
        "description": (
            "Look up a specific standing fact, preference, or rule by its exact topic key. "
            "Call this when you need to retrieve something previously saved with save_topic "
            "and you know the key (e.g. 'user.language_preference', 'constraint.no_external_apis'). "
            "Use search_memory instead when you don't know the exact key."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": (
                        "Exact topic key in dot-notation, e.g. 'user.language_preference', "
                        "'project.tech_stack', 'constraint.no_external_apis'."
                    ),
                },
            },
            "required": ["topic"],
        },
    },
}


class RecallTopicTool:
    TOOL_ID = "recall_topic_tool"

    def __init__(self, memory_service: MemoryService | None = None) -> None:
        self._memory = memory_service or MemoryService()
        self._pub, self._sub = make_participant_bus([TOOL_REQUEST_RECALL_TOPIC])

    def run(self) -> None:
        self._announce_schema()
        print("[recall_topic_tool] ready")
        while True:
            try:
                envelope = self._sub.receive()
            except Exception as exc:
                logger.error("RecallTopicTool: receive error: %s", exc)
                continue
            if envelope.subject == TOOL_REQUEST_RECALL_TOPIC:
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
        correlation_id = envelope.correlation_id

        try:
            result = self._recall(topic)
        except Exception as exc:
            logger.error("RecallTopicTool: recall failed: %s", exc)
            result = f"[recall_topic error: {exc}]"

        self._pub.publish(MessageEnvelope.create(
            message_type="tool_result",
            subject=TOOL_RESULT_RECALL_TOPIC,
            sender_id=self.TOOL_ID,
            payload={"result": result, "tool": "recall_topic"},
            correlation_id=correlation_id,
            metadata={},
        ))

    def _recall(self, topic: str) -> str:
        if not topic:
            return "[recall_topic: topic is required]"
        value = self._memory.recall_topic(topic)
        if value is None:
            return f"[no memory found for topic: {topic!r}]"
        return value
