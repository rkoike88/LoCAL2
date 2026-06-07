"""Unit tests for DocumentService — uses in-memory ChromaDB, mocked ollama embed."""

from unittest.mock import patch

from local.services.document_service import DocumentService, _chunk_text, _chunk_id


# ---------------------------------------------------------------------------
# _chunk_text
# ---------------------------------------------------------------------------

class TestChunkText:
    def test_short_text_single_chunk(self):
        assert _chunk_text("hello world", 1500, 200) == ["hello world"]

    def test_long_text_multiple_chunks(self):
        assert len(_chunk_text("x" * 4000, 1500, 200)) == 3

    def test_overlap_shared_content(self):
        text = "a" * 1500 + "b" * 1500
        chunks = _chunk_text(text, 1500, 200)
        assert chunks[1][:200] == "a" * 200

    def test_empty_text_returns_empty(self):
        assert _chunk_text("", 1500, 200) == []

    def test_whitespace_only_returns_empty(self):
        assert _chunk_text("   \n  ", 1500, 200) == []

    def test_exact_chunk_size_single_chunk(self):
        assert len(_chunk_text("z" * 1500, 1500, 200)) == 1


# ---------------------------------------------------------------------------
# _chunk_id — includes collection
# ---------------------------------------------------------------------------

class TestChunkId:
    def test_deterministic(self):
        assert _chunk_id("mba", "file.pdf", 0) == _chunk_id("mba", "file.pdf", 0)

    def test_different_index(self):
        assert _chunk_id("mba", "file.pdf", 0) != _chunk_id("mba", "file.pdf", 1)

    def test_different_file(self):
        assert _chunk_id("mba", "a.pdf", 0) != _chunk_id("mba", "b.pdf", 0)

    def test_different_collection_same_file(self):
        assert _chunk_id("mba", "Finance.pdf", 0) != _chunk_id("econ", "Finance.pdf", 0)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_service() -> DocumentService:
    import chromadb, uuid
    client = chromadb.EphemeralClient()
    svc = DocumentService.__new__(DocumentService)
    svc._embed_model = "nomic-embed-text"
    svc._chunk_size = 100
    svc._chunk_overlap = 10
    svc._n_results = 5
    svc._client = client
    svc._chroma_col = client.get_or_create_collection(f"test_{uuid.uuid4().hex[:8]}")
    return svc


def _fake_embed(text: str) -> list[float]:
    import hashlib
    h = int(hashlib.md5(text.encode()).hexdigest(), 16)
    return [(h >> i & 0xFF) / 255.0 for i in range(384)]


# ---------------------------------------------------------------------------
# Basic ingest + search
# ---------------------------------------------------------------------------

class TestIngestSearch:
    def setup_method(self):
        self.svc = _make_service()
        self._e1 = patch.object(self.svc, "_embed_document", side_effect=_fake_embed)
        self._e2 = patch.object(self.svc, "_embed_query", side_effect=_fake_embed)

    def test_ingest_text_returns_chunk_count(self):
        with self._e1, self._e2:
            n = self.svc.ingest_text("x" * 500, "test.txt", "mba")
        assert n > 0

    def test_count_increases_after_ingest(self):
        with self._e1, self._e2:
            self.svc.ingest_text("hello world " * 10, "doc.txt", "mba")
        assert self.svc.count() > 0

    def test_deterministic_ids_no_duplicates(self):
        with self._e1, self._e2:
            n1 = self.svc.ingest_text("a" * 300, "same.txt", "mba")
            n2 = self.svc.ingest_text("a" * 300, "same.txt", "mba")
        assert n1 == n2
        assert self.svc.count() == n1

    def test_search_returns_results(self):
        with self._e1, self._e2:
            self.svc.ingest_text("The quick brown fox", "fox.txt", "mba")
            results = self.svc.search("fox")
        assert len(results) > 0
        assert "content" in results[0]
        assert "source_file" in results[0]
        assert "collection" in results[0]

    def test_search_filtered_by_collection(self):
        with self._e1, self._e2:
            self.svc.ingest_text("alpha content " * 5, "alpha.txt", "mba")
            self.svc.ingest_text("beta content " * 5, "beta.txt", "econ")
            results = self.svc.search("content", collection="mba")
        assert all(r["collection"] == "mba" for r in results)

    def test_search_all_collections_when_none(self):
        with self._e1, self._e2:
            self.svc.ingest_text("alpha " * 5, "a.txt", "mba")
            self.svc.ingest_text("beta " * 5, "b.txt", "econ")
            results = self.svc.search("alpha beta", collection=None)
        assert len(results) > 0

    def test_empty_search_returns_empty(self):
        with self._e1, self._e2:
            results = self.svc.search("anything")
        assert results == []


# ---------------------------------------------------------------------------
# Same filename in different collections — no ID collision
# ---------------------------------------------------------------------------

class TestSameFilenameInDifferentCollections:
    def setup_method(self):
        self.svc = _make_service()
        self._e1 = patch.object(self.svc, "_embed_document", side_effect=_fake_embed)
        self._e2 = patch.object(self.svc, "_embed_query", side_effect=_fake_embed)

    def test_independent_chunk_counts(self):
        with self._e1, self._e2:
            n1 = self.svc.ingest_text("mba content " * 5, "Finance.pdf", "mba")
            n2 = self.svc.ingest_text("econ content " * 5, "Finance.pdf", "econ")
        assert self.svc.count() == n1 + n2

    def test_delete_one_leaves_other(self):
        with self._e1, self._e2:
            self.svc.ingest_text("mba content " * 5, "Finance.pdf", "mba")
            self.svc.ingest_text("econ content " * 5, "Finance.pdf", "econ")
            before = self.svc.count()
            deleted = self.svc.delete_source("Finance.pdf", "mba")
        assert deleted > 0
        assert self.svc.count() == before - deleted
        assert "Finance.pdf" in self.svc.list_sources("econ")
        assert "Finance.pdf" not in self.svc.list_sources("mba")


# ---------------------------------------------------------------------------
# list_sources / list_sources_detail / count
# ---------------------------------------------------------------------------

class TestListSources:
    def setup_method(self):
        self.svc = _make_service()
        self._e1 = patch.object(self.svc, "_embed_document", side_effect=_fake_embed)
        self._e2 = patch.object(self.svc, "_embed_query", side_effect=_fake_embed)

    def test_list_sources_filtered_by_collection(self):
        with self._e1, self._e2:
            self.svc.ingest_text("content a", "a.txt", "mba")
            self.svc.ingest_text("content b", "b.txt", "econ")
        assert "a.txt" in self.svc.list_sources("mba")
        assert "b.txt" not in self.svc.list_sources("mba")

    def test_list_sources_detail_has_chunk_counts(self):
        with self._e1, self._e2:
            self.svc.ingest_text("x" * 300, "big.txt", "mba")
        detail = self.svc.list_sources_detail("mba")
        assert any(d["source_file"] == "big.txt" and d["chunk_count"] > 0 for d in detail)

    def test_count_per_collection(self):
        with self._e1, self._e2:
            self.svc.ingest_text("a " * 5, "a.txt", "mba")
            self.svc.ingest_text("b " * 5, "b.txt", "econ")
        mba = self.svc.count("mba")
        econ = self.svc.count("econ")
        assert mba > 0
        assert econ > 0
        assert mba + econ == self.svc.count()


# ---------------------------------------------------------------------------
# move_source
# ---------------------------------------------------------------------------

class TestMoveSource:
    def setup_method(self):
        self.svc = _make_service()
        self._e1 = patch.object(self.svc, "_embed_document", side_effect=_fake_embed)
        self._e2 = patch.object(self.svc, "_embed_query", side_effect=_fake_embed)

    def test_move_changes_collection(self):
        with self._e1, self._e2:
            self.svc.ingest_text("content " * 5, "doc.txt", "mba")
            n = self.svc.move_source("doc.txt", "mba", "econ")
        assert n > 0
        assert "doc.txt" in self.svc.list_sources("econ")
        assert "doc.txt" not in self.svc.list_sources("mba")

    def test_move_preserves_chunk_count(self):
        with self._e1, self._e2:
            original = self.svc.ingest_text("x " * 50, "big.txt", "mba")
            moved = self.svc.move_source("big.txt", "mba", "econ")
        assert moved == original
        assert self.svc.count("econ") == original
        assert self.svc.count("mba") == 0

    def test_move_nonexistent_returns_zero(self):
        assert self.svc.move_source("ghost.txt", "mba", "econ") == 0


# ---------------------------------------------------------------------------
# delete_source / delete_collection_chunks
# ---------------------------------------------------------------------------

class TestDelete:
    def setup_method(self):
        self.svc = _make_service()
        self._e1 = patch.object(self.svc, "_embed_document", side_effect=_fake_embed)
        self._e2 = patch.object(self.svc, "_embed_query", side_effect=_fake_embed)

    def test_delete_source_removes_only_target(self):
        with self._e1, self._e2:
            self.svc.ingest_text("delete me " * 10, "del.txt", "mba")
            self.svc.ingest_text("keep me " * 10, "keep.txt", "mba")
            before = self.svc.count()
            deleted = self.svc.delete_source("del.txt", "mba")
        assert deleted > 0
        assert self.svc.count() == before - deleted
        assert "del.txt" not in self.svc.list_sources("mba")
        assert "keep.txt" in self.svc.list_sources("mba")

    def test_delete_collection_chunks_leaves_other(self):
        with self._e1, self._e2:
            self.svc.ingest_text("mba stuff " * 10, "m.txt", "mba")
            self.svc.ingest_text("econ stuff " * 10, "e.txt", "econ")
            n = self.svc.delete_collection_chunks("mba")
        assert n > 0
        assert self.svc.count("mba") == 0
        assert self.svc.count("econ") > 0
