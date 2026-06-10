# LoCAL2

Loosely Coupled Agent Language model — Second Generation.

LLM-native tool calling with Gemma 4 as the orchestrator. Web search, memory recall, and feedback loops augment Gemma's native reasoning — the model decides when to use them.

**Reference hardware:** Mac Mini M4 Pro, 64GB unified memory. Tool calling and thinking tokens work best with sufficient VRAM/unified memory; performance on lower-spec hardware will vary.

---

## Quick start

```bash
brew install ollama          # one-time prerequisite
pip install local2
local2 setup                 # pulls required models, writes initial config
local2                       # opens the web UI at http://localhost:8000
```

---

## Prerequisites

- Python 3.11+
- [Ollama](https://ollama.com) — download the macOS app or `brew install ollama`

---

## Install

```bash
pip install local2
local2 setup
```

`local2 setup` does three things:
1. Writes default config files to `~/.local2/config/`
2. Pulls `gemma4:e4b` (generator + memory classifier)
3. Pulls `nomic-embed-text` (embeddings for memory and RAG library)

---

## Run

```bash
local2                       # web UI, opens browser at http://localhost:8000
local2 --headless            # web server only, no browser pop
local2 --panels              # web UI + read-only Qt observer windows
local2 --desktop             # legacy PySide6 full desktop UI
local2 --model gemma4:27b    # override the generator model at startup
local2 --web-port 9000       # use a different port
```

---

## Web search

Web search is optional. Two ways to enable it:

### Option A — Brave or Tavily (no Docker required)

Get an API key from [brave.com/search/api](https://brave.com/search/api) or [tavily.com](https://tavily.com), then edit `~/.local2/config/web_search.yaml`:

```yaml
provider: brave          # or: tavily
brave_api_key: sk-...    # or tavily_api_key: tvly-...
```

### Option B — SearXNG (self-hosted, no API key)

Requires Docker Desktop.

```bash
docker compose up -d
```

SearXNG runs at `http://localhost:8080`. This is the default provider if you don't change `web_search.yaml`.

You need a secret key in `.env` for SearXNG to start:

```bash
echo "MY_SEARX_SECRET=$(openssl rand -hex 32)" > .env
```

---

## Academic search (optional)

`search_papers` uses the Semantic Scholar API. It works without a key at the free rate limit (1 req/sec). For higher limits:

```bash
echo 'export SEMANTIC_SCHOLAR_API_KEY=<your-key>' >> ~/.zshrc
source ~/.zshrc
```

Get a free key at [semanticscholar.org/product/api](https://www.semanticscholar.org/product/api).

---

## Configuration

User config lives in `~/.local2/config/`. Defaults are written there by `local2 setup` and can be edited freely — upgrades never overwrite them.

| File | Controls |
|---|---|
| `generator.yaml` | Model, context size, temperature, tool timeout, system prompt |
| `web_search.yaml` | Search provider, API keys, max results |
| `web_fetch.yaml` | Max chars extracted, fetch timeout |
| `critic.yaml` | Critic model, grading rubric, grade timeout |
| `memory.yaml` | ChromaDB path, episodic memory collection |
| `search_memory.yaml` | Max results from memory search |
| `semantic_scholar.yaml` | Max results, request timeout |
| `documents.yaml` | Chunk size/overlap, RAG library collections |
| `location.yaml` | Optional static location override (skips live IP lookup) |
| `bus.yaml` | ZMQ proxy ports |
| `system.yaml` | Instance ID, debug flags |

---

## Document library (RAG)

LoCAL2 maintains a persistent local knowledge base you can query with `search_library`. Use the library window in the Qt UI, or the CLI:

```bash
# Ingest one or more files
PYTHONPATH=src python scripts/ingest.py path/to/file.pdf

# List all ingested sources
PYTHONPATH=src python scripts/ingest.py --list

# Delete a source by filename
PYTHONPATH=src python scripts/ingest.py --delete "file.pdf"
```

Supported formats: PDF, TXT, MD, PY, YAML, JSON, CSV. Files are chunked into 1500-character segments and embedded with `nomic-embed-text`. Re-ingesting the same file is safe — chunks are upserted by deterministic ID.

---

## After a reboot

**Ollama:** On macOS, a stale `ollama serve` process can persist after reboot alongside the freshly launched Ollama.app, splitting IPv4 and IPv6 across two processes. If `ollama.chat()` hangs silently, check:

```bash
pgrep -fl ollama   # should show exactly one process
```

Kill the older PID if two appear.

**SearXNG (if using Docker):** Docker Desktop needs to be running before `docker compose up -d`.

---

## Development

Clone the repo and install in editable mode:

```bash
git clone https://github.com/rkoike88/LoCAL2
cd LoCAL2
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

Run the app:

```bash
python run_local.py          # equivalent to 'local2'
```

Run tests:

```bash
make test                    # or: PYTHONPATH=src python -m pytest tests/ -q
```

Build a distributable wheel (builds frontend first):

```bash
make dist
python -m build
```

---

## Architecture

See [docs/architecture/](docs/architecture/) for design documents covering the bus topology, agent state machines, tool protocol, and configuration reference.
