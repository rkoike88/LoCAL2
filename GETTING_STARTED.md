# Getting Started

**← [README / Architecture](README.md)**

---

## Quick start (macOS)

```bash
brew install ollama pipx     # one-time prerequisites
pipx install local2
local2 setup                 # pulls required models, writes initial config
local2                       # opens the web UI at http://localhost:8000
```

---

## Windows Installation Guide

This guide walks through installation step by step. No prior experience with Python, Docker, or the command line is assumed.

**What you'll install:**

| Software | Purpose | Size |
|---|---|---|
| Python | Runs LoCAL2 | ~25 MB |
| Ollama | Runs the AI models locally — nothing is sent to the cloud | ~50 MB |
| AI models | The brains (downloaded once, stored on your machine) | ~11 GB |
| Docker Desktop | Runs private web search (optional) | ~500 MB |
| LoCAL2 | The AI assistant | ~50 MB |

**Estimated time:** 20–30 minutes, plus download time for the AI models.

**Minimum hardware:** 16 GB RAM, Windows 10 version 2004 or newer (or Windows 11). 8 GB RAM will work but will be slower.

---

### Step 1 — Install Python

1. Open your web browser and go to **https://www.python.org/downloads/**
2. Click the large yellow **"Download Python 3.x.x"** button (any version 3.11 or newer is fine).
3. Open the downloaded file (it will be named something like `python-3.x.x-amd64.exe`).
4. **Before clicking anything else:** at the bottom of the first screen, check the box that says **"Add Python to PATH"**. This step is easy to miss and causes problems if skipped.
5. Click **"Install Now"** and wait for it to finish.
6. Click **Close**.

**Verify it worked:** Press the Windows key, type `cmd`, press Enter. A black window opens — this is the Command Prompt. Type the following and press Enter:

```
python --version
```

You should see something like `Python 3.12.4`. If you see an error instead, go back and reinstall Python, making sure to check "Add Python to PATH."

---

### Step 2 — Install Ollama

Ollama runs the AI models on your computer. No data leaves your machine.

1. Go to **https://ollama.com**
2. Click **"Download"** at the top of the page, then click **"Download for Windows"**.
3. Open the downloaded file (`OllamaSetup.exe`) and follow the prompts.
4. When installation finishes, a small llama icon will appear in the system tray — the row of small icons in the bottom-right corner of your screen, near the clock. This means Ollama is running.

**Verify it worked:** In Command Prompt, type:

```
ollama --version
```

You should see a version number like `ollama version 0.x.x`.

---

### Step 3 — Download the AI models

LoCAL2 uses several AI models. You download them once — they are stored permanently on your machine.

Open Command Prompt and run each of these commands, pressing Enter after each one and waiting for it to finish:

**Default text model (~5 GB):**
```
ollama pull gemma4:e4b-mlx
```

**Vision model (~10 GB — for image queries):**
```
ollama pull gemma4:e4b
```

**Memory indexing model (~274 MB — downloads quickly):**
```
ollama pull nomic-embed-text
```

**Response quality evaluator (~4.1 GB — optional but recommended):**
```
ollama pull prometheus-7b:latest
```

Each command shows a progress bar. Wait for it to say `success` before moving on. If your internet connection drops partway through, just run the same command again — it will resume where it left off.

> You can skip `prometheus-7b` for now. LoCAL2 will still work fully — you just won't see quality scores on responses. You can always pull it later.

---

### Step 4 — Install Docker Desktop (for web search)

Docker Desktop lets LoCAL2 run its own private web search engine (SearXNG) on your machine. Your searches are not logged or tracked. **Skip this step if you don't need web search — LoCAL2 works fine without it.**

1. Go to **https://www.docker.com/products/docker-desktop/**
2. Click **"Download for Windows"**.
3. Open the downloaded file (`Docker Desktop Installer.exe`).
4. When asked about WSL 2, leave the checkbox checked and click OK.
5. If prompted to restart your computer, click Restart. After restarting, Docker Desktop will open on its own.
6. On first launch, Docker Desktop may show a tutorial or setup screen — you can close or skip it.
7. Wait until the whale icon in the system tray shows **"Docker Desktop is running"** (hover over the icon to check).

> If Docker Desktop shows an error about virtualization, you may need to enable it in your computer's BIOS/UEFI settings. Search for your computer model + "enable virtualization" for instructions specific to your hardware.

---

### Step 5 — Install LoCAL2

Open Command Prompt and run:

```
pip install local2
```

You'll see a lot of text scroll by. Wait for the line that says `Successfully installed local2`.

Then run the first-time setup:

```
local2 setup
```

This creates a data folder at `C:\Users\YourName\.local2\`, copies default settings there, and verifies the AI models are ready.

---

### Step 6 — Start web search (optional)

If you installed Docker Desktop in Step 4, make sure the Docker Desktop whale icon is visible in the system tray (it must be running), then open Command Prompt and run:

```
local2 searxng up
```

You only need to do this once per session. SearXNG keeps running in the background until you shut down Docker Desktop or run `local2 searxng down`.

---

### Step 7 — Start LoCAL2

```
local2
```

Your default browser will open to **http://localhost:8000** and you can start chatting.

---

### Every time you use LoCAL2

1. Make sure the **Ollama llama icon** is visible in the system tray. If it's not there, open the Start menu, search for **Ollama**, and launch it. Wait about 30 seconds.
2. *(If you want web search)* Make sure **Docker Desktop** is running, then open Command Prompt and run `local2 searxng up`.
3. Open Command Prompt and run `local2`.

---

### Windows Troubleshooting

**"'python' is not recognized as an internal or external command"**
You skipped the "Add Python to PATH" checkbox. Go to Control Panel → Programs → Uninstall a program, remove Python, then reinstall from Step 1 and check that box.

**"'local2' is not recognized as an internal or external command"**
Close Command Prompt and open a new one. If that doesn't fix it, run `python -m pip install local2` instead, then try again.

**Ollama isn't responding / chat just spins**
Check the system tray for the llama icon. If it's not there, open the Start menu, find Ollama, and launch it. Give it 30 seconds to start before trying LoCAL2 again.

**"local2 searxng up" fails with a Docker error**
Docker Desktop must be fully running before this command will work. Open Docker Desktop from the Start menu, wait until the whale icon shows "Docker Desktop is running", then try again.

**Docker Desktop won't start / WSL 2 error**
Open PowerShell as administrator: Start menu → search "PowerShell" → right-click → "Run as administrator". Run:
```
wsl --install
```
Restart your computer and try Docker Desktop again.

---

## Prerequisites (macOS)

- Python 3.11+
- [Ollama](https://ollama.com) — download the macOS app or `brew install ollama`

---

## Install (macOS)

```bash
brew install pipx
pipx install local2
local2 setup
```

`local2 setup` does five things:
1. Writes default config files to `~/.local2/config/`
2. Copies `docker-compose.yml` and SearXNG settings to `~/.local2/`
3. Generates a random `MY_SEARX_SECRET` in `~/.local2/.env`
4. Pulls `gemma4:e4b` (vision model for image queries)
5. Pulls `nomic-embed-text` (embeddings for memory and RAG library)

The default text model is `gemma4:e4b-mlx`. To pre-fetch it: `ollama pull gemma4:e4b-mlx`.

The critic uses `prometheus-7b:latest` (pulled on first use, or run `ollama pull prometheus-7b:latest` to pre-fetch).

---

## Run

```bash
local2                          # web UI, opens browser at http://localhost:8000
local2 --headless               # full local stack, no browser pop
local2 --panels                 # web UI + read-only Qt observer windows
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

`search_papers` uses the Semantic Scholar API. It works without a key at public rate limits. For higher limits:

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
| `generator.yaml` | Models, context size, temperature, tool timeout, system prompt |
| `web_search.yaml` | Search provider, SearXNG URL, max results |
| `web_fetch.yaml` | Max chars extracted, fetch timeout |
| `critic.yaml` | Critic model, grading rubrics, grade timeout |
| `memory.yaml` | ChromaDB path, collection, retrieval settings |
| `search_memory.yaml` | Max results from memory search |
| `semantic_scholar.yaml` | Max results, request timeout |
| `documents.yaml` | Chunk size/overlap, RAG library collections |
| `personas.yaml` | Persona definitions (name, seed text) |
| `location.yaml` | Optional static location override (skips live IP lookup) |
| `bus.yaml` | ZMQ proxy ports |
| `system.yaml` | Instance ID, debug flags |

---

## Document library (RAG)

LoCAL2 maintains a persistent local knowledge base. Use the Documents panel in the web UI, or the CLI:

```bash
# Ingest files into a named collection
PYTHONPATH=src python scripts/ingest.py --collection mba path/to/file.pdf

# List all ingested sources
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

**Outside the local network:** Use [Tailscale](https://tailscale.com) or a VPN.

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

Run the comparison harness (requires LoCAL2 running on port 3000):

```bash
make harness                 # or: python -m harness.server
```
