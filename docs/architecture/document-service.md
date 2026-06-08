# Document Service

`DocumentService` (`src/local/services/document_service.py`) manages the RAG document store. It ingests files into named collections, chunks and embeds content, and serves semantic search results to `SearchLibraryTool`.

---

## Collections

Documents are organized into named collections. Each collection has a `name` (used as an identifier), `display_name`, and `description`. Collections are configured in `config/documents.yaml` and managed via DocumentsWindow in the UI.

Example collections (user-configured):
- `mba` — MBA Textbooks
- `cs` — Computer Science textbooks

All collections share the same ChromaDB path (`.chroma`) and collection name (`collective.documents`). The `collection` field in each chunk's metadata distinguishes them.

---

## Chunk ID Scheme

Each chunk gets a deterministic ID:
```
sha256(collection::source_path::chunk_index)[:16]
```

Including `collection` in the hash prevents ID collisions across collections that happen to have identically-named source files. Re-ingesting the same file regenerates the same IDs — ChromaDB upserts handle deduplication.

---

## Ingestion

`ingest_file(path, collection)` or `ingest_text(text, source_name, collection)`:

1. Extract text from the file (PDF page-by-page, or plain text for `.txt`/`.md`/`.py`/etc.) via `src/local/utils/file_extract.py`.
2. Split into chunks: `chunk_size` characters with `chunk_overlap` overlap.
3. For each chunk: call `ollama.embeddings(embed_model, text)` to get the vector.
4. Upsert into ChromaDB with metadata: `{source, collection, chunk_index}`.

Progress callbacks are supported — `DocumentsWindow` uses them to update the per-chunk progress bar in the UI.

**PDF ingestion** is page-by-page: each page is extracted and embedded before moving to the next. This ensures the progress bar updates immediately rather than waiting for the full extraction pass.

---

## Search

`search(query, collection=None, n_results=5)`:

- If `collection` is specified, adds a `where={"collection": collection}` filter to the ChromaDB query.
- If `collection=None`, searches across all collections.
- Returns top `n_results` chunks sorted by embedding similarity.

Results include: `document` (chunk text), `source`, `collection`, `chunk_index`, `distance`.

---

## Key Methods

| Method | Description |
|---|---|
| `ingest_file(path, collection)` | Chunk + embed a file into a collection |
| `ingest_text(text, source, collection)` | Chunk + embed raw text |
| `search(query, collection, n_results)` | Semantic search, optionally filtered by collection |
| `list_collections()` | Returns collection names present in the store |
| `list_sources_detail(collection)` | Returns sources in a collection with chunk counts |
| `delete_source(source, collection)` | Remove all chunks for a source from a collection |
| `delete_collection_chunks(collection)` | Remove all chunks in a collection |
| `move_source(source, from_coll, to_coll)` | Re-tag chunks without re-embedding (get + upsert + delete) |

---

## DocumentsWindow

The DocumentsWindow (col 1, row 0 in the panel grid) provides a two-level UI:

- **Collections view:** list of all collections with chunk counts. Actions: `+ Collection`, `Del`, `Refresh`.
- **Sources view (drill-in):** double-click a collection to see its source files. Actions: ingest new folder, move source to another collection, delete source.

Folder ingestion scans recursively for supported file types (PDF, txt, md, py, js, ts, yaml, json, csv) and ingests each file with a per-chunk progress bar.

---

## CLI Ingestion

`scripts/ingest.py` provides command-line access:

```bash
# List all collections
python scripts/ingest.py --list

# List sources in a specific collection
python scripts/ingest.py --list --collection mba

# Ingest a file or folder
python scripts/ingest.py --path /path/to/docs --collection mba

# Delete a source
python scripts/ingest.py --delete /path/to/file.pdf --collection mba
```

---

## Key Config Knobs

All settings in `config/documents.yaml`.

| Key | Default | Description |
|---|---|---|
| `collection` | `collective.documents` | ChromaDB collection name |
| `chroma_path` | `.chroma` | Filesystem path for the store |
| `embed_model` | `nomic-embed-text` | Ollama embedding model |
| `chunk_size` | `1500` | Characters per chunk |
| `chunk_overlap` | `200` | Overlap between adjacent chunks |
| `n_results` | `5` | Results returned per search query |
| `collections` | see config | List of `{name, display_name, description}` entries |
