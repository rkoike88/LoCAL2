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
- The compaction flow (summarizing and replacing session history)
- The `generator.status` publishing (identity + live state snapshot)

---

## Startup Sequence

1. Load `config/generator.yaml` and `config/system.yaml`.
2. Broadcast `schema.request` — all running tools re-announce their schemas.
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

## Tool Execution — _execute_tool()

Tool calls are synchronous within the generation loop. The pattern avoids a race between publishing the request and subscribing to the result:

1. Open a short-lived `ZmqSubscriber` for `tool.result.<name>` **before** publishing the request.
2. Publish `tool.request.<name>` with the tool arguments.
3. Poll with timeout (`tool_timeout` seconds, default 20s) for a response whose `correlation_id` matches.
4. Return the result string, or a `[tool timeout: ...]` error string on expiry.

The tool name is normalized via `_normalize_tool_name()` before dispatch — this catches Gemma hallucinating variant spellings (e.g. `"search_web"` → `"web_search"`).

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

## Compaction — _handle_compaction()

Triggered by `compaction.request`. Rejected if the generator is not IDLE.

1. Fetch session history and current token count from ConversationService.
2. Build a text summary of all user/assistant turns (tool turns omitted).
3. Call `ollama.chat()` non-streaming with a summarization system prompt (separate from the normal model call — no tools, no streaming).
4. Walk the history backwards to collect the last `compaction_tail_turns` verbatim user+assistant pairs.
5. Replace messages: `[{"role": "assistant", "content": "[SUMMARY] ..."}]` + tail pairs.
6. Estimate post-compaction tokens from character count (`total_chars // 4`).
7. Publish `compaction.result` with tokens_before, tokens_after, and the summary text.

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
| `model` | `gemma4:e4b` | Ollama model tag |
| `num_ctx` | `128000` | Context window size (always set explicitly) |
| `temperature` | `0.1` | Required for reliable tool calling |
| `max_tool_iterations` | `5` | Max tool call rounds per generation turn |
| `tool_timeout` | `20` | Seconds to wait for a tool result |
| `max_attachment_chars` | `32000` | Truncation limit per text attachment |
| `compaction_tail_turns` | `4` | Verbatim turn pairs kept after compaction |
| `system_prompt` | see config | Injected as `{"role": "system", ...}` at message array start |
