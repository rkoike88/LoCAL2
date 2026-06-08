# Memory Participant

Two components handle memory: **MemoryAgent** (the bus participant that auto-ingests turns and annotates engrams) and **MemoryService** (the ChromaDB-backed store). SearchMemoryTool is Gemma's interface to the store at query time — see [tools.md](tools.md).

For state machine diagram, see [../diagrams/memory-agent-state-machine.md](../diagrams/memory-agent-state-machine.md).

---

## MemoryAgent

`src/local/agents/memory_agent.py`

MemoryAgent is system-triggered. It subscribes to three subjects and handles each independently:

### response.generation → engram ingest

1. Skip if `error=True`, or if query or answer is empty.
2. Skip RespondentB answers — only A is ingested into the shared episodic store.
3. Transition: `IDLE → INGESTING`.
4. Call LLM (gemma4:e4b, non-streaming) to classify intent and extract named entities.
5. Write engram to ChromaDB via MemoryService, including: query, answer, intent, entities, session_id, respondent_id, query_id.
6. Transition: `INGESTING → IDLE`.

Classification is best-effort. If the LLM call fails or produces unparseable JSON, the engram is written without intent/entity fields — the ingest never blocks on classification.

Intent classes: `fact | explanation | comparison | procedure`

### critique.result → score annotation

When `critique.result` arrives with a non-null `score` and a `query_id`:
1. Transition: `IDLE → UPDATING_SCORE`.
2. Call `MemoryService.update_engram_score(query_id, score)` — patches the matching engram with `critic_score`.
3. Transition: `UPDATING_SCORE → IDLE`.

### pairwise.result → winner annotation

When `pairwise.result` arrives with valid `query_id_a`, `query_id_b`, and `winner` (A or B):
1. Transition: `IDLE → ANNOTATING_PAIRWISE`.
2. Call `MemoryService.annotate_pairwise(query_id_a, query_id_b, winner)` — patches both engrams with `pairwise_winner: True/False`.
3. Transition: `ANNOTATING_PAIRWISE → IDLE`.

---

## MemoryService

`src/local/services/memory_service.py`

Wraps a ChromaDB collection (`local_memory` by default). Uses `nomic-embed-text` for embeddings.

### Key methods

| Method | Description |
|---|---|
| `write_episodic(query, answer, metadata, query_id)` | Embed and store a Q+A pair |
| `search_episodic(query, n_results)` | Semantic similarity search with score bias |
| `update_engram_score(query_id, score)` | Patch `critic_score` on an existing engram |
| `annotate_pairwise(qid_a, qid_b, winner)` | Patch `pairwise_winner` on both engrams |
| `list_episodic(n)` | Return the N most recent engrams (for MemoryWindow browse) |

### Retrieval weighting

`search_episodic()` applies a score bias to ChromaDB distance-ranked results:

```
bias = (critic_score - 3) × 0.05   if critic_score is set
     = 0                            otherwise
```

A score of 5 adds `+0.1` (floats up). A score of 1 adds `-0.1` (sinks down). Engrams without a critic score are unaffected. This biases recall toward answers that Prometheus rated highly.

### Memory namespaces

| Namespace | Content |
|---|---|
| `agent.<name>.episodic` | Per-agent interaction traces (episodic store) |
| `collective.knowledge` | Cross-agent elevated patterns (not yet implemented in v2) |
| `collective.sessions` | Summarized Q&A exchanges (not yet implemented in v2) |

---

## State Machine

States: `IDLE`, `INGESTING`, `UPDATING_SCORE`, `ANNOTATING_PAIRWISE`

All transitions follow the same pattern: `IDLE → <active state>` on start, `<active state> → IDLE` on complete. Errors are caught and logged; the transition to IDLE happens in a `finally` block regardless.

---

## Key Config Knobs

All settings in `config/memory.yaml`.

| Key | Default | Description |
|---|---|---|
| `collection` | `local_memory` | ChromaDB collection name |
| `chroma_path` | `.chroma` | Filesystem path for the ChromaDB store |
| `embed_model` | `nomic-embed-text` | Ollama embedding model |
| `n_results` | `5` | Number of results returned by search_episodic |
| `model` | `gemma4:e4b` | LLM used by MemoryAgent for intent/entity classification |
