# LoCAL2

Loosely Coupled Agent Language model — Second Generation.

LLM-native tool calling with Gemma 4 as the orchestrator. Web search, memory recall, and feedback loops augment Gemma's native reasoning — the model decides when to use them.

## Prerequisites

- Python 3.11+ with packages from `requirements.txt`
- [Ollama](https://ollama.com) running locally with `gemma4:e4b` pulled
- Docker (for SearXNG web search)

## Setup

### 1. Install Docker Desktop

Download from [docker.com/products/docker-desktop](https://www.docker.com/products/docker-desktop/). Choose the right version for your chip:

```bash
uname -m   # arm64 = Apple Silicon, x86_64 = Intel
```

After installing, launch Docker Desktop and verify:

```bash
docker --version
```

### 2. Python dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure secrets

```bash
cp .env.example .env
```

Open `.env` and fill in `MY_SEARX_SECRET`. SearXNG requires this to start — it's an internal signing key, not a user-facing password. Generate one with:

```bash
openssl rand -hex 32
```

Paste the output as `MY_SEARX_SECRET=<value>` in `.env`. You only need to do this once.

`BRAVE_API_KEY` and `TAVILY_API_KEY` are only needed if you switch the search provider in `config/web_search.yaml` away from the default SearXNG.

### 4. Start SearXNG

```bash
docker compose up -d
```

SearXNG will be available at `http://localhost:8080`. Verify it's running:

```bash
curl "http://localhost:8080/search?q=test&format=json" | head -c 200
```

### 5. Pull the model

```bash
ollama pull gemma4:e4b
```

### 6. Run LoCAL2

```bash
# UI only
python run_local.py

# UI + REST API
python run_local.py --api

# Headless (API only)
python run_local.py --headless --api
```

## Configuration

All tunable parameters live in `config/`:

| File | Controls |
|---|---|
| `config/generator.yaml` | Model, context size, temperature, tool schemas, tool timeout |
| `config/web_search.yaml` | Search provider, max results, request timeout |
| `config/web_fetch.yaml` | Max chars extracted, fetch timeout |
| `config/bus.yaml` | ZMQ proxy ports |
| `config/system.yaml` | Debug flags |

## Running stories

```bash
PYTHONPATH=src python tests/run_story.py tests/stories/s1_basic_qa.yaml
PYTHONPATH=src python tests/run_story.py tests/stories/s2_multi_turn.yaml
```

## Architecture

See `.claude/plan_local2.html` for the full architecture plan.
