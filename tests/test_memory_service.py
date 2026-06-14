"""Unit tests for MemoryService — ChromaDB and ollama are fully mocked."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from local.services.memory_service import MemoryService

FAKE_EMBEDDING = [0.1, 0.2, 0.3]


def _make_service(collection: MagicMock) -> MemoryService:
    """Build a MemoryService with ChromaDB and ollama mocked."""
    mock_client = MagicMock()
    mock_client.get_or_create_collection.return_value = collection
    with patch("local.services.memory_service.chromadb.PersistentClient", return_value=mock_client), \
         patch("local.services.memory_service.ollama.embeddings", return_value={"embedding": FAKE_EMBEDDING}):
        svc = MemoryService(chroma_path="/tmp/test_chroma", collection_name="test", n_results=5)
    return svc


# ------------------------------------------------------------------
# Topic store
# ------------------------------------------------------------------

class TestWriteTopic:
    def test_upsert_called_with_correct_id(self):
        col = MagicMock()
        svc = _make_service(col)
        with patch("local.services.memory_service.ollama.embeddings", return_value={"embedding": FAKE_EMBEDDING}):
            svc.write_topic("user.language", "Python")
        col.upsert.assert_called_once()
        call_kwargs = col.upsert.call_args.kwargs
        assert call_kwargs["ids"] == ["topic:user.language"]
        assert call_kwargs["documents"] == ["Python"]
        assert call_kwargs["embeddings"] == [FAKE_EMBEDDING]
        assert call_kwargs["metadatas"][0]["type"] == "topic"
        assert call_kwargs["metadatas"][0]["topic"] == "user.language"

    def test_upsert_uses_search_document_prefix(self):
        col = MagicMock()
        svc = _make_service(col)
        with patch("local.services.memory_service.ollama.embeddings", return_value={"embedding": FAKE_EMBEDDING}) as mock_embed:
            svc.write_topic("project.stack", "FastAPI")
        prompt = mock_embed.call_args.kwargs.get("prompt") or mock_embed.call_args.args[0] if mock_embed.call_args.args else mock_embed.call_args.kwargs["prompt"]
        assert prompt.startswith("search_document:")


class TestRecallTopic:
    def test_returns_value_when_found(self):
        col = MagicMock()
        col.get.return_value = {"documents": ["Python"], "metadatas": [{"type": "topic"}]}
        svc = _make_service(col)
        result = svc.recall_topic("user.language")
        assert result == "Python"
        col.get.assert_called_once_with(ids=["topic:user.language"])

    def test_returns_none_when_missing(self):
        col = MagicMock()
        col.get.return_value = {"documents": [], "metadatas": []}
        svc = _make_service(col)
        result = svc.recall_topic("user.missing")
        assert result is None

    def test_returns_none_on_empty_collection(self):
        col = MagicMock()
        col.get.return_value = {}
        svc = _make_service(col)
        assert svc.recall_topic("anything") is None


# ------------------------------------------------------------------
# Episodic store — write
# ------------------------------------------------------------------

class TestWriteEpisodic:
    def test_add_called_with_episodic_type(self):
        col = MagicMock()
        svc = _make_service(col)
        with patch("local.services.memory_service.ollama.embeddings", return_value={"embedding": FAKE_EMBEDDING}):
            engram_id = svc.write_episodic("what is Python?", "A programming language.")
        col.add.assert_called_once()
        kwargs = col.add.call_args.kwargs
        assert kwargs["metadatas"][0]["type"] == "episodic"
        assert kwargs["metadatas"][0]["query"] == "what is Python?"
        assert isinstance(engram_id, str) and len(engram_id) == 36  # UUID

    def test_document_is_query_plus_answer(self):
        col = MagicMock()
        svc = _make_service(col)
        with patch("local.services.memory_service.ollama.embeddings", return_value={"embedding": FAKE_EMBEDDING}):
            svc.write_episodic("Q", "A")
        doc = col.add.call_args.kwargs["documents"][0]
        assert "Q" in doc and "A" in doc

    def test_intent_stored_when_provided(self):
        col = MagicMock()
        svc = _make_service(col)
        with patch("local.services.memory_service.ollama.embeddings", return_value={"embedding": FAKE_EMBEDDING}):
            svc.write_episodic("Q", "A", metadata={"intent": "fact", "entities": []})
        meta = col.add.call_args.kwargs["metadatas"][0]
        assert meta["intent"] == "fact"

    def test_entities_stored_as_json(self):
        col = MagicMock()
        svc = _make_service(col)
        with patch("local.services.memory_service.ollama.embeddings", return_value={"embedding": FAKE_EMBEDDING}):
            svc.write_episodic("Q", "A", metadata={"intent": "fact", "entities": ["Python", "Alice"]})
        meta = col.add.call_args.kwargs["metadatas"][0]
        assert json.loads(meta["entities"]) == ["Python", "Alice"]

    def test_no_intent_field_when_classification_empty(self):
        col = MagicMock()
        svc = _make_service(col)
        with patch("local.services.memory_service.ollama.embeddings", return_value={"embedding": FAKE_EMBEDDING}):
            svc.write_episodic("Q", "A", metadata={"intent": "", "entities": []})
        meta = col.add.call_args.kwargs["metadatas"][0]
        assert "intent" not in meta
        assert "entities" not in meta

    def test_write_without_metadata(self):
        col = MagicMock()
        svc = _make_service(col)
        with patch("local.services.memory_service.ollama.embeddings", return_value={"embedding": FAKE_EMBEDDING}):
            svc.write_episodic("Q", "A")
        meta = col.add.call_args.kwargs["metadatas"][0]
        assert "intent" not in meta
        assert "entities" not in meta

    def test_uses_provided_query_id_as_doc_id(self):
        col = MagicMock()
        svc = _make_service(col)
        with patch("local.services.memory_service.ollama.embeddings", return_value={"embedding": FAKE_EMBEDDING}):
            returned_id = svc.write_episodic("Q", "A", query_id="test-query-id-123")
        assert returned_id == "test-query-id-123"
        assert col.add.call_args.kwargs["ids"] == ["test-query-id-123"]

    def test_falls_back_to_uuid_when_query_id_absent(self):
        col = MagicMock()
        svc = _make_service(col)
        with patch("local.services.memory_service.ollama.embeddings", return_value={"embedding": FAKE_EMBEDDING}):
            returned_id = svc.write_episodic("Q", "A")
        assert len(returned_id) == 36  # UUID4 format


# ------------------------------------------------------------------
# Episodic store — search
# ------------------------------------------------------------------

def _make_query_result(docs, metas, distances):
    ids = [f"id-{i}" for i in range(len(docs))]
    return {"ids": [ids], "documents": [docs], "metadatas": [metas], "distances": [distances]}


class TestSearchEpisodic:
    def test_returns_ranked_candidates(self):
        col = MagicMock()
        col.query.return_value = _make_query_result(
            ["doc1", "doc2"],
            [{"type": "episodic", "query": "q1"}, {"type": "episodic", "query": "q2"}],
            [0.2, 0.4],
        )
        svc = _make_service(col)
        with patch("local.services.memory_service.ollama.embeddings", return_value={"embedding": FAKE_EMBEDDING}):
            results = svc.search_episodic("test query")
        assert results[0]["score"] > results[1]["score"]
        assert results[0]["content"] == "doc1"

    def test_entity_overlap_boosts_score(self):
        col = MagicMock()
        col.query.return_value = _make_query_result(
            ["doc_with_entity", "doc_without"],
            [
                {"type": "episodic", "query": "q", "entities": json.dumps(["Python"])},
                {"type": "episodic", "query": "q"},
            ],
            [0.3, 0.1],  # doc_without would win on raw similarity
        )
        svc = _make_service(col)
        with patch("local.services.memory_service.ollama.embeddings", return_value={"embedding": FAKE_EMBEDDING}):
            results = svc.search_episodic("tell me about Python")
        # doc_with_entity: score = 0.7 + 0.1 boost = 0.8
        # doc_without: score = 0.9 (no boost)
        # doc_without still wins — boost is additive, not override
        assert results[0]["content"] == "doc_without"
        entity_result = next(r for r in results if r["content"] == "doc_with_entity")
        assert entity_result["score"] == pytest.approx(0.8, abs=0.01)

    def test_uses_search_query_prefix(self):
        col = MagicMock()
        col.query.return_value = _make_query_result([], [], [])
        svc = _make_service(col)
        with patch("local.services.memory_service.ollama.embeddings", return_value={"embedding": FAKE_EMBEDDING}) as mock_embed:
            svc.search_episodic("my query")
        prompt = mock_embed.call_args.kwargs["prompt"]
        assert prompt.startswith("search_query:")

    def test_returns_empty_on_exception(self):
        col = MagicMock()
        col.query.side_effect = Exception("collection empty")
        svc = _make_service(col)
        with patch("local.services.memory_service.ollama.embeddings", return_value={"embedding": FAKE_EMBEDDING}):
            results = svc.search_episodic("anything")
        assert results == []

    def test_where_filter_restricts_to_episodic(self):
        col = MagicMock()
        col.query.return_value = _make_query_result([], [], [])
        svc = _make_service(col)
        with patch("local.services.memory_service.ollama.embeddings", return_value={"embedding": FAKE_EMBEDDING}):
            svc.search_episodic("query")
        kwargs = col.query.call_args.kwargs
        assert kwargs.get("where") == {"type": "episodic"}

    def test_critic_score_5_boosts_result(self):
        col = MagicMock()
        col.query.return_value = _make_query_result(
            ["high_quality", "unscored"],
            [
                {"type": "episodic", "query": "q", "critic_score": 5},
                {"type": "episodic", "query": "q"},
            ],
            [0.3, 0.1],  # unscored would win on raw similarity (score 0.9 vs 0.7)
        )
        svc = _make_service(col)
        cfg_patch = {"critic_score_weight": 0.05}
        with patch("local.services.memory_service.ollama.embeddings", return_value={"embedding": FAKE_EMBEDDING}), \
             patch("local.services.memory_service.get_config", side_effect=lambda name: cfg_patch if name == "search_memory" else {}):
            results = svc.search_episodic("query")
        # high_quality: 0.7 + (5-3)*0.05 = 0.70 + 0.10 = 0.80
        # unscored: 0.9 (no adjustment)
        # unscored still wins; high_quality score should be 0.80
        hq = next(r for r in results if r["content"] == "high_quality")
        assert hq["score"] == pytest.approx(0.80, abs=0.01)

    def test_critic_score_1_penalises_result(self):
        col = MagicMock()
        col.query.return_value = _make_query_result(
            ["low_quality"],
            [{"type": "episodic", "query": "q", "critic_score": 1}],
            [0.0],  # perfect similarity
        )
        svc = _make_service(col)
        cfg_patch = {"critic_score_weight": 0.05}
        with patch("local.services.memory_service.ollama.embeddings", return_value={"embedding": FAKE_EMBEDDING}), \
             patch("local.services.memory_service.get_config", side_effect=lambda name: cfg_patch if name == "search_memory" else {}):
            results = svc.search_episodic("query")
        # 1.0 + (1-3)*0.05 = 1.0 - 0.10 = 0.90
        assert results[0]["score"] == pytest.approx(0.90, abs=0.01)

    def test_unscored_engram_not_adjusted(self):
        col = MagicMock()
        col.query.return_value = _make_query_result(
            ["unscored"],
            [{"type": "episodic", "query": "q"}],
            [0.2],
        )
        svc = _make_service(col)
        cfg_patch = {"critic_score_weight": 0.05}
        with patch("local.services.memory_service.ollama.embeddings", return_value={"embedding": FAKE_EMBEDDING}), \
             patch("local.services.memory_service.get_config", side_effect=lambda name: cfg_patch if name == "search_memory" else {}):
            results = svc.search_episodic("query")
        assert results[0]["score"] == pytest.approx(0.80, abs=0.01)


# ------------------------------------------------------------------
# Episodic store — update_engram_score
# ------------------------------------------------------------------

class TestUpdateEngramScore:
    def _existing_meta(self) -> dict:
        return {
            "type": "episodic",
            "query": "what is Python?",
            "timestamp": 1234567890.0,
            "intent": "fact",
            "entities": '["Python"]',
        }

    def test_merges_score_preserving_existing_metadata(self):
        col = MagicMock()
        col.get.return_value = {"ids": ["qid-1"], "metadatas": [self._existing_meta()]}
        svc = _make_service(col)
        svc.update_engram_score("qid-1", 4)
        col.update.assert_called_once()
        updated_meta = col.update.call_args.kwargs["metadatas"][0]
        assert updated_meta["critic_score"] == 4
        assert updated_meta["type"] == "episodic"
        assert updated_meta["intent"] == "fact"
        assert updated_meta["entities"] == '["Python"]'
        assert updated_meta["timestamp"] == 1234567890.0

    def test_update_called_with_correct_id(self):
        col = MagicMock()
        col.get.return_value = {"ids": ["qid-42"], "metadatas": [self._existing_meta()]}
        svc = _make_service(col)
        svc.update_engram_score("qid-42", 5)
        assert col.update.call_args.kwargs["ids"] == ["qid-42"]

    def test_skips_update_when_engram_not_found(self):
        col = MagicMock()
        col.get.return_value = {"ids": [], "metadatas": []}
        svc = _make_service(col)
        svc.update_engram_score("missing-id", 3)
        col.update.assert_not_called()

    def test_skips_update_when_get_returns_no_ids_key(self):
        col = MagicMock()
        col.get.return_value = {}
        svc = _make_service(col)
        svc.update_engram_score("missing-id", 3)
        col.update.assert_not_called()


# ------------------------------------------------------------------
# Episodic store — update_engram_sentiment
# ------------------------------------------------------------------

class TestUpdateEngramSentiment:
    def _existing_meta(self) -> dict:
        return {"type": "episodic", "query": "q", "timestamp": 1234567890.0}

    def test_positive_stores_plus_one(self):
        col = MagicMock()
        col.get.return_value = {"ids": ["qid-1"], "metadatas": [self._existing_meta()]}
        svc = _make_service(col)
        svc.update_engram_sentiment("qid-1", "positive")
        updated = col.update.call_args.kwargs["metadatas"][0]
        assert updated["user_sentiment"] == 1

    def test_negative_stores_minus_one(self):
        col = MagicMock()
        col.get.return_value = {"ids": ["qid-1"], "metadatas": [self._existing_meta()]}
        svc = _make_service(col)
        svc.update_engram_sentiment("qid-1", "negative")
        updated = col.update.call_args.kwargs["metadatas"][0]
        assert updated["user_sentiment"] == -1

    def test_preserves_existing_metadata(self):
        col = MagicMock()
        col.get.return_value = {"ids": ["qid-1"], "metadatas": [self._existing_meta()]}
        svc = _make_service(col)
        svc.update_engram_sentiment("qid-1", "positive")
        updated = col.update.call_args.kwargs["metadatas"][0]
        assert updated["type"] == "episodic"
        assert updated["timestamp"] == 1234567890.0

    def test_skips_update_when_engram_not_found(self):
        col = MagicMock()
        col.get.return_value = {"ids": []}
        svc = _make_service(col)
        svc.update_engram_sentiment("missing", "positive")
        col.update.assert_not_called()


# ------------------------------------------------------------------
# Episodic store — list_episodic
# ------------------------------------------------------------------

class TestListEpisodic:
    def test_returns_engrams_sorted_newest_first(self):
        col = MagicMock()
        col.get.return_value = {
            "ids": ["old", "new", "mid"],
            "documents": ["doc-old", "doc-new", "doc-mid"],
            "metadatas": [
                {"type": "episodic", "timestamp": 1000.0},
                {"type": "episodic", "timestamp": 3000.0},
                {"type": "episodic", "timestamp": 2000.0},
            ],
        }
        svc = _make_service(col)
        results = svc.list_episodic()
        assert [r["id"] for r in results] == ["new", "mid", "old"]

    def test_respects_n_limit(self):
        col = MagicMock()
        col.get.return_value = {
            "ids": ["a", "b", "c"],
            "documents": ["da", "db", "dc"],
            "metadatas": [
                {"type": "episodic", "timestamp": 3.0},
                {"type": "episodic", "timestamp": 2.0},
                {"type": "episodic", "timestamp": 1.0},
            ],
        }
        svc = _make_service(col)
        results = svc.list_episodic(n=2)
        assert len(results) == 2
        assert results[0]["id"] == "a"

    def test_result_contains_id_content_metadata(self):
        col = MagicMock()
        col.get.return_value = {
            "ids": ["qid-1"],
            "documents": ["Q\nA"],
            "metadatas": [{"type": "episodic", "timestamp": 1.0, "critic_score": 4}],
        }
        svc = _make_service(col)
        results = svc.list_episodic()
        assert results[0]["id"] == "qid-1"
        assert results[0]["content"] == "Q\nA"
        assert results[0]["metadata"]["critic_score"] == 4

    def test_empty_collection_returns_empty_list(self):
        col = MagicMock()
        col.get.return_value = {"ids": [], "documents": [], "metadatas": []}
        svc = _make_service(col)
        assert svc.list_episodic() == []

    def test_filters_by_episodic_type(self):
        col = MagicMock()
        col.get.return_value = {"ids": [], "documents": [], "metadatas": []}
        svc = _make_service(col)
        svc.list_episodic()
        kwargs = col.get.call_args.kwargs
        assert kwargs.get("where") == {"type": "episodic"}
