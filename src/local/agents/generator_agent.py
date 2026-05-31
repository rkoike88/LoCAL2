"""GeneratorAgent — core LLM agent for LoCAL2.

Subscribes to query.received. Maintains per-session conversation history.
Calls ollama.chat() directly (not via OllamaBackend) so response["message"]
is accessible for history appending and tool call inspection.

Phase 1a: no tools — tool loop always exits on first iteration.
Phase 1b: _execute_tool() will be wired to the bus.
"""
from __future__ import annotations

import logging
import uuid

from local.agents.generator_actions import GeneratorAction
from local.agents.generator_states import GeneratorState
from local.agents.generator_transitions import GeneratorStateMachine
from local.config_loader import get_config
from local.protocol.envelope import MessageEnvelope
from local.protocol.subjects import ANSWER_DIALOG, QUERY_RECEIVED, RESPONSE_GENERATION
from local.services.conversation_service import ConversationService
from local.transport.bus_config import make_participant_bus

logger = logging.getLogger(__name__)


class GeneratorAgent:
    AGENT_ID = "generator"

    def __init__(self, model: str | None = None) -> None:
        cfg = get_config("generator")
        self._model: str = model or cfg.get("model", "gemma4:e2b")
        self._options: dict = {
            "num_ctx": cfg.get("num_ctx", 32000),
            "temperature": cfg.get("temperature", 0.7),
        }
        self._system_prompt: str = cfg.get("system_prompt", "") or ""
        self._max_tool_iters: int = cfg.get("max_tool_iterations", 5)
        self._tool_schemas: list = cfg.get("tools", [])
        self._conv = ConversationService()
        self._sm = GeneratorStateMachine()
        self._pub, self._sub = make_participant_bus([QUERY_RECEIVED])

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self) -> None:
        print(f"[generator] model={self._model}  num_ctx={self._options['num_ctx']}")
        while True:
            try:
                envelope = self._sub.receive()
            except Exception as exc:
                logger.error("GeneratorAgent: receive error: %s", exc)
                continue
            if envelope.subject == QUERY_RECEIVED:
                try:
                    self._handle_query(envelope)
                except Exception as exc:
                    logger.error("GeneratorAgent: unhandled error: %s", exc, exc_info=True)
                    if self._sm.state != GeneratorState.IDLE:
                        self._sm.reset()

    # ------------------------------------------------------------------
    # Query handling
    # ------------------------------------------------------------------

    def _handle_query(self, envelope: MessageEnvelope) -> None:
        payload = envelope.payload
        query: str = payload.get("query", "")
        session_id: str | None = payload.get("session_id")
        query_id: str = payload.get("query_id") or str(uuid.uuid4())
        correlation_id: str = envelope.correlation_id or query_id

        self._sm.transition(GeneratorAction.RECEIVE)

        messages = self._build_messages(query, session_id)
        self._sm.transition(GeneratorAction.START_GENERATION)

        try:
            answer, thinking, tool_call_log = self._generate(messages, correlation_id)
        except Exception as exc:
            logger.error("GeneratorAgent: generation failed: %s", exc, exc_info=True)
            self._sm.transition(GeneratorAction.FAIL)
            self._pub.publish(self._make_envelope(
                RESPONSE_GENERATION, "response",
                {"answer": f"[generation error: {exc}]", "thinking": "",
                 "tool_calls": [], "session_id": session_id, "query_id": query_id,
                 "error": True},
                correlation_id, session_id,
            ))
            self._sm.transition(GeneratorAction.RESET)
            return

        self._conv.append_turn(session_id, query, answer)
        self._sm.transition(GeneratorAction.PUBLISH)

        self._pub.publish(self._make_envelope(
            RESPONSE_GENERATION, "response",
            {"answer": answer, "thinking": thinking, "tool_calls": tool_call_log,
             "session_id": session_id, "query_id": query_id},
            correlation_id, session_id,
        ))
        self._pub.publish(self._make_envelope(
            ANSWER_DIALOG, "dialog",
            {"query": query, "answer": answer,
             "session_id": session_id, "query_id": query_id},
            correlation_id, session_id,
        ))

        self._sm.transition(GeneratorAction.RESET)

    def _build_messages(self, query: str, session_id: str | None) -> list[dict]:
        """Construct the messages array for ollama.chat() from history + new query."""
        history = self._conv.get_history(session_id)
        messages: list[dict] = []
        if self._system_prompt and not history:
            messages.append({"role": "system", "content": self._system_prompt})
        messages.extend(history)
        messages.append({"role": "user", "content": query})
        return messages

    def _generate(
        self, messages: list[dict], correlation_id: str
    ) -> tuple[str, str, list[dict]]:
        """Run the ollama.chat() tool loop; return (answer, thinking, tool_call_log)."""
        import ollama

        raw_msg: dict = {}
        response = None
        tool_call_log: list[dict] = []

        for _ in range(self._max_tool_iters):
            response = ollama.chat(
                model=self._model,
                messages=messages,
                tools=self._tool_schemas or None,
                think=True,
                options=self._options,
            )
            raw_msg = response.message.model_dump()
            messages.append(raw_msg)

            tool_calls: list = raw_msg.get("tool_calls") or []
            if not tool_calls:
                break

            self._sm.transition(GeneratorAction.DISPATCH_TOOL)
            for tc in tool_calls:
                fn = tc.get("function") or {}
                name: str = fn.get("name", "")
                args: dict = fn.get("arguments") or {}
                result = self._execute_tool(name, args, correlation_id)
                tool_call_log.append({"tool": name, "args": args, "result": str(result)})
                messages.append({"role": "tool", "content": str(result), "name": name})
            self._sm.transition(GeneratorAction.TOOL_RESULT)

        answer = (raw_msg.get("content") or "").strip()
        thinking = (getattr(response, "thinking", None) or "").strip()
        return answer, thinking, tool_call_log

    def _execute_tool(self, name: str, args: dict, correlation_id: str) -> str:
        """Execute a tool call. Phase 1b wires this to the bus; Phase 1a returns a stub."""
        return f"[tool not available in Phase 1a: {name!r}]"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _make_envelope(
        self,
        subject: str,
        message_type: str,
        payload: dict,
        correlation_id: str,
        session_id: str | None,
    ) -> MessageEnvelope:
        return MessageEnvelope.create(
            message_type=message_type,
            subject=subject,
            sender_id=self.AGENT_ID,
            payload=payload,
            correlation_id=correlation_id,
            metadata={"session_id": session_id or ""},
        )
