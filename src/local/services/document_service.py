"""DocumentService — persistent RAG document knowledge base (ChromaDB + nomic-embed-text).

Documents are chunked, embedded, and stored in a dedicated collection separate from
episodic memory. Chunk IDs are deterministic so re-ingesting the same file is a safe
upsert with no duplicates.

nomic-embed-text prefixes (same as MemoryService):
  Write:  "search_document: "
  Query:  "search_query: "
"""
from __future__ import annotations

import hashlib
import logging
import time
from typing import Optional

import chromadb
import ollama

from local.config_loader import get_config
from local.utils.file_extract import PDF_EXT, TEXT_EXTS, extract_text

logger = logging.getLogger(__name__)

_CONFIG = "documents"


def _chunk_text(text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
    """Split text into overlapping fixed-size character chunks."""
    if not text.strip():
        return []
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        if end >= len(text):
            break
        start += chunk_size - chunk_overlap
    return chunks


def _chunk_id(source_file: str, chunk_index: int) -> str:
    """Deterministic chunk ID — safe upsert, no duplicates on re-ingest."""
    key = f"{source_file}::{chunk_index}"
    return hashlib.sha256(key.encode()).hexdigest()[:32]


class DocumentService:
    def __init__(
        self,
        chroma_path: Optional[str] = None,
        collection_name: Optional[str] = None,
        embed_model: Optional[str] = None,
    ) -> None:
        cfg = get_config(_CONFIG) or {}
        self._embed_model = embed_model or cfg.get("embed_model", "nomic-embed-text")
        self._chunk_size = cfg.get("chunk_size", 1500)
        self._chunk_overlap = cfg.get("chunk_overlap", 200)
        self._n_results = cfg.get("n_results", 5)
        path = chroma_path or cfg.get("chroma_path", ".chroma")
        name = collection_name or cfg.get("collection", "collective.documents")
        self._client = chromadb.PersistentClient(path=path)
        self._collection = self._client.get_or_create_collection(name=name)

    # ------------------------------------------------------------------
    # Ingestion
    # ------------------------------------------------------------------

    def ingest_file(self, path: str, on_progress=None) -> int:
        """Chunk, embed, and store a file. Returns number of chunks written.

        on_progress: optional callable(current: int, total: int) called after each chunk embedded.
        """
        from pathlib import Path as _Path
        ext = _Path(path).suffix.lower()
        source_name = _Path(path).name

        if ext == PDF_EXT:
            return self._ingest_pdf(path, source_name, on_progress=on_progress)
        elif ext in TEXT_EXTS:
            text = extract_text(path)
            return self.ingest_text(text, source_name, on_progress=on_progress)
        else:
            raise ValueError(f"Unsupported file type: {ext}")

    def ingest_text(self, text: str, source_name: str, on_progress=None) -> int:
        """Ingest already-extracted text. Returns number of chunks written."""
        chunks = _chunk_text(text, self._chunk_size, self._chunk_overlap)
        if not chunks:
            return 0
        self._upsert_chunks(chunks, source_name, page=None, on_progress=on_progress)
        return len(chunks)

    def _ingest_pdf(self, path: str, source_name: str, on_progress=None) -> int:
        """Ingest a PDF one page at a time, emitting progress after each page.

        Processes extract+embed per page so on_progress fires from the start
        rather than only after all pages are extracted upfront.
        """
        from pypdf import PdfReader
        reader = PdfReader(path)
        total_pages = len(reader.pages)
        if total_pages == 0:
            return 0

        now = time.time()
        ids, docs, embeddings, metas = [], [], [], []
        chunk_index = 0

        for page_num, page in enumerate(reader.pages, 1):
            page_text = page.extract_text() or ""
            for chunk_text in _chunk_text(page_text, self._chunk_size, self._chunk_overlap):
                ids.append(_chunk_id(source_name, chunk_index))
                docs.append(chunk_text)
                embeddings.append(self._embed_document(chunk_text))
                metas.append({
                    "source_file": source_name,
                    "chunk_index": chunk_index,
                    "page": page_num,
                    "ingested_at": now,
                    "type": "document",
                })
                chunk_index += 1
            if on_progress:
                on_progress(page_num, total_pages)

        if not ids:
            return 0
        self._collection.upsert(ids=ids, documents=docs, embeddings=embeddings, metadatas=metas)
        logger.info("DocumentService: ingested %d chunks from %s", chunk_index, source_name)
        return chunk_index

    def _upsert_chunks(self, chunks: list[str], source_name: str, page: Optional[int], on_progress=None) -> None:
        total = len(chunks)
        now = time.time()
        ids, docs, embeddings, metas = [], [], [], []
        for i, chunk_text in enumerate(chunks):
            ids.append(_chunk_id(source_name, i))
            docs.append(chunk_text)
            embeddings.append(self._embed_document(chunk_text))
            meta: dict = {
                "source_file": source_name,
                "chunk_index": i,
                "ingested_at": now,
                "type": "document",
            }
            if page is not None:
                meta["page"] = page
            metas.append(meta)
            if on_progress:
                on_progress(i + 1, total)
        self._collection.upsert(ids=ids, documents=docs, embeddings=embeddings, metadatas=metas)
        logger.info("DocumentService: ingested %d chunks from %s", len(ids), source_name)

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(self, query: str, n: Optional[int] = None) -> list[dict]:
        """Return top-n chunks by similarity.

        Each result: {content, source_file, chunk_index, page (optional), score}
        """
        n = n or self._n_results
        query_embedding = self._embed_query(query)
        try:
            result = self._collection.query(
                query_embeddings=[query_embedding],
                n_results=min(n, max(self._collection.count(), 1)),
                where={"type": "document"},
            )
        except Exception as exc:
            logger.warning("DocumentService: search failed: %s", exc)
            return []

        docs = (result.get("documents") or [[]])[0]
        metas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]

        results = []
        for doc, meta, dist in zip(docs, metas, distances):
            entry = {
                "content": doc,
                "source_file": meta.get("source_file", "unknown"),
                "chunk_index": meta.get("chunk_index", 0),
                "score": round(1.0 - dist, 4),
            }
            if "page" in meta:
                entry["page"] = meta["page"]
            results.append(entry)
        return results

    # ------------------------------------------------------------------
    # Management
    # ------------------------------------------------------------------

    def list_sources(self) -> list[str]:
        """Return unique source filenames in the collection."""
        try:
            result = self._collection.get(where={"type": "document"}, include=["metadatas"])
            metas = result.get("metadatas") or []
            seen: set[str] = set()
            sources = []
            for m in metas:
                name = m.get("source_file", "")
                if name and name not in seen:
                    seen.add(name)
                    sources.append(name)
            return sorted(sources)
        except Exception as exc:
            logger.warning("DocumentService: list_sources failed: %s", exc)
            return []

    def delete_source(self, source_file: str) -> int:
        """Delete all chunks for a source file. Returns count deleted."""
        try:
            result = self._collection.get(
                where={"source_file": source_file}, include=["metadatas"]
            )
            ids = result.get("ids") or []
            if ids:
                self._collection.delete(ids=ids)
            logger.info("DocumentService: deleted %d chunks for %s", len(ids), source_file)
            return len(ids)
        except Exception as exc:
            logger.warning("DocumentService: delete_source failed: %s", exc)
            return 0

    def count(self) -> int:
        """Total number of chunks in the collection."""
        return self._collection.count()

    # ------------------------------------------------------------------
    # Embedding helpers
    # ------------------------------------------------------------------

    def _embed_document(self, text: str) -> list[float]:
        resp = ollama.embed(model=self._embed_model, input=f"search_document: {text}")
        return resp["embeddings"][0]

    def _embed_query(self, text: str) -> list[float]:
        resp = ollama.embed(model=self._embed_model, input=f"search_query: {text}")
        return resp["embeddings"][0]
