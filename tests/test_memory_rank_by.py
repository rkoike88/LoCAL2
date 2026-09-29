"""search_memory.rank_by: which engram score re-ranks recall, and faithfulness metadata updates."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from local.services.memory_service import MemoryService

FAKE_EMBEDDING = [0.1] * 768


def _make_service(collection):
    mock_client = MagicMock()
    mock_client.get_or_create_collection.return_value = collection
    with patch("local.services.memory_service.chromadb.PersistentClient", return_value=mock_client), \
         patch("local.services.memory_service.ollama.embeddings", return_value={"embedding": FAKE_EMBEDDING}):
        return MemoryService()


def _query_result(ids, metas, distances):
    return {"ids": [ids], "documents": [ids], "metadatas": [metas], "distances": [distances]}


def _search(svc, rank_by, weight=0.05):
    cfg = {"critic_score_weight": weight, "rank_by": rank_by}
    with patch("local.services.memory_service.ollama.embeddings", return_value={"embedding": FAKE_EMBEDDING}), \
         patch("local.services.memory_service.get_config", side_effect=lambda name: cfg if name == "search_memory" else {}):
        return {r["content"]: r["score"] for r in svc.search_episodic("query")}


META = [
    {"type": "episodic", "query": "q", "critic_score": 5, "faithfulness_score": 1},  # pretty but wrong
    {"type": "episodic", "query": "q", "critic_score": 2, "faithfulness_score": 5},  # terse but grounded
    {"type": "episodic", "query": "q", "critic_score": 4},                           # no faithfulness grade
]


class TestRankBy:
    def _svc(self):
        col = MagicMock()
        col.query.return_value = _query_result(["pretty", "grounded", "ungraded"], META, [0.0, 0.0, 0.0])
        return _make_service(col)

    def test_default_ranks_by_critic_score(self):
        s = _search(self._svc(), "critic_score")
        assert s["pretty"] == pytest.approx(1.10) and s["grounded"] == pytest.approx(0.95) and s["ungraded"] == pytest.approx(1.05)

    def test_faithfulness_score_flips_order(self):
        s = _search(self._svc(), "faithfulness_score")
        assert s["pretty"] == pytest.approx(0.90) and s["grounded"] == pytest.approx(1.10)
        assert s["ungraded"] == pytest.approx(1.0)  # missing score → no adjustment

    def test_min_takes_lower_of_the_two(self):
        s = _search(self._svc(), "min")
        assert s["pretty"] == pytest.approx(0.90)     # min(5,1)=1
        assert s["grounded"] == pytest.approx(0.95)   # min(2,5)=2
        assert s["ungraded"] == pytest.approx(1.05)   # only critic_score present

    def test_unknown_rank_by_falls_back_to_critic_score(self):
        assert _search(self._svc(), "bogus") == _search(self._svc(), "critic_score")

    def test_weight_scales_offset(self):
        s = _search(self._svc(), "faithfulness_score", weight=0.10)
        assert s["pretty"] == pytest.approx(0.80) and s["grounded"] == pytest.approx(1.20)


class TestUpdateEngramFaithfulness:
    def test_merges_without_clobbering_existing_metadata(self):
        col = MagicMock()
        col.get.return_value = {"ids": ["q1"], "metadatas": [{"type": "episodic", "critic_score": 4, "entities": "[]"}]}
        svc = _make_service(col)
        svc.update_engram_faithfulness("q1", 1, "claims macOS", judge_model="qwen3.5:27b")
        merged = col.update.call_args.kwargs["metadatas"][0]
        assert merged["critic_score"] == 4 and merged["type"] == "episodic"
        assert merged["faithfulness_score"] == 1
        assert merged["faithfulness_feedback"] == "claims macOS"
        assert merged["faithfulness_judge"] == "qwen3.5:27b"

    def test_missing_engram_is_skipped(self):
        col = MagicMock()
        col.get.return_value = {"ids": []}
        svc = _make_service(col)
        svc.update_engram_faithfulness("nope", 5)
        col.update.assert_not_called()
