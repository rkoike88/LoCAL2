# Tool Call Flow

This diagram shows the sequence of events during a single tool call within a generation turn. The tool call is synchronous from the generator's perspective — the streaming loop pauses while waiting for the result.

```mermaid
sequenceDiagram
    participant Gemma as Gemma (ollama)
    participant Gen as GeneratorAgent
    participant Disp as ToolDispatcher
    participant Bus as ZMQ Bus
    participant Tool as *Tool

    Gen->>Gemma: ollama.chat(messages, tools, stream=True)
    Gemma-->>Gen: stream thinking chunks
    Gen-->>Bus: publish generation.thinking (each chunk)
    Gemma-->>Gen: tool_calls=[{name, args}] in final chunk

    Note over Gen: transition GENERATING → DISPATCHING_TOOL
    Gen->>Disp: delegate tool call (name, args, correlation_id)
    Disp->>Bus: subscribe tool.result.<name>  (BEFORE publishing call)
    Disp->>Bus: publish tool.call.<name> {args, correlation_id}
    Bus-->>Tool: tool.call.<name>

    Tool->>Tool: execute (search, fetch, etc.)
    Tool->>Bus: publish tool.result.<name> {result, correlation_id}
    Tool->>Bus: publish tool.activity.<name>
    Bus-->>Disp: tool.result.<name>

    Disp->>Disp: verify correlation_id matches
    Disp->>Gen: return result string
    Note over Gen: transition DISPATCHING_TOOL → GENERATING
    Gen->>Gen: append tool result to messages array

    Gen->>Gemma: ollama.chat(messages with tool result, stream=True)
    Gemma-->>Gen: final text answer
    Note over Gen: transition GENERATING → PUBLISHING
```

## Race-Free Subscribe Pattern

`ToolDispatcher` opens the `ZmqSubscriber` for `tool.result.<name>` **before** publishing `tool.call.<name>`. This eliminates the subscribe/publish race: if the tool responds very quickly, the result is already in the ZMQ buffer when the dispatcher starts polling.

## Correlation ID Matching

Every `tool.call.*` envelope carries a `correlation_id` (the query's UUID). `ToolDispatcher` only accepts a `tool.result.*` response whose `correlation_id` matches. This prevents cross-query result contamination if a previous timed-out request arrives late.

## Timeout Behavior

If no matching result arrives within `tool_timeout` seconds (default 120s), `ToolDispatcher` returns `[tool timeout: '<name>' did not respond within 120s]` as the tool result. Gemma receives this error string as the tool output and can report it to the user.

## Activity Logging

Every tool publishes `tool.activity.<name>` on every request/result cycle. The UI subscribes and displays it in the corresponding ToolWindow's activity log. Activity events are fire-and-forget; no participant depends on them for correctness.
