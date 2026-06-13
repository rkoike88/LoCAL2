"""GeneratorAgent — core LLM agent for LoCAL2.

Subscribes to query.received and tool.schema. Maintains per-session conversation history.
Calls ollama.chat() directly (not via OllamaBackend) so response["message"]
is accessible for history appending and tool call inspection.

Tool schemas are populated dynamically as tools announce themselves on tool.schema.
Tool calls are dispatched synchronously via ToolDispatcher, which owns the bus I/O.
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
from local.protocol.messages import (
    AnswerDialog, CompactionResult, GenerationThinking,
    GeneratorStatus, ResponseGeneration, ToolSchemaRequest,
)
from local.protocol.subjects import (
    COMPACTION_REQUEST, QUERY_RECEIVED, TOOL_SCHEMA,
)
from local.services.conversation_service import ConversationService
from local.tools.tool_dispatcher import ToolDispatcher
from local.transport.bus_config import make_participant_bus

logger = logging.getLogger(__name__)


class GeneratorAgent(BaseAgent):
    """Core LLM agent for LoCAL2.

    Maintains per-session conversation history, dispatches tool calls over
    the bus, and streams thinking tokens to the UI.
    """

    CONFIG_NAME = "generator"

    def __init__(
        self,
        model: str | None = None,
        temperature: float | None = None,
        conversation_service=None,
        compaction_service=None,
        tool_dispatcher=None,
    ) -> None:
        """Initialize the GeneratorAgent.

        All parameters fall back to ``config/generator.yaml`` when ``None``.

        Args:
            model: Ollama model name (e.g. ``"gemma4:e2b"``).
            temperature: Sampling temperature (0.0 = deterministic).
            conversation_service: Injected for testing; defaults to a fresh
                ``ConversationService``.
            compaction_service: Injected for testing; handles history compaction.
            tool_dispatcher: Injected for testing; handles synchronous tool I/O.
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
        self._compaction_service = compaction_service
        self._tool_dispatcher = tool_dispatcher or ToolDispatcher(self._tool_timeout)
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
            self._pub.publish(
                ResponseGeneration(
                    query=query, answer=f"[generation error: {exc}]",
                    thinking="", tool_calls=[], session_id=session_id or "",
                    query_id=query_id, error=True,
                ),
                sender_id=self.id, correlation_id=correlation_id, session_id=session_id or "",
            )
            self._do_transition(GeneratorAction.RESET)
            return

        new_messages = [self._clean_for_history(m) for m in messages[initial_len - 1:]]
        self._conv.append_messages(session_id, new_messages)
        self._conv.set_token_count(session_id, prompt_tokens)
        self._token_count = prompt_tokens
        self._do_transition(GeneratorAction.PUBLISH)

        self._pub.publish(
            ResponseGeneration(
                query=query, answer=answer, thinking=thinking,
                tool_calls=tool_call_log, session_id=session_id or "",
                query_id=query_id, prompt_tokens=prompt_tokens,
            ),
            sender_id=self.id, correlation_id=correlation_id, session_id=session_id or "",
        )
        self._pub.publish(
            AnswerDialog(
                query=query, answer=answer,
                session_id=session_id or "", query_id=query_id,
            ),
            sender_id=self.id, correlation_id=correlation_id, session_id=session_id or "",
        )

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
                    self._pub.publish(
                        GenerationThinking(
                            chunk=chunk.message.thinking,
                            session_id=session_id or "", query_id=query_id,
                        ),
                        sender_id=self.id, correlation_id=correlation_id, session_id=session_id or "",
                    )
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
                self._do_transition(GeneratorAction.AWAIT_RESULT)
                result, timed_out = self._tool_dispatcher.execute(
                    name, args, correlation_id, self._tool_schemas
                )
                if timed_out:
                    self._do_transition(GeneratorAction.TOOL_TIMEOUT)
                else:
                    self._do_transition(GeneratorAction.TOOL_RESULT)
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
        """Gate compaction on IDLE state and delegate execution to CompactionService."""
        if self._sm.state != GeneratorState.IDLE:
            self._pub.publish(
                CompactionResult(
                    session_id=envelope.payload.get("session_id") or "",
                    error="generator busy — try again after current query finishes",
                ),
                sender_id=self.id, correlation_id=envelope.correlation_id or str(uuid.uuid4()),
            )
            return

        self._do_transition(GeneratorAction.START_COMPACTION)
        try:
            self._compaction_service.compact(envelope)
        finally:
            self._do_transition(GeneratorAction.COMPLETE_COMPACTION)

    def _after_transition(self) -> None:
        self._publish_status()

    def _publish_status(self) -> None:
        """Publish a full generator state snapshot on generator.status."""
        tool_names = [
            s.get("function", {}).get("name", "")
            for s in self._tool_schemas
            if s.get("function", {}).get("name")
        ]
        self._pub.publish(
            GeneratorStatus(
                instance_id=self._instance_id,
                model=self._model,
                temperature=self._options.get("temperature", 0.0),
                num_ctx=self._options.get("num_ctx", 0),
                state=self._sm.state.value,
                token_count=self._token_count,
                tool_names=tool_names,
                system_prompt=self._system_prompt,
            ),
            sender_id=self.id,
        )

    def _request_schemas(self) -> None:
        self._pub.publish(ToolSchemaRequest(), sender_id=self.id)

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

