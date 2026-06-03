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

from local.agents.critic_actions import CriticAction
from local.agents.critic_states import CriticState
from local.agents.critic_transitions import TRANSITIONS
from local.config_loader import get_config
from local.protocol.envelope import MessageEnvelope
from local.protocol.subjects import CRITIQUE, PAIRWISE_RESULT, RESPONSE_GENERATION
from local.services.ollama_backend import OllamaBackend
from local.transport.bus_config import make_participant_bus

logger = logging.getLogger(__name__)

_GRADE_PROMPT = """\
###Task Description:
An instruction (might include an Input inside it), a response to evaluate, and a \
score rubric representing evaluation criteria are given.
1. Write a detailed feedback that assesses the quality of the response strictly \
based on the given score rubric, not evaluating in general.
2. After writing a feedback, write a score that is an integer between 1 and 5. \
You should refer to the score rubric.
3. Output format: "Feedback: (write a feedback) [RESULT] (integer 1-5)"
4. Do not generate any other opening, closing, or explanations.

###The instruction to evaluate:
{query}

###Response to evaluate:
{answer}

###Score Rubrics:
{rubric}

###Feedback:"""

_PAIRWISE_PROMPT = """\
###Task Description:
Given an instruction and two responses (A and B), determine which response is better.
1. Compare the two responses on accuracy, helpfulness, and clarity.
2. Write a brief comparison, then declare the winner.
3. Output format: "Feedback: (write comparison) [RESULT] (A or B)"
4. Do not generate any other opening, closing, or explanations.

###The instruction to evaluate:
{query}

###Response A:
{answer_a}

###Response B:
{answer_b}

###Feedback:"""

_PAIRWISE_BUFFER_MAX = 100


class _StateMachine:
    def __init__(self) -> None:
        self._state = CriticState.IDLE

    @property
    def state(self) -> CriticState:
        return self._state

    def transition(self, action: CriticAction) -> None:
        key = (self._state, action)
        next_state = TRANSITIONS.get(key)
        if next_state is None:
            logger.warning("CriticAgent: invalid transition %s + %s", self._state, action)
            return
        self._state = next_state


class CriticAgent:
    AGENT_ID = "critic"

    def __init__(self, llm: OllamaBackend | None = None) -> None:
        cfg = get_config("critic")
        model: str = cfg.get("model", "prometheus:7b")
        self._rubric: str = cfg.get("rubric", "")
        self._options: dict = {
            "num_ctx": cfg.get("num_ctx", 4096),
            "temperature": cfg.get("temperature", 0.0),
        }
        timeout: int = cfg.get("grade_timeout", 30)
        self._llm = llm or OllamaBackend(model=model, agent_name=self.AGENT_ID, timeout=timeout)
        self._sm = _StateMachine()
        # correlation_id → {"A": entry, "B": entry}; evict oldest when > max
        self._pairwise_buffer: dict[str, dict] = {}
        self._pub, self._sub = make_participant_bus([RESPONSE_GENERATION])

    def run(self) -> None:
        print("[critic] ready")
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
                        self._sm.transition(CriticAction.FAIL)
                        self._sm.transition(CriticAction.RESET)

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
        self._sm.transition(CriticAction.RECEIVE)
        self._sm.transition(CriticAction.START_GRADE)

        score, feedback = self._grade(query, answer)

        if score is None:
            self._sm.transition(CriticAction.FAIL)
        else:
            self._sm.transition(CriticAction.PUBLISH)

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

        self._sm.transition(CriticAction.RESET)

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
        if correlation_id not in self._pairwise_buffer:
            if len(self._pairwise_buffer) >= _PAIRWISE_BUFFER_MAX:
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

        self._sm.transition(CriticAction.START_PAIRWISE)
        winner = self._grade_pairwise(a["query"], a["answer"], b["answer"])

        if winner:
            self._sm.transition(CriticAction.PUBLISH)
        else:
            self._sm.transition(CriticAction.FAIL)

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

        self._sm.transition(CriticAction.RESET)

    def _grade(self, query: str, answer: str) -> tuple[int | None, str]:
        """Call Prometheus absolute grading. Returns (score_or_None, feedback_text)."""
        prompt = _GRADE_PROMPT.format(query=query, answer=answer, rubric=self._rubric)
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

    def _grade_pairwise(self, query: str, answer_a: str, answer_b: str) -> str | None:
        """Call Prometheus pairwise comparison. Returns 'A', 'B', or None on failure."""
        prompt = _PAIRWISE_PROMPT.format(query=query, answer_a=answer_a, answer_b=answer_b)
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
