"""ModelService — meta-model operations: auto-compaction decision + execution.

Subscribes to:
  response.generation  — watches prompt_tokens; auto-triggers compaction.request
                         when threshold is crossed
  compaction.request   — executes compaction: summarises history and replaces it

Reads model/params fresh from config/generator.yaml on each compaction so that
model changes (via config.reload) take effect without a restart.
"""
from __future__ import annotations

import logging
import uuid

import ollama

from local.config_loader import get_config
from local.participants.base_service import BaseService
from local.protocol.envelope import MessageEnvelope
from local.protocol.messages import CompactionRequest as CompactionRequestMsg, CompactionResult
from local.protocol.subjects import (
    COMPACTION_REQUEST,
    RESPONSE_GENERATION,
)
from local.services.conversation_service import ConversationService
from local.transport.bus_config import make_participant_bus

logger = logging.getLogger(__name__)


class ModelService(BaseService):
    """Standalone bus participant for meta-model operations.

    Handles the auto-compaction decision (from response.generation token counts)
    and compaction execution (from compaction.request). Both operations read
    config fresh so model/parameter changes take effect on the next call.
    """

    CONFIG_NAME = "compaction"

    def __init__(self, conversation_service: ConversationService) -> None:
        self._conv = conversation_service
        self._pub, self._sub = make_participant_bus([RESPONSE_GENERATION, COMPACTION_REQUEST])

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    def _handle(self, envelope: MessageEnvelope) -> None:
        if envelope.subject == RESPONSE_GENERATION:
            self._maybe_request_compaction(envelope)
        elif envelope.subject == COMPACTION_REQUEST:
            self._compact(envelope)

    # ------------------------------------------------------------------
    # Auto-compaction decision
    # ------------------------------------------------------------------

    def _maybe_request_compaction(self, envelope: MessageEnvelope) -> None:
        cfg = get_config("generator") or {}
        threshold = cfg.get("compaction_threshold", 0.8)
        if not threshold:
            return

        num_ctx = cfg.get("num_ctx", 32000)
        prompt_tokens: int = envelope.payload.get("prompt_tokens", 0)
        session_id: str | None = envelope.payload.get("session_id")

        if prompt_tokens >= threshold * num_ctx:
            logger.info(
                "ModelService: auto-compacting session %s (%d / %d tokens, threshold %.0f%%)",
                (session_id or "")[:8], prompt_tokens, num_ctx, threshold * 100,
            )
            self._pub.publish(
                CompactionRequestMsg(session_id=session_id or "", auto=True),
                sender_id=self.id,
            )

    # ------------------------------------------------------------------
    # Compaction execution
    # ------------------------------------------------------------------

    def _compact(self, envelope: MessageEnvelope) -> None:
        """Summarise a session's history and replace it with summary + tail turns.

        Reads model and options fresh from config so that a model switch
        (via PUT /api/settings/generator) takes effect on the next compaction.
        """
        session_id: str | None = envelope.payload.get("session_id")
        corr_id = envelope.correlation_id or str(uuid.uuid4())

        cfg = get_config("generator") or {}
        model: str = cfg["model"]
        options = {
            "num_ctx": cfg["num_ctx"],
            "temperature": cfg["temperature"],
        }
        tail_turns: int = cfg.get("compaction_tail_turns", 4)

        history = self._conv.get_history(session_id)
        tokens_before = self._conv.get_token_count(session_id)

        if not history:
            self._pub.publish(
                CompactionResult(session_id=session_id or "", error="no history to compact"),
                sender_id=self.id,
                correlation_id=corr_id,
            )
            return

        convo_text = []
        for m in history:
            role = m.get("role", "")
            content = m.get("content") or ""
            if role in ("user", "assistant") and content:
                convo_text.append(f"{role.upper()}: {content}")
        summary_input = "\n\n".join(convo_text)

        compaction_system = (cfg.get("compaction_system_prompt") or
                             "Summarize this conversation concisely, preserving key facts and decisions.").strip()

        resp = ollama.chat(
            model=model,
            messages=[
                {"role": "system", "content": compaction_system},
                {"role": "user", "content": summary_input},
            ],
            stream=False,
            options=options,
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

        self._pub.publish(
            CompactionResult(
                session_id=session_id or "",
                tokens_before=tokens_before,
                tokens_after=tokens_estimated_after,
                summary=summary_text,
            ),
            sender_id=self.id,
            correlation_id=corr_id,
        )
        logger.info(
            "ModelService: compacted session %s — %d → ~%d tokens",
            (session_id or "")[:8], tokens_before, tokens_estimated_after,
        )
