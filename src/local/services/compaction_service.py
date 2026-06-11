"""CompactionService — auto-compaction decision and execution.

Bus listener: subscribes to response.generation, publishes compaction.request
when prompt_tokens crosses the configured threshold.

Executor: compact() is called synchronously by GeneratorAgent._handle_compaction()
while the generator is in COMPACTING state. Runs in the generator's thread,
serialized with _handle_query().
"""
from __future__ import annotations

import logging
import uuid

from local.config_loader import get_config
from local.participants.base_service import BaseService
from local.protocol.envelope import MessageEnvelope
from local.protocol.subjects import (
    COMPACTION_REQUEST,
    COMPACTION_RESULT,
    RESPONSE_GENERATION,
)
from local.services.conversation_service import ConversationService
from local.transport.bus_config import make_participant_bus

logger = logging.getLogger(__name__)


class CompactionService(BaseService):
    """Auto-compaction decision and execution for GeneratorAgent."""

    CONFIG_NAME = "compaction"

    def __init__(
        self,
        conversation_service: ConversationService,
        model: str,
        options: dict,
    ) -> None:
        self._conv = conversation_service
        self._model = model
        self._options = options
        self._pub, self._sub = make_participant_bus([RESPONSE_GENERATION])

    # ------------------------------------------------------------------
    # Bus listener — decision
    # ------------------------------------------------------------------

    def _handle(self, envelope: MessageEnvelope) -> None:
        cfg = get_config("generator") or {}
        threshold = cfg.get("compaction_threshold", 0.8)
        if not threshold:
            return

        num_ctx = cfg.get("num_ctx", 32000)
        prompt_tokens: int = envelope.payload.get("prompt_tokens", 0)
        session_id: str | None = envelope.payload.get("session_id")

        if prompt_tokens >= threshold * num_ctx:
            logger.info(
                "CompactionService: auto-compacting session %s (%d / %d tokens, threshold %.0f%%)",
                (session_id or "")[:8], prompt_tokens, num_ctx, threshold * 100,
            )
            self._pub.publish(MessageEnvelope.create(
                message_type="compaction_request",
                subject=COMPACTION_REQUEST,
                sender_id=self.id,
                payload={"session_id": session_id, "auto": True},
            ))

    # ------------------------------------------------------------------
    # Executor — called by GeneratorAgent under its IDLE gate
    # ------------------------------------------------------------------

    def compact(self, envelope: MessageEnvelope, publisher, make_envelope) -> None:
        """Summarize a session's history and replace it with a summary + tail turns.

        Runs in the generator's thread while the generator is in COMPACTING state.
        Reads config fresh on each call so threshold/prompt changes take effect
        without restart.

        Args:
            envelope: The compaction.request envelope; must contain session_id.
            publisher: GeneratorAgent's ZmqPublisher for compaction.result.
            make_envelope: GeneratorAgent._make_envelope bound method.
        """
        import ollama

        session_id: str | None = envelope.payload.get("session_id")
        cfg = get_config("generator") or {}
        tail_turns: int = cfg.get("compaction_tail_turns", 4)

        history = self._conv.get_history(session_id)
        tokens_before = self._conv.get_token_count(session_id)

        if not history:
            publisher.publish(make_envelope(
                COMPACTION_RESULT, "compaction",
                {"error": "no history to compact", "session_id": session_id},
                envelope.correlation_id or str(uuid.uuid4()), None,
            ))
            return

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
        total_chars = sum(len(m.get("content") or "") for m in new_messages)
        tokens_estimated_after = total_chars // 4

        self._conv.replace_messages(session_id, new_messages)
        self._conv.set_token_count(session_id, tokens_estimated_after)

        publisher.publish(make_envelope(
            COMPACTION_RESULT, "compaction",
            {"session_id": session_id,
             "tokens_before": tokens_before,
             "tokens_after": tokens_estimated_after,
             "summary": summary_text},
            envelope.correlation_id or str(uuid.uuid4()), None,
        ))
        logger.info(
            "CompactionService: compacted session %s — %d → ~%d tokens",
            (session_id or "")[:8], tokens_before, tokens_estimated_after,
        )
