"""MemorySaveTool — executes save_memory tool calls from GeneratorAgent.

Writes a standing fact to the topic store, keyed by an explicit topic string.
Gemma provides the key; the dot-notation prefix guides what namespace it falls
under (user.*, project.*, constraint.*). Any key is accepted — the schema
description is the guide, not a hard gate.

Announces JSON schema to tool.schema on startup.
"""

from __future__ import annotations

import logging

from local.protocol.envelope import MessageEnvelope
from local.protocol.subjects import (
    TOOL_REQUEST_SAVE_MEMORY,
    TOOL_RESULT_SAVE_MEMORY,
    TOOL_SCHEMA,
)
from local.services.memory_service import MemoryService
from local.transport.bus_config import make_participant_bus

logger = logging.getLogger(__name__)

SCHEMA = {
    "type": "function",
    "function": {
        "name": "save_memory",
        "description": (
            "Save information to persistent memory so it survives future sessions. "
            "Call this tool whenever the user says 'remember', 'save', 'keep in mind', "
            "'don't forget', or asks you to store any fact, preference, or rule for later. "
            "Use a dot-notation topic key: user.* for personal preferences and user facts, "
            "project.* for project decisions and context, "
            "constraint.* for rules and limits to enforce. "
            "Existing values for the same topic key are overwritten."
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


class MemorySaveTool:
    TOOL_ID = "memory_save_tool"

    def __init__(self, memory_service: MemoryService | None = None) -> None:
        self._memory = memory_service or MemoryService()
        self._pub, self._sub = make_participant_bus([TOOL_REQUEST_SAVE_MEMORY])

    def run(self) -> None:
        self._announce_schema()
        print(f"[memory_save_tool] ready")
        while True:
            try:
                envelope = self._sub.receive()
            except Exception as exc:
                logger.error("MemorySaveTool: receive error: %s", exc)
                continue
            if envelope.subject == TOOL_REQUEST_SAVE_MEMORY:
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
            logger.error("MemorySaveTool: save failed: %s", exc)
            result = f"[save_memory error: {exc}]"

        self._pub.publish(MessageEnvelope.create(
            message_type="tool_result",
            subject=TOOL_RESULT_SAVE_MEMORY,
            sender_id=self.TOOL_ID,
            payload={"result": result, "tool": "save_memory"},
            correlation_id=correlation_id,
            metadata={},
        ))

    def _save(self, topic: str, value: str) -> str:
        if not topic:
            return "[save_memory: topic is required]"
        if not value:
            return "[save_memory: value is required]"
        self._memory.write_topic(topic, value)
        return f"[saved: {topic!r} = {value!r}]"
