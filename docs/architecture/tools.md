# Tools

LoCAL2 has seven tools. Each is a stateless `*Tool` participant: it subscribes to `tool.request.<name>`, executes, and publishes `tool.result.<name>` + `tool.activity.<name>`. Gemma decides when to call them based on the description in the tool's JSON schema.

For the tool call protocol and timing, see [../diagrams/tool-call-flow.md](../diagrams/tool-call-flow.md).

---

## Tool Protocol

All tools follow the same bus contract:

1. On startup, publish JSON schema on `tool.schema`.
2. Subscribe to `tool.request.<name>` and `schema.request`.
3. On `schema.request`, re-announce schema (supports late-joining generators and UI).
4. On `tool.request.<name>`, execute, publish `tool.result.<name>` + `tool.activity.<name>`.

The `function.name` in the schema **must** match the `tool.request.<name>` subject suffix exactly. A mismatch causes the generator to publish to the right subject but the tool to never subscribe — silent timeout.

Schema descriptions carry "when to call" guidance. This is the correct place for trigger conditions (e.g. "call for any question about the current time"). It does not belong in the system prompt.

Config hot-reload: when the UI saves a tool's YAML settings, it publishes `config.reload`. The tool invalidates its config cache and re-announces its schema.

---

## search_memory

`src/local/tools/search_memory_tool.py`

Semantic search over the episodic store (ChromaDB `local_memory` collection).

**Input:** `query` (string)
**Output:** ranked list of past Q+A pairs with similarity scores, formatted as text.

Uses `MemoryService.search_episodic()` with `n_results` from `config/search_memory.yaml`. Results are weighted by critic score (see [memory-participant.md](memory-participant.md)).

Gemma is instructed to call this when the user asks about prior preferences, habits, or anything they may have told the system before. This is enforced via the system prompt rule and the schema description.

---

## web_search

`src/local/tools/web_search_tool.py`

Web search via a configurable provider. Default: SearXNG (self-hosted, no API key required).

**Input:** `query` (string)
**Output:** list of results with titles, URLs, and snippets, formatted as text.

Supported providers: `searxng`, `brave`, `tavily`. Provider is set in `config/web_search.yaml`.

The schema description instructs Gemma to call this for current events, live data, or anything it cannot reliably know from training data.

| Config key | Default | Description |
|---|---|---|
| `provider` | `searxng` | Search backend |
| `searxng_url` | `http://localhost:8080` | SearXNG instance URL |
| `max_results` | `5` | Results to return |
| `timeout` | `10` | Request timeout (seconds) |

---

## web_fetch

`src/local/tools/web_fetch_tool.py`

Fetches a URL and extracts readable text content using httpx + BeautifulSoup.

**Input:** `url` (string)
**Output:** extracted page text, truncated to a configurable limit.

Typically called after `web_search` when Gemma wants to read the full content of a specific page. The schema description ties it explicitly to this follow-up pattern.

---

## get_datetime

`src/local/tools/datetime_tool.py`

Returns the current local date, time, day of week, and timezone. No parameters. Stdlib only — no external dependencies, no network call.

**Output format:** `"Tuesday 2026-06-07 09:17:42 PDT (UTC-7)"`

The schema description instructs Gemma to always call this for time/date questions and never answer from training data (training cutoff ≠ current date).

---

## get_location

`src/local/tools/location_tool.py`

Returns the current location via IP geolocation (ipinfo.io). Results are cached for 5 minutes. `config/location.yaml` can override the live lookup with a static location.

**Output format:** `"Cupertino, CA, US"`

If `config/location.yaml` exists with a `city` key, that value is returned directly without a network call.

| Config key | Description |
|---|---|
| `city` | Static override for city |
| `region` | Static override for region |
| `country` | Static override for country |

---

## search_papers

`src/local/tools/semantic_scholar_tool.py`

Academic paper search via the Semantic Scholar Graph API.

**Input:** `query` (string)
**Output:** list of papers with title, authors, year, abstract, and URL.

**Rate limiting:** 1 request/second enforced by a token bucket. Retries automatically on HTTP 429 with 2-second backoff.

**arXiv URL fallback:** if Semantic Scholar does not provide a direct PDF URL, the tool checks `externalIds.ArXiv` and constructs `https://arxiv.org/abs/{id}`. This makes results compatible with `web_fetch`.

**API key:** set `SEMANTIC_SCHOLAR_API_KEY` in `~/.zshrc`. Without it the tool works at the public rate limit.

---

## search_library

`src/local/tools/search_library_tool.py`

Semantic search over ingested document collections (RAG). Backed by `DocumentService` / ChromaDB `collective.documents`.

**Input:** `query` (string), and optionally `collection` (enum when multiple collections exist).

**Dynamic schema:** the schema adapts to the current number of collections:
- 0 or 1 collection: no `collection` parameter — Gemma just provides a query.
- 2+ collections: `collection` is a required enum with per-value descriptions so Gemma can target the right collection.

For document ingestion and collection management, see [document-service.md](document-service.md).

| Config key (documents.yaml) | Default | Description |
|---|---|---|
| `chunk_size` | `1500` | Characters per chunk |
| `chunk_overlap` | `200` | Overlap between chunks |
| `n_results` | `5` | Results returned per search |
| `embed_model` | `nomic-embed-text` | Ollama embedding model |
