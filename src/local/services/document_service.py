"""DocumentService — persistent RAG document knowledge base (ChromaDB + nomic-embed-text).

Documents are chunked, embedded, and stored in a dedicated ChromaDB collection.
Each chunk carries a `collection` metadata field for logical grouping.

Chunk IDs are deterministic — safe upsert on re-ingest.
ID key: sha256(collection::source_file::chunk_index)

nomic-embed-text prefixes:
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

logger = logging.getLogger(__name__)

_CONFIG = "documents"


def _chunk_text(text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
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


def _chunk_id(collection: str, source_file: str, chunk_index: int) -> str:
    """Deterministic chunk ID scoped to collection — prevents cross-collection collisions."""
    key = f"{collection}::{source_file}::{chunk_index}"
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
        self._chroma_col = self._client.get_or_create_collection(name=name)

    # ------------------------------------------------------------------
    # Config helpers
    # ------------------------------------------------------------------

    @staticmethod
    def get_collections_config() -> list[dict]:
        """Return the collections list from documents.yaml, or []."""
        cfg = get_config(_CONFIG) or {}
        return cfg.get("collections") or []

    # ------------------------------------------------------------------
    # Ingestion
    # ------------------------------------------------------------------

    def ingest_file(self, path: str, collection: str, on_progress=None) -> int:
        """Chunk, embed, and store a file into the named collection.

        Returns number of chunks written.
        """
        from pathlib import Path as _Path
        from local.utils.file_extract import PDF_EXT, TEXT_EXTS, extract_text

        ext = _Path(path).suffix.lower()
        source_name = _Path(path).name

        if ext == PDF_EXT:
            return self._ingest_pdf(path, source_name, collection, on_progress=on_progress)
        elif ext in TEXT_EXTS:
            text = extract_text(path)
            return self.ingest_text(text, source_name, collection, on_progress=on_progress)
        else:
            raise ValueError(f"Unsupported file type: {ext}")

    def ingest_text(self, text: str, source_name: str, collection: str, on_progress=None) -> int:
        """Ingest already-extracted text into the named collection."""
        chunks = _chunk_text(text, self._chunk_size, self._chunk_overlap)
        if not chunks:
            return 0
        self._upsert_chunks(chunks, source_name, collection, page=None, on_progress=on_progress)
        return len(chunks)

    def _ingest_pdf(self, path: str, source_name: str, collection: str, on_progress=None) -> int:
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
            for chunk in _chunk_text(page_text, self._chunk_size, self._chunk_overlap):
                ids.append(_chunk_id(collection, source_name, chunk_index))
                docs.append(chunk)
                embeddings.append(self._embed_document(chunk))
                metas.append({
                    "collection": collection,
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
        self._chroma_col.upsert(ids=ids, documents=docs, embeddings=embeddings, metadatas=metas)
        logger.info("DocumentService: ingested %d chunks from %s into %s", chunk_index, source_name, collection)
        return chunk_index

    def _upsert_chunks(self, chunks: list[str], source_name: str, collection: str,
                       page: Optional[int], on_progress=None) -> None:
        total = len(chunks)
        now = time.time()
        ids, docs, embeddings, metas = [], [], [], []
        for i, chunk in enumerate(chunks):
            ids.append(_chunk_id(collection, source_name, i))
            docs.append(chunk)
            embeddings.append(self._embed_document(chunk))
            meta: dict = {
                "collection": collection,
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
        self._chroma_col.upsert(ids=ids, documents=docs, embeddings=embeddings, metadatas=metas)
        logger.info("DocumentService: ingested %d chunks from %s into %s", len(ids), source_name, collection)

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(self, query: str, collection: Optional[str] = None, n: Optional[int] = None) -> list[dict]:
        """Return top-n chunks by similarity.

        collection=None searches across all collections.
        Each result: {content, source_file, collection, chunk_index, page?, score}
        """
        n = n or self._n_results
        total = self._chroma_col.count()
        if total == 0:
            return []
        query_embedding = self._embed_query(query)

        where = self._where(collection)
        try:
            result = self._chroma_col.query(
                query_embeddings=[query_embedding],
                n_results=min(n, total),
                where=where,
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
                "collection": meta.get("collection", ""),
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

    def list_collections(self) -> list[dict]:
        """Return collection definitions from documents.yaml with chunk counts from Chroma.

        Each item: {name, display_name, description, chunk_count, source_count}
        """
        configs = self.get_collections_config()
        result = []
        for col in configs:
            name = col.get("name", "")
            chunk_count = self._count_where({"collection": name})
            source_count = len(self._unique_sources(name))
            result.append({
                "name": name,
                "display_name": col.get("display_name", name),
                "description": col.get("description", ""),
                "chunk_count": chunk_count,
                "source_count": source_count,
            })
        return result

    def list_sources(self, collection: Optional[str] = None) -> list[str]:
        """Return unique source filenames, optionally filtered by collection."""
        return sorted(self._unique_sources(collection))

    def list_sources_detail(self, collection: Optional[str] = None) -> list[dict]:
        """Return [{source_file, chunk_count}] sorted by name, optionally filtered by collection."""
        try:
            result = self._chroma_col.get(where=self._where(collection), include=["metadatas"])
            metas = result.get("metadatas") or []
        except Exception as exc:
            logger.warning("DocumentService: list_sources_detail failed: %s", exc)
            return []

        counts: dict[str, int] = {}
        for m in metas:
            name = m.get("source_file", "")
            if name:
                counts[name] = counts.get(name, 0) + 1
        return [{"source_file": k, "chunk_count": v} for k, v in sorted(counts.items())]

    def delete_source(self, source_file: str, collection: str) -> int:
        """Delete all chunks for source_file within collection. Returns count deleted."""
        try:
            where = {"$and": [{"source_file": {"$eq": source_file}},
                               {"collection": {"$eq": collection}}]}
            result = self._chroma_col.get(where=where, include=["metadatas"])
            ids = result.get("ids") or []
            if ids:
                self._chroma_col.delete(ids=ids)
            logger.info("DocumentService: deleted %d chunks for %s/%s", len(ids), collection, source_file)
            return len(ids)
        except Exception as exc:
            logger.warning("DocumentService: delete_source failed: %s", exc)
            return 0

    def move_source(self, source_file: str, from_collection: str, to_collection: str) -> int:
        """Move all chunks of source_file from one collection to another.

        Uses get+upsert+delete so all metadata fields are preserved.
        Embeddings are reused from Chroma — no Ollama call needed.
        Returns number of chunks moved.
        """
        try:
            where = {"$and": [{"source_file": {"$eq": source_file}},
                               {"collection": {"$eq": from_collection}}]}
            result = self._chroma_col.get(
                where=where,
                include=["documents", "embeddings", "metadatas"],
            )
            old_ids = result.get("ids") or []
            if not old_ids:
                return 0

            raw_docs = result.get("documents") or []
            raw_embeddings = result.get("embeddings")
            if raw_embeddings is None:
                raw_embeddings = []
            raw_metas = result.get("metadatas") or []

            new_ids = [
                _chunk_id(to_collection, source_file, m.get("chunk_index", i))
                for i, m in enumerate(raw_metas)
            ]
            new_metas = [{**m, "collection": to_collection} for m in raw_metas]

            self._chroma_col.upsert(
                ids=new_ids,
                documents=raw_docs,
                embeddings=raw_embeddings,
                metadatas=new_metas,
            )
            self._chroma_col.delete(ids=old_ids)
            logger.info("DocumentService: moved %d chunks %s → %s/%s",
                        len(old_ids), from_collection, to_collection, source_file)
            return len(old_ids)
        except Exception as exc:
            logger.warning("DocumentService: move_source failed: %s", exc)
            return 0

    def delete_collection_chunks(self, collection_name: str) -> int:
        """Delete all chunks belonging to a collection. Returns count deleted."""
        try:
            result = self._chroma_col.get(
                where={"collection": {"$eq": collection_name}},
                include=["metadatas"],
            )
            ids = result.get("ids") or []
            if ids:
                self._chroma_col.delete(ids=ids)
            logger.info("DocumentService: deleted %d chunks for collection %s", len(ids), collection_name)
            return len(ids)
        except Exception as exc:
            logger.warning("DocumentService: delete_collection_chunks failed: %s", exc)
            return 0

    def count(self, collection: Optional[str] = None) -> int:
        """Total chunks, optionally filtered by collection."""
        if collection is None:
            return self._chroma_col.count()
        return self._count_where({"collection": collection})

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _where(self, collection: Optional[str]) -> dict:
        if collection:
            return {"$and": [{"type": {"$eq": "document"}},
                              {"collection": {"$eq": collection}}]}
        return {"type": {"$eq": "document"}}

    def _count_where(self, where: dict) -> int:
        try:
            result = self._chroma_col.get(where=where, include=["metadatas"])
            return len(result.get("ids") or [])
        except Exception:
            return 0

    def _unique_sources(self, collection: Optional[str] = None) -> list[str]:
        try:
            result = self._chroma_col.get(where=self._where(collection), include=["metadatas"])
            metas = result.get("metadatas") or []
            seen: set[str] = set()
            sources = []
            for m in metas:
                name = m.get("source_file", "")
                if name and name not in seen:
                    seen.add(name)
                    sources.append(name)
            return sources
        except Exception as exc:
            logger.warning("DocumentService: _unique_sources failed: %s", exc)
            return []

    def _embed_document(self, text: str) -> list[float]:
        resp = ollama.embed(model=self._embed_model, input=f"search_document: {text}")
        return resp["embeddings"][0]

    def _embed_query(self, text: str) -> list[float]:
        resp = ollama.embed(model=self._embed_model, input=f"search_query: {text}")
        return resp["embeddings"][0]
