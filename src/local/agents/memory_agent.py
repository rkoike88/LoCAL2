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

from local.agents.memory_agent_actions import MemoryAgentAction
from local.agents.memory_agent_states import MemoryAgentState
from local.agents.memory_agent_transitions import TRANSITIONS
from local.config_loader import get_config
from local.protocol.subjects import CRITIQUE, RESPONSE_GENERATION
from local.services.memory_service import MemoryService
from local.services.ollama_backend import OllamaBackend
from local.transport.bus_config import make_participant_bus

logger = logging.getLogger(__name__)

_CLASSIFY_PROMPT = """\
Classify this Q&A pair and extract named entities. Output JSON only, no explanation:
{{"intent": "fact|explanation|comparison|procedure", "entities": ["entity1"]}}

Rules for intent:
- fact: single specific value (name, date, price, setting, preference)
- explanation: how or why something works
- comparison: two or more things compared side by side
- procedure: step-by-step instructions or commands

Rules for entities:
- Extract proper nouns: people, tools, technologies, projects, places
- Return [] if none apply

Q: {query}
A: {answer}"""


class _StateMachine:
    def __init__(self) -> None:
        self._state = MemoryAgentState.IDLE

    @property
    def state(self) -> MemoryAgentState:
        return self._state

    def transition(self, action: MemoryAgentAction) -> None:
        key = (self._state, action)
        next_state = TRANSITIONS.get(key)
        if next_state is None:
            logger.warning("MemoryAgent: invalid transition %s + %s", self._state, action)
            return
        self._state = next_state


class MemoryAgent:
    AGENT_ID = "memory_agent"

    def __init__(
        self,
        memory_service: MemoryService | None = None,
        llm: OllamaBackend | None = None,
    ) -> None:
        cfg = get_config("memory")
        model = cfg.get("model", "gemma4:e4b")
        self._memory = memory_service or MemoryService()
        self._llm = llm or OllamaBackend(model=model, agent_name=self.AGENT_ID)
        self._sm = _StateMachine()
        self._pub, self._sub = make_participant_bus([RESPONSE_GENERATION, CRITIQUE])

    def run(self) -> None:
        print(f"[memory_agent] ready")
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

    def _handle_generation(self, envelope) -> None:
        payload = envelope.payload
        query: str = payload.get("query", "").strip()
        answer: str = payload.get("answer", "").strip()
        query_id: str = payload.get("query_id") or ""

        if not query or not answer:
            return
        if payload.get("error"):
            return

        self._sm.transition(MemoryAgentAction.START_INGEST)
        try:
            classification = self._classify(query, answer)
            self._memory.write_episodic(query, answer, metadata=classification, query_id=query_id or None)
            logger.info(
                "MemoryAgent: ingested engram intent=%r entities=%r",
                classification.get("intent", ""),
                classification.get("entities", []),
            )
        except Exception as exc:
            logger.error("MemoryAgent: ingest failed: %s", exc)
        finally:
            self._sm.transition(MemoryAgentAction.COMPLETE)

    def _handle_critique(self, envelope) -> None:
        payload = envelope.payload
        query_id: str = payload.get("query_id") or ""
        score = payload.get("score")

        if not query_id or score is None:
            return

        self._sm.transition(MemoryAgentAction.UPDATE_SCORE)
        try:
            self._memory.update_engram_score(query_id, score)
            logger.info("MemoryAgent: scored engram %s → %d", query_id, score)
        except Exception as exc:
            logger.error("MemoryAgent: update_engram_score failed: %s", exc)
        finally:
            self._sm.transition(MemoryAgentAction.COMPLETE)

    def _classify(self, query: str, answer: str) -> dict:
        """Call LLM to classify intent and extract entities. Returns {} on failure."""
        prompt = _CLASSIFY_PROMPT.format(query=query, answer=answer[:500])
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
