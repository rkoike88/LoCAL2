# Configuration Reference

All configuration lives in `config/*.yaml`. Files are loaded via `get_config(name)` which reads `config/<name>.yaml`. Changes take effect on the next read unless a component caches the config (tools invalidate via `ConfigManager.invalidate(name)` on `config.reload`).

---

## config/generator.yaml

Controls GeneratorAgent.

| Key | Default | Description |
|---|---|---|
| `model` | `gemma4:e4b` | Ollama model tag for generation |
| `num_ctx` | `128000` | Context window size — always set explicitly; never rely on Ollama's default |
| `temperature` | `0.1` | Sampling temperature — 0.1 required for reliable tool calling |
| `max_tool_iterations` | `5` | Max tool-call rounds per generation turn before forcing a final answer |
| `tool_timeout` | `20` | Seconds to wait for a tool result before returning an error string |
| `max_attachment_chars` | `32000` | Truncation limit per text attachment |
| `compaction_tail_turns` | `4` | Verbatim user+assistant pairs kept after compaction summary |
| `system_prompt` | see file | Injected as `{"role": "system", ...}` at the start of every messages array |
| `tools` | `[]` | Do not populate; tools register dynamically via `tool.schema` at runtime |
| `ollama_debug` | `false` | (in system.yaml) Print Ollama request/response timing to stdout |

---

## config/system.yaml

Instance-level identity. Read by GeneratorAgent at startup.

| Key | Default | Description |
|---|---|---|
| `instance_id` | hostname | Unique identifier for this LoCAL2 instance. Used in `generator.status` payloads and reserved for distributed routing (future). Falls back to `socket.gethostname()` if absent. |
| `ollama_debug` | `false` | Print Ollama request/response timing to stdout |

---

## config/critic.yaml

Controls CriticAgent and Prometheus.

| Key | Default | Description |
|---|---|---|
| `model` | `prometheus-7b:latest` | Prometheus model tag |
| `temperature` | `0.0` | Deterministic grading |
| `num_ctx` | `32000` | Prometheus context window |
| `grade_timeout` | `30` | Seconds to wait for Prometheus response; can be slow on first call |
| `rubric` | see file | Evaluation rubric injected into every grading prompt |

---

## config/memory.yaml

Controls MemoryService and MemoryAgent classification.

| Key | Default | Description |
|---|---|---|
| `collection` | `local_memory` | ChromaDB collection name for episodic store |
| `chroma_path` | `.chroma` | Filesystem path for the ChromaDB store |
| `embed_model` | `nomic-embed-text` | Ollama embedding model |
| `n_results` | `5` | Results returned by `search_episodic()` |
| `model` | `gemma4:e4b` | LLM for intent/entity classification in MemoryAgent |

---

## config/documents.yaml

Controls DocumentService and SearchLibraryTool.

| Key | Default | Description |
|---|---|---|
| `collection` | `collective.documents` | ChromaDB collection name for document store |
| `chroma_path` | `.chroma` | Filesystem path (shared with memory store) |
| `embed_model` | `nomic-embed-text` | Ollama embedding model |
| `chunk_size` | `1500` | Characters per chunk |
| `chunk_overlap` | `200` | Character overlap between adjacent chunks |
| `n_results` | `5` | Results returned per search query |
| `collections` | see file | List of `{name, display_name, description}` entries defining available collections |

---

## config/bus.yaml

ZMQ proxy network configuration.

| Key | Default | Description |
|---|---|---|
| `proxy_host` | `127.0.0.1` | Host to connect to for all participants |
| `ports.proxy_frontend` | `5570` | ZMQ frontend (external producers) |
| `ports.proxy_backend` | `5571` | ZMQ backend (all participants subscribe/publish) |

To connect a remote agent: change `proxy_host` to the IP of the machine running the proxy. The proxy binds to `0.0.0.0` so it accepts connections from any interface.

---

## config/web_search.yaml

Controls WebSearchTool.

| Key | Default | Description |
|---|---|---|
| `provider` | `searxng` | Backend: `searxng`, `brave`, or `tavily` |
| `searxng_url` | `http://localhost:8080` | SearXNG instance URL |
| `max_results` | `5` | Number of results to return |
| `timeout` | `10` | HTTP request timeout (seconds) |
| `description` | see file | Tool schema description injected into Gemma's tool registry |
| `param_query` | see file | Parameter description for the `query` field |

---

## config/location.yaml

Optional static location override for LocationTool.

| Key | Description |
|---|---|
| `city` | Override city name (e.g. `"Cupertino"`) |
| `region` | Override region/state |
| `country` | Override country code |

If this file exists and contains a `city` key, the live IP geolocation lookup is skipped entirely. The file is optional — if absent, the tool uses ipinfo.io with a 5-minute TTL cache.

---

## config/search_memory.yaml

Controls SearchMemoryTool schema description.

| Key | Description |
|---|---|
| `description` | Tool schema description injected into Gemma's tool registry |
| `param_query` | Parameter description for the `query` field |

---

## config/semantic_scholar.yaml

Controls SemanticScholarTool.

| Key | Default | Description |
|---|---|---|
| `max_results` | `5` | Papers to return per search |
| `timeout` | `15` | HTTP request timeout (seconds) |

API key is set via `SEMANTIC_SCHOLAR_API_KEY` environment variable (in `~/.zshrc`), not in config. Without it the tool works at the public rate limit.

---

## Environment Variables

| Variable | Used by | Description |
|---|---|---|
| `SEMANTIC_SCHOLAR_API_KEY` | SemanticScholarTool | Optional API key for higher rate limits |

---

## Startup Order

Tools must start before the generator. The generator broadcasts `schema.request` 0.5s after startup; if tools aren't running yet they miss the request (MonitorApp also broadcasts `schema.request` 600ms after connecting to catch late starters).

Order enforced by `run_local.py`:
1. ZMQ proxy
2. All 7 tools (parallel threads)
3. Sleep 0.5s
4. GeneratorAgent
5. MemoryAgent, CriticAgent, RewardService
6. FastAPI web server (uvicorn)
7. Browser / Qt panels (depending on flags)

---

## Run Modes

`python run_local.py [flags]`

| Flag | Effect |
|---|---|
| *(none)* | Web UI only — FastAPI on port 8000, browser opens automatically |
| `--headless` | Web server only, no browser pop |
| `--panels` | Web UI + read-only Qt observer windows (GeneratorWindow, CriticWindow, MemoryWindow, ToolWindows) tiled to the right 2/3 of screen; browser in left 1/3 |
| `--desktop` | Legacy PySide6 full desktop UI (MainWindow); no web server |
| `--web-port PORT` | Override default port 8000 |
| `--model MODEL` | Override the Ollama model tag at startup |

In `--panels` mode, Qt windows are read-only observers (no bus commands). The single exception is a one-time `schema.request` broadcast at startup so tools re-announce their schemas and ToolWindows can be spawned.
