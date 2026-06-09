"""CriticAgent — post-generation quality observer.

Subscribes to response.generation. For each answer, calls Prometheus via
OllamaBackend to produce an absolute quality score (1–5) and a feedback
string. Publishes critique.result.

When both RespondentA and RespondentB answers arrive (keyed by correlation_id),
also runs a Prometheus pairwise comparison and publishes pairwise.result.

Never blocks or raises: on Prometheus failure or score parse failure,
publishes critique.result with score=None so downstream consumers
(MemoryAgent, UI) can treat null as "not graded" and continue normally.
"""
from __future__ import annotations

import logging
import re
import uuid

from local.agents.critic_actions import CriticAction
from local.agents.critic_states import CriticState
from local.agents.critic_transitions import CriticStateMachine
from local.config_loader import get_config
from local.protocol.envelope import MessageEnvelope
from local.protocol.subjects import AGENT_TRANSITION, CRITIQUE, PAIRWISE_RESULT, RESPONSE_GENERATION
from local.services.ollama_backend import OllamaBackend
from local.transport.bus_config import make_participant_bus

logger = logging.getLogger(__name__)


class CriticAgent:
    """Post-generation quality observer.

    Grades every non-tool-call answer with an absolute score (1–5) via
    Prometheus. When both RespondentA and RespondentB answers arrive for the
    same ``correlation_id``, also runs a pairwise comparison. Never raises —
    publishes ``score=None`` on any Prometheus failure so downstream consumers
    (MemoryAgent, UI) can treat null as "not graded" and continue normally.
    """

    AGENT_ID = "critic"

    def __init__(self, llm: OllamaBackend | None = None) -> None:
        """Initialize the CriticAgent.

        Config keys read from ``config/critic.yaml``: ``model``, ``rubric``,
        ``grade_prompt``, ``pairwise_prompt``, ``pairwise_buffer_max``,
        ``num_ctx``, ``temperature``, ``grade_timeout``.

        Args:
            llm: Injected for testing; defaults to an ``OllamaBackend`` built
                from config.
        """
        cfg = get_config("critic")
        model: str = cfg.get("model", "prometheus:7b")
        self._rubric: str = cfg.get("rubric", "")
        self._grade_prompt: str = cfg.get("grade_prompt", "").strip()
        self._pairwise_prompt: str = cfg.get("pairwise_prompt", "").strip()
        self._pairwise_buffer_max: int = cfg.get("pairwise_buffer_max", 100)
        self._options: dict = {
            "num_ctx": cfg.get("num_ctx", 4096),
            "temperature": cfg.get("temperature", 0.0),
        }
        timeout: int = cfg.get("grade_timeout", 30)
        self._llm = llm or OllamaBackend(model=model, agent_name=self.AGENT_ID, timeout=timeout)
        # correlation_id → {"A": entry, "B": entry}; evict oldest when > max
        self._pairwise_buffer: dict[str, dict] = {}
        self._pub, self._sub = make_participant_bus([RESPONSE_GENERATION])
        self._sm = CriticStateMachine()

    def run(self) -> None:
        logger.info("critic ready")
        while True:
            try:
                envelope = self._sub.receive()
            except Exception as exc:
                logger.error("CriticAgent: receive error: %s", exc)
                continue
            if envelope.subject == RESPONSE_GENERATION:
                try:
                    self._handle_generation(envelope)
                except Exception as exc:
                    logger.error("CriticAgent: unhandled error: %s", exc, exc_info=True)
                    if self._sm.state != CriticState.IDLE:
                        self._do_transition(CriticAction.FAIL)
                        self._do_transition(CriticAction.RESET)

    def _handle_generation(self, envelope: MessageEnvelope) -> None:
        payload = envelope.payload
        query: str = payload.get("query", "").strip()
        answer: str = payload.get("answer", "").strip()
        session_id: str = payload.get("session_id") or ""
        query_id: str = payload.get("query_id") or ""
        respondent_id: str = payload.get("respondent_id", "A")
        correlation_id: str = envelope.correlation_id or query_id

        if not query or not answer or payload.get("error"):
            return

        if payload.get("tool_calls"):
            logger.debug("CriticAgent: skipping grade — tool calls present")
            return

        # -- Absolute grade --------------------------------------------------
        self._do_transition(CriticAction.RECEIVE)
        self._do_transition(CriticAction.START_GRADE)

        score, feedback = self._grade(query, answer)

        if score is None:
            self._do_transition(CriticAction.FAIL)
        else:
            self._do_transition(CriticAction.PUBLISH)

        self._pub.publish(MessageEnvelope.create(
            message_type="critique",
            subject=CRITIQUE,
            sender_id=self.AGENT_ID,
            payload={
                "score": score,
                "feedback": feedback,
                "query": query,
                "answer": answer,
                "session_id": session_id,
                "query_id": query_id,
                "respondent_id": respondent_id,
            },
            correlation_id=correlation_id,
            metadata={"session_id": session_id},
        ))

        self._do_transition(CriticAction.RESET)

        # -- Pairwise buffer -------------------------------------------------
        self._buffer_for_pairwise(correlation_id, respondent_id, query_id, query, answer, score)
        if self._is_pairwise_ready(correlation_id):
            self._run_pairwise(correlation_id, session_id)

    def _buffer_for_pairwise(
        self,
        correlation_id: str,
        respondent_id: str,
        query_id: str,
        query: str,
        answer: str,
        score: int | None,
    ) -> None:
        """Stage a graded response in the pairwise buffer.

        The buffer accumulates A and B respondent answers keyed by
        ``correlation_id``. When the buffer reaches ``pairwise_buffer_max``
        entries the oldest is evicted (FIFO) to prevent unbounded growth.
        ``_is_pairwise_ready`` checks when both sides have arrived.
        """
        if correlation_id not in self._pairwise_buffer:
            if len(self._pairwise_buffer) >= self._pairwise_buffer_max:
                oldest_key = next(iter(self._pairwise_buffer))
                del self._pairwise_buffer[oldest_key]
            self._pairwise_buffer[correlation_id] = {}
        self._pairwise_buffer[correlation_id][respondent_id] = {
            "query_id": query_id,
            "query": query,
            "answer": answer,
            "score": score,
        }

    def _is_pairwise_ready(self, correlation_id: str) -> bool:
        entry = self._pairwise_buffer.get(correlation_id, {})
        return "A" in entry and "B" in entry

    def _run_pairwise(self, correlation_id: str, session_id: str) -> None:
        entry = self._pairwise_buffer.pop(correlation_id)
        a = entry["A"]
        b = entry["B"]

        self._do_transition(CriticAction.START_PAIRWISE)
        winner = self._grade_pairwise(a["query"], a["answer"], b["answer"])

        if winner:
            self._do_transition(CriticAction.PUBLISH)
        else:
            self._do_transition(CriticAction.FAIL)

        self._pub.publish(MessageEnvelope.create(
            message_type="pairwise",
            subject=PAIRWISE_RESULT,
            sender_id=self.AGENT_ID,
            payload={
                "query_id_a": a["query_id"],
                "query_id_b": b["query_id"],
                "winner": winner,
                "session_id": session_id,
            },
            correlation_id=correlation_id,
            metadata={"session_id": session_id},
        ))

        self._do_transition(CriticAction.RESET)

    def _grade(self, query: str, answer: str) -> tuple[int | None, str]:
        """Call Prometheus absolute grading. Returns (score_or_None, feedback_text)."""
        prompt = self._grade_prompt.format(query=query, answer=answer, rubric=self._rubric)
        text, _ = self._llm.chat(
            [{"role": "user", "content": prompt}],
            options=self._options,
        )
        if not text:
            logger.warning("CriticAgent: Prometheus returned empty response")
            return None, ""

        m = re.search(r'\[RESULT\]\s*([1-5])', text)
        score = int(m.group(1)) if m else None
        if score is None:
            logger.warning("CriticAgent: could not parse score from: %r", text[:120])

        feedback = re.sub(r'\s*\[RESULT\].*$', '', text, flags=re.DOTALL).strip()
        if feedback.lower().startswith("feedback:"):
            feedback = feedback[len("feedback:"):].strip()

        return score, feedback

    def _do_transition(self, action: CriticAction) -> None:
        """Execute a state machine transition and publish ``AGENT_TRANSITION``.

        The publish is wrapped in try/except — transition logging must never
        propagate and interrupt grading.
        """
        from_state = self._sm.state
        to_state = self._sm.transition(action)
        try:
            self._pub.publish(MessageEnvelope.create(
                message_type="agent_transition",
                subject=AGENT_TRANSITION,
                sender_id=self.AGENT_ID,
                payload={
                    "agent": self.AGENT_ID,
                    "from": from_state.value,
                    "action": action.value,
                    "to": to_state.value,
                },
                correlation_id=str(uuid.uuid4()),
            ))
        except Exception:
            pass  # never let transition logging break the agent

    def _grade_pairwise(self, query: str, answer_a: str, answer_b: str) -> str | None:
        """Call Prometheus pairwise comparison. Returns 'A', 'B', or None on failure."""
        prompt = self._pairwise_prompt.format(query=query, answer_a=answer_a, answer_b=answer_b)
        text, _ = self._llm.chat(
            [{"role": "user", "content": prompt}],
            options=self._options,
        )
        if not text:
            logger.warning("CriticAgent: pairwise Prometheus returned empty response")
            return None

        m = re.search(r'\[RESULT\]\s*([AB])', text, re.IGNORECASE)
        if not m:
            logger.warning("CriticAgent: could not parse pairwise winner from: %r", text[:120])
            return None
        return m.group(1).upper()
