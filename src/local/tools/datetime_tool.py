"""DateTimeTool — returns current local date, time, day of week, and timezone.

No parameters required. Stdlib only — no external dependencies.
"""
from __future__ import annotations

import logging
from datetime import datetime

from local.protocol.envelope import MessageEnvelope
from local.protocol.subjects import (
    TOOL_ACTIVITY_GET_DATETIME,
    TOOL_CALL_GET_DATETIME,
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
    CONFIG_NAME = "datetime"
    TOOL_NAME = "get_datetime"
    ACTIVITY_SUBJECT = TOOL_ACTIVITY_GET_DATETIME
    RESULT_SUBJECT = TOOL_RESULT_GET_DATETIME

    def __init__(self) -> None:
        super().__init__(TOOL_CALL_GET_DATETIME)

    def _handle_request(self, envelope: MessageEnvelope) -> None:
        correlation_id = envelope.correlation_id
        self._publish_activity("request", {}, correlation_id)
        result = _get_datetime()
        logger.info("DateTimeTool: %s", result)
        self._publish_activity("result", {"result": result}, correlation_id)
        self._publish_result(result, correlation_id)


if __name__ == "__main__":
    DateTimeTool().run()
