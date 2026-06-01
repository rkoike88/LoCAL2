# LoCAL2 — Claude Code Instructions

## Project Philosophy

LoCAL2 is the second generation of LoCAL (Loosely Coupled Agent Language model). The core principles — loose coupling, agent-based, bus coordination — are unchanged. What changes is where the intelligence lives.

In v1, an explicit orchestration layer (Analyst, Synthesizer, Gateway) wrapped around the LLM. In v2, the LLM is the orchestrator. Gemma handles conversation understanding, reasoning, and task decomposition natively via tool calling and full conversation history. LoCAL2's job is to provide the tools, memory, and feedback loops that augment those native capabilities — not replace them.

**Three going-in objectives:**
1. **Native conversation history** — the generator receives the full conversation messages array; Gemma handles followup, pronoun resolution, and multi-turn reasoning without preprocessing
2. **Tool-native architecture** — web search and memory recall via tool calling; Gemma decides when to use them
3. **Externalized LLM workings** — thinking tokens, tool calls, memory recalls, and conversation state are first-class visible artifacts surfaced in the UI

## Debugging Approach

When asked to describe or diagnose a problem, give a direct concise answer first. Avoid lengthy analysis or speculative debugging before checking the obvious root cause — inspect config/startup/subscription lists first.

## Project Stack

- Primary language: Python (with YAML config)
- Test runner: `PYTHONPATH=src python -m pytest tests/ -q`
- Run a single file: `PYTHONPATH=src python -m pytest tests/test_<name>.py --tb=short -q`
- Architecture: LLM-native tool calling with message bus for outer coordination

## Design Principles

**LLM as orchestrator.** Gemma decides when to call tools, how to decompose questions, and how to handle multi-turn conversation. The system provides tools and context; the LLM decides what to do with them.

**Loose coupling over convenience.** Agents communicate only through the bus (pub/sub). No direct function calls between agents.

**Tools, not task pipelines.** External capabilities (web search, memory recall) are synchronous tool calls within a generation turn — not async bus-dispatched tasks with explicit decomposition and aggregation.

**Externalize, don't hide.** Thinking tokens, tool calls, and memory retrievals are surfaced visibly. Observability is a first-class feature, not a debug side channel.

**Additive enrichment.** LoCAL2 adds memory, tools, and feedback loops to Gemma's native capabilities. It does not preprocess or transform the query before the generator sees it.

**Externalize configuration.** Keep tunable parameters in `config/*.yaml` and load via `get_config()`. Don't hardcode thresholds, counts, or feature flags in Python.

**Explicit state machines.** Every agent must have a state machine defined in `states.py` and `transitions.py`. No implicit state.

**Generalize, don't patch.** When you encounter a one-off problem, ask: "Is this a symptom of a broader design issue?" Add the general solution, not a workaround.

## Participant Naming Convention

| Suffix | LLM? | Triggered by | Output destination |
|---|---|---|---|
| `*Agent` | Yes | System (bus event) | Bus subject (not Gemma) |
| `*Tool` | No | Gemma (`tool.request.*`) | Back to Gemma via `tool.result.*` |
| `*AgentTool` | Yes | Gemma (`tool.request.*`) | Back to Gemma via `tool.result.*` |

## Key Participants

| Participant | Role |
|---|---|
| `generator_agent.py` | Core LLM agent: receives `query.received`, maintains conversation history, natively orchestrates tool calls (web_search, web_fetch, recall_memory), publishes `response.generation` |
| `web_search_tool.py` | Executes `web_search` tool call — configurable provider (SearXNG / Brave / Tavily); publishes tool schema on startup |
| `web_fetch_tool.py` | Executes `web_fetch` tool call — httpx + BeautifulSoup extraction; Gemma decides which URL to fetch after web_search |
| `critic_agent.py` | System-triggered post-generation observer; Prometheus absolute grading (1–5); fires on `response.generation`; publishes `critique.result` |
| `critic_agent_tool.py` | Gemma-callable pairwise comparison; Prometheus pairwise ranking; fires on `tool.request.critic_comparison`; result returns to Gemma context |
| `memory_service.py` | Episodic memory store (ChromaDB) — Phase 2; surfaced as `recall_memory` tool |
| `reward_service.py` | Routes `user.feedback` → `reward.event` to producing agents — Phase 4 |

## Bus Subjects

- `query.received` — new user query arrives
- `response.generation` — generator publishes final answer
- `tool.schema` — tool publishes its JSON schema on startup; GeneratorAgent maintains live registry
- `tool.request.web_search` / `tool.result.web_search` — web search execution
- `tool.request.web_fetch` / `tool.result.web_fetch` — URL fetch + extraction
- `tool.request.critic_comparison` / `tool.result.critic_comparison` — Prometheus pairwise (Gemma-callable)
- `critique.result` — Prometheus absolute grade (1–5); system-triggered after every generation
- `answer.dialog` — conversation turn appended for history tracking
- `user.feedback` — user thumbs up/down signal
- `reward.event` — targeted reward to producing agent

## Architecture Invariants

- The bus is the only coordination mechanism — no direct agent-to-agent calls
- The LLM receives the raw query and full conversation history — no preprocessing or query rewriting before the generator
- Tool calls (web_search, recall_memory) are synchronous within a generation turn — not bus events
- Thinking tokens are surfaced to the UI — not stripped and discarded
- Conversation history is passed as a messages array to the Ollama chat endpoint — never embedded in a flat prompt string
- `num_ctx` is always set explicitly in config — never rely on Ollama's hardware default (clips to 4k below 24GB VRAM)
- Gemma 4 thinking tokens must be stripped from assistant turns before passing history back to the model

## Memory Namespaces

- `agent.<name>.episodic` — per-agent interaction traces
- `collective.knowledge` — cross-agent elevated patterns
- `collective.sessions` — summarized Q&A exchanges

## Testing

Stories are the acceptance criteria. Unit tests verify isolated logic; stories verify that the full system produces the right behavior end-to-end across conversation turns.

- Stories live in `tests/stories/*.yaml` — YAML files defining multi-turn conversations with expected bus events and answer content
- Before declaring a feature done, there must be a story covering it
- Story structure: `story_id`, `title`, `turns` (query + assertions), `expected` (subjects present/absent), `critic`, `notes`
- Run stories against the live stack after any significant change

## Verification Before Claiming Done

- Always run `git status` and `git log --oneline -10` before claiming work is incomplete
- Run the full test suite after refactors and report exact pass/fail counts
- If sandbox blocks test execution, say so explicitly rather than implying success

## Refactoring Discipline

- Before removing any symbol (subjects, states, methods), grep for all consumers across the repo — including scripts/ and tests/
- Shared subjects/state enums are the connective tissue of this system — treat removals as breaking changes
- Bus monitor before code reading: when a subject is missing from observed events, tap the bus first to confirm whether the message is reaching the bus at all
