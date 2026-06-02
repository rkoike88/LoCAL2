"""GetTopicTool — retrieves a standing fact from the topic store by exact key."""

from __future__ import annotations

import logging

from local.config_loader import ConfigManager, get_config
from local.protocol.envelope import MessageEnvelope
from local.protocol.subjects import (
    TOOL_ACTIVITY_GET_TOPIC,
    TOOL_REQUEST_GET_TOPIC,
    TOOL_RESULT_GET_TOPIC,
    TOOL_SCHEMA,
    TOOL_SCHEMA_REQUEST,
)
from local.services.memory_service import MemoryService
from local.transport.bus_config import make_participant_bus

logger = logging.getLogger(__name__)

CONFIG_NAME = "get_topic"


class GetTopicTool:
    TOOL_ID = "get_topic_tool"

    def __init__(self, memory_service: MemoryService | None = None) -> None:
        self._memory = memory_service or MemoryService()
        self._pub, self._sub = make_participant_bus([TOOL_REQUEST_GET_TOPIC, TOOL_SCHEMA_REQUEST])

    def run(self) -> None:
        self._announce_schema()
        print("[recall_topic_tool] ready")
        while True:
            try:
                envelope = self._sub.receive()
            except Exception as exc:
                logger.error("GetTopicTool: receive error: %s", exc)
                continue
            if envelope.subject == TOOL_SCHEMA_REQUEST:
                ConfigManager.invalidate(CONFIG_NAME)
                self._announce_schema()
            elif envelope.subject == TOOL_REQUEST_GET_TOPIC:
                self._handle_request(envelope)

    def _build_schema(self) -> dict:
        cfg = get_config(CONFIG_NAME)
        description = cfg.get("description", "Look up a fact by its exact topic key.").strip()
        param_key = cfg.get("param_key", "Exact dot-notation key, e.g. 'user.coffee_preference'.").strip()
        return {
            "type": "function",
            "function": {
                "name": "get_topic",
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string", "description": param_key},
                    },
                    "required": ["key"],
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
        topic: str = (args.get("key") or args.get("topic") or "").strip()
        correlation_id = envelope.correlation_id

        self._publish_activity("request", {"topic": topic}, correlation_id)

        try:
            result = self._recall(topic)
        except Exception as exc:
            logger.error("GetTopicTool: recall failed: %s", exc)
            result = f"[recall_topic error: {exc}]"

        self._publish_activity("result", {"result": result}, correlation_id)

        self._pub.publish(MessageEnvelope.create(
            message_type="tool_result",
            subject=TOOL_RESULT_GET_TOPIC,
            sender_id=self.TOOL_ID,
            payload={"result": result, "tool": "get_topic"},
            correlation_id=correlation_id,
            metadata={},
        ))

    def _recall(self, topic: str) -> str:
        if not topic:
            return "[get_topic: key is required]"
        value = self._memory.recall_topic(topic)
        if value is None:
            return f"[no memory found for topic: {topic!r}]"
        return value

    def _publish_activity(self, event_type: str, data: dict, correlation_id: str | None) -> None:
        self._pub.publish(MessageEnvelope.create(
            message_type="tool_activity",
            subject=TOOL_ACTIVITY_GET_TOPIC,
            sender_id=self.TOOL_ID,
            payload={"event": event_type, "tool": "get_topic", **data},
            correlation_id=correlation_id or "",
            metadata={},
        ))
