"""UserInstructionMemoryTool (UIM) — saves an explicitly user-directed note to episodic memory.

Call this when the user gives a specific instruction to note or remember something
during the conversation that goes beyond a standing preference or key/value fact —
for example, "note that we discussed X", "keep in mind that Y", "remember this moment".
This is distinct from the automatic episodic ingestion MemoryAgent does after every turn.
"""

from __future__ import annotations

import logging

from local.protocol.envelope import MessageEnvelope
from local.protocol.subjects import (
    TOOL_REQUEST_USER_INSTRUCTION_MEMORY,
    TOOL_RESULT_USER_INSTRUCTION_MEMORY,
    TOOL_SCHEMA,
)
from local.services.memory_service import MemoryService
from local.transport.bus_config import make_participant_bus

logger = logging.getLogger(__name__)

SCHEMA = {
    "type": "function",
    "function": {
        "name": "user_instruction_memory",
        "description": (
            "Save a specific note to episodic memory on explicit user instruction. "
            "Call this when the user says 'note that', 'keep in mind', 'remember this', "
            "'don't forget', or asks you to capture something specific about the current "
            "conversation that they want retrievable in future sessions. "
            "Use save_topic instead for standing preferences or rules with a clear key. "
            "Use this for instructed one-off notes, context, or observations."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "note": {
                    "type": "string",
                    "description": "The note or observation to save to episodic memory.",
                },
            },
            "required": ["note"],
        },
    },
}


class UserInstructionMemoryTool:
    TOOL_ID = "user_instruction_memory_tool"

    def __init__(self, memory_service: MemoryService | None = None) -> None:
        self._memory = memory_service or MemoryService()
        self._pub, self._sub = make_participant_bus([TOOL_REQUEST_USER_INSTRUCTION_MEMORY])

    def run(self) -> None:
        self._announce_schema()
        print("[user_instruction_memory_tool] ready")
        while True:
            try:
                envelope = self._sub.receive()
            except Exception as exc:
                logger.error("UserInstructionMemoryTool: receive error: %s", exc)
                continue
            if envelope.subject == TOOL_REQUEST_USER_INSTRUCTION_MEMORY:
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
        note: str = args.get("note", "").strip()
        correlation_id = envelope.correlation_id

        try:
            result = self._save(note)
        except Exception as exc:
            logger.error("UserInstructionMemoryTool: save failed: %s", exc)
            result = f"[user_instruction_memory error: {exc}]"

        self._pub.publish(MessageEnvelope.create(
            message_type="tool_result",
            subject=TOOL_RESULT_USER_INSTRUCTION_MEMORY,
            sender_id=self.TOOL_ID,
            payload={"result": result, "tool": "user_instruction_memory"},
            correlation_id=correlation_id,
            metadata={},
        ))

    def _save(self, note: str) -> str:
        if not note:
            return "[user_instruction_memory: note is required]"
        self._memory.write_episodic(query="[user instruction]", answer=note)
        return f"[noted: {note!r}]"
