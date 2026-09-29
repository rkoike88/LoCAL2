"""CriticAgent — post-generation quality observer.

Subscribes to response.generation. For each answer, resolves the evaluation
rubric from the tool_calls in the response (highest-priority tool wins), then
calls Prometheus to produce an absolute quality score (1–5) and a feedback
string. Publishes critique.result with the rubric_name used.

Never blocks or raises: on Prometheus failure or score parse failure,
publishes critique.result with score=None so downstream consumers
(MemoryAgent, UI) can treat null as "not graded" and continue normally.

Two details worth knowing:

* Prometheus's prompt format uses "###" as its section delimiter
  ("###Response to evaluate:", "###Score Rubrics:" ...). Generator models
  emit Markdown "### headings" freely, and the first one in the answer
  ends the response section as far as Prometheus is concerned — it then
  grades only the text *before* the heading. Everything sent to the grader
  passes through sanitize_for_grader(), which rewrites headings as bold.

* Optional second pass — faithfulness. For turns that used a retrieval
  tool, the tool results are passed to a (configurable) judge model as
  reference material and the answer is graded on whether its claims are
  supported by that material. Published as a separate critique.result
  with kind="faithfulness" so the primary critique is not delayed.
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

_MD_HEADING = re.compile(r"^[ \t]*#{1,6}[ \t]+(.+?)[ \t]*#*[ \t]*$", re.MULTILINE)
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL)


def sanitize_for_grader(text: str) -> str:
    """Rewrite Markdown ATX headings ("### Title") as bold ("**Title**").

    Prometheus (and any grader using the same template) treats a line
    beginning with "###" as the start of its next prompt section, so a
    heading inside the answer truncates what gets graded. Bold keeps the
    visual structure without colliding with the delimiter. Bare "#" lines
    are dropped.
    """
    if not text:
        return text
    text = _MD_HEADING.sub(lambda m: f"**{m.group(1)}**", text)
    return re.sub(r"(?m)^[ \t]*#{1,6}[ \t]*$", "", text)


def _parse_grade(text: str) -> tuple[int | None, str]:
    """Parse "Feedback: ... [RESULT] N" into (score_or_None, feedback)."""
    text = _THINK_BLOCK.sub("", text or "").strip()
    m = re.search(r"\[RESULT\]\s*([1-5])", text)
    score = int(m.group(1)) if m else None
    feedback = re.sub(r"\s*\[RESULT\].*$", "", text, flags=re.DOTALL).strip()
    if feedback.lower().startswith("feedback:"):
        feedback = feedback[len("feedback:"):].strip()
    return score, feedback


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

    def __init__(self, llm: OllamaBackend | None = None, faithfulness_llm: OllamaBackend | None = None) -> None:
        """Initialize the CriticAgent.

        Config keys read from ``config/critic.yaml``: ``model``, ``rubric``,
        ``style_rubric``, ``clarity_rubric``, ``grade_prompt``, ``num_ctx``,
        ``temperature``, ``grade_timeout``, and the optional ``faithfulness``
        block (``enabled``, ``model``, ``timeout``, ``num_ctx``, ``think``,
        ``max_reference_chars``, ``exclude_tools``, ``rubric``, ``grade_prompt``).

        Args:
            llm: Injected for testing; defaults to Prometheus ``OllamaBackend`` from config.
            faithfulness_llm: Injected for testing; defaults to the judge model
                named in ``faithfulness.model`` (or the critic model when unset).
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

        # Optional faithfulness pass (see module docstring).
        fcfg: dict = cfg.get("faithfulness") or {}
        self._faith_enabled: bool = bool(fcfg.get("enabled", False))
        self._faith_rubric: str = (fcfg.get("rubric") or "").strip()
        self._faith_prompt: str = (fcfg.get("grade_prompt") or "").strip()
        self._faith_think: bool = bool(fcfg.get("think", False))
        self._faith_max_ref: int = int(fcfg.get("max_reference_chars", 12000))
        self._faith_exclude: set[str] = set(fcfg.get("exclude_tools") or ["persona", "set_the_stage", "remember_this"])
        self._faith_options: dict = {
            "num_ctx": fcfg.get("num_ctx", 16384),
            "temperature": fcfg.get("temperature", 0.0),
        }
        self._faith_model: str = fcfg.get("model") or model
        if faithfulness_llm is not None:
            self._faith_llm: OllamaBackend | None = faithfulness_llm
        elif self._faith_enabled:
            self._faith_llm = OllamaBackend(model=self._faith_model, agent_name=self.id, timeout=fcfg.get("timeout", 600))
        else:
            self._faith_llm = None
        if self._faith_enabled and not (self._faith_rubric and self._faith_prompt):
            logger.warning("CriticAgent: faithfulness.enabled but rubric/grade_prompt missing — disabling")
            self._faith_enabled = False
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

        # Second, slower pass: is the answer faithful to what the tools returned?
        # Published separately so the primary critique reaches the UI/memory first.
        reference = self._collect_reference(msg.tool_calls) if self._faith_enabled else ""
        if reference:
            f_score, f_feedback = self._grade_faithfulness(msg.query, msg.answer, reference)
            self._pub.publish(
                CritiqueResult(
                    score=f_score, feedback=f_feedback,
                    query=msg.query, answer=msg.answer,
                    session_id=msg.session_id, query_id=msg.query_id,
                    rubric_name="faithfulness", rubric_text=self._faith_rubric,
                    kind="faithfulness", judge_model=self._faith_model,
                ),
                sender_id=self.id, correlation_id=correlation_id, session_id=msg.session_id,
            )

    def _collect_reference(self, tool_calls: list) -> str:
        """Concatenate tool results into the reference block for the faithfulness grader."""
        parts: list[str] = []
        for tc in tool_calls or []:
            name = tc.get("tool") or ""
            result = (tc.get("result") or "").strip()
            if not name or name in self._faith_exclude or not result:
                continue
            parts.append(f"[{name}]\n{result}")
        reference = "\n\n".join(parts)
        if len(reference) > self._faith_max_ref:
            reference = reference[: self._faith_max_ref] + "\n[... reference truncated ...]"
        return reference

    def _grade(self, query: str, answer: str, rubric: str) -> tuple[int | None, str]:
        """Call Prometheus absolute grading. Returns (score_or_None, feedback_text)."""
        prompt = self._grade_prompt.format(
            query=sanitize_for_grader(query),
            answer=sanitize_for_grader(answer),
            rubric=rubric,
        )
        text, _ = self._llm.chat(
            [{"role": "user", "content": prompt}],
            options=self._options,
        )
        if not text:
            logger.warning("CriticAgent: Prometheus returned empty response")
            return None, ""

        score, feedback = _parse_grade(text)
        if score is None:
            logger.warning("CriticAgent: could not parse score from: %r", text[:120])
        return score, feedback

    def _grade_faithfulness(self, query: str, answer: str, reference: str) -> tuple[int | None, str]:
        """Grade the answer against the tool results it was generated from.

        Returns (score_or_None, feedback_text). Never raises.
        """
        if self._faith_llm is None:
            return None, ""
        prompt = self._faith_prompt.format(
            query=sanitize_for_grader(query),
            answer=sanitize_for_grader(answer),
            reference=sanitize_for_grader(reference),
            rubric=self._faith_rubric,
        )
        try:
            text, _ = self._faith_llm.chat(
                [{"role": "user", "content": prompt}],
                think=self._faith_think,
                options=self._faith_options,
            )
        except Exception as exc:
            logger.warning("CriticAgent: faithfulness judge %s failed: %s", self._faith_model, exc)
            return None, ""
        if not text:
            logger.warning("CriticAgent: faithfulness judge %s returned empty response", self._faith_model)
            return None, ""
        score, feedback = _parse_grade(text)
        if score is None:
            logger.warning("CriticAgent: could not parse faithfulness score from: %r", text[:120])
        else:
            logger.info("CriticAgent: faithfulness %d/5 (judge=%s)", score, self._faith_model)
        return score, feedback

    def run(self) -> None:
        # Broadcast tool.schema.request so tools re-announce their schemas to us
        self._pub.publish(ToolSchemaRequest(), sender_id=self.id)
        super().run()
