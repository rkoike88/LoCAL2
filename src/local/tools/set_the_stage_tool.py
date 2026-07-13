"""SetTheStageTool — passthrough formatter for stage-setting.

The model fills in the five stage dimensions as tool call arguments.
This tool formats them and returns them — no LLM calls.
"""
from __future__ import annotations

import logging

from local.protocol.envelope import MessageEnvelope
from local.protocol.subjects import (
    TOOL_ACTIVITY_SET_THE_STAGE,
    TOOL_CALL_SET_THE_STAGE,
    TOOL_RESULT_SET_THE_STAGE,
)
from local.tools.base_tool import BaseTool

logger = logging.getLogger(__name__)


class SetTheStageTool(BaseTool):
    CONFIG_NAME = "set_the_stage"
    TOOL_NAME = "set_the_stage"
    ACTIVITY_SUBJECT = TOOL_ACTIVITY_SET_THE_STAGE
    RESULT_SUBJECT = TOOL_RESULT_SET_THE_STAGE

    def __init__(self) -> None:
        super().__init__(TOOL_CALL_SET_THE_STAGE)

    def _handle_request(self, envelope: MessageEnvelope) -> None:
        args = envelope.payload.get("args", {})
        correlation_id = envelope.correlation_id

        role     = args.get("role", "helpful assistant")
        audience = args.get("audience", "general user")
        intent   = args.get("intent", "")
        success  = args.get("success", "")
        bounds   = args.get("bounds", "")

        lines = [
            f"Role: {role}",
            f"Audience: {audience}",
            f"Intent: {intent}",
            f"Success: {success}",
        ]
        if bounds:
            lines.append(f"Bounds: {bounds}")

        result = "\n".join(lines)
        logger.info("SetTheStageTool: %s", result.replace("\n", " | "))
        self._publish_activity("request", args, correlation_id)
        self._publish_activity("result", {"result": result}, correlation_id)
        self._publish_result(result, correlation_id)
