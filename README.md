# LoCAL2

Privacy-first local AI assistant. Gemma 4 runs entirely on-device via [Ollama](https://ollama.com) — no queries, documents, or memory ever leave the machine.

Gemma is the orchestrator. It receives the raw user query and the full conversation history, then decides natively whether to search the web, recall episodic memory, fetch a URL, consult the document library, or answer directly. LoCAL2 provides the tools, memory infrastructure, quality-scoring loop, and observability layer — not the orchestration.

## Research status

LoCAL2 is an open-source experimental platform for investigating memory,
evaluation, tool use, and behavioral context in locally hosted generative-AI
systems.

The repository is a research prototype, not a production-supported enterprise
platform. Its architecture and behavior may change between public releases,
and individual experimental results should not be treated as independently
validated unless accompanied by a published methodology and dataset.

The public pre-thesis baseline was recorded on July 18, 2026, as the
[`local2-public-baseline-2026-07-18`](https://github.com/rkoike88/LoCAL2/releases/tag/local2-public-baseline-2026-07-18)
release at commit
[`e8a5054`](https://github.com/rkoike88/LoCAL2/commit/e8a505476e5eeade64386c3d0c615b670344e837).

Thesis-related development after that baseline is being conducted privately.
New experimental mechanisms, datasets, controlled evaluations, statistical
analyses, and results are not included in this public repository.

**→ [Getting Started](GETTING_STARTED.md) · [Architecture Reference](docs/architecture/local2-design.md) · [Docs Index](docs/)**

---

## Core design ideas

**LLM-native tool calling.** There is no orchestration layer between the user and the model. No query rewriting, no task decomposition pipeline, no routing rules. Gemma sees the raw input and full conversation history, and decides what tools to call.

**ZMQ pub/sub bus.** Every participant — agents, tools, services, the web gateway — communicates exclusively through a ZeroMQ XPUB/XSUB proxy. No direct function calls across components. Tools publish their JSON schemas on startup; the generator picks them up without restart. New tools added while the system is running are discovered automatically.

**Episodic memory with critic scoring.** Every Q&A turn is stored as a ChromaDB engram. CriticAgent (Prometheus-7b) grades each response 1–5 on a context-sensitive rubric. Retrieval is score-weighted — high-quality answers float up in future recall, poor ones sink. User thumbs-up/down annotates engrams as sentiment signal.

**Full observability.** Thinking tokens, tool calls, memory retrievals, state machine transitions, and token counts are first-class visible artifacts in the UI — not stripped or discarded.

**Private web search.** SearXNG runs locally in Docker. Searches are not logged or tracked by any third party.

---

## Features

| Feature | Notes |
|---|---|
| Web search + fetch | SearXNG (self-hosted, no API key); Gemma fetches URLs selectively |
| Episodic memory | Auto-ingested per turn; critic-scored; score-weighted retrieval |
| Pinned user facts | `remember_this` stores persistent facts always injected into context |
| Document library (RAG) | Multi-collection ChromaDB; routed through an LLM-powered LibraryAgentTool |
| Cognitive personas | `persona` tool primes a cognitive register via distilled conversation seeds |
| Academic search | Semantic Scholar Graph API; arXiv URL fallback |
| Datetime + location | Live IP geolocation with static override; stdlib clock |
| Critic scoring | Prometheus-7b; rubric dynamically selected by turn context |
| Context compaction | Token-counted gauge; on-demand compaction with summary injection |
| Multi-model routing | Vision → `gemma4:e4b`; default → `gemma4:e4b-mlx`; quality → `gemma4:31b-mlx` |
| File attachments | Images → vision input; PDFs/code/text → extracted and prepended |
| Multi-user isolation | `user_id` threading partitions episodic memory per user |
| Comparison harness | Side-by-side LoCAL2 vs. bare model; pairwise verdict store (SQLite) |

---

## Architecture

### 1. Design Philosophy

**LoCAL1 vs LoCAL2.** In v1, an explicit orchestration layer (AnalystAgent, SynthesizerAgent, GatewayAgent) wrapped around the LLM. The LLM was a leaf — it received a preprocessed, decomposed sub-task and returned a raw answer. In v2 the LLM is the root. Gemma receives the raw user query and the full conversation history, and decides natively whether to search, recall, fetch, or answer directly.

Three going-in objectives:

1. **Native conversation history** — the generator receives the full messages array; Gemma handles follow-up, pronoun resolution, and multi-turn reasoning without preprocessing.
2. **Tool-native architecture** — web search, memory recall, document search, and other capabilities are synchronous tool calls within a generation turn, not async bus-dispatched task pipelines.
3. **Externalized LLM workings** — thinking tokens, tool calls, memory recalls, state transitions, and context window fill are first-class visible artifacts in the UI.

What was removed from v1: GatewayAgent, AnalystAgent, SynthesizerAgent, task DAG pipeline, query preprocessing, explicit query rewriting, task decomposition, the complexity gate.


### 2. Participants

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

| Suffix | Has LLM | Triggered by | Output destination |
|---|---|---|---|
| `*Agent` | Yes | System (bus event) | Bus subject |
| `*Tool` | No | Gemma (`tool.call.*`) | Back to Gemma via `tool.result.*` |
| `*AgentTool` | Yes | Gemma (`tool.call.*`) | Back to Gemma via `tool.result.*` |

---

### 3. Query Flow — Happy Path

```
User types query (optionally with file attachments)
  → Gateway publishes query.received {query, session_id, query_id, user_id, attachments?}
  → MemoryAgent receives query.received
      → searches episodic store for relevant prior turns
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
  → UI receives response.generation → displays answer + XAI footer (model, tokens, score)
  → User optionally thumbs up/down → user.feedback → RewardService → reward.event → engram annotation
```

---

### 4. Tool Schema Discovery

Tools register dynamically. On startup, every tool publishes its JSON schema on `tool.schema`. GeneratorAgent subscribes, builds a live registry, and passes the current schema list to every `ollama.chat()` call.

On reconnect, GeneratorAgent and the UI broadcast `tool.schema.request`. All running tools respond by re-announcing their schemas. A tool that starts after the generator is still picked up without restarting anything.

Schema descriptions carry "when to call" guidance — not the system prompt. This keeps routing logic in the tool, not in a global instruction that has to be updated every time a tool is added.

---

### 5. ToolDispatcher

ToolDispatcher is a synchronous bus participant, not an agent. It subscribes to `tool.call.*` (published by GeneratorAgent during a generation turn), routes each call to the correct tool, collects the `tool.result.*` reply, and returns it to GeneratorAgent. This decouples the generator from knowing which tool handles which subject and centralizes timeout handling.

---

### 6. Multi-Model Routing

GeneratorAgent routes queries to different models via `config/generator.yaml`:

```yaml
models:
  default: gemma4:e4b-mlx   # standard text queries (2× faster via Apple MLX)
  vision:  gemma4:e4b        # queries with image attachments (selected automatically)
  quality: gemma4:31b-mlx    # high-quality mode
```

The `response.generation` payload includes a `model` field. The UI displays this in the XAI footer so it is always visible which model answered.

---

### 7. Memory Model

**Episodic store (ChromaDB):** Every Q&A turn is ingested as an engram by MemoryAgent after the response is published. Each engram captures: query text, answer summary, intent classification (fact/explanation/comparison/procedure), named entities, session ID, and user ID.

**Score annotation:** When `critique.result` arrives, MemoryAgent patches the matching engram with `critic_score` (1–5) and `critic_feedback`. This forms an auditable trail: rubric → feedback → score.

**Sentiment annotation:** When `user.feedback` arrives (`+1`/`-1`), RewardService patches the engram with `user_sentiment`.

**Score-weighted retrieval:** `search_episodic()` applies a score bias of `(critic_score − 3) × 0.05` to ranked results. Engrams without a score are unaffected.

**Pinned facts:** `remember_this` stores permanent key-value facts in a separate ChromaDB collection. Pinned facts are always injected into the generation context as a fixed prefix — not subject to similarity thresholds.

**Structured context — three tiers injected before generation:**

| Tier | Source | When present |
|---|---|---|
| Pinned facts | `remember_this` store | Always |
| Episodic summaries | Similarity search over engrams for this user | Relevance score ≥ threshold |
| Cross-session patterns | Elevated insights from collective namespace | When available |

---

### 8. Critic

CriticAgent (Prometheus-7b) grades every response on a 1–5 absolute scale. The rubric is dynamically selected based on turn context:

| Turn context | Rubric | Question |
|---|---|---|
| Standard knowledge turn | **Realistic** | Is the response accurate and achievable? |
| Tool-use turn (web search / fetch) | **Style** | Is it well-formatted and comprehensive given the retrieved data? |
| `remember_this` call | **Clarity** | Does it clearly confirm what was stored? |

The critic skips grading on turns where it cannot assess content (e.g., pure tool-result echoes). The UI displays the score as a badge in the XAI footer alongside the rubric name.

---

### 9. Context Management

**Token tracking:** After each generation, the final Ollama streaming chunk includes `prompt_eval_count` — the exact token count of the prompt sent. GeneratorAgent stores this in ConversationService and includes it in `response.generation`. The UI's token gauge reads it.

**Compaction:** When the user triggers compaction, the gateway publishes `compaction.request`. ModelService summarizes the session history via a separate non-streaming Ollama call, replaces the messages array with `[SUMMARY] + last N verbatim turn pairs`, and publishes `compaction.result`. Compaction is rejected while the generator is busy.

---

### 10. Web UI

**Stack:** React + Vite + TypeScript frontend served as static files by FastAPI. All communication over WebSocket.

#### WebSocket event protocol (`/ws/chat`)

Client → server (one message per query):
```json
{ "query": "...", "session_id": "uuid", "user_id": "...", "attachments": [...] }
```

Server → client (streaming, multiple messages per query):
```json
{ "type": "thinking_chunk",  "chunk": "...",                             "query_id": "uuid" }
{ "type": "tool_start",      "tool": "web_search", "args": {...},        "query_id": "uuid" }
{ "type": "tool_result",     "tool": "web_search", "result": "...",      "query_id": "uuid" }
{ "type": "response",        "answer": "...", "model": "gemma4:e4b-mlx", "prompt_tokens": 4710, ... }
{ "type": "critique",        "score": 4, "rubric_name": "realistic",     "query_id": "uuid" }
```

The gateway (`ws_bridge.py`) manages ZMQ subscriptions per connected session and fans out to the WebSocket.

#### Run modes

| Command | What starts |
|---|---|
| `local2` | Full stack — proxy, agents, tools, FastAPI; opens browser |
| `local2 --headless` | Same, no browser |
| `local2 --panels` | Full stack + Qt observer windows (read-only bus views) |
| `local2 --web-only --ipaddress <ip>` | FastAPI only; connects to a remote ZMQ bus |

---

### 11. Comparison Harness

A side-by-side evaluation tool for measuring LoCAL2 against a bare model (`python -m harness.server`, port 7001).

**Arm A — full LoCAL2:** Routed via WebSocket proxy that injects a synthetic `user_id` to isolate episodic memory per run. Each session sees only its own engrams — no process restart needed between runs.

**Arm B — bare model:** Same Ollama model with a standard tool-call loop. Same web search and fetch backends (direct HTTP — no bus). No memory, no critic, no structured context, no state machines.

Each turn is logged to SQLite (`runs` / `items` / `judgments`). After both arms respond, the evaluator records a pairwise verdict (A better / tie / B better) with an optional rationale. The History tab shows aggregate win rates and allows reviewing and re-judging any past turn.

---

### 12. User Identity

`user_id` is threaded through every bus envelope and stored on each episodic engram. A `"default"` user_id is transparent — no filtering, full backward compatibility with existing sessions. Non-default user IDs (synthetic harness IDs, or future multi-user accounts) partition the episodic store so each user sees only their own history.

---

### 13. File Attachments

On submit, the frontend uploads each file to `POST /api/attachments`, which returns an `Attachment` object:

```json
{ "type": "text" | "image" | "error", "name": "filename.pdf", "data": "..." }
```

Images are base64-encoded. Documents (PDF, TXT, MD, code files) are text-extracted and truncated to `max_attachment_chars`. The processed list flows in the `query.received` payload unchanged to the generator.

- Text attachments → prepended to the user message content
- Image attachments → passed in the Ollama message `images` key (vision input)

---

### 14. Remote-Bus Mode

The ZMQ proxy binds to `0.0.0.0`, making it reachable from the local network. Participants connect to `127.0.0.1` by default; `--ipaddress` redirects to a remote proxy.

`local2 --web-only --ipaddress <host-ip>` starts only FastAPI — no local proxy, no agents. The web server publishes `query.received` to the remote bus and subscribes to receive the response stream. From the generator's perspective, the query is indistinguishable from a local one.

---

### 15. Architecture Invariants

- The bus is the only coordination mechanism — no direct agent-to-agent function calls.
- The LLM receives the raw query and full conversation history — no preprocessing or rewriting before the generator sees it.
- Tool calls are synchronous within a generation turn — not async bus events the generator waits on asynchronously.
- Thinking tokens are surfaced to the UI — not stripped and discarded.
- `num_ctx` is always set explicitly in config — never rely on Ollama's hardware default (clips to 4 K below 24 GB VRAM).
- Gemma 4 thinking tokens are stripped from assistant turns before passing history back to the model.
- Conversation history is passed as a messages array to the Ollama chat endpoint — never embedded in a flat prompt string.
- Every agent has an explicit state machine defined in `states.py` and `transitions.py`. No implicit state.

---

## Reference

- **[Getting Started](GETTING_STARTED.md)** — installation, quick start, configuration, document library, remote access
- **[Architecture Reference](docs/architecture/local2-design.md)** — detailed design document
- **[Docs Index](docs/)** — participant docs, bus topology, state machines, config reference, plans

---

## Citations

The comparison harness uses [Prometheus](https://huggingface.co/prometheus-eval/prometheus-7b-v2.0) for pairwise evaluation and the [Preference-Collection](https://huggingface.co/datasets/prometheus-eval/Preference-Collection) as the prompt set.

```bibtex
@misc{kim2023prometheus,
    title={Prometheus: Inducing Fine-grained Evaluation Capability in Language Models},
    author={Seungone Kim and Jamin Shin and Yejin Cho and Joel Jang and Shayne Longpre and Hwaran Lee and Sangdoo Yun and Seongjin Shin and Sungdong Kim and James Thorne and Minjoon Seo},
    year={2023},
    eprint={2310.08491},
    archivePrefix={arXiv},
    primaryClass={cs.CL}
}
@misc{kim2024prometheus,
    title={Prometheus 2: An Open Source Language Model Specialized in Evaluating Other Language Models},
    author={Seungone Kim and Juyoung Suk and Shayne Longpre and Bill Yuchen Lin and Jamin Shin and Sean Welleck and Graham Neubig and Moontae Lee and Kyungjae Lee and Minjoon Seo},
    year={2024},
    eprint={2405.01535},
    archivePrefix={arXiv},
    primaryClass={cs.CL}
}
```
