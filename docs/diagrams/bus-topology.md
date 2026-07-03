# Bus Topology

All participants connect to a single ZMQ XPUB/XSUB proxy. The proxy is the only network node — there are no direct participant-to-participant connections.

```
                    ┌─────────────────────────────────────────────┐
                    │           ZMQ XPUB/XSUB Proxy               │
                    │  frontend :5570  ←→  backend :5571          │
                    │  binds 0.0.0.0 (LAN-accessible)             │
                    └─────────────────────────────────────────────┘
                          │                │
              ┌───────────┘                └──────────────┐
              │  PUBLISHERS                SUBSCRIBERS    │
              │                                           │
  ┌─────────────────┐          Subjects published:        │
  │  GeneratorAgent │──────── response.generation ────────┤→ FastAPI Gateway, CriticAgent, MemoryAgent, ModelService
  │                 │──────── answer.dialog ──────────────┤→ MemoryAgent
  │                 │──────── generation.thinking ────────┤→ FastAPI Gateway
  │                 │──────── agent.transition ───────────┤→ MonitorApp
  │                 │──────── generator.status ───────────┤→ MonitorApp (GeneratorWindow)
  │                 │──────── compaction.result ──────────┤→ FastAPI Gateway
  └─────────────────┘
  ┌─────────────────┐
  │  ToolDispatcher │──────── tool.call.* ────────────────┤→ *Tools
  └─────────────────┘
  ┌─────────────────┐
  │   ModelService  │──────── compaction.request (auto)───┤→ GeneratorAgent/ModelService
  │                 │──────── compaction.result ──────────┤→ FastAPI Gateway
  └─────────────────┘
  ┌─────────────────┐
  │   CriticAgent   │──────── critique.result ────────────┤→ FastAPI Gateway, MemoryAgent
  │                 │──────── agent.transition ───────────┤→ MonitorApp
  └─────────────────┘
  ┌─────────────────┐
  │   MemoryAgent   │──────── agent.transition ───────────┤→ MonitorApp
  └─────────────────┘
  ┌─────────────────┐
  │  RewardService  │──────── reward.event ───────────────┤→ (logged)
  └─────────────────┘
  ┌─────────────────┐
  │    *Tools (8)   │──────── tool.result.* ──────────────┤→ ToolDispatcher
  │                 │──────── tool.activity.* ────────────┤→ MonitorApp (ToolWindows)
  │                 │──────── tool.schema ────────────────┤→ GeneratorAgent, MonitorApp
  └─────────────────┘
  ┌─────────────────┐
  │  FastAPI        │──────── query.received ─────────────┤→ GeneratorAgent
  │  Gateway        │──────── tool.schema.request ────────┤→ *Tools, GeneratorAgent
  │                 │──────── user.feedback ──────────────┤→ RewardService
  │                 │──────── compaction.request ─────────┤→ ModelService
  └─────────────────┘
  ┌─────────────────┐
  │  MonitorApp     │──────── tool.schema.request ────────┤→ *Tools (once at startup)
  │  (Qt panels,    │
  │  --panels only) │
  └─────────────────┘
  ┌─────────────────┐
  │  schema_refresh │──────── tool.schema.request ────────┤→ *Tools (2s after web server starts)
  │  (daemon thread,│  ZMQ slow-joiner fix: connects pub
  │  web mode only) │  socket before sleeping, so the message
  └─────────────────┘  is not dropped on the floor
  ┌─────────────────┐
  │  Qt settings    │──────── config.reload ──────────────┤→ *Tools, GeneratorAgent
  │  (ToolWindow /  │
  │  GeneratorWindow│
  │  save button)   │
  └─────────────────┘
```

## Subject Subscription Map

| Participant | Subscribes to |
|---|---|
| GeneratorAgent | `query.received`, `tool.schema`, `tool.schema.request`, `compaction.request`, `config.reload` |
| ToolDispatcher | `tool.result.*` (per-call short-lived subscriptions) |
| ModelService | `response.generation`, `compaction.request` |
| CriticAgent | `response.generation` |
| MemoryAgent | `response.generation`, `critique.result` |
| RewardService | `user.feedback` |
| Each `*Tool` | `tool.call.<name>`, `tool.schema.request` |
| FastAPI Gateway | `generation.thinking`, `response.generation`, `critique.result`, `answer.dialog`, `compaction.result` |
| MonitorApp (Qt) | `tool.schema`, `generator.status`, `agent.transition`, `critique.result`, `tool.activity.*` |

## LAN Distribution

The proxy binds to `0.0.0.0`, so any participant on the LAN can connect by setting `proxy_host` in `config/bus.yaml` (or `LOCAL2_PROXY_HOST` env var / `--ipaddress` CLI flag) to the host machine's IP.

**Remote-bus mode** (`local2 --web-only --ipaddress <host-ip>`): starts only the FastAPI web server, no proxy or agents. The web server connects to the remote bus and forwards queries there. This lets any browser on the network (iPad, iPhone, secondary Mac) use the host machine's agents and LLM without installing anything locally.
