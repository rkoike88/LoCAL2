"""DateTimeTool — returns current local date, time, day of week, and timezone.

No parameters required. Stdlib only — no external dependencies.
"""
from __future__ import annotations

import logging
from datetime import datetime

from local.protocol.envelope import MessageEnvelope
from local.protocol.subjects import (
    TOOL_ACTIVITY_GET_DATETIME,
    TOOL_REQUEST_GET_DATETIME,
    TOOL_RESULT_GET_DATETIME,
    TOOL_SCHEMA,
    TOOL_SCHEMA_REQUEST,
)
from local.transport.bus_config import make_participant_bus

logger = logging.getLogger(__name__)

TOOL_NAME = "get_datetime"

_SCHEMA = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": (
            "Returns the current local date, time, day of week, and timezone. "
            "Call this tool for any question about the current time, date, day, year, "
            "or timezone. Do not answer from training data — your training cutoff is "
            "not the current date."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
}


def _get_datetime() -> str:
    now = datetime.now().astimezone()
    tz_name = now.strftime("%Z")      # e.g. "PDT"
    offset_h = int(now.strftime("%z")[:3])
    utc_str = f"UTC{offset_h:+d}"
    return now.strftime("%A %Y-%m-%d %H:%M:%S ") + f"{tz_name} ({utc_str})"


class DateTimeTool:
    TOOL_ID = "datetime_tool"

    def __init__(self) -> None:
        self._pub, self._sub = make_participant_bus([TOOL_REQUEST_GET_DATETIME, TOOL_SCHEMA_REQUEST])

    def run(self) -> None:
        self._announce_schema()
        logger.info("datetime_tool ready")
        while True:
            try:
                envelope = self._sub.receive()
            except Exception as exc:
                logger.error("DateTimeTool: receive error: %s", exc)
                continue
            if envelope.subject == TOOL_SCHEMA_REQUEST:
                self._announce_schema()
            elif envelope.subject == TOOL_REQUEST_GET_DATETIME:
                self._handle_request(envelope)

    def _announce_schema(self) -> None:
        self._pub.publish(MessageEnvelope.create(
            message_type="tool_schema",
            subject=TOOL_SCHEMA,
            sender_id=self.TOOL_ID,
            payload={"schema": _SCHEMA},
        ))

    def _handle_request(self, envelope: MessageEnvelope) -> None:
        result = _get_datetime()
        logger.info("DateTimeTool: %s", result)
        self._pub.publish(MessageEnvelope.create(
            message_type="tool_activity",
            subject=TOOL_ACTIVITY_GET_DATETIME,
            sender_id=self.TOOL_ID,
            payload={"request": {}, "result": result},
            correlation_id=envelope.correlation_id,
        ))
        self._pub.publish(MessageEnvelope.create(
            message_type="tool_result",
            subject=TOOL_RESULT_GET_DATETIME,
            sender_id=self.TOOL_ID,
            payload={"result": result},
            correlation_id=envelope.correlation_id,
        ))


if __name__ == "__main__":
    DateTimeTool().run()
