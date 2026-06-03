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
    def __init__(
        self,
        chroma_path: Optional[str] = None,
        collection_name: Optional[str] = None,
        embed_model: Optional[str] = None,
        n_results: Optional[int] = None,
    ) -> None:
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
    ) -> str:
        """Write a Q+A pair as an episodic engram. Returns the engram ID.

        query_id, when provided, is used as the ChromaDB document ID so
        CriticAgent can later call update_engram_score() with the same ID.
        Falls back to a random UUID if omitted.
        """
        content = f"{query}\n{answer}"
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
            if intent:
                meta["intent"] = intent
            if entities:
                meta["entities"] = json.dumps(entities)
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
        """Return top-n episodic engrams by similarity, with entity overlap boost.

        Candidates with stored entities that appear in the query text are
        boosted. Candidates without entity metadata pass through unmodified.
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

        docs = (result.get("documents") or [[]])[0]
        metas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]

        sm_cfg = get_config("search_memory")
        critic_weight: float = sm_cfg.get("critic_score_weight", 0.05)

        candidates = []
        query_lower = query.lower()
        for doc, meta, dist in zip(docs, metas, distances):
            score = 1.0 - dist
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
            candidates.append({"content": doc, "metadata": meta, "score": score})

        candidates.sort(key=lambda c: c["score"], reverse=True)
        return candidates[:n]

    def update_engram_score(self, query_id: str, score: int) -> None:
        """Merge critic_score into an existing engram's metadata.

        Reads existing metadata first so the update does not wipe type,
        query, timestamp, intent, or entities — ChromaDB update() replaces
        the entire metadata dict, not individual fields.
        Logs a warning and returns cleanly if the engram is not found.
        """
        result = self._collection.get(ids=[query_id])
        if not result.get("ids"):
            logger.warning("MemoryService: engram %s not found — skipping score update", query_id)
            return
        existing_meta = (result.get("metadatas") or [{}])[0]
        merged = {**existing_meta, "critic_score": score}
        self._collection.update(ids=[query_id], metadatas=[merged])
        logger.debug("MemoryService: updated critic_score=%d on engram %s", score, query_id)

    def update_engram_sentiment(self, query_id: str, sentiment: str) -> None:
        """Merge user_sentiment into an existing engram's metadata.

        sentiment: "positive" (+1) or "negative" (-1) stored as integer.
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
        return resp["embedding"]

    def _embed_query(self, text: str) -> list[float]:
        resp = ollama.embeddings(model=self._embed_model, prompt=f"search_query: {text}")
        return resp["embedding"]
