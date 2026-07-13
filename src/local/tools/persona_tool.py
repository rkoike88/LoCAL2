"""PersonaTool — returns a pre-written cognitive-mode seed for the requested persona.

The model picks a mode when calling this tool. The tool looks up the seed text
from personas.yaml and returns it — no LLM calls.
"""
from __future__ import annotations

import logging

from local.config_loader import get_config
from local.protocol.envelope import MessageEnvelope
from local.protocol.subjects import (
    TOOL_ACTIVITY_PERSONA,
    TOOL_CALL_PERSONA,
    TOOL_RESULT_PERSONA,
)
from local.tools.base_tool import BaseTool

logger = logging.getLogger(__name__)


class PersonaTool(BaseTool):
    CONFIG_NAME = "persona"
    TOOL_NAME = "persona"
    ACTIVITY_SUBJECT = TOOL_ACTIVITY_PERSONA
    RESULT_SUBJECT = TOOL_RESULT_PERSONA

    def __init__(self) -> None:
        super().__init__(TOOL_CALL_PERSONA)

    def _handle_request(self, envelope: MessageEnvelope) -> None:
        args = envelope.payload.get("args", {})
        correlation_id = envelope.correlation_id
        name = args.get("name", "general")
        reason = args.get("reason", "")

        personas_cfg = get_config(self.CONFIG_NAME) or {}
        personas = personas_cfg.get("personas", {})
        persona = personas.get(name) or personas.get("general", {})
        seed = (persona.get("seed") or f"[{name.upper()}]").strip()

        logger.info("PersonaTool: mode=%s", name)
        self._publish_activity("request", {"name": name, "reason": reason}, correlation_id)
        self._publish_activity("result", {"result": seed}, correlation_id)
        self._publish_result(seed, correlation_id)
