"""MemoryAgent — auto-ingests Q+A pairs into episodic memory.

System-triggered: subscribes to response.generation (ingest) and
critique.result (score annotation). Runs a small LLM call to classify
intent and extract entities before each write. Classification failure
never blocks the ingest — the engram is written without those fields.
"""

from __future__ import annotations

import json
import logging
import re

from local.agents.base_agent import BaseAgent
from local.agents.memory_agent_actions import MemoryAgentAction
from local.agents.memory_agent_states import MemoryAgentState
from local.agents.memory_agent_transitions import MemoryAgentStateMachine
from local.config_loader import get_config
from local.protocol.envelope import MessageEnvelope
from local.protocol.messages import CritiqueResult, ResponseGeneration
from local.protocol.subjects import CRITIQUE, RESPONSE_GENERATION  # used in make_participant_bus subscription
from local.services.memory_service import MemoryService
from local.services.ollama_backend import OllamaBackend
from local.transport.bus_config import make_participant_bus

logger = logging.getLogger(__name__)


class MemoryAgent(BaseAgent):
    """Episodic memory auto-ingest agent.

    System-triggered: subscribes to ``response.generation`` (ingest Q+A)
    and ``critique.result`` (annotate with score). Runs a small LLM call
    to classify intent and extract entities before each write. Classification
    failure never blocks the ingest — the engram is written without those fields.
    """

    CONFIG_NAME = "memory"

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
        model = cfg["model"]
        self._classify_prompt: str = (cfg.get("classify_prompt") or "").strip()
        self._memory = memory_service or MemoryService()
        self._llm = llm or OllamaBackend(model=model, agent_name=self.id)
        self._pub, self._sub = make_participant_bus([RESPONSE_GENERATION, CRITIQUE])
        self._sm = MemoryAgentStateMachine()

    def _dispatch(self, envelope: MessageEnvelope) -> None:
        if envelope.subject == RESPONSE_GENERATION:
            self._handle_generation(envelope)
        elif envelope.subject == CRITIQUE:
            self._handle_critique(envelope)

    def _handle_generation(self, envelope) -> None:
        msg = ResponseGeneration.from_envelope(envelope)

        if not msg.query or not msg.answer or msg.error:
            return

        query, answer, query_id, session_id = msg.query, msg.answer, msg.query_id, msg.session_id

        self._do_transition(MemoryAgentAction.START_INGEST)
        try:
            classification = self._classify(query, answer)
            if session_id:
                classification["session_id"] = session_id
            self._memory.write_episodic(query, answer, metadata=classification, query_id=query_id or None)
            logger.info(
                "MemoryAgent: ingested engram intent=%r entities=%r",
                classification.get("intent", ""),
                classification.get("entities", []),
            )
        except Exception as exc:
            logger.error("MemoryAgent: ingest failed: %s", exc)
        finally:
            self._do_transition(MemoryAgentAction.COMPLETE)

    def _handle_critique(self, envelope) -> None:
        msg = CritiqueResult.from_envelope(envelope)
        query_id: str = msg.query_id
        score = msg.score

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

    def _classify(self, query: str, answer: str) -> dict:
        """Classify intent and extract named entities via the small LLM.

        Expects the LLM to return a JSON object with ``intent`` and
        ``entities`` keys. The intent value is taken as-is from the LLM;
        valid values are defined by the ``classify_prompt`` in config.

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
            entities = [str(e) for e in data.get("entities", []) if e]
            return {"intent": intent, "entities": entities}
        except Exception:
            return {}
