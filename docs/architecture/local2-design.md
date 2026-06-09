# LoCAL2 Architecture Overview

LoCAL2 (Loosely-Coupled Agent Language model, v2) is the second generation of LoCAL. The bus-based pub/sub architecture is unchanged. What changed is where the intelligence lives.

For message format and subject constants, see [messaging.md](messaging.md).

---

## 1. Design Philosophy

**LoCAL1 vs LoCAL2.** In v1 an explicit orchestration layer (AnalystAgent, SynthesizerAgent, GatewayAgent) wrapped around the LLM. The LLM was a leaf — it received a preprocessed, decomposed sub-task and returned a raw answer. In v2 the LLM is the root. Gemma receives the raw user query and the full conversation history, and decides natively whether to search, recall, fetch, or answer directly.

**Three going-in objectives:**

1. **Native conversation history** — the generator receives the full messages array; Gemma handles follow-up, pronoun resolution, and multi-turn reasoning without preprocessing.
2. **Tool-native architecture** — web search, memory recall, document search, and other capabilities are synchronous tool calls within a generation turn, not async bus-dispatched task pipelines.
3. **Externalized LLM workings** — thinking tokens, tool calls, memory recalls, state transitions, and context window fill are first-class visible artifacts surfaced in the UI, not hidden intermediates.

**What was removed from v1:** GatewayAgent, AnalystAgent, SynthesizerAgent, task DAG pipeline, query preprocessing, explicit query rewriting, task decomposition, the complexity gate.

---

## 2. Participant Roles

| Participant | Type | Triggered by | Publishes to |
|---|---|---|---|
| **GeneratorAgent** | `*Agent` (LLM) | `query.received` | `response.generation`, `answer.dialog`, `agent.transition`, `generator.status` |
| **CriticAgent** | `*Agent` (LLM) | `response.generation` | `critique.result`, `agent.transition` |
| **MemoryAgent** | `*Agent` (LLM) | `response.generation`, `critique.result` | `agent.transition` |
| **RewardService** | Service | `user.feedback` | `reward.event` |
| **SearchMemoryTool** | `*Tool` | `tool.request.search_memory` | `tool.result.search_memory`, `tool.activity.search_memory` |
| **WebSearchTool** | `*Tool` | `tool.request.web_search` | `tool.result.web_search`, `tool.activity.web_search` |
| **WebFetchTool** | `*Tool` | `tool.request.web_fetch` | `tool.result.web_fetch`, `tool.activity.web_fetch` |
| **DateTimeTool** | `*Tool` | `tool.request.get_datetime` | `tool.result.get_datetime`, `tool.activity.get_datetime` |
| **LocationTool** | `*Tool` | `tool.request.get_location` | `tool.result.get_location`, `tool.activity.get_location` |
| **SemanticScholarTool** | `*Tool` | `tool.request.search_papers` | `tool.result.search_papers`, `tool.activity.search_papers` |
| **SearchLibraryTool** | `*Tool` | `tool.request.search_library` | `tool.result.search_library`, `tool.activity.search_library` |
| **FastAPI Gateway** | UI/API | HTTP/WebSocket | `query.received`, `compaction.request`, `user.feedback`, `schema.request` |

**Participant naming convention:**

| Suffix | Has LLM | Triggered by | Output |
|---|---|---|---|
| `*Agent` | Yes | System (bus event) | Bus subject (not Gemma) |
| `*Tool` | No | Gemma (`tool.request.*`) | Back to Gemma via `tool.result.*` |
| `*AgentTool` | Yes | Gemma (`tool.request.*`) | Back to Gemma via `tool.result.*` |

---

## 3. Query Flow — Happy Path

```
User types query
  → Gateway publishes query.received
  → GeneratorAgent receives, transitions IDLE → RECEIVING → GENERATING
  → Gemma streams: thinking tokens → GENERATION_THINKING
  → If Gemma calls a tool:
      GeneratorAgent → DISPATCHING_TOOL
      publishes tool.request.<name>
      Tool executes, publishes tool.result.<name>
      GeneratorAgent receives result, feeds back to Gemma → GENERATING
      (repeats up to max_tool_iterations)
  → Final text answer → PUBLISHING
  → GeneratorAgent publishes response.generation + answer.dialog → IDLE
  → CriticAgent receives response.generation → grades it → publishes critique.result
  → MemoryAgent receives response.generation → ingests engram
  → MemoryAgent receives critique.result → annotates engram with score
  → UI receives response.generation → displays answer with critic badge
  → User optionally thumbs up/down → user.feedback → RewardService → reward.event
```

---

## 4. Tool Schema Discovery

Tools are registered dynamically. On startup, every tool publishes its JSON schema on `tool.schema`. GeneratorAgent subscribes, builds a live registry, and passes the current schema list to every `ollama.chat()` call.

On reconnect, GeneratorAgent (and the UI) broadcast `schema.request`. All running tools respond by re-announcing their schema. This means a tool that starts after the generator is still picked up without restarting anything.

Schema descriptions are the mechanism for "when to call" guidance — they tell Gemma under what conditions to use a tool. This belongs in the tool description, not the system prompt.

---

## 5. Memory Model

**Episodic store (ChromaDB):** Every Q&A turn is ingested as an engram by MemoryAgent. The engram includes: query text, answer text, intent classification, named entities, session ID, and metadata fields populated by later events.

**Score annotation:** When `critique.result` arrives, MemoryAgent patches the matching engram with `critic_score` (1–5).

**Sentiment annotation:** When `user.feedback` arrives (`+1`/`-1`), RewardService patches the engram with `user_sentiment`.

**Retrieval weighting:** `search_episodic()` applies a score bias of `(critic_score - 3) × 0.05` to ranked results. Engrams without a score are unaffected. High-scoring answers float up; low-scoring answers sink.

---

## 6. Context Management

**Token tracking:** After each generation, the final Ollama streaming chunk includes `prompt_eval_count` — the exact token count of the prompt sent. GeneratorAgent stores this in ConversationService and includes it in `response.generation`. The UI's TokenGauge reads it.

**Compaction:** When the user triggers compaction, the gateway publishes `compaction.request`. GeneratorAgent (if IDLE) summarizes the session history via a separate non-streaming Ollama call, replaces the messages array with `[SUMMARY] + last N verbatim turn pairs`, and publishes `compaction.result`. GeneratorAgent rejects compaction requests while busy.

---

## 7. Architecture Invariants

- The bus is the only coordination mechanism. No direct agent-to-agent function calls.
- The LLM receives the raw query and full conversation history — no preprocessing or rewriting before the generator sees it.
- Tool calls are synchronous within a generation turn — not async bus events that the generator waits on asynchronously.
- Thinking tokens are surfaced to the UI — not stripped and discarded.
- `num_ctx` is always set explicitly in config — never rely on Ollama's hardware default.
- Gemma 4 thinking tokens must be stripped from assistant turns before passing history back to the model.
- Conversation history is passed as a messages array to the Ollama chat endpoint — never embedded in a flat prompt string.
- Every agent has an explicit state machine defined in `states.py` and `transitions.py`. No implicit state.
