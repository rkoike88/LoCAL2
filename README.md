# LoCAL2

Loosely Coupled Agent Language model — Second Generation.

LLM-native tool calling with Gemma 4 as the orchestrator. Web search, memory recall, and feedback loops augment Gemma's native reasoning — the model decides when to use them.

**Reference hardware:** Mac Mini M4 Pro, 64GB unified memory. Tool calling and thinking tokens work best with sufficient VRAM/unified memory; performance on lower-spec hardware will vary.

---

## Quick start

```bash
brew install ollama pipx     # one-time prerequisites
pipx install local2
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
brew install pipx
pipx install local2
local2 setup
```

`local2 setup` does five things:
1. Writes default config files to `~/.local2/config/`
2. Copies `docker-compose.yml` and SearXNG settings to `~/.local2/`
3. Generates a random `MY_SEARX_SECRET` in `~/.local2/.env`
4. Pulls `gemma4:e4b` (generator + memory classifier)
5. Pulls `nomic-embed-text` (embeddings for memory and RAG library)

The critic uses `prometheus-7b:latest` (pulled on first use, or run `ollama pull prometheus-7b:latest` to pre-fetch).

---

## Run

```bash
local2                          # web UI, opens browser at http://localhost:8000
local2 --headless               # full local stack, no browser pop
local2 --panels                 # web UI + read-only Qt observer windows
local2 --desktop                # legacy PySide6 full desktop UI
local2 --model gemma4:27b       # override the generator model at startup
local2 --web-port 9000          # use a different port

# Remote-bus mode — run only the web server; agents stay on another machine
local2 --web-only --ipaddress 192.168.1.10
```

---

## Web search

Web search requires SearXNG (self-hosted, no API key). Requires Docker Desktop.

```bash
local2 searxng up
```

SearXNG runs at `http://localhost:8080` and is the default provider in `web_search.yaml`.

---

## Academic search (optional)

`search_papers` uses the Semantic Scholar API. It works without a key at public rate limits. For higher limits, set an API key:

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
| `web_search.yaml` | Search provider (`searxng`), SearXNG URL, max results |
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
# Ingest one or more files into a named collection
PYTHONPATH=src python scripts/ingest.py --collection mba path/to/file.pdf

# List all ingested sources (all collections, or one)
PYTHONPATH=src python scripts/ingest.py --list
PYTHONPATH=src python scripts/ingest.py --list --collection mba

# Delete a source by filename
PYTHONPATH=src python scripts/ingest.py --delete "file.pdf" --collection mba
```

Supported formats: PDF, TXT, MD, PY, YAML, JSON, CSV. Files are chunked into 1500-character segments and embedded with `nomic-embed-text`. Re-ingesting the same file is safe — chunks are upserted by deterministic ID.

---

## Remote access

The web UI works from any browser on the same network — no installation needed on the remote device.

**From another Mac, iPad, or iPhone (same WiFi):**

1. Find the host machine's IP: `ipconfig getifaddr en0`
2. Open a browser to `http://<host-ip>:8000`

macOS firewall must allow inbound connections on port 8000 (System Settings → Network → Firewall).

**Running only the web server on a remote machine:**

```bash
# On the remote machine — no agents, no proxy, no GPU needed
local2 --web-only --ipaddress <host-ip>
```

This starts just the web server, which connects to the host machine's ZMQ bus. The host machine runs `local2` as normal and handles all generation and tool calls.

The host firewall must allow inbound connections on ports **8000** (HTTP/WebSocket) and **5570/5571** (ZMQ bus).

**Outside the local network:**

Use [Tailscale](https://tailscale.com) or a VPN. Direct port-forwarding on a router works but exposes the server without authentication — not recommended.

---

## File attachments

The web UI supports file attachments. Click the paperclip icon in the input bar to attach:

- **Images** (jpg, png, gif, webp) — sent to the model as vision input
- **Documents** (pdf, txt, md, py, js, ts, yaml, json, csv) — text is extracted and prepended to the query

Attachments are processed server-side before being included in the generation context.

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
