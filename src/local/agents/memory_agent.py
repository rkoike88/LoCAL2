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
from local.protocol.messages import CritiqueResult, MemoryContext, QueryReceived, ResponseGeneration, UserContext
from local.protocol.subjects import CRITIQUE, MEMORY_CONTEXT, QUERY_RECEIVED, RESPONSE_GENERATION, USER_CONTEXT_REQUEST
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
        retrieval_cfg = cfg.get("retrieval") or {}
        ingest_cfg = cfg.get("ingest") or {}
        self._classify_prompt: str = (cfg.get("classify_prompt") or "").strip()
        self._summarize_prompt: str = (cfg.get("summarize_prompt") or "").strip()
        self._merge_prompt: str = (cfg.get("merge_prompt") or "").strip()
        self._min_similarity: float = retrieval_cfg.get("min_similarity", 0.85)
        self._max_results: int = retrieval_cfg.get("max_results", 7)
        self._base_cap: int = retrieval_cfg.get("base_cap", 4)
        self._high_confidence_threshold: float = retrieval_cfg.get("high_confidence_threshold", 0.90)
        self._dedup_threshold: float = ingest_cfg.get("dedup_threshold", 0.90)
        self._write_enabled: bool = cfg.get("write_enabled", True)
        self._llm_options: dict = {
            "temperature": cfg.get("temperature", 0.0),
            "top_p": cfg.get("top_p", 0.95),
            "top_k": cfg.get("top_k", 64),
        }
        self._memory = memory_service or MemoryService()
        self._llm = llm or OllamaBackend(model=model, agent_name=self.id)
        self._pub, self._sub = make_participant_bus([QUERY_RECEIVED, RESPONSE_GENERATION, CRITIQUE, USER_CONTEXT_REQUEST])
        self._sm = MemoryAgentStateMachine()

    def _dispatch(self, envelope: MessageEnvelope) -> None:
        if envelope.subject == QUERY_RECEIVED:
            self._handle_query_received(envelope)
        elif envelope.subject == USER_CONTEXT_REQUEST:
            self._handle_user_context_request(envelope)
        elif envelope.subject == RESPONSE_GENERATION:
            self._handle_generation(envelope)
        elif envelope.subject == CRITIQUE:
            self._handle_critique(envelope)

    def _handle_user_context_request(self, envelope: MessageEnvelope) -> None:
        """Respond to GeneratorAgent startup bootstrap with all pinned facts."""
        facts = self._memory.list_pinned()
        self._pub.publish(
            UserContext(facts=facts),
            sender_id=self.id,
            correlation_id=envelope.correlation_id,
        )
        logger.info("MemoryAgent: sent user.context with %d pinned facts", len(facts))

    def _handle_query_received(self, envelope: MessageEnvelope) -> None:
        """Relay: search episodic memory and publish memory.context before generation.

        Always publishes, even when capsules is empty, so the generator is never
        starved. Wraps everything in try/except so a Chroma error doesn't block
        the hot path.
        """
        msg = QueryReceived.from_envelope(envelope)
        query, session_id, query_id, user_id = msg.query, msg.session_id, msg.query_id, msg.user_id

        capsules: list = []
        candidates: list = []
        self._do_transition(MemoryAgentAction.START_RETRIEVE)
        try:
            filter_user = user_id if user_id and user_id != "default" else None
            all_results = self._memory.search_episodic(query, n=self._max_results, user_id=filter_user)
            above = [c for c in all_results if c["score"] >= self._min_similarity]
            candidates = [c for c in all_results if c["score"] < self._min_similarity]
            # base cap: 4; allow a 5th if it clears the high-confidence threshold
            cap = self._base_cap
            if len(above) > cap and above[cap]["score"] >= self._high_confidence_threshold:
                cap += 1
            capsules = above[:cap]
            top_scores = [round(c["score"], 3) for c in all_results[:3]]
            logger.info("MemoryAgent: relay capsules=%d below_threshold=%d top_scores=%s user_id=%s", len(capsules), len(candidates), top_scores, user_id)
        except Exception as exc:
            logger.error("MemoryAgent: retrieval failed (publishing empty context): %s", exc)
        finally:
            self._do_transition(MemoryAgentAction.COMPLETE)

        self._pub.publish(
            MemoryContext(
                query=query,
                session_id=session_id,
                query_id=query_id,
                capsules=capsules,
                candidates=candidates,
                attachments=msg.attachments,
                user_id=user_id,
                native=msg.native,
            ),
            sender_id=self.id,
            correlation_id=envelope.correlation_id,
            session_id=session_id,
        )

    def _handle_generation(self, envelope) -> None:
        msg = ResponseGeneration.from_envelope(envelope)

        if not msg.query or not msg.answer or msg.error:
            return
        if not self._write_enabled:
            logger.debug("MemoryAgent: write_enabled=false — skipping ingest")
            return

        query, answer, query_id, session_id = msg.query, msg.answer, msg.query_id, msg.session_id
        thinking = msg.thinking or ""

        self._do_transition(MemoryAgentAction.START_INGEST)
        try:
            classification = self._classify(query, answer)
            if session_id:
                classification["session_id"] = session_id
            if msg.user_id and msg.user_id != "default":
                classification["user_id"] = msg.user_id
            if thinking:
                classification["thinking"] = thinking
            summary = self._summarize(query, answer)

            filter_user = msg.user_id if msg.user_id and msg.user_id != "default" else None
            near_dups = self._memory.search_episodic(query, n=1, user_id=filter_user)
            old_id: str | None = None
            if near_dups and near_dups[0]["score"] >= self._dedup_threshold:
                dup = near_dups[0]
                old_id = dup["id"]
                merged = self._merge(dup["content"], query, answer)
                if merged:
                    summary = merged
                logger.info("MemoryAgent: near-duplicate score=%.3f — merging with engram %s", dup["score"], old_id)

            self._memory.write_episodic(query, answer, metadata=classification, query_id=query_id or None, summary=summary)
            if old_id:
                self._memory.delete_episodic(old_id)
                logger.info("MemoryAgent: deleted old engram %s after merge", old_id)
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
        feedback: str = msg.feedback or ""

        if not query_id or score is None:
            return

        self._do_transition(MemoryAgentAction.UPDATE_SCORE)
        try:
            self._memory.update_engram_score(query_id, score, feedback, rubric_name=msg.rubric_name, rubric_text=msg.rubric_text)
            logger.info("MemoryAgent: scored engram %s → %d (%s)", query_id, score, msg.rubric_name or "realistic")
        except Exception as exc:
            logger.error("MemoryAgent: update_engram_score failed: %s", exc)
        finally:
            self._do_transition(MemoryAgentAction.COMPLETE)

    def _summarize(self, query: str, answer: str) -> str | None:
        """Produce a compact prose summary of the Q+A exchange for storage.

        The summary becomes the ChromaDB document — what gets embedded,
        retrieved, and injected verbatim into generation context. Falls back
        to None on failure so the caller uses raw Q+A instead.

        Args:
            query: The user's question.
            answer: The agent's response, truncated to 1000 chars in the prompt.

        Returns:
            Summary string, or None if the LLM returns no text.
        """
        if not self._summarize_prompt:
            return None
        prompt = self._summarize_prompt.format(query=query, answer=answer[:1000])
        text, _ = self._llm.chat([{"role": "user", "content": prompt}], options=self._llm_options)
        return text.strip() or None

    def _merge(self, old_content: str, query: str, answer: str) -> str | None:
        """Merge an existing engram with a new Q+A into a single compact summary.

        Args:
            old_content: The stored document of the existing near-duplicate engram.
            query: The new user question.
            answer: The new agent response, truncated to 1000 chars in the prompt.

        Returns:
            Merged summary string, or None if the LLM returns no text.
        """
        if not self._merge_prompt:
            return None
        prompt = self._merge_prompt.format(old_content=old_content, query=query, answer=answer[:1000])
        text, _ = self._llm.chat([{"role": "user", "content": prompt}], options=self._llm_options)
        return text.strip() or None

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
        text, _ = self._llm.chat([{"role": "user", "content": prompt}], options=self._llm_options)
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
