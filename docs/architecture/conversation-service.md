# Conversation Service

`ConversationService` (`src/local/services/conversation_service.py`) manages per-session conversation history. It is owned by GeneratorAgent and shared with the UI (read-only for session navigation).

---

## Session Model

Each session is identified by a UUID (`session_id`). Sessions are created implicitly on first write and persisted to disk as JSON. The default persistence path is `.conversation_history.json` (gitignored).

Session entry structure:
```json
{
  "messages":     [...],
  "started_at":   "2026-06-07T09:14:22",
  "last_active":  "2026-06-07T09:41:55",
  "title":        "What is the capital of France?",
  "token_count":  47200
}
```

`title` is auto-derived from the first user message in the session (truncated to ~60 characters). It is used in ConversationsWindow for display.

---

## Key Methods

| Method | Description |
|---|---|
| `get_history(session_id)` | Returns the full messages array for a session |
| `append_messages(session_id, messages)` | Appends new messages to the session history |
| `append_turn(session_id, query, answer)` | Convenience: appends user + assistant pair |
| `set_token_count(session_id, count)` | Updates the token count (called after each generation) |
| `get_token_count(session_id)` | Returns the stored token count (0 if not set) |
| `replace_messages(session_id, messages)` | Atomic replacement of history (used by compaction) |
| `list_sessions()` | Returns all sessions sorted by `last_active` descending |
| `delete_session(session_id)` | Removes a session from the store |

`ConversationService(persist_path=":memory:")` disables disk persistence — used in tests.

---

## Message Format

Messages stored in history follow the Ollama chat API format:

```json
[
  {"role": "user",      "content": "What is the capital of France?"},
  {"role": "assistant", "content": "Paris."},
  {"role": "tool",      "content": "web_search result...", "name": "web_search"},
  {"role": "assistant", "content": "Based on the search, Paris is..."}
]
```

**Thinking tokens are stripped** before saving (`_clean_for_history()` in GeneratorAgent removes the `thinking` field). Empty `tool_calls` arrays are also stripped. Ollama ToolCall SDK objects are serialized to plain dicts for JSON compatibility.

---

## Token Tracking

After each generation turn, GeneratorAgent calls `set_token_count(session_id, prompt_eval_count)`. The `prompt_eval_count` value comes from the final Ollama streaming chunk — it is the exact token count of the prompt sent to the model, as counted by Ollama itself (no tokenizer library needed).

The UI reads this via the `response.generation` payload's `prompt_tokens` field and updates the ContextGauge.

---

## Compaction

When the user requests compaction:

1. GeneratorAgent fetches the full history and current `token_count` from ConversationService.
2. After summarization, calls `replace_messages(session_id, new_messages)` atomically.
3. Calls `set_token_count(session_id, estimated_tokens)` with a character-count heuristic (`total_chars // 4`).

The ContextGauge updates immediately to reflect the estimated post-compaction count.

---

## Session Navigator

ConversationsWindow in the UI shows all sessions via `list_sessions()`. The user can:
- **Rejoin** a session: sets `_session_id` in MainWindow, replays history into the log as rendered Q+A cards.
- **Delete** a session: calls `delete_session(session_id)` and refreshes the list.

Rejoining replays the history visually (rendered markdown in QTextBrowser widgets) but does not re-publish any bus events. The generator resumes the session on the next query as if it had never left.
