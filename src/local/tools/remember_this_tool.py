"""RememberThisTool — stores a user-specified fact as a pinned memory.

Gemma calls this explicitly when the user says "remember this" or asks to
store something for future sessions. Pinned facts are always injected into
the system context on every generation turn — unlike episodic engrams which
are retrieved by similarity only when relevant.

On success, publishes user.context.updated so GeneratorAgent updates its
in-memory cache immediately (effective from the *next* turn in this session).
"""
from __future__ import annotations

import logging

from local.protocol.envelope import MessageEnvelope
from local.protocol.messages import UserContextUpdated
from local.protocol.subjects import (
    TOOL_ACTIVITY_REMEMBER_THIS,
    TOOL_CALL_REMEMBER_THIS,
    TOOL_RESULT_REMEMBER_THIS,
)
from local.services.memory_service import MemoryService
from local.tools.base_tool import BaseTool

logger = logging.getLogger(__name__)


class RememberThisTool(BaseTool):
    CONFIG_NAME      = "remember_this"
    TOOL_NAME        = "remember_this"
    ACTIVITY_SUBJECT = TOOL_ACTIVITY_REMEMBER_THIS
    RESULT_SUBJECT   = TOOL_RESULT_REMEMBER_THIS

    def __init__(self, memory_service: MemoryService | None = None) -> None:
        super().__init__(TOOL_CALL_REMEMBER_THIS)
        self._memory = memory_service or MemoryService()

    def _handle_request(self, envelope: MessageEnvelope) -> None:
        args = envelope.payload.get("args") or {}
        fact = (args.get("fact") or "").strip()
        reason = (args.get("reason") or "").strip()
        cid = envelope.correlation_id

        self._publish_activity("request", {"fact": fact, "reason": reason}, cid)

        if not fact:
            result = "[remember_this: no fact provided]"
            self._publish_activity("result", {"result": result}, cid)
            self._publish_result(result, cid)
            return

        self._memory.write_pinned(fact, reason)
        logger.info("RememberThisTool: stored pinned fact %r", fact[:80])

        self._pub.publish(
            UserContextUpdated(fact=fact, reason=reason),
            sender_id=self.id,
            correlation_id=cid,
        )

        result = f"Remembered: {fact}"
        self._publish_activity("result", {"result": result}, cid)
        self._publish_result(result, cid)


if __name__ == "__main__":
    RememberThisTool().run()
