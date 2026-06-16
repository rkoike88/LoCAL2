# XAI and Privacy in LoCAL2

LoCAL2 is designed around two complementary commitments: **explainability** (you can see how every answer was produced) and **privacy** (your data stays on your machine). This document describes both in detail.

---

## Privacy

### Local LLM — No Data Leaves

All language model inference runs locally via [Ollama](https://ollama.com). The default model is Gemma 4 (`gemma4:e2b`). Your queries, conversation history, and documents are never sent to an external API. There is no telemetry, no usage logging to a remote server, and no cloud dependency for the core chat loop.

### Local Vector Store — ChromaDB

Episodic memory (past Q&A turns), document chunks (RAG library), and session data are stored in ChromaDB on disk at `~/.local2/chroma/`. Nothing is uploaded to a cloud embedding service — embeddings are produced locally using [nomic-embed-text](https://ollama.com/library/nomic-embed-text) via Ollama.

### Self-Hosted Web Search — SearXNG

When the model calls `web_search`, requests are routed to a local [SearXNG](https://searxng.github.io/searxng/) instance (default: `http://localhost:8080`). SearXNG is a privacy-respecting metasearch engine: it aggregates results from multiple sources without forwarding your identity or IP to any individual search provider. You run it yourself; no third-party search API key is required.

### Optional External Calls

Three tools make outbound requests by design:

| Tool | Where it calls | What it sends |
|---|---|---|
| `web_fetch` | Any URL the model chooses to fetch | The URL — your IP is visible to the target server |
| `search_papers` | Semantic Scholar Graph API | The paper search query |
| `web_search` → SearXNG | Your local SearXNG, which fans out to search engines | SearXNG proxies the request; your IP is not forwarded |

All three are opt-in: the model only calls them when the query warrants it. You can disable any of them by removing the tool from the active tool list or setting a mock provider in `config/web_search.yaml`.

### No Login Required (Current)

LoCAL2 currently has no authentication layer. It is designed for single-user local deployment. A user profile and login system are planned for a future phase; when added, credentials will be stored locally.

---

## Explainable AI (XAI)

LoCAL2 surfaces the intermediate steps of every answer so you can evaluate not just *what* the model said, but *why* and *how confident the system is*.

### Thinking Tokens

Gemma 4 produces extended reasoning ("thinking") before generating a response. LoCAL2 captures these tokens and displays them in a collapsible block above the answer. During streaming, thinking appears in real time. After the response completes, it collapses to a summary (`thinking (N words)`) that can be expanded on demand.

This is the model's scratchpad — its reasoning before it commits to an answer.

### Tool Call Trace

Every tool call Gemma makes during a generation turn is shown as a chip between the thinking block and the answer. Clicking a chip expands the call arguments and the result returned to the model. This lets you see exactly which tools fired, what they were asked, and what they returned.

The active tool is shown with a spinner while in progress. Completed tools appear as static chips.

### Retrieval Attribution Strip

When `search_memory` or `search_library` returns results, the sources that influenced the answer are displayed in a collapsible strip below the tool chips. Each entry shows:

- **Memory sources** — the query used for recall, the similarity score, and a snippet of the retrieved text
- **Library sources** — the source file name, page number, and chunk index

The strip uses a tree display (┌ / ├ / └) for visual clarity. Clicking `▶ N sources` expands the list; clicking again collapses it.

This answers the question: *which past conversations or documents did the model draw from?*

### Groundedness Indicator

Every assistant message carries a groundedness badge derived from which tools fired during that turn:

| Badge | Meaning |
|---|---|
| `⊙ grounded` | Answer drew on retrieved memory (`search_memory`) or library sources (`search_library`) |
| `◉ web` | Answer drew on live web search (`web_search`) or fetched page content (`web_fetch`) |
| `○ knowledge` | No retrieval tools used — answer came from the model's training knowledge alone |

The badge appears in the lower-left of the response card. Hover for a tooltip description.

### Prometheus Critic Score

After every response, [Prometheus](https://arxiv.org/abs/2310.08491) (a purpose-built LLM evaluator) grades the answer on a 1–5 scale against a configurable rubric:

| Score | Meaning |
|---|---|
| 5 | Accurate, complete, and clearly explained |
| 4 | Mostly correct with minor gaps |
| 3 | Partially correct but incomplete or unclear |
| 2 | Mostly wrong or missing important information |
| 1 | Incorrect, harmful, or completely unhelpful |

The score appears as a colored dot (`●`) in the response card footer. Clicking the score expands the full Prometheus natural-language feedback — a detailed justification of why that score was assigned. The rubric itself is visible in the ⚙ Settings view under the Critic tab.

Prometheus scores are stored with each memory engram. The retrieval system uses them to weight search results: higher-scoring past answers surface preferentially.

### User Sentiment Feedback

Each response card has `↑` (good) and `↓` (poor) buttons. Clicking one fires a `user.feedback` event on the bus, which the `RewardService` routes back to the producing agent. Sentiment is also stored on the memory engram and incorporated into future retrieval ranking.

### Session Persistence

When you rejoin a past conversation, all XAI metadata is restored: groundedness badge, Prometheus score and feedback, and retrieval sources. Nothing is recalculated — the stored state is replayed from the session record.

---

## Backend Observability Windows

LoCAL2 ships a set of PySide6 operator windows for live system inspection. These are separate from the browser-based user chat UI:

| Window | What it shows |
|---|---|
| **MemoryWindow** | Browse and search episodic engrams; similarity scores; score annotations |
| **CriticWindow** | Live Prometheus grading stream; score history |
| **ToolWindow** | Real-time tool activity log per tool |
| **GeneratorWindow** | Generator identity, state machine, context fill, tool registry, system prompt, peer registry |
| **DocumentsWindow** | RAG library collections; ingested documents; per-chunk progress |

These windows connect to the same ZMQ bus as the chat pipeline. They do not add any processing overhead — they observe passively.

---

## Configuration

XAI and privacy-relevant settings live in the following config files:

| File | Controls |
|---|---|
| `config/critic.yaml` | Prometheus model, rubric text, grading prompt, timeout |
| `config/web_search.yaml` | Search provider (`searxng` or `mock`), SearXNG URL |
| `config/web_fetch.yaml` | Max chars fetched per page |
| `config/search_memory.yaml` | Tool description and trigger phrases |
| `config/generator.yaml` | Model, context window, system prompt, tool enable/disable |

See [config-reference.md](config-reference.md) for all knobs and defaults.
