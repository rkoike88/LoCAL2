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
  │  GeneratorAgent │──────── response.generation ────────┤→ FastAPI Gateway, CriticAgent, MemoryAgent
  │                 │──────── answer.dialog ──────────────┤→ MemoryAgent
  │                 │──────── generation.thinking ────────┤→ FastAPI Gateway
  │                 │──────── agent.transition ───────────┤→ MonitorApp
  │                 │──────── generator.status ───────────┤→ MonitorApp (GeneratorWindow)
  │                 │──────── tool.request.* ─────────────┤→ *Tools
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
  │    *Tools (7)   │──────── tool.result.* ──────────────┤→ GeneratorAgent
  │                 │──────── tool.activity.* ────────────┤→ MonitorApp (ToolWindows)
  │                 │──────── tool.schema ────────────────┤→ GeneratorAgent, MonitorApp
  └─────────────────┘
  ┌─────────────────┐
  │  FastAPI        │──────── query.received ─────────────┤→ GeneratorAgent
  │  Gateway        │──────── schema.request ─────────────┤→ *Tools, GeneratorAgent
  │                 │──────── user.feedback ──────────────┤→ RewardService
  │                 │──────── compaction.request ─────────┤→ GeneratorAgent
  └─────────────────┘
  ┌─────────────────┐
  │  MonitorApp     │──────── schema.request ─────────────┤→ *Tools (once at startup)
  │  (Qt panels,    │
  │  --panels only) │
  └─────────────────┘
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
| GeneratorAgent | `query.received`, `tool.schema`, `schema.request`, `compaction.request`, `tool.result.*` |
| CriticAgent | `response.generation` |
| MemoryAgent | `response.generation`, `critique.result` |
| RewardService | `user.feedback` |
| Each `*Tool` | `tool.request.<name>`, `schema.request` |
| FastAPI Gateway | `generation.thinking`, `response.generation`, `critique.result`, `answer.dialog`, `tool.request.*`, `tool.result.*`, `compaction.result`, `query.received` |
| MonitorApp (Qt) | `tool.schema`, `generator.status`, `agent.transition`, `critique.result`, `tool.activity.*` |

## LAN Distribution

The proxy binds to `0.0.0.0`, so any participant on the LAN can connect by setting `proxy_host` in `config/bus.yaml` to the proxy machine's IP. A remote agent looks identical to a local one — pub/sub routing is transparent to participants.
