"""BaseTool — abstract base class for all LoCAL2 tools.

Subclasses must set TOOL_NAME, ACTIVITY_SUBJECT, RESULT_SUBJECT class vars and
implement _handle_request(). Override _build_schema() when the schema must be
built dynamically; otherwise add a 'schema:' key to config/<CONFIG_NAME>.yaml.
"""
from __future__ import annotations

import logging
import time
from abc import abstractmethod
from contextlib import contextmanager
from enum import Enum
from typing import ClassVar, Generator

from local.config_loader import ConfigManager, get_config
from local.participants.participant import Participant
from local.protocol.envelope import MessageEnvelope
from local.protocol.messages import ToolActivity, ToolResult, ToolSchema, ToolTransition
from local.protocol.subjects import TOOL_SCHEMA_REQUEST
from local.transport.bus_config import PROXY_FRONTEND_ADDR, make_participant_bus
from local.transport.zmq_pubsub import ZmqPublisher

logger = logging.getLogger(__name__)


class ToolState(Enum):
    IDLE      = "IDLE"
    EXECUTING = "EXECUTING"
    ERROR     = "ERROR"


class BaseTool(Participant):
    """Abstract base for all LoCAL2 tools.

    Handles the run loop, schema broadcasting, activity event publishing,
    state machine transitions, and thread-safe publishing.

    Class Variables:
        CONFIG_NAME: Config key for this tool's yaml (required by Participant).
        TOOL_NAME: Function name in the OpenAI schema and in activity payloads.
        ACTIVITY_SUBJECT: Bus subject for ``tool.activity.*`` events.
        RESULT_SUBJECT: Bus subject for ``tool.result.*`` events.
    """

    TOOL_NAME:        ClassVar[str]
    ACTIVITY_SUBJECT: ClassVar[str]
    RESULT_SUBJECT:   ClassVar[str]

    def __init__(self, request_subject: str, extra_subjects: list[str] | None = None) -> None:
        subjects = [request_subject, TOOL_SCHEMA_REQUEST] + (extra_subjects or [])
        self._pub, self._sub = make_participant_bus(subjects)
        self._request_subject = request_subject
        self._state = ToolState.IDLE

    def _build_schema(self) -> dict:
        """Return the OpenAI-compatible function schema for this tool.

        Default: reads from the ``schema:`` key in ``config/<CONFIG_NAME>.yaml``.
        Override when the schema must be built dynamically.
        """
        cfg = get_config(self.CONFIG_NAME) or {}
        schema = cfg.get("schema")
        if schema is None:
            raise NotImplementedError(
                f"{self.__class__.__name__} must implement _build_schema() or "
                f"add a 'schema:' key to config/{self.CONFIG_NAME}.yaml"
            )
        return schema

    @abstractmethod
    def _handle_request(self, envelope: MessageEnvelope) -> None:
        """Handle an incoming tool.call.* envelope.

        Implementations must call _publish_activity("request", ...) before
        executing, then _publish_activity("result", ...) and _publish_result()
        after. Forward envelope.correlation_id to all published envelopes.
        """

    def _publish_result(
        self,
        result: str,
        correlation_id: str | None,
        sources: list | None = None,
    ) -> None:
        self._pub.publish(
            ToolResult(
                tool=self.TOOL_NAME,
                result=result,
                correlation_id=correlation_id or "",
                sources=sources or [],
            ),
            sender_id=self.id,
        )

    def _announce_schema(self) -> None:
        cfg = get_config(self.CONFIG_NAME) or {} if self.CONFIG_NAME else {}
        self._pub.publish(
            ToolSchema(
                schema=self._build_schema(),
                critique_rubric_name=cfg.get("critique_rubric_name") or "",
                critique_priority=cfg.get("critique_priority") or 0,
            ),
            sender_id=self.id,
        )

    def _publish_activity(self, event_type: str, data: dict, correlation_id: str | None) -> None:
        self._pub.publish(
            ToolActivity(tool=self.TOOL_NAME, event=event_type, data=data),
            sender_id=self.id,
            correlation_id=correlation_id or "",
        )

    def _do_transition(self, action: str, error: str = "", correlation_id: str = "") -> None:
        """Publish a tool.transition event and update internal state.

        The published ``to`` field reflects the logical outcome (ERROR for
        failures). Internal state always resets to IDLE after any action so
        the next request can be handled.
        """
        from_state = self._state
        if action == "REQUEST":
            to_state = ToolState.EXECUTING
        elif action == "RESULT":
            to_state = ToolState.IDLE
        else:
            to_state = ToolState.ERROR

        self._state = ToolState.IDLE if to_state == ToolState.ERROR else to_state

        try:
            self._pub.publish(
                ToolTransition(
                    tool=self.TOOL_NAME,
                    from_state=from_state.value,
                    action=action,
                    to=to_state.value,
                    error=error,
                ),
                sender_id=self.id,
                correlation_id=correlation_id,
            )
        except Exception:
            pass

    @contextmanager
    def _thread_publisher(self) -> Generator[ZmqPublisher, None, None]:
        """Yield a ZmqPublisher safe to use from a daemon thread.

        ZMQ sockets are not thread-safe. This creates a dedicated publisher
        and waits 100ms for the async connect() to establish before yielding
        (slow-joiner fix). Always closes the publisher on exit.
        """
        pub = ZmqPublisher(PROXY_FRONTEND_ADDR, bind=False)
        time.sleep(0.1)
        try:
            yield pub
        finally:
            pub.close()

    def _handle_extra(self, envelope: MessageEnvelope) -> None:
        """Handle extra subscribed subjects. Override in subclasses."""

    def run(self) -> None:
        self._announce_schema()
        logger.info("%s ready", self.id)
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
            elif envelope.subject == self._request_subject:
                cid = envelope.correlation_id or ""
                self._do_transition("REQUEST", correlation_id=cid)
                try:
                    self._handle_request(envelope)
                    self._do_transition("RESULT", correlation_id=cid)
                except Exception as exc:
                    logger.error("%s: handler error: %s", self.__class__.__name__, exc)
                    self._do_transition("ERROR", error=str(exc), correlation_id=cid)
            else:
                self._handle_extra(envelope)
