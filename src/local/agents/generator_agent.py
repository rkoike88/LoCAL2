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
from local.agents.ollama_types import OllamaToolCall, clean_for_history, make_assistant_msg, make_tool_result_msg
from local.config_loader import get_config
from local.protocol.envelope import MessageEnvelope
from local.protocol.messages import (
    AnswerDialog, GenerationThinking,
    GeneratorStatus, MemoryContext, ResponseGeneration, ToolSchemaRequest,
    UserContext, UserContextRequest, UserContextUpdated,
)
from local.protocol.subjects import (
    CONFIG_RELOAD, MEMORY_CONTEXT, TOOL_SCHEMA, USER_CONTEXT, USER_CONTEXT_UPDATED,
)
from local.protocol.types import Attachment
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
        tool_dispatcher=None,
    ) -> None:
        """Initialize the GeneratorAgent.

        All parameters fall back to ``config/generator.yaml`` when ``None``.

        Args:
            model: Ollama model name (e.g. ``"gemma4:e2b"``).
            temperature: Sampling temperature (0.0 = deterministic).
            conversation_service: Injected for testing; defaults to a fresh
                ``ConversationService``.
            tool_dispatcher: Injected for testing; handles synchronous tool I/O.
        """
        cfg = get_config("generator")
        sys_cfg = get_config("system") or {}
        self._models: dict = cfg.get("models") or {"default": cfg.get("model", "")}
        self._model: str = model or self._models.get("default", "")
        self._active_model: str = self._model
        self._options: dict = {
            "num_ctx": cfg["num_ctx"],
            "temperature": temperature if temperature is not None else cfg["temperature"],
        }
        self._system_prompt: str = cfg.get("system_prompt") or ""
        self._max_tool_iters: int = cfg["max_tool_iterations"]
        self._tool_timeout: float = cfg["tool_timeout"]
        self._tool_schemas: list = cfg.get("tools") or []
        self._instance_id: str = sys_cfg.get("instance_id") or socket.gethostname()
        self._token_count: int = 0
        self._active_persona: dict[str, str] = {}  # session_id -> active persona name
        self._user_context: list[dict] = []        # [{fact, reason}] — bootstrapped from pinned store
        self._conv = conversation_service or ConversationService()
        self._tool_dispatcher = tool_dispatcher or ToolDispatcher(self._tool_timeout)
        self._sm = GeneratorStateMachine()
        self._pub, self._sub = make_participant_bus([MEMORY_CONTEXT, TOOL_SCHEMA, CONFIG_RELOAD, USER_CONTEXT, USER_CONTEXT_UPDATED])

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self) -> None:
        logger.info(
            "generator model=%s  num_ctx=%s  temperature=%s",
            self._model, self._options["num_ctx"], self._options["temperature"],
        )
        self._request_schemas()
        self._pub.publish(UserContextRequest(), sender_id=self.id)
        time.sleep(0.5)  # startup window: let tool schema and user.context responses queue up
        self._publish_status()
        while True:
            try:
                envelope = self._sub.receive()
            except Exception as exc:
                logger.error("GeneratorAgent: receive error: %s", exc)
                continue
            self._dispatch(envelope)

    def _dispatch(self, envelope: MessageEnvelope) -> None:
        if envelope.subject == MEMORY_CONTEXT:
            try:
                self._handle_query(envelope)
            except Exception as exc:
                logger.error("GeneratorAgent: unhandled error: %s", exc, exc_info=True)
                if self._sm.state != GeneratorState.IDLE:
                    self._sm.reset()
        elif envelope.subject == USER_CONTEXT:
            msg = UserContext.from_envelope(envelope)
            self._user_context = msg.facts
            logger.info("GeneratorAgent: bootstrapped %d pinned facts", len(self._user_context))
        elif envelope.subject == USER_CONTEXT_UPDATED:
            msg = UserContextUpdated.from_envelope(envelope)
            self._user_context.append({"fact": msg.fact, "reason": msg.reason})
            logger.info("GeneratorAgent: added pinned fact %r", msg.fact[:60])
        elif envelope.subject == TOOL_SCHEMA:
            self._register_tool_schema(envelope.payload.get("schema", {}))
        elif envelope.subject == CONFIG_RELOAD:
            self._handle_config_reload()

    # ------------------------------------------------------------------
    # Query handling
    # ------------------------------------------------------------------

    def _handle_query(self, envelope: MessageEnvelope) -> None:
        msg = MemoryContext.from_envelope(envelope)
        query: str = msg.query
        session_id: str | None = msg.session_id or None
        original_query_id: str = msg.query_id or str(uuid.uuid4())
        attachments: list = msg.attachments
        capsules: list = msg.capsules

        query_id = original_query_id
        correlation_id = envelope.correlation_id or query_id

        has_images = any(a.get("type") == "image" for a in attachments if isinstance(a, dict))
        active_model = self._models.get("vision", self._model) if has_images else self._model
        self._active_model = active_model

        self._do_transition(GeneratorAction.RECEIVE)

        self._conv.write_context_biscuit(query_id, {
            "capsules": capsules,
            "pinned_facts": list(self._user_context),
            "persona": self._active_persona.get(session_id or ""),
        }, session_id=session_id or "")
        messages = self._build_messages(query, session_id, attachments, capsules=capsules)
        initial_len = len(messages)
        self._do_transition(GeneratorAction.START_GENERATION)

        try:
            answer, thinking, tool_call_log, prompt_tokens = self._generate(
                messages, correlation_id, session_id, query_id, model=active_model
            )
        except Exception as exc:
            logger.error("GeneratorAgent: generation failed: %s", exc, exc_info=True)
            self._do_transition(GeneratorAction.FAIL)
            self._pub.publish(
                ResponseGeneration(
                    query=query, answer=f"[generation error: {exc}]",
                    thinking="", tool_calls=[], session_id=session_id or "",
                    query_id=query_id, error=True, model=active_model,
                ),
                sender_id=self.id, correlation_id=correlation_id, session_id=session_id or "",
            )
            self._do_transition(GeneratorAction.RESET)
            return

        for tc in tool_call_log:
            if tc.get("tool") == "persona":
                name = (tc.get("args") or {}).get("name", "")
                if name and session_id:
                    self._active_persona[session_id] = name

        new_messages = [clean_for_history(m) for m in messages[initial_len - 1:]]
        self._conv.append_messages(session_id, new_messages)
        self._conv.set_token_count(session_id, prompt_tokens)
        self._token_count = prompt_tokens
        self._do_transition(GeneratorAction.PUBLISH)

        self._pub.publish(
            ResponseGeneration(
                query=query, answer=answer, thinking=thinking,
                tool_calls=tool_call_log, session_id=session_id or "",
                query_id=query_id, prompt_tokens=prompt_tokens, model=active_model,
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

        self._active_model = self._model
        self._do_transition(GeneratorAction.RESET)

    def _build_messages(
        self,
        query: str,
        session_id: str | None,
        attachments: list[dict] | None = None,
        capsules: list | None = None,
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
        active_persona = self._active_persona.get(session_id or "")
        persona_clause = f" Your current persona is {active_persona}." if active_persona else ""
        system_text = self._system_prompt.replace("{persona_clause}", persona_clause)
        if self._user_context:
            facts_text = "\n".join(f"- {f['fact']}" for f in self._user_context)
            system_text += f"\n\n[Things to always remember about the user:]\n{facts_text}"
        if capsules:
            summaries = "\n".join(f"- {c['content']}" for c in capsules)
            system_text += f"\n\n[Relevant prior sessions:]\n{summaries}"
        if system_text:
            messages.append({"role": "system", "content": system_text})
        messages.extend(history)

        max_chars: int = get_config("generator")["max_attachment_chars"]
        content_parts: list[str] = []
        image_b64s: list[str] = []
        for att in (attachments or []):
            a = Attachment.from_dict(att) if isinstance(att, dict) else att
            if a.type == "text":
                content_parts.append(f'[Attached: {a.name}]\n{a.data[:max_chars]}')
            elif a.type == "image":
                image_b64s.append(a.data)
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
        model: str = "",
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
                model=model or self._model,
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

            raw_msg = make_assistant_msg(iter_content, iter_thinking or None, iter_tool_calls)
            messages.append(raw_msg)

            tool_calls: list = iter_tool_calls or []
            if not tool_calls:
                break

            for tc in tool_calls:
                call = OllamaToolCall.from_any(tc)
                self._do_transition(GeneratorAction.DISPATCH_TOOL)
                self._do_transition(GeneratorAction.AWAIT_RESULT)
                result, timed_out = self._tool_dispatcher.execute(
                    call.name, call.arguments, correlation_id, self._tool_schemas
                )
                if timed_out:
                    self._do_transition(GeneratorAction.TOOL_TIMEOUT)
                else:
                    self._do_transition(GeneratorAction.TOOL_RESULT)
                tool_call_log.append({"tool": call.name, "args": call.arguments, "result": str(result)})
                messages.append(make_tool_result_msg(call.name, str(result)))

        answer = (raw_msg.get("content") or "").strip()
        thinking = accumulated_thinking.strip()
        if not answer and tool_call_log:
            logger.warning(
                "GeneratorAgent: max_tool_iterations (%d) exhausted without a final text answer",
                self._max_tool_iters,
            )
        return answer, thinking, tool_call_log, prompt_tokens

    def _handle_config_reload(self) -> None:
        """Re-read model and params from config — takes effect on the next generation."""
        cfg = get_config("generator")
        self._models = cfg.get("models") or {"default": cfg.get("model", "")}
        self._model = self._models.get("default", "")
        self._active_model = self._model
        self._options["num_ctx"] = cfg["num_ctx"]
        self._options["temperature"] = cfg["temperature"]
        self._system_prompt = cfg.get("system_prompt") or ""
        logger.info("GeneratorAgent: config reloaded — model=%s", self._model)
        self._publish_status()

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
                model=self._active_model,
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


