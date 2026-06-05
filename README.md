# LoCAL2

Loosely Coupled Agent Language model — Second Generation.

LLM-native tool calling with Gemma 4 as the orchestrator. Web search, memory recall, and feedback loops augment Gemma's native reasoning — the model decides when to use them.

**Reference hardware:** Mac Mini M4 Pro, 64GB memory. Performance on lower-spec hardware will vary — tool calling and thinking tokens work best with sufficient VRAM/unified memory.

## Prerequisites

- Python 3.11+
- [Ollama](https://ollama.com)
- Docker Desktop (for SearXNG web search)

## Setup

### 1. Install Ollama

Download from [ollama.com](https://ollama.com) and install. Verify it's running:

```bash
curl http://127.0.0.1:11434/api/tags
```

### 2. Pull the models

```bash
ollama pull gemma4:e4b
ollama pull nomic-embed-text
```

`gemma4:e4b` is the default model for the generator and critic. `gemma4:26b` is also supported for stronger tool calling reliability; configure in `config/generator.yaml`. A dedicated grading model can be used for the critic by setting `model` in `config/critic.yaml` — the default is `prometheus-7b:latest` (Prometheus-7B-v2.0).

`nomic-embed-text` is required for episodic memory and the RAG library. Both use it to embed and retrieve passages.

After pulling, do a quick sanity check:

```bash
ollama run gemma4:e4b
>>> hello
```

Type `/bye` to exit.

### 3. Install Docker Desktop

Download from [docker.com/products/docker-desktop](https://www.docker.com/products/docker-desktop/). Check your chip first:

```bash
uname -m   # arm64 = Apple Silicon, x86_64 = Intel
```

After installing, launch Docker Desktop and verify:

```bash
docker --version
```

### 4. Set up Python environment

Create and activate a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

You should see `(.venv)` on the left of your prompt. Upgrade pip first:

```bash
python3 -m pip install --upgrade pip
```

Then install dependencies:

```bash
pip install -r requirements.txt
```

### 5. Configure secrets

```bash
cp .env.example .env
```

Open `.env` and fill in `MY_SEARX_SECRET`. SearXNG requires this to start — it's an internal signing key, not a user-facing password. Generate one with:

```bash
openssl rand -hex 32
```

Paste the output as `MY_SEARX_SECRET=<value>` in `.env`. You only need to do this once.

`BRAVE_API_KEY` and `TAVILY_API_KEY` are only needed if you switch the search provider in `config/web_search.yaml` away from the default SearXNG.

`SEMANTIC_SCHOLAR_API_KEY` is optional but recommended — the free tier rate-limits to 1 req/sec without a key. Add it to your shell environment (not `.env`):

```bash
echo 'export SEMANTIC_SCHOLAR_API_KEY=<your-key>' >> ~/.zshrc
source ~/.zshrc
```

Get a free key at [semanticscholar.org/product/api](https://www.semanticscholar.org/product/api).

### 6. Start SearXNG

```bash
docker compose up -d
```

SearXNG will be available at `http://localhost:8080`. Verify it's running:

```bash
curl "http://localhost:8080/search?q=test&format=json" | head -c 200
```

Once started, SearXNG runs in the background and restarts automatically with Docker Desktop on login.

### 7. Run LoCAL2

```bash
# UI only
python run_local.py

# UI + REST API
python run_local.py --api

# Headless (API only)
python run_local.py --headless --api

# Use a different model
python run_local.py --model gemma4:26b
```

## Configuration

All tunable parameters live in `config/`:

| File | Controls |
|---|---|
| `config/generator.yaml` | Model, context size, temperature, tool schemas, tool timeout |
| `config/web_search.yaml` | Search provider, max results, request timeout |
| `config/web_fetch.yaml` | Max chars extracted, fetch timeout |
| `config/critic.yaml` | Critic model, grading rubric, grade timeout |
| `config/memory.yaml` | ChromaDB path, episodic memory collection name |
| `config/search_memory.yaml` | Max results returned by `search_memory` tool |
| `config/semantic_scholar.yaml` | Max results, timeout, API fields returned |
| `config/documents.yaml` | Chunk size/overlap, topic, ChromaDB collection for RAG library |
| `config/location.yaml` | Optional static location override (skips live IP geolocation) |
| `config/bus.yaml` | ZMQ proxy ports |
| `config/system.yaml` | Debug flags |

## Document library (RAG)

LoCAL2 maintains a persistent local knowledge base you can query with `search_library`. Use the **library** window (📚 button in the sidebar) to ingest files.

### Ingest from the UI

1. Click **📚** in the sidebar to open the library window.
2. Set a **Topic** (e.g. "MBA textbooks covering strategy, finance, and marketing") and click **Save** — Gemma uses this to decide when to search the library instead of the web.
3. Click **+ Files** to pick individual files, or **+ Folder** to ingest an entire folder recursively. Supported formats: PDF, TXT, MD, PY, YAML, JSON, CSV.

A progress bar shows embedding progress per file. Files are chunked into 1500-character segments and embedded with `nomic-embed-text`. Re-ingesting the same file is safe — chunks are upserted by deterministic ID.

### Ingest from the CLI

```bash
# Ingest one or more files
PYTHONPATH=src python scripts/ingest.py path/to/file.pdf path/to/file.txt

# List all ingested sources
PYTHONPATH=src python scripts/ingest.py --list

# Delete a source by filename
PYTHONPATH=src python scripts/ingest.py --delete "file.pdf"
```

## Running stories

```bash
PYTHONPATH=src python tests/run_story.py tests/stories/s1_basic_qa.yaml
PYTHONPATH=src python tests/run_story.py tests/stories/s2_multi_turn.yaml
```

## After a reboot

Two services don't start automatically and need to be launched before running LoCAL2:

**1. Docker Desktop** — SearXNG won't be running. Open Docker Desktop, wait for it to be ready, then verify:

```bash
docker compose ps   # searxng should show "Up"
docker compose up -d   # if it's not running
```

**2. Ollama** — On macOS, a stale `ollama serve` process from before the reboot can persist alongside the freshly launched Ollama.app, splitting IPv4 and IPv6 across two processes. The Python `ollama` library may connect to the wrong one, causing `ollama.chat()` to hang silently with no error.

Check before starting:

```bash
pgrep -fl ollama   # should show exactly one "ollama serve" process
```

If two appear (one from `/usr/local/bin/ollama`, one from `/Applications/Ollama.app`), kill the older one:

```bash
kill <old-pid>
```

The Ollama.app process will then own port 11434 on both IPv4 and IPv6.

## Architecture

See `.claude/plan_local2.html` for the full architecture plan.
