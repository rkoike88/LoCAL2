"""GeneratorAgent — core LLM agent for LoCAL2.

Subscribes to query.received and tool.schema. Maintains per-session conversation history.
Calls ollama.chat() directly (not via OllamaBackend) so response["message"]
is accessible for history appending and tool call inspection.

Tool schemas are populated dynamically as tools announce themselves on tool.schema.
_execute_tool() opens a short-lived ZmqSubscriber for tool.result.* BEFORE publishing
the tool.request.*, then polls until correlation_id matches or timeout expires.
"""
from __future__ import annotations

import logging
import time
import uuid

from local.agents.generator_actions import GeneratorAction
from local.agents.generator_states import GeneratorState
from local.agents.generator_transitions import GeneratorStateMachine
from local.config_loader import get_config
from local.protocol.envelope import MessageEnvelope
from local.protocol.subjects import (
    ANSWER_DIALOG, GENERATION_THINKING, QUERY_RECEIVED, RESPONSE_GENERATION,
    TOOL_SCHEMA, TOOL_SCHEMA_REQUEST,
)
from local.services.conversation_service import ConversationService
from local.transport.bus_config import PROXY_BACKEND_ADDR, make_participant_bus
from local.transport.zmq_pubsub import ZmqSubscriber

logger = logging.getLogger(__name__)


class GeneratorAgent:
    AGENT_ID = "generator"

    def __init__(
        self,
        model: str | None = None,
        temperature: float | None = None,
        respondent_id: str = "A",
        conversation_service=None,
    ) -> None:
        cfg = get_config("generator")
        self._model: str = model or cfg.get("model", "gemma4:e2b")
        self._options: dict = {
            "num_ctx": cfg.get("num_ctx", 32000),
            "temperature": temperature if temperature is not None else cfg.get("temperature", 0.7),
        }
        self._system_prompt: str = cfg.get("system_prompt", "") or ""
        self._max_tool_iters: int = cfg.get("max_tool_iterations", 5)
        self._tool_timeout: float = cfg.get("tool_timeout", 20)
        self._tool_schemas: list = cfg.get("tools", [])
        self._respondent_id: str = respondent_id
        self._conv = conversation_service or ConversationService()
        self._sm = GeneratorStateMachine()
        self._pub, self._sub = make_participant_bus([QUERY_RECEIVED, TOOL_SCHEMA])

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self) -> None:
        print(
            f"[generator:{self._respondent_id}] model={self._model}"
            f"  num_ctx={self._options['num_ctx']}"
            f"  temperature={self._options['temperature']}"
        )
        self._request_schemas()
        time.sleep(0.5)  # startup window: let tool schema responses queue up
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
            elif envelope.subject == TOOL_SCHEMA:
                self._register_tool_schema(envelope.payload.get("schema", {}))

    # ------------------------------------------------------------------
    # Query handling
    # ------------------------------------------------------------------

    def _handle_query(self, envelope: MessageEnvelope) -> None:
        payload = envelope.payload
        query: str = payload.get("query", "")
        session_id: str | None = payload.get("session_id")
        original_query_id: str = payload.get("query_id") or str(uuid.uuid4())
        attachments: list = payload.get("attachments") or []

        # RespondentB gets its own query_id to avoid ChromaDB collision;
        # correlation_id links it back to the original query for pairwise matching.
        if self._respondent_id == "B":
            query_id = str(uuid.uuid4())
            correlation_id = original_query_id
        else:
            query_id = original_query_id
            correlation_id = envelope.correlation_id or query_id

        self._sm.transition(GeneratorAction.RECEIVE)

        messages = self._build_messages(query, session_id, attachments)
        initial_len = len(messages)
        self._sm.transition(GeneratorAction.START_GENERATION)

        try:
            answer, thinking, tool_call_log = self._generate(
                messages, correlation_id, session_id, query_id
            )
        except Exception as exc:
            logger.error("GeneratorAgent: generation failed: %s", exc, exc_info=True)
            self._sm.transition(GeneratorAction.FAIL)
            self._pub.publish(self._make_envelope(
                RESPONSE_GENERATION, "response",
                {"answer": f"[generation error: {exc}]", "thinking": "",
                 "tool_calls": [], "session_id": session_id, "query_id": query_id,
                 "respondent_id": self._respondent_id, "error": True},
                correlation_id, session_id,
            ))
            self._sm.transition(GeneratorAction.RESET)
            return

        # Only RespondentA maintains the shared conversation history.
        # B generates a comparison answer but doesn't pollute the context window.
        if self._respondent_id == "A":
            new_messages = [self._clean_for_history(m) for m in messages[initial_len - 1:]]
            self._conv.append_messages(session_id, new_messages)
            print(f"[generator] stored {len(new_messages)} msgs session={session_id!r}")
        self._sm.transition(GeneratorAction.PUBLISH)

        self._pub.publish(self._make_envelope(
            RESPONSE_GENERATION, "response",
            {"query": query, "answer": answer, "thinking": thinking,
             "tool_calls": tool_call_log,
             "session_id": session_id, "query_id": query_id,
             "respondent_id": self._respondent_id},
            correlation_id, session_id,
        ))
        self._pub.publish(self._make_envelope(
            ANSWER_DIALOG, "dialog",
            {"query": query, "answer": answer,
             "session_id": session_id, "query_id": query_id,
             "respondent_id": self._respondent_id},
            correlation_id, session_id,
        ))

        self._sm.transition(GeneratorAction.RESET)

    def _build_messages(
        self,
        query: str,
        session_id: str | None,
        attachments: list[dict] | None = None,
    ) -> list[dict]:
        """Construct the messages array for ollama.chat() from history + new query."""
        history = self._conv.get_history(session_id)
        messages: list[dict] = []
        if self._system_prompt:
            messages.append({"role": "system", "content": self._system_prompt})
        messages.extend(history)

        max_chars: int = get_config("generator").get("max_attachment_chars", 8000)
        content_parts: list[str] = []
        image_b64s: list[str] = []
        for att in (attachments or []):
            if att.get("type") == "text":
                text = (att.get("data") or "")[:max_chars]
                content_parts.append(f'[Attached: {att["name"]}]\n{text}')
            elif att.get("type") == "image":
                image_b64s.append(att.get("data", ""))
        content_parts.append(query)

        user_msg: dict = {"role": "user", "content": "\n\n".join(content_parts)}
        if image_b64s:
            user_msg["images"] = image_b64s
        messages.append(user_msg)
        return messages

    def _generate(
        self,
        messages: list[dict],
        correlation_id: str,
        session_id: str | None = None,
        query_id: str = "",
    ) -> tuple[str, str, list[dict]]:
        """Stream ollama.chat(); publish thinking chunks; return (answer, thinking, tool_call_log)."""
        import ollama

        raw_msg: dict = {}
        tool_call_log: list[dict] = []
        accumulated_thinking: str = ""

        for _ in range(self._max_tool_iters):
            iter_content = ""
            iter_thinking = ""
            iter_tool_calls = None

            for chunk in ollama.chat(
                model=self._model,
                messages=messages,
                tools=self._tool_schemas or None,
                think=True,
                stream=True,
                options=self._options,
            ):
                if chunk.message.thinking:
                    iter_thinking += chunk.message.thinking
                    accumulated_thinking += chunk.message.thinking
                    self._pub.publish(self._make_envelope(
                        GENERATION_THINKING, "thinking",
                        {"chunk": chunk.message.thinking,
                         "session_id": session_id, "query_id": query_id,
                         "respondent_id": self._respondent_id},
                        correlation_id, session_id,
                    ))
                if chunk.message.content:
                    iter_content += chunk.message.content
                if chunk.message.tool_calls:
                    iter_tool_calls = chunk.message.tool_calls

            raw_msg = {
                "role": "assistant",
                "content": iter_content,
                "thinking": iter_thinking or None,
                "tool_calls": iter_tool_calls,
            }
            messages.append(raw_msg)

            tool_calls: list = iter_tool_calls or []
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
        thinking = accumulated_thinking.strip()
        if not answer and tool_call_log:
            logger.warning(
                "GeneratorAgent: max_tool_iterations (%d) exhausted without a final text answer",
                self._max_tool_iters,
            )
        return answer, thinking, tool_call_log

    def _normalize_tool_name(self, name: str) -> str:
        """Map hallucinated or variant tool names to registered schema names."""
        registered = {s.get("function", {}).get("name") for s in self._tool_schemas}
        if name in registered:
            return name
        name_lower = name.lower()
        for rname in registered:
            if rname in name_lower or name_lower in rname:
                logger.warning("GeneratorAgent: normalizing tool name %r → %r", name, rname)
                return rname
        return name

    def _execute_tool(self, name: str, args: dict, correlation_id: str) -> str:
        """Dispatch a tool call over the bus and block until the result arrives or timeout.

        Opens a short-lived ZmqSubscriber for tool.result.<name> BEFORE publishing
        tool.request.<name> to avoid a race between publish and subscribe.
        """
        name = self._normalize_tool_name(name)
        req_subject = f"tool.request.{name}"
        res_subject = f"tool.result.{name}"

        result_sub = ZmqSubscriber(PROXY_BACKEND_ADDR, subscriptions=[res_subject])
        try:
            self._pub.publish(self._make_envelope(
                req_subject, "tool_request",
                {"tool": name, "args": args},
                correlation_id, None,
            ))
            deadline = time.monotonic() + self._tool_timeout
            while time.monotonic() < deadline:
                remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
                msg = result_sub.receive_with_timeout(remaining_ms)
                if msg is None:
                    break
                if msg.correlation_id == correlation_id:
                    return msg.payload.get("result", "")
        finally:
            result_sub.close()

        logger.warning("GeneratorAgent: tool %r timed out after %ss", name, self._tool_timeout)
        return f"[tool timeout: {name!r} did not respond within {self._tool_timeout}s]"

    def _request_schemas(self) -> None:
        self._pub.publish(MessageEnvelope.create(
            message_type="schema_request",
            subject=TOOL_SCHEMA_REQUEST,
            sender_id=self.AGENT_ID,
            payload={},
        ))

    def _register_tool_schema(self, schema: dict) -> None:
        """Add or replace a tool schema in the registry."""
        name = schema.get("function", {}).get("name", "")
        if not name:
            return
        self._tool_schemas = [s for s in self._tool_schemas if s.get("function", {}).get("name") != name]
        self._tool_schemas.append(schema)
        logger.info("GeneratorAgent: registered tool %r (total: %d)", name, len(self._tool_schemas))

    @staticmethod
    def _clean_for_history(m: dict) -> dict:
        """Strip thinking and empty tool_calls from a message dict before saving to history."""
        result = {k: v for k, v in m.items() if k != "thinking"}
        if not result.get("tool_calls"):
            result.pop("tool_calls", None)
        return result

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
