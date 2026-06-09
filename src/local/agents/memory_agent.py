"""MemoryAgent — auto-ingests Q+A pairs into episodic memory.

System-triggered: subscribes to response.generation and writes each
Q+A turn to the episodic store. Runs a background LLM call (gemma4:e4b,
non-streaming) to classify intent and extract entities before writing.
On classification failure the engram is written without those fields —
write never blocks on classification.
"""

from __future__ import annotations

import json
import logging
import re
import uuid

from local.agents.memory_agent_actions import MemoryAgentAction
from local.agents.memory_agent_states import MemoryAgentState
from local.agents.memory_agent_transitions import MemoryAgentStateMachine
from local.config_loader import get_config
from local.protocol.envelope import MessageEnvelope
from local.protocol.subjects import AGENT_TRANSITION, CRITIQUE, PAIRWISE_RESULT, RESPONSE_GENERATION
from local.services.memory_service import MemoryService
from local.services.ollama_backend import OllamaBackend
from local.transport.bus_config import make_participant_bus

logger = logging.getLogger(__name__)


class MemoryAgent:
    """Episodic memory auto-ingest agent.

    System-triggered: subscribes to ``response.generation`` (ingest Q+A),
    ``critique.result`` (annotate with score), and ``pairwise.result``
    (annotate with pairwise winner). Runs a small LLM call to classify
    intent and extract entities before each write. Classification failure
    never blocks the ingest — the engram is written without those fields.
    """

    AGENT_ID = "memory_agent"

    def __init__(
        self,
        memory_service: MemoryService | None = None,
        llm: OllamaBackend | None = None,
    ) -> None:
        """Initialize the MemoryAgent.

        Config keys read from ``config/memory.yaml``: ``model``,
        ``classify_prompt``.

        Args:
            memory_service: Injected for testing; defaults to a fresh
                ``MemoryService``.
            llm: Injected for testing; defaults to an ``OllamaBackend``
                built from config.
        """
        cfg = get_config("memory")
        model = cfg.get("model", "gemma4:e4b")
        self._classify_prompt: str = cfg.get("classify_prompt", "").strip()
        self._memory = memory_service or MemoryService()
        self._llm = llm or OllamaBackend(model=model, agent_name=self.AGENT_ID)
        self._pub, self._sub = make_participant_bus([RESPONSE_GENERATION, CRITIQUE, PAIRWISE_RESULT])
        self._sm = MemoryAgentStateMachine()

    def run(self) -> None:
        logger.info("memory_agent ready")
        while True:
            try:
                envelope = self._sub.receive()
            except Exception as exc:
                logger.error("MemoryAgent: receive error: %s", exc)
                continue
            if envelope.subject == RESPONSE_GENERATION:
                self._handle_generation(envelope)
            elif envelope.subject == CRITIQUE:
                self._handle_critique(envelope)
            elif envelope.subject == PAIRWISE_RESULT:
                self._handle_pairwise(envelope)

    def _do_transition(self, action: MemoryAgentAction) -> None:
        """Execute a state machine transition and publish ``AGENT_TRANSITION``.

        The publish is wrapped in try/except — transition logging must never
        propagate and interrupt memory writes.
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

    def _handle_generation(self, envelope) -> None:
        payload = envelope.payload
        query: str = payload.get("query", "").strip()
        answer: str = payload.get("answer", "").strip()
        query_id: str = payload.get("query_id") or ""
        session_id: str = payload.get("session_id") or ""
        respondent_id: str = payload.get("respondent_id", "A")

        if not query or not answer:
            return
        if payload.get("error"):
            return

        self._do_transition(MemoryAgentAction.START_INGEST)
        try:
            classification = self._classify(query, answer)
            classification["respondent_id"] = respondent_id
            if session_id:
                classification["session_id"] = session_id
            self._memory.write_episodic(query, answer, metadata=classification, query_id=query_id or None)
            logger.info(
                "MemoryAgent: ingested engram respondent=%s intent=%r entities=%r",
                respondent_id,
                classification.get("intent", ""),
                classification.get("entities", []),
            )
        except Exception as exc:
            logger.error("MemoryAgent: ingest failed: %s", exc)
        finally:
            self._do_transition(MemoryAgentAction.COMPLETE)

    def _handle_critique(self, envelope) -> None:
        payload = envelope.payload
        query_id: str = payload.get("query_id") or ""
        score = payload.get("score")

        if not query_id or score is None:
            return

        self._do_transition(MemoryAgentAction.UPDATE_SCORE)
        try:
            self._memory.update_engram_score(query_id, score)
            logger.info("MemoryAgent: scored engram %s → %d", query_id, score)
        except Exception as exc:
            logger.error("MemoryAgent: update_engram_score failed: %s", exc)
        finally:
            self._do_transition(MemoryAgentAction.COMPLETE)

    def _handle_pairwise(self, envelope) -> None:
        payload = envelope.payload
        query_id_a: str = payload.get("query_id_a") or ""
        query_id_b: str = payload.get("query_id_b") or ""
        winner: str = payload.get("winner") or ""

        if not query_id_a or not query_id_b or winner not in ("A", "B"):
            return

        self._do_transition(MemoryAgentAction.ANNOTATE_PAIRWISE)
        try:
            self._memory.annotate_pairwise(query_id_a, query_id_b, winner)
            logger.info("MemoryAgent: annotated pairwise winner=%s", winner)
        except Exception as exc:
            logger.error("MemoryAgent: annotate_pairwise failed: %s", exc)
        finally:
            self._do_transition(MemoryAgentAction.COMPLETE)

    def _classify(self, query: str, answer: str) -> dict:
        """Classify intent and extract named entities via the small LLM.

        Expects the LLM to return a JSON object with ``intent`` and
        ``entities`` keys. Valid intent values: ``"fact"``,
        ``"explanation"``, ``"comparison"``, ``"procedure"``. Any other
        value is discarded.

        Args:
            query: The user's question.
            answer: The agent's response, truncated to 500 chars in the prompt.

        Returns:
            Dict with ``intent`` (str) and ``entities`` (list[str]),
            or ``{}`` if the LLM returns no text or unparseable JSON.
        """
        prompt = self._classify_prompt.format(query=query, answer=answer[:500])
        text, _ = self._llm.chat([{"role": "user", "content": prompt}])
        if not text:
            return {}
        m = re.search(r'\{[^{}]+\}', text, re.DOTALL)
        if not m:
            return {}
        try:
            data = json.loads(m.group())
            intent = str(data.get("intent", "")).lower()
            if intent not in ("fact", "explanation", "comparison", "procedure"):
                intent = ""
            entities = [str(e) for e in data.get("entities", []) if e]
            return {"intent": intent, "entities": entities}
        except Exception:
            return {}
