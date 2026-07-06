# LoCAL2 Documentation

LoCAL2 (Loosely-Coupled Agent Language model, v2) — a bus-based personal AI assistant where Gemma is the orchestrator.

- **[README](../README.md)** — project overview, feature list, architecture summary
- **[Getting Started](../GETTING_STARTED.md)** — installation, quick start, configuration

---

## Architecture

| Doc | What it covers |
|---|---|
| [local2-design.md](architecture/local2-design.md) | Design philosophy, participant roles, query flow, memory model, architecture invariants |
| [messaging.md](architecture/messaging.md) | All bus subjects, MessageEnvelope format, key payload schemas |
| [generator-participant.md](architecture/generator-participant.md) | GeneratorAgent: query handling, tool dispatch, compaction, generator.status, state machine |
| [critic-participant.md](architecture/critic-participant.md) | CriticAgent: absolute grading (1–5), Prometheus prompts, skip-on-tool-calls rule |
| [memory-participant.md](architecture/memory-participant.md) | MemoryAgent + MemoryService: auto-ingest, score annotation, retrieval weighting |
| [tools.md](architecture/tools.md) | All 7 tools: protocol, schema discovery, per-tool config and behavior |
| [conversation-service.md](architecture/conversation-service.md) | Session management, history format, token tracking, compaction |
| [document-service.md](architecture/document-service.md) | RAG document store: collections, chunking, ingestion, DocumentsWindow |
| [config-reference.md](architecture/config-reference.md) | Every config file and every knob, with defaults and descriptions |
| [xai-privacy.md](architecture/xai-privacy.md) | XAI features (thinking tokens, tool trace, retrieval attribution, groundedness, Prometheus scoring) and privacy model (local LLM, SearXNG, ChromaDB) |

---

## Diagrams

| Diagram | What it shows |
|---|---|
| [generator-state-machine.md](diagrams/generator-state-machine.md) | Generator states: IDLE → RECEIVING → GENERATING → DISPATCHING_TOOL → PUBLISHING → IDLE |
| [critic-state-machine.md](diagrams/critic-state-machine.md) | Critic states: IDLE → RECEIVING → GRADING → PUBLISHING → IDLE |
| [memory-agent-state-machine.md](diagrams/memory-agent-state-machine.md) | Memory agent states: ingest path + score annotation path |
| [tool-call-flow.md](diagrams/tool-call-flow.md) | Sequence diagram of a single tool call within a generation turn |
| [bus-topology.md](diagrams/bus-topology.md) | All participants, what they publish, what they subscribe to |

---

## Plans

Phase-by-phase implementation plans: [plans/index.md](plans/index.md)

---

## Stories

End-to-end acceptance criteria: [stories/index.md](stories/index.md)
