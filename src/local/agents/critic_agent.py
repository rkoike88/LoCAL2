"""CriticAgent — post-generation quality observer.

Subscribes to response.generation. For each answer, resolves the evaluation
rubric from the tool_calls in the response (highest-priority tool wins), then
calls Prometheus to produce an absolute quality score (1–5) and a feedback
string. Publishes critique.result with the rubric_name used.

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
from local.protocol.messages import CritiqueResult, ResponseGeneration, ToolSchema, ToolSchemaRequest
from local.protocol.subjects import RESPONSE_GENERATION, TOOL_SCHEMA, TOOL_SCHEMA_REQUEST
from local.services.ollama_backend import OllamaBackend
from local.transport.bus_config import make_participant_bus

logger = logging.getLogger(__name__)


class CriticAgent(BaseAgent):
    """Post-generation quality observer.

    Grades every non-tool-call answer with an absolute score (1–5) via
    Prometheus. Rubric is selected from a live registry built from tool.schema
    announcements — the highest-priority tool called in the response wins.
    Falls back to the default rubric when no tool was called or no tool has
    declared a rubric. Never raises — publishes score=None on any Prometheus
    failure.
    """

    CONFIG_NAME = "critic"

    def __init__(self, llm: OllamaBackend | None = None) -> None:
        """Initialize the CriticAgent.

        Config keys read from ``config/critic.yaml``: ``model``, ``rubric``,
        ``style_rubric``, ``clarity_rubric``, ``grade_prompt``, ``num_ctx``,
        ``temperature``, ``grade_timeout``.

        Args:
            llm: Injected for testing; defaults to Prometheus ``OllamaBackend`` from config.
        """
        cfg = get_config("critic")
        model: str = cfg["model"]
        self._rubrics: dict[str, str] = {
            "realistic":    (cfg.get("rubric") or "").strip(),
            "style":        (cfg.get("style_rubric") or "").strip(),
            "clarity":      (cfg.get("clarity_rubric") or "").strip(),
            "empathic":     (cfg.get("empathic_rubric") or "").strip(),
            "interpretive": (cfg.get("interpretive_rubric") or "").strip(),
            "creative":     (cfg.get("creative_rubric") or "").strip(),
        }
        self._persona_rubric_map: dict[str, str] = cfg.get("persona_rubric_map") or {}
        self._grade_prompt: str = (cfg.get("grade_prompt") or "").strip()
        self._options: dict = {
            "num_ctx": cfg["num_ctx"],
            "temperature": cfg["temperature"],
            "top_p": cfg.get("top_p", 0.95),
            "top_k": cfg.get("top_k", 64),
        }
        timeout: int = cfg["grade_timeout"]
        self._llm = llm or OllamaBackend(model=model, agent_name=self.id, timeout=timeout)
        self._rubric_registry: dict[str, dict] = {}  # tool_name → {rubric_name, priority}
        self._pub, self._sub = make_participant_bus([RESPONSE_GENERATION, TOOL_SCHEMA, TOOL_SCHEMA_REQUEST])
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
        elif envelope.subject == TOOL_SCHEMA:
            self._handle_schema(envelope)
        elif envelope.subject == TOOL_SCHEMA_REQUEST:
            pass  # we're a consumer, not a tool — nothing to re-announce

    def _handle_schema(self, envelope: MessageEnvelope) -> None:
        msg = ToolSchema.from_envelope(envelope)
        tool_name: str = (msg.schema.get("function") or {}).get("name") or ""
        if not tool_name or not msg.critique_rubric_name:
            return
        self._rubric_registry[tool_name] = {
            "rubric_name": msg.critique_rubric_name,
            "priority":    msg.critique_priority,
        }
        logger.debug("CriticAgent: registered rubric %r for tool %r (priority %d)",
                     msg.critique_rubric_name, tool_name, msg.critique_priority)

    def _resolve_rubric(self, tool_calls: list) -> tuple[str, str]:
        """Return (rubric_text, rubric_name) for the given tool_calls list.

        Priority: tool-declared rubric (highest priority wins) → persona rubric map → realistic.
        """
        best: dict | None = None
        persona_mode: str | None = None
        for tc in tool_calls:
            name = tc.get("tool") or ""
            if name == "persona":
                args = tc.get("args", {})
                persona_mode = args.get("name") or args.get("mode")
            entry = self._rubric_registry.get(name)
            if entry and (best is None or entry["priority"] > best["priority"]):
                best = entry
        if best:
            rubric_name = best["rubric_name"]
        elif persona_mode and persona_mode in self._persona_rubric_map:
            rubric_name = self._persona_rubric_map[persona_mode]
        else:
            rubric_name = "realistic"
        rubric_text = self._rubrics.get(rubric_name) or self._rubrics["realistic"]
        return rubric_text, rubric_name

    def _handle_generation(self, envelope: MessageEnvelope) -> None:
        msg = ResponseGeneration.from_envelope(envelope)
        correlation_id: str = envelope.correlation_id or msg.query_id

        if not msg.query or not msg.answer or msg.error:
            return

        self._do_transition(CriticAction.RECEIVE)
        self._do_transition(CriticAction.START_GRADE)

        rubric_text, rubric_name = self._resolve_rubric(msg.tool_calls)
        score, feedback = self._grade(msg.query, msg.answer, rubric_text)

        if score is None:
            self._do_transition(CriticAction.FAIL)
        else:
            self._do_transition(CriticAction.PUBLISH)

        self._pub.publish(
            CritiqueResult(
                score=score, feedback=feedback,
                query=msg.query, answer=msg.answer,
                session_id=msg.session_id, query_id=msg.query_id,
                rubric_name=rubric_name, rubric_text=rubric_text,
            ),
            sender_id=self.id, correlation_id=correlation_id, session_id=msg.session_id,
        )

        self._do_transition(CriticAction.RESET)

    def _grade(self, query: str, answer: str, rubric: str) -> tuple[int | None, str]:
        """Call Prometheus absolute grading. Returns (score_or_None, feedback_text)."""
        prompt = self._grade_prompt.format(query=query, answer=answer, rubric=rubric)
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

    def run(self) -> None:
        # Broadcast tool.schema.request so tools re-announce their schemas to us
        self._pub.publish(ToolSchemaRequest(), sender_id=self.id)
        super().run()
