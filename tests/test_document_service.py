"""Unit tests for DocumentService — uses in-memory ChromaDB, mocked ollama embed."""

from unittest.mock import MagicMock, patch
import pytest

from local.services.document_service import DocumentService, _chunk_text, _chunk_id


# ---------------------------------------------------------------------------
# _chunk_text
# ---------------------------------------------------------------------------

class TestChunkText:
    def test_short_text_single_chunk(self):
        chunks = _chunk_text("hello world", chunk_size=1500, chunk_overlap=200)
        assert chunks == ["hello world"]

    def test_long_text_multiple_chunks(self):
        text = "x" * 4000
        chunks = _chunk_text(text, chunk_size=1500, chunk_overlap=200)
        assert len(chunks) == 3

    def test_overlap_shared_content(self):
        text = "a" * 1500 + "b" * 1500
        chunks = _chunk_text(text, chunk_size=1500, chunk_overlap=200)
        # Second chunk starts 1300 chars in, so it begins with 200 "a"s
        assert chunks[1][:200] == "a" * 200

    def test_empty_text_returns_empty(self):
        assert _chunk_text("", chunk_size=1500, chunk_overlap=200) == []

    def test_whitespace_only_returns_empty(self):
        assert _chunk_text("   \n  ", chunk_size=1500, chunk_overlap=200) == []

    def test_exact_chunk_size_single_chunk(self):
        text = "z" * 1500
        chunks = _chunk_text(text, chunk_size=1500, chunk_overlap=200)
        assert len(chunks) == 1


# ---------------------------------------------------------------------------
# _chunk_id — deterministic, no duplicates
# ---------------------------------------------------------------------------

class TestChunkId:
    def test_deterministic(self):
        assert _chunk_id("file.pdf", 0) == _chunk_id("file.pdf", 0)

    def test_different_index_different_id(self):
        assert _chunk_id("file.pdf", 0) != _chunk_id("file.pdf", 1)

    def test_different_file_different_id(self):
        assert _chunk_id("a.pdf", 0) != _chunk_id("b.pdf", 0)


# ---------------------------------------------------------------------------
# DocumentService — in-memory ChromaDB, mocked embeddings
# ---------------------------------------------------------------------------

def _make_service() -> DocumentService:
    """Create a DocumentService backed by an ephemeral in-memory ChromaDB.

    Uses a unique collection name per call so tests never share state even
    though EphemeralClient instances share the same in-process memory store.
    """
    import chromadb
    import uuid
    client = chromadb.EphemeralClient()
    unique_name = f"test_docs_{uuid.uuid4().hex[:8]}"

    svc = DocumentService.__new__(DocumentService)
    svc._embed_model = "nomic-embed-text"
    svc._chunk_size = 100
    svc._chunk_overlap = 10
    svc._n_results = 5
    svc._client = client
    svc._collection = client.get_or_create_collection(unique_name)
    return svc


def _fake_embed(text: str) -> list[float]:
    """Simple deterministic fake embedding based on text length."""
    import hashlib
    h = int(hashlib.md5(text.encode()).hexdigest(), 16)
    return [(h >> i & 0xFF) / 255.0 for i in range(384)]


class TestDocumentService:
    def setup_method(self):
        self.svc = _make_service()

    def _patch_embed(self):
        return patch.object(self.svc, "_embed_document", side_effect=_fake_embed), \
               patch.object(self.svc, "_embed_query", side_effect=_fake_embed)

    def test_ingest_text_returns_chunk_count(self):
        e1, e2 = self._patch_embed()
        with e1, e2:
            n = self.svc.ingest_text("x" * 500, "test.txt")
        assert n > 0

    def test_ingest_text_count_increases_collection(self):
        e1, e2 = self._patch_embed()
        with e1, e2:
            self.svc.ingest_text("hello world " * 10, "doc.txt")
        assert self.svc.count() > 0

    def test_deterministic_ids_no_duplicates(self):
        e1, e2 = self._patch_embed()
        with e1, e2:
            n1 = self.svc.ingest_text("a" * 300, "same.txt")
            n2 = self.svc.ingest_text("a" * 300, "same.txt")
        assert n1 == n2
        assert self.svc.count() == n1  # no duplicates

    def test_search_returns_results(self):
        e1, e2 = self._patch_embed()
        with e1, e2:
            self.svc.ingest_text("The quick brown fox jumps over the lazy dog", "fox.txt")
            results = self.svc.search("fox")
        assert len(results) > 0
        assert "content" in results[0]
        assert "source_file" in results[0]

    def test_metadata_stored(self):
        e1, e2 = self._patch_embed()
        with e1, e2:
            self.svc.ingest_text("metadata test content " * 5, "meta.txt")
            results = self.svc.search("metadata")
        assert results[0]["source_file"] == "meta.txt"
        assert "chunk_index" in results[0]

    def test_list_sources(self):
        e1, e2 = self._patch_embed()
        with e1, e2:
            self.svc.ingest_text("content a", "file_a.txt")
            self.svc.ingest_text("content b", "file_b.txt")
        sources = self.svc.list_sources()
        assert "file_a.txt" in sources
        assert "file_b.txt" in sources

    def test_delete_source(self):
        e1, e2 = self._patch_embed()
        with e1, e2:
            self.svc.ingest_text("delete me " * 20, "to_delete.txt")
            self.svc.ingest_text("keep me " * 20, "keep.txt")
            before = self.svc.count()
            deleted = self.svc.delete_source("to_delete.txt")
        assert deleted > 0
        assert self.svc.count() == before - deleted
        assert "to_delete.txt" not in self.svc.list_sources()

    def test_empty_collection_search_returns_empty(self):
        e1, e2 = self._patch_embed()
        with e1, e2:
            results = self.svc.search("anything")
        assert results == []
