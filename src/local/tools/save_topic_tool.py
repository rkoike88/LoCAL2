"""SaveTopicTool — persists a standing fact, preference, or rule to the topic store.

Call this tool when the user asks to remember a preference, rule, or fact that
should persist across sessions and be retrievable by exact key lookup.
"""

from __future__ import annotations

import logging

from local.protocol.envelope import MessageEnvelope
from local.protocol.subjects import (
    TOOL_REQUEST_SAVE_TOPIC,
    TOOL_RESULT_SAVE_TOPIC,
    TOOL_SCHEMA,
)
from local.services.memory_service import MemoryService
from local.transport.bus_config import make_participant_bus

logger = logging.getLogger(__name__)

SCHEMA = {
    "type": "function",
    "function": {
        "name": "save_topic",
        "description": (
            "Permanently store a standing fact, preference, or rule so it survives future sessions. "
            "Call this when the user says 'remember that', 'always do X', 'my preference is', "
            "or explicitly asks to save a fact or rule for ongoing use. "
            "Use dot-notation topic keys: user.* for personal preferences, "
            "project.* for project decisions, constraint.* for rules to enforce. "
            "Existing values for the same key are overwritten."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": (
                        "Topic key in dot-notation, e.g. 'user.language_preference', "
                        "'project.tech_stack', 'constraint.no_external_apis'."
                    ),
                },
                "value": {
                    "type": "string",
                    "description": "The fact or value to store under this topic key.",
                },
            },
            "required": ["topic", "value"],
        },
    },
}


class SaveTopicTool:
    TOOL_ID = "save_topic_tool"

    def __init__(self, memory_service: MemoryService | None = None) -> None:
        self._memory = memory_service or MemoryService()
        self._pub, self._sub = make_participant_bus([TOOL_REQUEST_SAVE_TOPIC])

    def run(self) -> None:
        self._announce_schema()
        print("[save_topic_tool] ready")
        while True:
            try:
                envelope = self._sub.receive()
            except Exception as exc:
                logger.error("SaveTopicTool: receive error: %s", exc)
                continue
            if envelope.subject == TOOL_REQUEST_SAVE_TOPIC:
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
        value: str = args.get("value", "").strip()
        correlation_id = envelope.correlation_id

        try:
            result = self._save(topic, value)
        except Exception as exc:
            logger.error("SaveTopicTool: save failed: %s", exc)
            result = f"[save_topic error: {exc}]"

        self._pub.publish(MessageEnvelope.create(
            message_type="tool_result",
            subject=TOOL_RESULT_SAVE_TOPIC,
            sender_id=self.TOOL_ID,
            payload={"result": result, "tool": "save_topic"},
            correlation_id=correlation_id,
            metadata={},
        ))

    def _save(self, topic: str, value: str) -> str:
        if not topic:
            return "[save_topic: topic is required]"
        if not value:
            return "[save_topic: value is required]"
        self._memory.write_topic(topic, value)
        return f"[saved: {topic!r} = {value!r}]"
