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
  │  GeneratorAgent │──────── response.generation ────────┤→ CriticAgent
  │                 │──────── answer.dialog ──────────────┤→ MemoryAgent (OBSERVE)
  │                 │──────── generation.thinking ────────┤→ UI
  │                 │──────── agent.transition ───────────┤→ UI
  │                 │──────── generator.status ───────────┤→ UI (GeneratorWindow)
  │                 │──────── tool.request.* ─────────────┤→ *Tools
  │                 │──────── compaction.result ──────────┤→ UI
  └─────────────────┘
  ┌─────────────────┐
  │   CriticAgent   │──────── critique.result ────────────┤→ UI, MemoryAgent
  │                 │──────── pairwise.result ────────────┤→ UI, MemoryAgent
  │                 │──────── agent.transition ───────────┤→ UI
  └─────────────────┘
  ┌─────────────────┐
  │   MemoryAgent   │──────── agent.transition ───────────┤→ UI
  └─────────────────┘
  ┌─────────────────┐
  │  RewardService  │──────── reward.event ───────────────┤→ (logged)
  └─────────────────┘
  ┌─────────────────┐
  │    *Tools (7)   │──────── tool.result.* ──────────────┤→ GeneratorAgent
  │                 │──────── tool.activity.* ────────────┤→ UI (ToolWindows)
  │                 │──────── tool.schema ────────────────┤→ GeneratorAgent, UI
  └─────────────────┘
  ┌─────────────────┐
  │       UI        │──────── query.received ─────────────┤→ GeneratorAgent
  │ (MainWindow)    │──────── schema.request ─────────────┤→ *Tools, GeneratorAgent
  │                 │──────── user.feedback ──────────────┤→ RewardService
  │                 │──────── compaction.request ─────────┤→ GeneratorAgent
  │                 │──────── config.reload ──────────────┤→ *Tools
  └─────────────────┘
```

## Subject Subscription Map

| Participant | Subscribes to |
|---|---|
| GeneratorAgent | `query.received`, `tool.schema`, `schema.request`, `compaction.request` |
| CriticAgent | `response.generation` |
| MemoryAgent | `response.generation`, `critique.result`, `pairwise.result` |
| RewardService | `user.feedback` |
| Each `*Tool` | `tool.request.<name>`, `schema.request` |
| UI (BusMonitor) | `query.received`, `response.generation`, `generation.thinking`, `answer.dialog`, `critique.result`, `pairwise.result`, `agent.transition`, `generator.status`, `tool.schema`, `tool.activity.*`, `compaction.result` |

## LAN Distribution

The proxy binds to `0.0.0.0`, so any participant on the LAN can connect by setting `proxy_host` in `config/bus.yaml` to the proxy machine's IP. A remote agent looks identical to a local one — pub/sub routing is transparent to participants.

## RespondentB

When RespondentB is started (same process, `respondent_id="B"`), it connects to the same bus and subscribes to `query.received`. Both A and B receive every query. B's `response.generation` is filtered out by the UI (only A's answer is displayed) but consumed by CriticAgent for pairwise comparison.

B uses a fresh `query_id` (different from A's) to avoid ChromaDB ID collisions. Its `correlation_id` points back to the original query so CriticAgent can match the A+B pair.
