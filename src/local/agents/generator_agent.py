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
import socket
import time
import uuid

from local.agents.base_agent import BaseAgent
from local.agents.generator_actions import GeneratorAction
from local.agents.generator_states import GeneratorState
from local.agents.generator_transitions import GeneratorStateMachine
from local.config_loader import get_config
from local.protocol.envelope import MessageEnvelope
from local.protocol.subjects import (
    ANSWER_DIALOG, COMPACTION_REQUEST, COMPACTION_RESULT,
    GENERATION_THINKING, GENERATOR_STATUS, QUERY_RECEIVED, RESPONSE_GENERATION,
    TOOL_SCHEMA, TOOL_SCHEMA_REQUEST,
)
from local.services.conversation_service import ConversationService
from local.transport.bus_config import PROXY_BACKEND_ADDR, make_participant_bus
from local.transport.zmq_pubsub import ZmqSubscriber

logger = logging.getLogger(__name__)


class GeneratorAgent(BaseAgent):
    """Core LLM agent for LoCAL2.

    Maintains per-session conversation history, dispatches tool calls over
    the bus, and streams thinking tokens to the UI.
    """

    AGENT_ID = "generator"

    def __init__(
        self,
        model: str | None = None,
        temperature: float | None = None,
        conversation_service=None,
    ) -> None:
        """Initialize the GeneratorAgent.

        All parameters fall back to ``config/generator.yaml`` when ``None``.

        Args:
            model: Ollama model name (e.g. ``"gemma4:e2b"``).
            temperature: Sampling temperature (0.0 = deterministic).
            conversation_service: Injected for testing; defaults to a fresh
                ``ConversationService``.
        """
        cfg = get_config("generator")
        sys_cfg = get_config("system") or {}
        self._model: str = model or cfg.get("model", "gemma4:e2b")
        self._options: dict = {
            "num_ctx": cfg.get("num_ctx", 32000),
            "temperature": temperature if temperature is not None else cfg.get("temperature", 0.7),
        }
        self._system_prompt: str = cfg.get("system_prompt", "") or ""
        self._max_tool_iters: int = cfg.get("max_tool_iterations", 5)
        self._tool_timeout: float = cfg.get("tool_timeout", 20)
        self._tool_schemas: list = cfg.get("tools", [])
        self._instance_id: str = sys_cfg.get("instance_id") or socket.gethostname()
        self._token_count: int = 0
        self._conv = conversation_service or ConversationService()
        self._sm = GeneratorStateMachine()
        self._pub, self._sub = make_participant_bus([QUERY_RECEIVED, TOOL_SCHEMA, COMPACTION_REQUEST])

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self) -> None:
        logger.info(
            "generator model=%s  num_ctx=%s  temperature=%s",
            self._model, self._options["num_ctx"], self._options["temperature"],
        )
        self._request_schemas()
        time.sleep(0.5)  # startup window: let tool schema responses queue up
        self._publish_status()
        while True:
            try:
                envelope = self._sub.receive()
            except Exception as exc:
                logger.error("GeneratorAgent: receive error: %s", exc)
                continue
            self._dispatch(envelope)

    def _dispatch(self, envelope: MessageEnvelope) -> None:
        if envelope.subject == QUERY_RECEIVED:
            try:
                self._handle_query(envelope)
            except Exception as exc:
                logger.error("GeneratorAgent: unhandled error: %s", exc, exc_info=True)
                if self._sm.state != GeneratorState.IDLE:
                    self._sm.reset()
        elif envelope.subject == TOOL_SCHEMA:
            self._register_tool_schema(envelope.payload.get("schema", {}))
        elif envelope.subject == COMPACTION_REQUEST:
            try:
                self._handle_compaction(envelope)
            except Exception as exc:
                logger.error("GeneratorAgent: compaction error: %s", exc, exc_info=True)

    # ------------------------------------------------------------------
    # Query handling
    # ------------------------------------------------------------------

    def _handle_query(self, envelope: MessageEnvelope) -> None:
        payload = envelope.payload
        query: str = payload.get("query", "")
        session_id: str | None = payload.get("session_id")
        original_query_id: str = payload.get("query_id") or str(uuid.uuid4())
        attachments: list = payload.get("attachments") or []

        query_id = original_query_id
        correlation_id = envelope.correlation_id or query_id

        self._do_transition(GeneratorAction.RECEIVE)

        messages = self._build_messages(query, session_id, attachments)
        initial_len = len(messages)
        self._do_transition(GeneratorAction.START_GENERATION)

        try:
            answer, thinking, tool_call_log, prompt_tokens = self._generate(
                messages, correlation_id, session_id, query_id
            )
        except Exception as exc:
            logger.error("GeneratorAgent: generation failed: %s", exc, exc_info=True)
            self._do_transition(GeneratorAction.FAIL)
            self._pub.publish(self._make_envelope(
                RESPONSE_GENERATION, "response",
                {"answer": f"[generation error: {exc}]", "thinking": "",
                 "tool_calls": [], "session_id": session_id, "query_id": query_id,
                 "error": True},
                correlation_id, session_id,
            ))
            self._do_transition(GeneratorAction.RESET)
            return

        new_messages = [self._clean_for_history(m) for m in messages[initial_len - 1:]]
        self._conv.append_messages(session_id, new_messages)
        self._conv.set_token_count(session_id, prompt_tokens)
        self._token_count = prompt_tokens
        self._do_transition(GeneratorAction.PUBLISH)

        self._pub.publish(self._make_envelope(
            RESPONSE_GENERATION, "response",
            {"query": query, "answer": answer, "thinking": thinking,
             "tool_calls": tool_call_log,
             "session_id": session_id, "query_id": query_id,
             "prompt_tokens": prompt_tokens},
            correlation_id, session_id,
        ))
        self._pub.publish(self._make_envelope(
            ANSWER_DIALOG, "dialog",
            {"query": query, "answer": answer,
             "session_id": session_id, "query_id": query_id},
            correlation_id, session_id,
        ))

        self._do_transition(GeneratorAction.RESET)

    def _build_messages(
        self,
        query: str,
        session_id: str | None,
        attachments: list[dict] | None = None,
    ) -> list[dict]:
        """Build the messages array for ``ollama.chat()`` from history + new query.

        Message order: system prompt (if configured), session history, new
        user turn. Attachment text is prepended to the user message content;
        images are passed as base64 strings in the ``"images"`` key.

        Args:
            query: The user's raw query text.
            session_id: Active session for history lookup. ``None`` produces
                a stateless single-turn call with no history.
            attachments: List of dicts with keys ``type`` (``"text"`` or
                ``"image"``), ``name``, and ``data``. Text is truncated to
                ``max_attachment_chars`` from config (default 8000).

        Returns:
            A messages list suitable for ``ollama.chat(messages=...)``.
        """
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
    ) -> tuple[str, str, list[dict], int]:
        """Run the Ollama streaming chat loop with tool call dispatch.

        Streams thinking chunks to the bus as they arrive. On each tool call,
        dispatches via ``_execute_tool()``, appends the result as a ``"tool"``
        role message, and continues the loop. Stops when the model produces a
        plain-text turn or ``max_tool_iterations`` is reached.

        Args:
            messages: Full messages array (history + new user turn). Modified
                in-place — assistant and tool turns are appended each iteration
                and later persisted by the caller via ``ConversationService``.
            correlation_id: Forwarded to all bus events published during this
                generation (thinking chunks, tool requests).
            session_id: Included in bus event payloads for UI routing.
            query_id: Included in bus event payloads for memory linking.

        Returns:
            A 4-tuple ``(answer, thinking, tool_call_log, prompt_tokens)``:

            - ``answer``: Final assistant text; empty if ``max_tool_iterations``
              exhausted without a text turn.
            - ``thinking``: All concatenated thinking tokens from this generation.
            - ``tool_call_log``: List of ``{tool, args, result}`` dicts, one per
              tool call.
            - ``prompt_tokens``: ``prompt_eval_count`` from the last Ollama chunk
              (0 if unavailable).
        """
        import ollama

        raw_msg: dict = {}
        tool_call_log: list[dict] = []
        accumulated_thinking: str = ""
        prompt_tokens: int = 0

        for _ in range(self._max_tool_iters):
            iter_content = ""
            iter_thinking = ""
            iter_tool_calls = None
            last_chunk = None

            for chunk in ollama.chat(
                model=self._model,
                messages=messages,
                tools=self._tool_schemas or None,
                think=True,
                stream=True,
                options=self._options,
            ):
                last_chunk = chunk
                if chunk.message.thinking:
                    iter_thinking += chunk.message.thinking
                    accumulated_thinking += chunk.message.thinking
                    self._pub.publish(self._make_envelope(
                        GENERATION_THINKING, "thinking",
                        {"chunk": chunk.message.thinking,
                         "session_id": session_id, "query_id": query_id},
                        correlation_id, session_id,
                    ))
                if chunk.message.content:
                    iter_content += chunk.message.content
                if chunk.message.tool_calls:
                    iter_tool_calls = chunk.message.tool_calls

            if last_chunk is not None:
                prompt_tokens = getattr(last_chunk, "prompt_eval_count", 0) or 0

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

            for tc in tool_calls:
                fn = tc.get("function") or {}
                name: str = fn.get("name", "")
                args: dict = fn.get("arguments") or {}
                self._do_transition(GeneratorAction.DISPATCH_TOOL)
                result = self._execute_tool(name, args, correlation_id)
                tool_call_log.append({"tool": name, "args": args, "result": str(result)})
                messages.append({"role": "tool", "content": str(result), "name": name})

        answer = (raw_msg.get("content") or "").strip()
        thinking = accumulated_thinking.strip()
        if not answer and tool_call_log:
            logger.warning(
                "GeneratorAgent: max_tool_iterations (%d) exhausted without a final text answer",
                self._max_tool_iters,
            )
        return answer, thinking, tool_call_log, prompt_tokens

    def _handle_compaction(self, envelope: MessageEnvelope) -> None:
        """Summarize a session's history and replace it with a summary + tail turns.

        Calls Ollama synchronously with ``compaction_system_prompt`` from
        config. Keeps the last ``compaction_tail_turns`` (default 4)
        user+assistant pairs verbatim after the summary. Post-compaction token
        count is estimated from character count (÷ 4).

        Args:
            envelope: Must contain ``payload["session_id"]``. If the generator
                is not IDLE, publishes a ``COMPACTION_RESULT`` error and returns
                immediately without modifying history.
        """
        import ollama

        if self._sm.state != GeneratorState.IDLE:
            self._pub.publish(self._make_envelope(
                COMPACTION_RESULT, "compaction",
                {"error": "generator busy — try again after current query finishes",
                 "session_id": envelope.payload.get("session_id")},
                envelope.correlation_id or str(uuid.uuid4()), None,
            ))
            return

        session_id: str | None = envelope.payload.get("session_id")
        cfg = get_config("generator") or {}
        tail_turns: int = cfg.get("compaction_tail_turns", 4)

        history = self._conv.get_history(session_id)
        tokens_before = self._conv.get_token_count(session_id)

        if not history:
            self._pub.publish(self._make_envelope(
                COMPACTION_RESULT, "compaction",
                {"error": "no history to compact", "session_id": session_id},
                envelope.correlation_id or str(uuid.uuid4()), None,
            ))
            return

        # Build summarization prompt — only user/assistant pairs, skip tool turns
        convo_text = []
        for m in history:
            role = m.get("role", "")
            content = m.get("content") or ""
            if role in ("user", "assistant") and content:
                convo_text.append(f"{role.upper()}: {content}")
        summary_input = "\n\n".join(convo_text)

        compaction_system = cfg.get(
            "compaction_system_prompt",
            "Summarize this conversation concisely, preserving key facts and decisions.",
        ).strip()

        resp = ollama.chat(
            model=self._model,
            messages=[
                {"role": "system", "content": compaction_system},
                {"role": "user", "content": summary_input},
            ],
            stream=False,
            options=self._options,
        )
        summary_text = (resp.message.content or "").strip()

        # Keep last tail_turns user+assistant pairs verbatim
        # Walk history backwards collecting complete pairs
        tail_messages: list[dict] = []
        pairs_collected = 0
        i = len(history) - 1
        while i >= 1 and pairs_collected < tail_turns:
            if history[i].get("role") == "assistant" and history[i - 1].get("role") == "user":
                tail_messages = history[i - 1: i + 1] + tail_messages
                pairs_collected += 1
                i -= 2
            else:
                i -= 1

        new_messages = [{"role": "assistant", "content": f"[SUMMARY] {summary_text}"}] + tail_messages

        # Estimate post-compaction tokens from character count
        total_chars = sum(len(m.get("content") or "") for m in new_messages)
        tokens_estimated_after = total_chars // 4

        self._conv.replace_messages(session_id, new_messages)
        self._conv.set_token_count(session_id, tokens_estimated_after)

        self._pub.publish(self._make_envelope(
            COMPACTION_RESULT, "compaction",
            {"session_id": session_id,
             "tokens_before": tokens_before,
             "tokens_after": tokens_estimated_after,
             "summary": summary_text},
            envelope.correlation_id or str(uuid.uuid4()), None,
        ))
        logger.info(
            "GeneratorAgent: compacted session %s — %d → ~%d tokens",
            (session_id or "")[:8], tokens_before, tokens_estimated_after,
        )

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
        """Dispatch a single tool call over the bus and block for the result.

        Opens a short-lived ``ZmqSubscriber`` for ``tool.result.<name>``
        **before** publishing ``tool.request.<name>`` to avoid a race between
        publish and subscribe. Polls until ``correlation_id`` matches or
        ``tool_timeout`` (from config) expires.

        Args:
            name: Tool function name; normalized via ``_normalize_tool_name``
                first to handle Gemma hallucinations.
            args: Tool arguments dict from the model's tool call.
            correlation_id: Forwarded on the request envelope; used to match
                the response.

        Returns:
            Tool result string, or an error string on timeout.
        """
        name = self._normalize_tool_name(name)
        req_subject = f"tool.request.{name}"
        res_subject = f"tool.result.{name}"

        result_sub = ZmqSubscriber(PROXY_BACKEND_ADDR, subscriptions=[res_subject])
        self._do_transition(GeneratorAction.AWAIT_RESULT)
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
                    self._do_transition(GeneratorAction.TOOL_RESULT)
                    return msg.payload.get("result", "")
        finally:
            result_sub.close()

        logger.warning("GeneratorAgent: tool %r timed out after %ss", name, self._tool_timeout)
        self._do_transition(GeneratorAction.TOOL_TIMEOUT)
        return f"[tool timeout: {name!r} did not respond within {self._tool_timeout}s]"

    def _after_transition(self) -> None:
        self._publish_status()

    def _publish_status(self) -> None:
        """Publish a full generator state snapshot on generator.status."""
        tool_names = [
            s.get("function", {}).get("name", "")
            for s in self._tool_schemas
            if s.get("function", {}).get("name")
        ]
        self._pub.publish(MessageEnvelope.create(
            message_type="generator_status",
            subject=GENERATOR_STATUS,
            sender_id=self.AGENT_ID,
            payload={
                "instance_id":   self._instance_id,
                "model":         self._model,
                "temperature":   self._options.get("temperature", 0.0),
                "num_ctx":       self._options.get("num_ctx", 0),
                "state":         self._sm.state.value,
                "token_count":   self._token_count,
                "tool_names":    tool_names,
                "system_prompt": self._system_prompt,
            },
        ))

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
        self._publish_status()

    @staticmethod
    def _clean_for_history(m: dict) -> dict:
        """Strip thinking and empty tool_calls from a message dict before saving to history.

        Converts Ollama ToolCall SDK objects to plain dicts so the history is
        JSON-serializable (required by MemoryWindow and any future persistence).
        """
        result = {k: v for k, v in m.items() if k != "thinking"}
        tool_calls = result.get("tool_calls")
        if not tool_calls:
            result.pop("tool_calls", None)
        else:
            serialized = []
            for tc in tool_calls:
                if isinstance(tc, dict):
                    serialized.append(tc)
                else:
                    # Ollama SDK ToolCall object
                    fn = getattr(tc, "function", None) or {}
                    serialized.append({
                        "function": {
                            "name": getattr(fn, "name", "") if not isinstance(fn, dict) else fn.get("name", ""),
                            "arguments": getattr(fn, "arguments", {}) if not isinstance(fn, dict) else fn.get("arguments", {}),
                        }
                    })
            result["tool_calls"] = serialized
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
