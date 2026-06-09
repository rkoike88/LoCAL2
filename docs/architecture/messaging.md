# Messaging — Subjects and Envelope Format

LoCAL2 participants communicate exclusively through a ZMQ XPUB/XSUB proxy. Every message is a `MessageEnvelope` serialized to JSON.

---

## 1. MessageEnvelope

```python
@dataclass
class MessageEnvelope:
    message_id:     str            # UUID, generated on create()
    message_type:   str            # semantic label (e.g. "query", "response", "tool_request")
    subject:        str            # ZMQ subject prefix — the routing key
    sender_id:      str            # participant identity (e.g. "generator", "ui")
    payload:        dict           # event-specific content
    correlation_id: str | None     # ties related events together (query lifecycle)
    recipient_id:   str | None     # targeted delivery (unused in most flows)
    metadata:       dict           # session_id and other context
    timestamp_utc:  str            # ISO 8601 UTC
```

`MessageEnvelope.create()` is the factory — generates `message_id` and `timestamp_utc` automatically.

`metadata["session_id"]` is the per-conversation session ID, injected by the generator and UI on every envelope in a session's lifecycle.

---

## 2. Bus Topology

The proxy binds to `0.0.0.0` on ports 5570 (frontend) and 5571 (backend). Participants connect to the backend at `127.0.0.1:5571` by default. Remote agents on the same LAN connect by changing `proxy_host` in `config/bus.yaml`.

| Config key | Default | Description |
|---|---|---|
| `proxy_host` | `127.0.0.1` | Host to connect to (UI and agents) |
| `ports.proxy_frontend` | `5570` | External producers connect here |
| `ports.proxy_backend` | `5571` | All participants subscribe/publish here |

---

## 3. Subject Reference

### Core conversation flow

| Subject constant | String | Publisher | Subscribers |
|---|---|---|---|
| `QUERY_RECEIVED` | `query.received` | FastAPI Gateway | GeneratorAgent |
| `GENERATION_THINKING` | `generation.thinking` | GeneratorAgent | FastAPI Gateway |
| `RESPONSE_GENERATION` | `response.generation` | GeneratorAgent | FastAPI Gateway, CriticAgent, MemoryAgent |
| `ANSWER_DIALOG` | `answer.dialog` | GeneratorAgent | MemoryAgent |

### Tool schema discovery

| Subject constant | String | Publisher | Subscribers |
|---|---|---|---|
| `TOOL_SCHEMA` | `tool.schema` | All tools (on startup + re-announce) | GeneratorAgent, MonitorApp |
| `TOOL_SCHEMA_REQUEST` | `schema.request` | GeneratorAgent, MonitorApp | All tools |

### Tool request / result pairs

| Tool | Request subject | Result subject |
|---|---|---|
| `search_memory` | `tool.request.search_memory` | `tool.result.search_memory` |
| `web_search` | `tool.request.web_search` | `tool.result.web_search` |
| `web_fetch` | `tool.request.web_fetch` | `tool.result.web_fetch` |
| `get_datetime` | `tool.request.get_datetime` | `tool.result.get_datetime` |
| `get_location` | `tool.request.get_location` | `tool.result.get_location` |
| `search_papers` | `tool.request.search_papers` | `tool.result.search_papers` |
| `search_library` | `tool.request.search_library` | `tool.result.search_library` |

The `function.name` in the tool's JSON schema must match the subject suffix exactly. Mismatches cause silent tool timeouts.

### Tool activity

Each tool publishes one `tool.activity.<name>` envelope per request/result cycle. The UI subscribes and displays it in the corresponding ToolWindow.

| Subject constant | String |
|---|---|
| `TOOL_ACTIVITY_SEARCH_MEMORY` | `tool.activity.search_memory` |
| `TOOL_ACTIVITY_WEB_SEARCH` | `tool.activity.web_search` |
| `TOOL_ACTIVITY_WEB_FETCH` | `tool.activity.web_fetch` |
| `TOOL_ACTIVITY_GET_DATETIME` | `tool.activity.get_datetime` |
| `TOOL_ACTIVITY_GET_LOCATION` | `tool.activity.get_location` |
| `TOOL_ACTIVITY_SEARCH_PAPERS` | `tool.activity.search_papers` |
| `TOOL_ACTIVITY_SEARCH_DOCUMENTS` | `tool.activity.search_library` |

### Feedback and grading

| Subject constant | String | Publisher | Subscribers |
|---|---|---|---|
| `CRITIQUE` | `critique.result` | CriticAgent | FastAPI Gateway, MemoryAgent |
| `USER_FEEDBACK` | `user.feedback` | FastAPI Gateway | RewardService |
| `REWARD_EVENT` | `reward.event` | RewardService | (logged; no current subscriber) |

### Agent observability

| Subject constant | String | Publisher | Subscribers |
|---|---|---|---|
| `AGENT_TRANSITION` | `agent.transition` | GeneratorAgent, CriticAgent, MemoryAgent | MonitorApp |
| `GENERATOR_STATUS` | `generator.status` | GeneratorAgent | MonitorApp (GeneratorWindow) |

### Compaction

| Subject constant | String | Publisher | Subscribers |
|---|---|---|---|
| `COMPACTION_REQUEST` | `compaction.request` | FastAPI Gateway | GeneratorAgent |
| `COMPACTION_RESULT` | `compaction.result` | GeneratorAgent | FastAPI Gateway |

### Config hot-reload

| Subject constant | String | Publisher | Subscribers |
|---|---|---|---|
| `CONFIG_RELOAD` | `config.reload` | Qt settings panels (ToolWindow / GeneratorWindow) | All tools, GeneratorAgent |

---

## 4. Key Payload Fields

### query.received
```json
{
  "query":       "What is the capital of France?",
  "session_id":  "uuid",
  "query_id":    "uuid",
  "attachments": []
}
```

### response.generation
```json
{
  "query":         "What is the capital of France?",
  "answer":        "Paris.",
  "thinking":      "France → Paris",
  "tool_calls":    [{"tool": "web_search", "args": {...}, "result": "..."}],
  "session_id":    "uuid",
  "query_id":      "uuid",
  "prompt_tokens": 4710
}
```

### critique.result
```json
{
  "score":    4,
  "feedback": "Accurate and concise. Minor: could add context.",
  "query_id": "uuid",
  "query":    "What is the capital of France?"
}
```

### generator.status
```json
{
  "instance_id": "local2-macbook",
  "model":       "gemma4:e4b",
  "temperature":   0.1,
  "num_ctx":       128000,
  "state":         "idle",
  "token_count":   47200,
  "tool_names":    ["search_memory", "web_search", "web_fetch", "get_datetime",
                    "get_location", "search_papers", "search_library"],
  "system_prompt": "You are a helpful assistant..."
}
```

### agent.transition
```json
{
  "agent":  "generator",
  "from":   "idle",
  "action": "receive",
  "to":     "receiving"
}
```

### compaction.result
```json
{
  "session_id":    "uuid",
  "tokens_before": 82400,
  "tokens_after":  14200,
  "summary":       "User asked about..."
}
```
