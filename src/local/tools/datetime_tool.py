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
)
from local.tools.base_tool import BaseTool

logger = logging.getLogger(__name__)


def _get_datetime() -> str:
    now = datetime.now().astimezone()
    tz_name = now.strftime("%Z")      # e.g. "PDT"
    offset_h = int(now.strftime("%z")[:3])
    utc_str = f"UTC{offset_h:+d}"
    return now.strftime("%A %Y-%m-%d %H:%M:%S ") + f"{tz_name} ({utc_str})"


class DateTimeTool(BaseTool):
    TOOL_ID = "datetime_tool"
    TOOL_NAME = "get_datetime"
    ACTIVITY_SUBJECT = TOOL_ACTIVITY_GET_DATETIME

    def __init__(self) -> None:
        super().__init__(TOOL_REQUEST_GET_DATETIME)

    def _build_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.TOOL_NAME,
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

    def _handle_request(self, envelope: MessageEnvelope) -> None:
        correlation_id = envelope.correlation_id
        self._publish_activity("request", {}, correlation_id)
        result = _get_datetime()
        logger.info("DateTimeTool: %s", result)
        self._publish_activity("result", {"result": result}, correlation_id)
        self._pub.publish(MessageEnvelope.create(
            message_type="tool_result",
            subject=TOOL_RESULT_GET_DATETIME,
            sender_id=self.TOOL_ID,
            payload={"result": result},
            correlation_id=correlation_id,
        ))


if __name__ == "__main__":
    DateTimeTool().run()
