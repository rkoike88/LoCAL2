"""CriticAgent — post-generation quality observer.

Subscribes to response.generation. For each answer, calls Prometheus via
OllamaBackend to produce an absolute quality score (1–5) and a feedback
string. Publishes critique.result.

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
from local.protocol.subjects import CRITIQUE, RESPONSE_GENERATION
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

        if not query or not answer or payload.get("error"):
            return

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
            },
            correlation_id=envelope.correlation_id or query_id,
            metadata={"session_id": session_id},
        ))

        self._sm.transition(CriticAction.RESET)

    def _grade(self, query: str, answer: str) -> tuple[int | None, str]:
        """Call Prometheus and parse score. Returns (score_or_None, feedback_text)."""
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
