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
from local.agents.base_agent import BaseAgent
from local.agents.critic_actions import CriticAction
from local.agents.critic_states import CriticState
from local.agents.critic_transitions import CriticStateMachine
from local.config_loader import get_config
from local.protocol.envelope import MessageEnvelope
from local.protocol.messages import CriticSkipped, CritiqueResult, ResponseGeneration
from local.protocol.subjects import CRITIC_SKIPPED, RESPONSE_GENERATION
from local.services.ollama_backend import OllamaBackend
from local.transport.bus_config import make_participant_bus

logger = logging.getLogger(__name__)


class CriticAgent(BaseAgent):
    """Post-generation quality observer.

    Grades every non-tool-call answer with an absolute score (1–5) via
    Prometheus. Never raises — publishes ``score=None`` on any Prometheus
    failure so downstream consumers (MemoryAgent, UI) can treat null as
    "not graded" and continue normally.
    """

    CONFIG_NAME = "critic"

    def __init__(self, llm: OllamaBackend | None = None, gatekeeper: OllamaBackend | None = None) -> None:
        """Initialize the CriticAgent.

        Config keys read from ``config/critic.yaml``: ``model``, ``rubric``,
        ``grade_prompt``, ``gatekeeper_model``, ``gatekeeper_prompt``,
        ``gatekeeper_timeout``, ``num_ctx``, ``temperature``, ``grade_timeout``.

        Args:
            llm: Injected for testing; defaults to Prometheus ``OllamaBackend`` from config.
            gatekeeper: Injected for testing; defaults to gatekeeper ``OllamaBackend`` from config.
        """
        cfg = get_config("critic")
        model: str = cfg["model"]
        self._rubric: str = cfg.get("rubric") or ""
        self._grade_prompt: str = (cfg.get("grade_prompt") or "").strip()
        self._gatekeeper_prompt: str = (cfg.get("gatekeeper_prompt") or "").strip()
        self._gatekeeper_skip_feedback: str = cfg["gatekeeper_skip_feedback"]
        self._options: dict = {
            "num_ctx": cfg["num_ctx"],
            "temperature": cfg["temperature"],
        }
        timeout: int = cfg["grade_timeout"]
        self._llm = llm or OllamaBackend(model=model, agent_name=self.id, timeout=timeout)
        self._gatekeeper = gatekeeper or OllamaBackend(
            model=cfg["gatekeeper_model"],
            agent_name=f"{self.id}.gatekeeper",
            timeout=cfg["gatekeeper_timeout"],
        )
        self._pub, self._sub = make_participant_bus([RESPONSE_GENERATION])
        self._sm = CriticStateMachine()

    def _dispatch(self, envelope: MessageEnvelope) -> None:
        if envelope.subject == RESPONSE_GENERATION:
            try:
                self._handle_generation(envelope)
            except Exception as exc:
                logger.error("CriticAgent: unhandled error: %s", exc, exc_info=True)
                if self._sm.state != CriticState.IDLE:
                    self._do_transition(CriticAction.FAIL)
                    self._do_transition(CriticAction.RESET)

    def _handle_generation(self, envelope: MessageEnvelope) -> None:
        msg = ResponseGeneration.from_envelope(envelope)
        correlation_id: str = envelope.correlation_id or msg.query_id

        if not msg.query or not msg.answer or msg.error:
            return

        self._do_transition(CriticAction.RECEIVE)

        # -- Gatekeeper: skip if response relies on live/retrieved data -------
        if self._is_live_data(msg.query, msg.answer):
            logger.debug("CriticAgent: gatekeeper skipped grade — live/retrieved data")
            self._do_transition(CriticAction.PUBLISH)
            self._pub.publish(
                CriticSkipped(
                    reason=self._gatekeeper_skip_feedback,
                    query=msg.query,
                    session_id=msg.session_id, query_id=msg.query_id,
                ),
                sender_id=self.id, correlation_id=correlation_id, session_id=msg.session_id,
            )
            self._do_transition(CriticAction.RESET)
            return

        # -- Absolute grade --------------------------------------------------
        self._do_transition(CriticAction.START_GRADE)

        score, feedback = self._grade(msg.query, msg.answer)

        if score is None:
            self._do_transition(CriticAction.FAIL)
        else:
            self._do_transition(CriticAction.PUBLISH)

        self._pub.publish(
            CritiqueResult(
                score=score, feedback=feedback,
                query=msg.query, answer=msg.answer,
                session_id=msg.session_id, query_id=msg.query_id,
            ),
            sender_id=self.id, correlation_id=correlation_id, session_id=msg.session_id,
        )

        self._do_transition(CriticAction.RESET)

    def _is_live_data(self, query: str, answer: str) -> bool:
        """Return True if the response relies on live or retrieved data.

        Uses a fast gatekeeper model (e2b) to classify whether the response
        makes claims based on data retrieved at query time rather than training
        knowledge. On any failure, returns False (fail open — let Prometheus grade).
        """
        if not self._gatekeeper_prompt:
            return False
        try:
            prompt = self._gatekeeper_prompt.format(query=query, answer=answer)
            text, _ = self._gatekeeper.chat(
                [{"role": "user", "content": prompt}],
                think=True,
            )
            return text.strip().upper().startswith("YES")
        except Exception as exc:
            logger.warning("CriticAgent: gatekeeper failed, proceeding to grade: %s", exc)
            return False

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


