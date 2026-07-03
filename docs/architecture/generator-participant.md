# Generator Participant

`GeneratorAgent` (`src/local/agents/generator_agent.py`) is the core LLM participant. It subscribes to `query.received`, maintains per-session conversation history, orchestrates tool calls natively via Gemma's tool-calling capability, and publishes the final answer on `response.generation`.

For envelope format and subject naming, see [messaging.md](messaging.md).
For state machine diagram, see [../diagrams/generator-state-machine.md](../diagrams/generator-state-machine.md).

---

## Role

The generator is the only participant that calls an LLM on the critical path. It owns:
- The Ollama chat API calls (streaming, with tool support)
- The live tool schema registry (populated by `tool.schema` announcements)
- The conversation history for each session (via ConversationService)
- Multi-model routing: selects `models.vision` for image-bearing queries, `models.default` otherwise
- The `generator.status` publishing (identity + live state snapshot)

**Compaction** is handled by `ModelService` — a separate bus participant that watches `response.generation` token counts and executes compaction via `compaction.request`. The generator is not involved.

**Tool dispatch** is handled by `ToolDispatcher` — a synchronous bus bridge that the generator delegates to. The generator publishes the tool call internally; `ToolDispatcher` routes it on the bus and returns the result.

---

## Startup Sequence

1. Load `config/generator.yaml` and `config/system.yaml`.
2. Broadcast `tool.schema.request` — all running tools re-announce their schemas.
3. Sleep 0.5s to let tool schemas queue up before the first query arrives.
4. Publish initial `generator.status` (token_count=0, all tool names registered so far).
5. Enter the main receive loop.

---

## Query Handling — _handle_query()

1. Extract `query`, `session_id`, `query_id`, `attachments` from envelope.
2. Transition: `IDLE → RECEIVING` (publishes `agent.transition` + `generator.status`).
4. Build messages array: system prompt + conversation history + new user message (with any attachments).
5. Transition: `RECEIVING → GENERATING`.
6. Call `_generate()` — streaming with tools.
7. On error: transition to `ERROR`, publish error `response.generation`, transition to `IDLE`.
8. On success: append new messages to history and store token count.
9. Transition: `GENERATING → PUBLISHING`.
10. Publish `response.generation` and `answer.dialog`.
11. Transition: `PUBLISHING → IDLE`.

---

## Generation Loop — _generate()

```
for _ in range(max_tool_iterations):
    stream ollama.chat(model, messages, tools, think=True, stream=True)
        → accumulate thinking chunks → publish generation.thinking
        → accumulate content
        → capture tool_calls from chunk
    capture prompt_eval_count from final chunk (exact token count)
    append assistant message to messages array

    if no tool_calls:
        break  ← done

    transition GENERATING → DISPATCHING_TOOL
    for each tool_call:
        _execute_tool(name, args, correlation_id)
        append tool result to messages array
    transition DISPATCHING_TOOL → GENERATING

return (answer, thinking, tool_call_log, prompt_tokens)
```

**Thinking token handling:** Gemma 4 streams `chunk.message.thinking` alongside `chunk.message.content`. Thinking chunks are published to `generation.thinking` in real time. They are **not** stored in conversation history — `_clean_for_history()` strips the `thinking` field before saving.

---

## Tool Execution — ToolDispatcher

Tool calls are delegated to `ToolDispatcher`, a synchronous bus participant that handles the ZMQ round-trip on behalf of the generator:

1. `ToolDispatcher` opens a short-lived `ZmqSubscriber` for `tool.result.<name>` **before** publishing the call.
2. Publishes `tool.call.<name>` with the tool arguments.
3. Polls with timeout (`tool_timeout` seconds, default 120s) for a response whose `correlation_id` matches.
4. Returns the result string, or a `[tool timeout: ...]` error string on expiry.

The tool name is normalized before dispatch to catch Gemma hallucinating variant spellings (e.g. `"search_web"` → `"web_search"`).

---

## Conversation History

After each successful turn, the new messages (user message + assistant turns + tool results) are appended to ConversationService. On the next turn, `_build_messages()` reconstructs the full messages array:

```
[system]          ← from config/generator.yaml (if set)
[user]            ← turn 1 query
[assistant]       ← turn 1 answer (thinking stripped, tool_calls serialized)
[tool]            ← tool result (if any)
[assistant]       ← tool follow-up answer
...
[user]            ← current query + any attachments
```

Attachments (images, PDFs, text files) are prepended to the user message content; images go in the `images` field for Ollama's vision support. Attachments are not stored in history.

---

## Compaction

Compaction is handled by `ModelService` (a separate bus participant, `src/local/services/model_service.py`). The generator is not involved.

`ModelService` watches `response.generation` token counts and auto-publishes `compaction.request` when the threshold in `config/compaction.yaml` is crossed. On `compaction.request`, it reads session history from `ConversationService`, summarizes via a non-streaming Ollama call, replaces the messages array with summary + tail turns, and publishes `compaction.result`.

---

## generator.status Publishing

`_publish_status()` is called after every `_do_transition()`, after every `_register_tool_schema()`, and once at startup. The payload is a full snapshot: instance_id, model, temperature, num_ctx, current state, token_count, tool_names, system_prompt.

`instance_id` is read from `config/system.yaml`; falls back to `socket.gethostname()` if absent.

GeneratorWindow in the UI subscribes to `generator.status` and updates all panels on every snapshot.

---

## State Machine

See [../diagrams/generator-state-machine.md](../diagrams/generator-state-machine.md) for the Mermaid diagram.

States: `IDLE`, `RECEIVING`, `GENERATING`, `DISPATCHING_TOOL`, `WAITING_FOR_TOOL`, `PUBLISHING`, `ERROR`

Every state transition calls `_do_transition(action)` which:
1. Executes the transition in the state machine.
2. Publishes `agent.transition` with `{agent, from, action, to}`.
3. Publishes `generator.status` snapshot.

---

## Key Config Knobs

All settings in `config/generator.yaml`.

| Key | Default | Description |
|---|---|---|
| `models.default` | `gemma4:e2b` | Model for standard text queries |
| `models.vision` | `gemma4:e4b` | Auto-selected for queries with image attachments |
| `models.quality` | `gemma4:31b-mlx` | High-capability model for quality-requested responses |
| `num_ctx` | `128000` | Context window size (always set explicitly) |
| `temperature` | `0.1` | Required for reliable tool calling |
| `max_tool_iterations` | `5` | Max tool call rounds per generation turn |
| `tool_timeout` | `120` | Seconds to wait for a tool result |
| `max_attachment_chars` | `32000` | Truncation limit per text attachment |
| `system_prompt` | see config | Injected as `{"role": "system", ...}` at message array start |

Compaction settings are in `config/compaction.yaml` (handled by `ModelService`).
