"""Shared memory data layer — ChromaDB + nomic-embed-text.

Two access modes, single ChromaDB collection:
  - Topic store: exact key/value facts, retrieved by deterministic ID
  - Episodic store: Q+A traces, retrieved by embedding similarity

Topic IDs are deterministic ("topic:<key>") so upsert works without a query.
Episodic IDs are UUIDs; entries accumulate and older ones recede naturally
as similarity scores favour recent, high-reward engrams (Phase 4).

nomic-embed-text requires:
  - Write prefix:  "search_document: "
  - Query prefix:  "search_query: "
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, Optional

import chromadb
import ollama

from local.config_loader import get_config

logger = logging.getLogger(__name__)


class MemoryService:
    """Episodic and topic memory backed by ChromaDB.

    Two logical stores share a single ChromaDB collection, distinguished
    by a ``type`` metadata field:

    - **Topic store** — exact lookup by deterministic ID
      (``"topic:<key>"``). Use for standing facts like user preferences.
    - **Episodic store** — Q+A traces retrieved by embedding similarity
      with entity overlap and critic score re-ranking.
    """

    def __init__(
        self,
        chroma_path: Optional[str] = None,
        collection_name: Optional[str] = None,
        embed_model: Optional[str] = None,
        n_results: Optional[int] = None,
    ) -> None:
        """Initialize the memory store.

        All parameters fall back to ``config/memory.yaml`` when ``None``:
        ``embed_model``, ``n_results``, ``chroma_path``, ``collection``.

        Args:
            chroma_path: Override ChromaDB storage path. Tests typically
                pass a ``tmp_path`` fixture here.
            collection_name: Override ChromaDB collection name.
            embed_model: Override Ollama embedding model.
            n_results: Override default candidate count for similarity search.
        """
        cfg = get_config("memory")
        self._embed_model = embed_model or cfg.get("embed_model", "nomic-embed-text")
        self._n_results = n_results or cfg.get("n_results", 5)
        path = chroma_path or cfg.get("chroma_path", ".chroma")
        name = collection_name or cfg.get("collection", "local_memory")
        self._client = chromadb.PersistentClient(path=path)
        self._collection = self._client.get_or_create_collection(name=name)

    # ------------------------------------------------------------------
    # Topic store — exact lookup by deterministic ID
    # ------------------------------------------------------------------

    def write_topic(self, topic: str, value: str) -> None:
        """Upsert a standing fact keyed by topic string (e.g. 'user.language')."""
        embedding = self._embed_document(value)
        self._collection.upsert(
            ids=[f"topic:{topic}"],
            documents=[value],
            embeddings=[embedding],
            metadatas=[{"type": "topic", "topic": topic}],
        )
        logger.debug("MemoryService: upserted topic %r", topic)

    def recall_topic(self, topic: str) -> Optional[str]:
        """Return the stored value for topic, or None if not found."""
        result = self._collection.get(ids=[f"topic:{topic}"])
        docs = result.get("documents") or []
        return docs[0] if docs else None

    # ------------------------------------------------------------------
    # Episodic store — similarity search with optional metadata boost
    # ------------------------------------------------------------------

    def write_episodic(
        self,
        query: str,
        answer: str,
        metadata: Optional[dict[str, Any]] = None,
        query_id: Optional[str] = None,
        summary: Optional[str] = None,
    ) -> str:
        """Write a Q+A pair as an episodic engram.

        Args:
            query: The user's question.
            answer: The agent's response.
            metadata: Optional enrichment. Supported keys: ``intent`` (str),
                ``entities`` (list[str]), ``respondent_id`` (str, default
                ``"A"``), ``session_id`` (str).
            query_id: When provided, used as the ChromaDB document ID so
                CriticAgent can later annotate the same record via
                ``update_engram_score()``. Falls back to a random UUID.
            summary: LLM-generated summary of the exchange. When provided,
                used as the stored document (what gets embedded and injected).
                Falls back to raw ``"{query}\\n{answer}"`` when absent.

        Returns:
            The engram ID (``query_id`` if supplied, otherwise a UUID).
        """
        content = f"{query}\n\n{summary}" if summary else f"{query}\n{answer}"
        embedding = self._embed_document(content)
        doc_id = query_id or str(uuid.uuid4())
        meta: dict[str, Any] = {
            "type": "episodic",
            "query": query[:500],
            "timestamp": time.time(),
        }
        if metadata:
            intent = metadata.get("intent", "")
            entities = metadata.get("entities", [])
            respondent_id = metadata.get("respondent_id", "A")
            session_id = metadata.get("session_id", "")
            if intent:
                meta["intent"] = intent
            if entities:
                meta["entities"] = json.dumps(entities)
            meta["respondent_id"] = respondent_id
            if session_id:
                meta["session_id"] = session_id
            thinking = metadata.get("thinking", "")
            if thinking:
                meta["thinking"] = thinking
        self._collection.add(
            ids=[doc_id],
            documents=[content],
            embeddings=[embedding],
            metadatas=[meta],
        )
        logger.debug("MemoryService: wrote episodic engram %s", doc_id)
        return doc_id

    def search_episodic(
        self,
        query: str,
        n: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        """Return the top-n most relevant episodic engrams by meaning.

        Fetches ``3 × n`` candidates from Chroma and re-ranks by a composite
        score:

        - Base: ``1.0 - cosine_distance``
        - Entity overlap: ``+0.1`` if any stored entity appears in the query
        - Critic offset: ``+(critic_score - 3) × critic_score_weight``

        Args:
            query: Natural-language search text; embedded with the
                ``"search_query:"`` nomic prefix.
            n: Max results to return. Defaults to ``config n_results``.

        Returns:
            List of dicts with keys ``content``, ``metadata``, ``score``,
            sorted by ``score`` descending. Returns ``[]`` on Chroma errors.
        """
        n = n or self._n_results
        fetch_n = n * 3
        query_embedding = self._embed_query(query)
        try:
            result = self._collection.query(
                query_embeddings=[query_embedding],
                n_results=fetch_n,
                where={"type": "episodic"},
            )
        except Exception as exc:
            logger.warning("MemoryService: search_episodic failed: %s", exc)
            return []

        ids       = (result.get("ids") or [[]])[0]
        docs      = (result.get("documents") or [[]])[0]
        metas     = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]

        sm_cfg = get_config("search_memory")
        critic_weight: float = sm_cfg.get("critic_score_weight", 0.05)

        candidates = []
        query_lower = query.lower()
        for id_, doc, meta, dist in zip(ids, docs, metas, distances):
            score = 1.0 - dist / 2  # ChromaDB returns squared-L2 for normalized vecs; convert to cosine similarity
            raw_entities = meta.get("entities", "")
            if raw_entities:
                try:
                    entities = json.loads(raw_entities)
                    if any(e.lower() in query_lower for e in entities if e):
                        score += 0.1
                except Exception:
                    pass
            critic_score = meta.get("critic_score")
            if critic_score is not None:
                score += (int(critic_score) - 3) * critic_weight
            candidates.append({"id": id_, "content": doc, "metadata": meta, "score": score})

        candidates.sort(key=lambda c: c["score"], reverse=True)
        return candidates[:n]

    def list_episodic(self, n: int = 100) -> list[dict[str, Any]]:
        """Return the n most recent episodic engrams, newest first."""
        result = self._collection.get(
            where={"type": "episodic"},
            include=["metadatas", "documents"],
        )
        ids   = result.get("ids") or []
        docs  = result.get("documents") or []
        metas = result.get("metadatas") or []
        items = sorted(
            zip(ids, docs, metas),
            key=lambda x: x[2].get("timestamp", 0),
            reverse=True,
        )
        return [
            {"id": id_, "content": doc, "metadata": meta}
            for id_, doc, meta in items[:n]
        ]

    def get_session_engrams(self, session_id: str) -> list[dict[str, Any]]:
        """Return all episodic engrams for session_id, sorted oldest-first."""
        if not session_id:
            return []
        try:
            result = self._collection.get(
                where={"$and": [{"type": {"$eq": "episodic"}}, {"session_id": {"$eq": session_id}}]},
                include=["metadatas", "documents"],
            )
        except Exception:
            return []
        ids   = result.get("ids") or []
        docs  = result.get("documents") or []
        metas = result.get("metadatas") or []
        items = sorted(
            zip(ids, docs, metas),
            key=lambda x: x[2].get("timestamp", 0),
        )
        return [{"id": id_, "content": doc, "metadata": meta} for id_, doc, meta in items]

    def delete_episodic(self, engram_id: str) -> None:
        """Delete a single episodic engram by its ChromaDB document ID."""
        self._collection.delete(ids=[engram_id])
        logger.debug("MemoryService: deleted engram %s", engram_id)

    def update_engram_score(self, query_id: str, score: int, feedback: str = "") -> None:
        """Merge ``critic_score`` and ``critic_feedback`` into an existing engram's metadata.

        Reads the existing metadata first to avoid wiping ``type``,
        ``query``, ``timestamp``, ``intent``, or ``entities`` — ChromaDB
        ``update()`` replaces the entire metadata dict, not individual fields.

        Args:
            query_id: The engram ID returned by ``write_episodic()``.
            score: Absolute critic score (1–5).
            feedback: Prometheus natural language feedback; stored as
                ``critic_feedback`` for XAI audit trail.

        Note:
            Logs a warning and returns cleanly if the engram is not found.
        """
        result = self._collection.get(ids=[query_id])
        if not result.get("ids"):
            logger.warning("MemoryService: engram %s not found — skipping score update", query_id)
            return
        existing_meta = (result.get("metadatas") or [{}])[0]
        merged = {**existing_meta, "critic_score": score}
        if feedback:
            merged["critic_feedback"] = feedback
        self._collection.update(ids=[query_id], metadatas=[merged])
        logger.debug("MemoryService: updated critic_score=%d on engram %s", score, query_id)

    def annotate_pairwise(self, query_id_a: str, query_id_b: str, winner: str) -> None:
        """Write ``pairwise_winner`` (bool) to both engrams.

        Args:
            query_id_a: Engram ID for respondent A.
            query_id_b: Engram ID for respondent B.
            winner: ``"A"`` or ``"B"`` — the respondent whose answer was
                judged better. The winning engram gets ``pairwise_winner=True``,
                the other gets ``False``.

        Note:
            Uses the same read-before-write pattern as ``update_engram_score``
            to preserve existing metadata fields.
        """
        for qid, respondent in ((query_id_a, "A"), (query_id_b, "B")):
            result = self._collection.get(ids=[qid])
            if not result.get("ids"):
                logger.warning("MemoryService: engram %s not found — skipping pairwise annotation", qid)
                continue
            existing_meta = (result.get("metadatas") or [{}])[0]
            merged = {**existing_meta, "pairwise_winner": winner == respondent}
            self._collection.update(ids=[qid], metadatas=[merged])
            logger.debug(
                "MemoryService: annotated pairwise_winner=%s on engram %s",
                winner == respondent, qid,
            )

    # ------------------------------------------------------------------
    # Pinned fact store — always-injected user context
    # ------------------------------------------------------------------

    def write_pinned(self, fact: str, reason: str = "") -> str:
        """Upsert a pinned fact.

        Uses a content-hash ID so identical facts don't accumulate as
        duplicates. Existing fact with same ID is silently replaced.

        Args:
            fact: The fact text to store and embed.
            reason: Optional human-readable rationale (stored in metadata).

        Returns:
            The ChromaDB document ID for this fact.
        """
        import hashlib
        doc_id = "pinned:" + hashlib.sha256(fact.encode()).hexdigest()[:16]
        embedding = self._embed_document(fact)
        self._collection.upsert(
            ids=[doc_id],
            documents=[fact],
            embeddings=[embedding],
            metadatas=[{"type": "pinned", "fact": fact, "reason": reason, "timestamp": time.time()}],
        )
        logger.debug("MemoryService: upserted pinned fact %s", doc_id)
        return doc_id

    def list_pinned(self) -> list[dict[str, Any]]:
        """Return all pinned facts, newest first.

        Returns:
            List of dicts with keys ``fact`` (str) and ``reason`` (str).
        """
        result = self._collection.get(
            where={"type": "pinned"},
            include=["metadatas", "documents"],
        )
        ids   = result.get("ids") or []
        docs  = result.get("documents") or []
        metas = result.get("metadatas") or []
        items = sorted(
            zip(ids, docs, metas),
            key=lambda x: x[2].get("timestamp", 0),
            reverse=True,
        )
        return [
            {"fact": doc, "reason": meta.get("reason", "")}
            for _, doc, meta in items
        ]

    def update_engram_sentiment(self, query_id: str, sentiment: str) -> None:
        """Merge ``user_sentiment`` into an existing engram's metadata.

        Args:
            query_id: The engram ID returned by ``write_episodic()``.
            sentiment: ``"positive"`` (stored as ``+1``) or
                ``"negative"`` (stored as ``-1``).

        Note:
            Uses the same read-before-write pattern as ``update_engram_score``
            to preserve existing metadata fields.
        """
        value = 1 if sentiment == "positive" else -1
        result = self._collection.get(ids=[query_id])
        if not result.get("ids"):
            logger.warning("MemoryService: engram %s not found — skipping sentiment update", query_id)
            return
        existing_meta = (result.get("metadatas") or [{}])[0]
        merged = {**existing_meta, "user_sentiment": value}
        self._collection.update(ids=[query_id], metadatas=[merged])
        logger.debug("MemoryService: updated user_sentiment=%d on engram %s", value, query_id)

    # ------------------------------------------------------------------
    # Embedding helpers
    # ------------------------------------------------------------------

    def _embed_document(self, text: str) -> list[float]:
        resp = ollama.embeddings(model=self._embed_model, prompt=f"search_document: {text}")
        return self._normalize(resp["embedding"])

    def _embed_query(self, text: str) -> list[float]:
        resp = ollama.embeddings(model=self._embed_model, prompt=f"search_query: {text}")
        return self._normalize(resp["embedding"])

    @staticmethod
    def _normalize(vec: list[float]) -> list[float]:
        import math
        norm = math.sqrt(sum(x * x for x in vec))
        return [x / norm for x in vec] if norm > 0 else vec
