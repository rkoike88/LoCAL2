"""PersonaTool — injects a persona seed into the generation context.

Reads persona definitions from config/personas.yaml. Each persona has a
'seed' — a distilled conversation summary that primes a cognitive register
without prescribing behavior. Gemma infers the stance from the pattern.

Bus subjects:
  Call:   tool.call.persona   {name, reason}
  Result: tool.result.persona {result}
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

TOOL_NAME = "persona"


class PersonaTool(BaseTool):
    CONFIG_NAME = "personas"
    TOOL_NAME = TOOL_NAME
    ACTIVITY_SUBJECT = TOOL_ACTIVITY_PERSONA
    RESULT_SUBJECT = TOOL_RESULT_PERSONA

    def __init__(self) -> None:
        super().__init__(TOOL_CALL_PERSONA)

    def _build_schema(self) -> dict:
        cfg = get_config(self.CONFIG_NAME) or {}
        personas = cfg.get("personas", {})
        enum_values = list(personas.keys())
        enum_desc = "; ".join(
            f"'{name}': {p.get('description', name)}"
            for name, p in personas.items()
        )
        return {
            "type": "function",
            "function": {
                "name": TOOL_NAME,
                "description": cfg.get("description", "Adopt a cognitive persona."),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "enum": enum_values,
                            "description": enum_desc,
                        },
                        "reason": {
                            "type": "string",
                            "description": "Brief rationale for the persona shift — visible in the XAI trace.",
                        },
                    },
                    "required": ["name", "reason"],
                },
            },
        }

    def _handle_request(self, envelope: MessageEnvelope) -> None:
        args = envelope.payload.get("args") or {}
        name = args.get("name", "")
        reason = args.get("reason", "")
        correlation_id = envelope.correlation_id

        self._publish_activity("request", {"name": name, "reason": reason}, correlation_id)

        cfg = get_config(self.CONFIG_NAME) or {}
        persona = (cfg.get("personas") or {}).get(name)

        if persona is None:
            result = f"[persona: unknown '{name}']"
        else:
            result = persona.get("seed", "").strip()

        logger.info("PersonaTool: activated '%s' — %s", name, reason)
        self._publish_activity("result", {"name": name, "result": result}, correlation_id)
        self._publish_result(result, correlation_id)


if __name__ == "__main__":
    PersonaTool().run()
