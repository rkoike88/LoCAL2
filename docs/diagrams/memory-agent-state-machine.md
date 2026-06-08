# Memory Agent State Machine

`src/local/agents/memory_agent_states.py`, `memory_agent_transitions.py`, `memory_agent_actions.py`

MemoryAgent handles three independent event types, each with its own IDLE→active→IDLE cycle. All transitions go through a `finally` block so errors never strand the agent in a non-IDLE state.

```mermaid
stateDiagram-v2
    [*] --> IDLE

    %% Engram ingest path
    IDLE --> INGESTING : START_INGEST\n(response.generation arrives)
    INGESTING --> IDLE : COMPLETE

    %% Score annotation path
    IDLE --> UPDATING_SCORE : UPDATE_SCORE\n(critique.result arrives)
    UPDATING_SCORE --> IDLE : COMPLETE

    %% Pairwise annotation path
    IDLE --> ANNOTATING_PAIRWISE : ANNOTATE_PAIRWISE\n(pairwise.result arrives)
    ANNOTATING_PAIRWISE --> IDLE : COMPLETE
```

## Key Characteristics

- **Three independent paths, all resetting to IDLE:** ingest, score update, and pairwise annotation are separate event handlers. There is no inter-dependency between them.
- **Errors are absorbed:** all handlers are wrapped in `try/except/finally`. On failure, the agent logs the error and transitions to IDLE regardless. The memory store may be incomplete, but the agent never stalls.
- **RespondentB skip on ingest:** `_handle_generation()` skips B answers. Only RespondentA Q+A pairs are written to episodic memory.
- **Classification is best-effort:** the LLM call for intent/entity classification happens before the ChromaDB write. If it fails, the engram is written without those fields.
- **Sequence of annotations for a single query:**
  1. `response.generation` → `INGESTING` → engram created (query_id as ChromaDB document ID)
  2. `critique.result` → `UPDATING_SCORE` → `critic_score` patched on engram
  3. `pairwise.result` → `ANNOTATING_PAIRWISE` → `pairwise_winner` patched on both engrams
