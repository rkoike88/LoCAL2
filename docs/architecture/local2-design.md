# LoCAL2 Architecture Reference

LoCAL2 (Loosely-Coupled Agent Language model, v2) is a privacy-first local AI assistant. This document is the canonical architecture reference. For the project overview and feature list, see [README.md](../../README.md).

For message format and subject constants, see [messaging.md](messaging.md).

---

## 1. Design Philosophy

**LoCAL1 vs LoCAL2.** In v1, an explicit orchestration layer (AnalystAgent, SynthesizerAgent, GatewayAgent) wrapped around the LLM. The LLM was a leaf — it received a preprocessed, decomposed sub-task and returned a raw answer. In v2 the LLM is the root. Gemma receives the raw user query and the full conversation history, and decides natively whether to search, recall, fetch, or answer directly.

**Three going-in objectives:**

1. **Native conversation history** — the generator receives the full messages array; Gemma handles follow-up, pronoun resolution, and multi-turn reasoning without preprocessing.
2. **Tool-native architecture** — web search, memory recall, document search, and other capabilities are synchronous tool calls within a generation turn, not async bus-dispatched task pipelines.
3. **Externalized LLM workings** — thinking tokens, tool calls, memory recalls, state transitions, and context window fill are first-class visible artifacts in the UI.

**What was removed from v1:** GatewayAgent, AnalystAgent, SynthesizerAgent, task DAG pipeline, query preprocessing, explicit query rewriting, task decomposition, the complexity gate.

---

## 2. Participant Roles

| Participant | Type | Triggered by | Publishes to |
|---|---|---|---|
| **GeneratorAgent** | `*Agent` (LLM) | `query.received` | `response.generation`, `answer.dialog`, `agent.transition` |
| **CriticAgent** | `*Agent` (LLM) | `response.generation` | `critique.result`, `agent.transition` |
| **MemoryAgent** | `*Agent` (LLM) | `query.received`, `response.generation`, `critique.result` | `memory.context`, `agent.transition` |
| **ToolDispatcher** | Synchronous bridge | `tool.call.*` (via GeneratorAgent) | `tool.result.*` (back to GeneratorAgent) |
| **WebSearchTool** | `*Tool` | `tool.call.web_search` | `tool.result.web_search`, `tool.activity.web_search` |
| **WebFetchTool** | `*Tool` | `tool.call.web_fetch` | `tool.result.web_fetch`, `tool.activity.web_fetch` |
| **SearchMemoryTool** | `*Tool` | `tool.call.search_memory` | `tool.result.search_memory`, `tool.activity.search_memory` |
| **DateTimeTool** | `*Tool` | `tool.call.get_datetime` | `tool.result.get_datetime`, `tool.activity.get_datetime` |
| **LocationTool** | `*Tool` | `tool.call.get_location` | `tool.result.get_location`, `tool.activity.get_location` |
| **SemanticScholarTool** | `*Tool` | `tool.call.search_papers` | `tool.result.search_papers`, `tool.activity.search_papers` |
| **PersonaTool** | `*Tool` | `tool.call.persona` | `tool.result.persona`, `tool.activity.persona` |
| **RememberThisTool** | `*Tool` | `tool.call.remember_this` | `tool.result.remember_this`, `user.context.updated` |
| **LibraryAgentTool** | `*AgentTool` (LLM) | `tool.call.consult_librarian` | `tool.result.consult_librarian`, `library.*` |
| **SearchLibraryTool** | `*Tool` (internal) | `tool.call.search_library` | `tool.result.search_library` |
| **FastAPI Gateway** | UI/API | HTTP/WebSocket | `query.received`, `compaction.request`, `user.feedback`, `tool.schema.request` |
| **ConversationService** | Service | — | — |
| **MemoryService** | Service | — | — |
| **DocumentService** | Service | — | — |
| **ModelService** | Service | `response.generation`, `compaction.request` | `compaction.request` (auto), `compaction.result` |
| **RewardService** | Service | `user.feedback` | `reward.event` |

**Participant naming convention:**

| Suffix | Has LLM | Triggered by | Output |
|---|---|---|---|
| `*Agent` | Yes | System (bus event) | Bus subject |
| `*Tool` | No | Gemma (`tool.call.*`) | Back to Gemma via `tool.result.*` |
| `*AgentTool` | Yes | Gemma (`tool.call.*`) | Back to Gemma via `tool.result.*` |

---

## 3. Query Flow — Happy Path

```
User types query (optionally with file attachments)
  → Gateway publishes query.received {query, session_id, query_id, user_id, attachments?}
  → MemoryAgent receives query.received
      → searches episodic store for relevant prior turns (filtered by user_id)
      → publishes memory.context {summaries, pinned_facts}
  → GeneratorAgent receives memory.context → injects context → IDLE → RECEIVING → GENERATING
  → Gemma streams thinking tokens → GENERATION_THINKING events
  → If Gemma calls a tool:
      GeneratorAgent → DISPATCHING_TOOL
      ToolDispatcher receives tool.call.<name> → executes tool → tool.result.<name>
      ToolDispatcher returns result to GeneratorAgent → GENERATING
      (repeats up to max_tool_iterations)
  → Final answer → PUBLISHING
  → GeneratorAgent publishes response.generation + answer.dialog → IDLE
  → CriticAgent receives response.generation → grades it → publishes critique.result
  → MemoryAgent receives response.generation → ingests engram
  → MemoryAgent receives critique.result → annotates engram with critic score
  → UI receives response.generation → displays answer + XAI footer
  → User optionally thumbs up/down → user.feedback → RewardService → reward.event → engram annotation
```

---

## 4. Tool Schema Discovery

Tools register dynamically. On startup, every tool publishes its JSON schema on `tool.schema`. GeneratorAgent subscribes, builds a live registry, and passes the current schema list to every `ollama.chat()` call.

On reconnect, GeneratorAgent (and the UI) broadcast `tool.schema.request`. All running tools respond by re-announcing their schemas. A tool that starts after the generator is still picked up without restarting anything.

A second `tool.schema.request` is broadcast automatically 2 seconds after the web server starts (the `schema_refresh` daemon thread in `run.py`). This catches tools that lose the first request due to the ZMQ slow-joiner problem — a PUB socket drops messages published before its connection has fully settled.

Schema descriptions carry "when to call" guidance — not the system prompt. This keeps routing logic in the tool, not in a global instruction that has to change every time a tool is added.

---

## 5. ToolDispatcher

ToolDispatcher is a synchronous bus participant, not an agent. It subscribes to `tool.call.*` (published by GeneratorAgent during a generation turn), routes each call to the correct tool participant, collects the `tool.result.*` reply, and returns it to GeneratorAgent. This decouples the generator from knowing which tool handles which subject and centralizes timeout handling in one place.

---

## 6. Multi-Model Routing

GeneratorAgent routes queries to different models via `config/generator.yaml`:

```yaml
models:
  default: gemma4:e4b-mlx   # standard text queries (2× faster via Apple MLX)
  vision:  gemma4:e4b        # queries with image attachments (selected automatically)
  quality: gemma4:31b-mlx    # high-quality mode
```

Model selection per turn:
- If the query includes image attachments → `models.vision`
- Otherwise → `models.default`

The `response.generation` payload includes a `model` field. The UI displays this in the XAI footer.

`ModelService` reads the model fresh from config on each compaction, so model changes via `PUT /api/settings/generator` take effect immediately without restart.

---

## 7. Memory Model

**Episodic store (ChromaDB):** Every Q&A turn is ingested as an engram by MemoryAgent. Each engram captures: query text, answer summary, intent classification (fact/explanation/comparison/procedure), named entities, session ID, and user ID.

**Score annotation:** When `critique.result` arrives, MemoryAgent patches the matching engram with `critic_score` (1–5) and `critic_feedback`. This forms an auditable trail: rubric → feedback → score.

**Sentiment annotation:** When `user.feedback` arrives (`+1`/`-1`), RewardService patches the engram with `user_sentiment`.

**Score-weighted retrieval:** `search_episodic()` applies a score bias of `(critic_score − 3) × 0.05` to ranked results. Engrams without a score are unaffected. High-scoring answers float up; low-scoring answers sink.

**Pinned facts:** `remember_this` stores permanent key-value facts in a separate ChromaDB collection. Pinned facts are always injected into the generation context as a fixed prefix — not subject to similarity thresholds. `user.context.updated` notifies GeneratorAgent to refresh its in-memory cache immediately.

**Structured context — three tiers injected before generation:**

| Tier | Source | When present |
|---|---|---|
| Pinned facts | `remember_this` store | Always (if any exist for this user) |
| Episodic summaries | Similarity search over engrams for this user | Relevance score ≥ threshold |
| Cross-session patterns | Elevated insights from collective namespace | When available |

**User partitioning:** All episodic searches are filtered by `user_id` when a non-default user ID is provided. Engrams stored without a `user_id` (legacy) are unaffected by filtering — they remain retrievable under the default identity.

**Ablation:** `write_enabled: false` in `memory.yaml` suppresses episodic writes without affecting retrieval. Used for harness studies where memory should not accumulate across runs.

---

## 8. Critic

CriticAgent (Prometheus-7b) grades every response on a 1–5 absolute scale. The rubric is dynamically selected based on turn context:

| Turn context | Rubric | Question |
|---|---|---|
| Standard knowledge turn | **Realistic** | Is the response accurate and genuinely achievable? |
| Tool-use turn (web search / fetch) | **Style** | Is it well-formatted and comprehensive given retrieved data? |
| `remember_this` call | **Clarity** | Does it clearly confirm what was stored? |

The rubric is declared in the tool schema — CriticAgent reads it from the tool-call record, not from a config file. Adding a new tool with a custom rubric requires no changes to CriticAgent.

The critic publishes `critique.result`. The UI displays the score as a badge in the XAI footer alongside the rubric name. The score is written back to the engram by MemoryAgent.

---

## 9. Context Management

**Token tracking:** After each generation, the final Ollama streaming chunk includes `prompt_eval_count` — the exact token count of the prompt sent. GeneratorAgent stores this in ConversationService and includes it in `response.generation`. The UI's token gauge reads it.

**Compaction:** When the user triggers compaction, the gateway publishes `compaction.request`. ModelService summarizes the session history via a separate non-streaming Ollama call, replaces the messages array with `[SUMMARY] + last N verbatim turn pairs`, and publishes `compaction.result`. Compaction is rejected while the generator is busy.

---

## 10. Web UI Layer

The user-facing layer is a browser-based frontend. Any browser on the same machine or same network can connect.

**Stack:** React + Vite + TypeScript frontend served as static files by the FastAPI gateway. Communication over WebSocket.

### WebSocket endpoints

| Endpoint | Purpose |
|---|---|
| `WS /ws/chat/{session_id}` | Query/response stream for the chat UI |
| `WS /ws/bus/{session_id}` | Raw bus event stream (developer/observer use) |

### WebSocket event protocol (`/ws/chat`)

Client → server (one message per query):
```json
{ "query": "...", "session_id": "uuid", "user_id": "...", "attachments": [...] }
```

Server → client (streaming, multiple messages per query):
```json
{ "type": "thinking_chunk",  "chunk": "...",                              "query_id": "uuid" }
{ "type": "tool_start",      "tool": "web_search", "args": {...},         "query_id": "uuid" }
{ "type": "tool_result",     "tool": "web_search", "result": "...",       "query_id": "uuid" }
{ "type": "response",        "answer": "...", "model": "gemma4:e4b-mlx",  "prompt_tokens": 4710, ... }
{ "type": "critique",        "score": 4, "rubric_name": "realistic",      "query_id": "uuid" }
```

The gateway (`ws_bridge.py`) manages ZMQ subscriptions per connected session and fans out to the WebSocket.

**Stream lifetime:** after `response`, the stream stays open to capture `critique`. For knowledge turns, the trail window is 90 seconds. For tool-use turns, CriticAgent skips grading, so the trail closes after 2 seconds. The stream closes early as soon as `critique` arrives.

### Run modes

| Command | What starts | Browser |
|---|---|---|
| `local2` | Proxy + agents + tools + FastAPI | Auto-opened |
| `local2 --headless` | Proxy + agents + tools + FastAPI | Not opened |
| `local2 --panels` | Proxy + agents + tools + FastAPI + Qt observer windows | Auto-opened |
| `local2 --web-only --ipaddress <ip>` | FastAPI only (no proxy, no agents) | Auto-opened |

### REST endpoints

| Endpoint | Purpose |
|---|---|
| `GET /api/sessions` | List sessions from ConversationService |
| `GET /api/sessions/{id}` | Load session message history |
| `DELETE /api/sessions/{id}` | Delete a session |
| `POST /api/sessions/{id}/compact` | Trigger compaction |
| `GET/PUT /api/settings/{section}` | Read/write a YAML config section |
| `POST /api/feedback` | Publish `user.feedback` to bus |
| `POST /api/attachments` | Upload and process file attachments |

---

## 11. Comparison Harness

A side-by-side evaluation server (`python -m harness.server`, port 7001) for measuring LoCAL2 against a bare model.

**Arm A — full LoCAL2:** WebSocket proxy to the LoCAL2 gateway. Injects a synthetic `user_id` (`arm_a_{run_id}`) to isolate episodic memory per run. Each session sees only its own engrams — no process restart between runs. `memory.yaml` `write_enabled: false` can be set for ablation studies.

**Arm B — bare model:** Ollama model with a standard tool-call loop. Same web search and fetch backends (direct HTTP — no bus). No memory, no critic, no structured context, no state machines. Configured via `harness/config.yaml`.

**SQLite store (`harness/db.py`):** `runs` / `items` / `judgments` tables. Items are upserted on conflict — verdict and critic score can be filled in after both arms complete. Aggregate win-rate stats available via `/api/aggregate/{run_id}`.

**Harness UI:** Three-panel layout (Arm A | Arm B | verdict). History tab shows run cards with aggregate stats; clicking a run shows all turns with existing verdicts, editable in-place.

---

## 12. User Identity

`user_id` is threaded through every bus envelope (`MessageEnvelope.metadata["user_id"]`) and stored on each episodic engram. A `"default"` user_id is transparent — no filtering, full backward compatibility. Non-default user IDs partition the episodic store so each user sees only their own history.

The gateway reads `user_id` from the WebSocket message and passes it to `LoCALSession`, which stamps every envelope it publishes.

---

## 13. Install and Packaging

LoCAL2 is a standard Python package installable via pip.

```bash
pip install local2
local2 setup     # first run: write config, pull Ollama models
local2           # start the web UI
```

### `local2` CLI

| Command | What it does |
|---|---|
| `local2` | Start the full stack (web UI, opens browser) |
| `local2 setup` | Init `~/.local2/config/`, pull `gemma4:e4b` and `nomic-embed-text` |
| `local2 setup --models-only` | Re-pull models without touching config |
| `local2 setup --config-only` | Re-init config without pulling models |
| `local2 searxng up` | Start SearXNG in Docker |
| `local2 searxng down` | Stop SearXNG |
| `local2 searxng status` | Show container status |

### Data directory (`~/.local2/`)

| Path | Contents |
|---|---|
| `~/.local2/config/*.yaml` | User YAML configs (written by `local2 setup`, editable freely) |
| `~/.local2/docker-compose.yml` | SearXNG compose file |
| `~/.local2/searxng/` | SearXNG settings |
| `~/.local2/.env` | Auto-generated `MY_SEARX_SECRET` |

`LOCAL2_DATA_DIR` env var overrides the default path.

### Config search order

`config_loader.py` resolves config files in this priority:

1. `~/.local2/config/<name>.yaml` — user config (takes precedence)
2. `config/<name>.yaml` — repo root (dev-mode checkout)
3. `src/local/defaults/<name>.yaml` — bundled package defaults (read-only)

`save()` always writes to `~/.local2/config/`, so user edits survive upgrades.

---

## 14. File Attachments

On submit, the frontend uploads each file to `POST /api/attachments`, which returns an `Attachment` object:

```json
{ "type": "text" | "image" | "error", "name": "filename.pdf", "data": "..." }
```

Images are base64-encoded; documents (PDF, TXT, MD, code files) are text-extracted and truncated to `max_attachment_chars`. The processed list flows in the `query.received` payload to the generator.

- Text attachments → prepended to the user message content
- Image attachments → passed in the Ollama message `images` key (vision input)

---

## 15. Remote-Bus Mode

The ZMQ proxy binds to `0.0.0.0`. Participants connect to `127.0.0.1` by default; `--ipaddress` redirects to a remote proxy.

`local2 --web-only --ipaddress <host-ip>` starts only FastAPI — no local proxy, no agents. The web server publishes `query.received` to the remote bus and subscribes to receive the response stream. From the generator's perspective, the query is indistinguishable from a local one.

---

## 16. Architecture Invariants

- The bus is the only coordination mechanism — no direct agent-to-agent function calls.
- The LLM receives the raw query and full conversation history — no preprocessing or rewriting before the generator sees it.
- Tool calls are synchronous within a generation turn — not async bus events the generator waits on asynchronously.
- Thinking tokens are surfaced to the UI — not stripped and discarded.
- `num_ctx` is always set explicitly in config — never rely on Ollama's hardware default (clips to 4 K below 24 GB VRAM).
- Gemma 4 thinking tokens are stripped from assistant turns before passing history back to the model.
- Conversation history is passed as a messages array to Ollama — never embedded in a flat prompt string.
- Every agent has an explicit state machine defined in `states.py` and `transitions.py`. No implicit state.
