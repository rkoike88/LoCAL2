"""BaseTool — abstract base class for all LoCAL2 tools.

Subclasses must set TOOL_ID, TOOL_NAME, ACTIVITY_SUBJECT class vars and
implement _build_schema() and _handle_request(). Everything else is inherited.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import ClassVar

from local.config_loader import ConfigManager
from local.protocol.envelope import MessageEnvelope
from local.protocol.subjects import TOOL_SCHEMA, TOOL_SCHEMA_REQUEST
from local.transport.bus_config import make_participant_bus

logger = logging.getLogger(__name__)


class BaseTool(ABC):
    """Abstract base for all LoCAL2 tools.

    Handles the run loop, schema broadcasting, and activity event publishing.
    Subclasses must declare four class variables and implement two methods.

    Class Variables:
        TOOL_ID: Unique participant identifier used as sender_id on bus events
            (e.g. ``"datetime_tool"``).
        TOOL_NAME: Function name in the OpenAI schema and in activity payloads
            (e.g. ``"get_datetime"``).
        ACTIVITY_SUBJECT: Bus subject for ``tool.activity.*`` events.
        CONFIG_NAME: Optional config key. When set, ``ConfigManager.invalidate``
            is called on each ``TOOL_SCHEMA_REQUEST`` so ``_build_schema`` always
            sees the latest on-disk config without restarting the tool.
    """

    TOOL_ID: ClassVar[str]
    TOOL_NAME: ClassVar[str]
    ACTIVITY_SUBJECT: ClassVar[str]
    CONFIG_NAME: ClassVar[str | None] = None

    def __init__(self, request_subject: str) -> None:
        """Set up pub/sub bus subscriptions.

        Args:
            request_subject: The ``tool.request.*`` subject this tool handles
                (e.g. ``TOOL_REQUEST_GET_DATETIME``).
        """
        self._pub, self._sub = make_participant_bus([request_subject, TOOL_SCHEMA_REQUEST])

    @abstractmethod
    def _build_schema(self) -> dict:
        """Return the OpenAI-compatible function schema for this tool.

        Returns:
            A dict in the form::

                {
                    "type": "function",
                    "function": {
                        "name": <TOOL_NAME>,
                        "description": "...",
                        "parameters": {...}
                    }
                }

            Published on ``tool.schema``; received by GeneratorAgent to
            populate its live tool registry.
        """

    @abstractmethod
    def _handle_request(self, envelope: MessageEnvelope) -> None:
        """Handle an incoming ``tool.request.*`` envelope.

        Implementations must:

        1. Extract args from ``envelope.payload["args"]``.
        2. Call ``_publish_activity("request", ...)`` before executing.
        3. Execute the tool logic.
        4. Call ``_publish_activity("result", {"result": <str>}, ...)`` after.
        5. Publish the ``tool.result.*`` envelope via ``self._pub``.

        Args:
            envelope: The incoming request envelope. ``envelope.correlation_id``
                must be forwarded to all published envelopes so GeneratorAgent
                can match the result to the pending tool call.
        """

    def _announce_schema(self) -> None:
        self._pub.publish(MessageEnvelope.create(
            message_type="tool_schema",
            subject=TOOL_SCHEMA,
            sender_id=self.TOOL_ID,
            payload={"schema": self._build_schema()},
        ))

    def _publish_activity(self, event_type: str, data: dict, correlation_id: str | None) -> None:
        """Publish a ``tool.activity.*`` event to the bus.

        Args:
            event_type: ``"request"`` (before execution) or ``"result"`` (after).
                ToolWindow renders ``"request"`` entries in green and
                ``"result"`` entries in blue; any other value renders as a
                gray subject-name fallback.
            data: Extra payload fields merged into the event. For ``"result"``
                events, include ``{"result": <str>}`` so ToolWindow can show
                a preview snippet.
            correlation_id: Forwarded from the originating request envelope.
        """
        self._pub.publish(MessageEnvelope.create(
            message_type="tool_activity",
            subject=self.ACTIVITY_SUBJECT,
            sender_id=self.TOOL_ID,
            payload={"event": event_type, "tool": self.TOOL_NAME, **data},
            correlation_id=correlation_id or "",
        ))

    def run(self) -> None:
        self._announce_schema()
        logger.info("%s ready", self.TOOL_ID)
        while True:
            try:
                envelope = self._sub.receive()
            except Exception as exc:
                logger.error("%s: receive error: %s", self.__class__.__name__, exc)
                continue
            if envelope.subject == TOOL_SCHEMA_REQUEST:
                if self.CONFIG_NAME:
                    ConfigManager.invalidate(self.CONFIG_NAME)
                self._announce_schema()
            else:
                self._handle_request(envelope)
