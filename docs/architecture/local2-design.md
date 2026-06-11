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
User types query (optionally with file attachments)
  → Gateway publishes query.received {query, session_id, query_id, attachments?}
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

A second `schema.request` is also broadcast automatically 2 seconds after the web server starts (the `schema_refresh` daemon thread in `run.py`). This catches tools that lose the first request due to the ZMQ slow-joiner problem — a PUB socket drops messages published before its connection has fully settled.

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

## 7. Web UI Layer (Phase 16)

The user-facing layer is a browser-based frontend. There is no required desktop app — any browser on the same machine (or same network) can connect.

**Stack:** React + Vite + TypeScript frontend served as static files by the FastAPI gateway. The frontend communicates over two WebSocket endpoints.

### WebSocket endpoints

| Endpoint | Purpose |
|---|---|
| `WS /ws/chat/{session_id}` | Query/response stream for the chat UI |
| `WS /ws/bus/{session_id}` | Raw bus event stream (developer/observer use) |

### WebSocket event protocol (`/ws/chat`)

Client → server (one message per query):
```json
{ "query": "...", "session_id": "uuid", "attachments": [...] }
```

Server → client (streaming, multiple messages per query):
```json
{ "type": "thinking_chunk",  "chunk": "...",  "query_id": "uuid" }
{ "type": "tool_start",  "tool": "web_search", "args": {...}, "query_id": "uuid" }
{ "type": "tool_result", "tool": "web_search", "result": "...", "query_id": "uuid" }
{ "type": "response", "answer": "...", "thinking": "...", "tool_calls": [...], "session_id": "uuid", "query_id": "uuid", "prompt_tokens": 4710 }
{ "type": "critique", "score": 4, "feedback": "...", "query_id": "uuid" }
```

The gateway translates ZMQ bus events into this WebSocket stream. The `ws_bridge.py` module manages the ZMQ subscriptions per connected session and fans out to the WebSocket.

### Run modes

| Command | What starts | Browser |
|---|---|---|
| `local2` | Proxy + agents + tools + FastAPI | Auto-opened |
| `local2 --headless` | Proxy + agents + tools + FastAPI | Not opened |
| `local2 --panels` | Proxy + agents + tools + FastAPI + Qt observer windows | Auto-opened; Qt windows tile to right 2/3 |
| `local2 --desktop` | Proxy + agents + tools + legacy PySide6 UI | N/A |
| `local2 --web-only --ipaddress <ip>` | FastAPI only (no proxy, no agents) | Auto-opened |

`--panels` mode starts the Qt observer windows (GeneratorWindow, CriticWindow, MemoryWindow, ToolWindows) alongside the web UI. The Qt windows are read-only — they subscribe to bus events but never publish except for config saves via the gear buttons.

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

## 8. Install and Packaging (Phase 17)

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
| `local2 searxng up` | Start SearXNG in Docker (uses `~/.local2/docker-compose.yml`) |
| `local2 searxng down` | Stop SearXNG |
| `local2 searxng status` | Show container status |

### Data directory (`~/.local2/`)

All user-editable state lives in `~/.local2/`, not inside the package or repo. This means upgrades never overwrite user settings.

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

## 9. File Attachments

The web UI supports attaching files to a query. On submit, the frontend uploads each file to `POST /api/attachments`, which processes it server-side and returns an `Attachment` object:

```json
{ "type": "text" | "image" | "error", "name": "filename.pdf", "data": "..." }
```

Images are base64-encoded; documents (PDF, TXT, MD, code files) are text-extracted. The processed attachment list is included in the `query.received` payload and flows unchanged through the bus to the generator.

GeneratorAgent builds the user message as:
- Text attachments → prepended to the user message content (truncated to `max_attachment_chars`)
- Image attachments → passed in the `images` key of the Ollama message (vision input)

---

## 10. Remote-Bus Mode

The ZMQ proxy binds to `0.0.0.0`, making it reachable from the local network. Participants connect to `127.0.0.1` by default; setting `LOCAL2_PROXY_HOST` (or `--ipaddress`) redirects connections to a remote proxy.

`local2 --web-only --ipaddress <host-ip>` starts only the FastAPI web server — no local proxy, no agents. The web server's `LoCALSession` publishes `query.received` to the remote bus and subscribes to receive the response stream. From the generator's perspective the query is indistinguishable from a local one.

**Use cases:**
- iPad/iPhone browser → host Mac's `local2` (same WiFi, navigate to `http://<host-ip>:8000`)
- Secondary Mac → host Mac's agents (run `local2 --web-only --ipaddress <host-ip>` on the secondary)

---

## 11. Architecture Invariants

- The bus is the only coordination mechanism. No direct agent-to-agent function calls.
- The LLM receives the raw query and full conversation history — no preprocessing or rewriting before the generator sees it.
- Tool calls are synchronous within a generation turn — not async bus events that the generator waits on asynchronously.
- Thinking tokens are surfaced to the UI — not stripped and discarded.
- `num_ctx` is always set explicitly in config — never rely on Ollama's hardware default.
- Gemma 4 thinking tokens must be stripped from assistant turns before passing history back to the model.
- Conversation history is passed as a messages array to the Ollama chat endpoint — never embedded in a flat prompt string.
- Every agent has an explicit state machine defined in `states.py` and `transitions.py`. No implicit state.
