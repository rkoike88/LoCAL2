"""ZMQ-to-WebSocket event translation for the LoCAL2 web UI.

Translates raw bus MessageEnvelopes into typed WebSocket event dicts that
the React frontend consumes. Also defines the subject subscription list
for the chat WebSocket endpoint.
"""

from __future__ import annotations

from local.protocol.envelope import MessageEnvelope
from local.protocol.subjects import (
    ANSWER_DIALOG,
    CRITIQUE,
    GENERATION_THINKING,
    LIBRARY_INGEST_COMPLETE,
    LIBRARY_INGEST_STARTED,
    QUERY_RECEIVED,
    RESPONSE_GENERATION,
    TOOL_CALL_CONSULT_LIBRARIAN,
    TOOL_TRANSITION,
    TOOL_CALL_GET_DATETIME,
    TOOL_CALL_GET_LOCATION,
    TOOL_CALL_SEARCH_DOCUMENTS,
    TOOL_CALL_SEARCH_MEMORY,
    TOOL_CALL_SEARCH_PAPERS,
    TOOL_CALL_WEB_FETCH,
    TOOL_CALL_WEB_SEARCH,
    TOOL_RESULT_CONSULT_LIBRARIAN,
    TOOL_RESULT_GET_DATETIME,
    TOOL_RESULT_GET_LOCATION,
    TOOL_RESULT_SEARCH_DOCUMENTS,
    TOOL_RESULT_SEARCH_MEMORY,
    TOOL_RESULT_SEARCH_PAPERS,
    TOOL_RESULT_WEB_FETCH,
    TOOL_RESULT_WEB_SEARCH,
)

# All subjects the chat WebSocket endpoint subscribes to.
CHAT_OBSERVE = [
    QUERY_RECEIVED,
    GENERATION_THINKING,
    TOOL_CALL_WEB_SEARCH,
    TOOL_RESULT_WEB_SEARCH,
    TOOL_CALL_WEB_FETCH,
    TOOL_RESULT_WEB_FETCH,
    TOOL_CALL_SEARCH_MEMORY,
    TOOL_RESULT_SEARCH_MEMORY,
    TOOL_CALL_GET_DATETIME,
    TOOL_RESULT_GET_DATETIME,
    TOOL_CALL_GET_LOCATION,
    TOOL_RESULT_GET_LOCATION,
    TOOL_CALL_SEARCH_PAPERS,
    TOOL_RESULT_SEARCH_PAPERS,
    TOOL_CALL_SEARCH_DOCUMENTS,
    TOOL_RESULT_SEARCH_DOCUMENTS,
    TOOL_CALL_CONSULT_LIBRARIAN,
    TOOL_RESULT_CONSULT_LIBRARIAN,
    LIBRARY_INGEST_COMPLETE,
    TOOL_TRANSITION,
    RESPONSE_GENERATION,
    ANSWER_DIALOG,
    CRITIQUE,
]

_TOOL_CALL_PREFIX = "tool.call."
_TOOL_RESULT_PREFIX = "tool.result."


def translate(envelope: MessageEnvelope) -> dict | None:
    """Translate a bus envelope into a typed WebSocket event dict.

    Returns None for subjects the frontend does not consume (e.g.
    QUERY_RECEIVED, ANSWER_DIALOG).

    Args:
        envelope: A bus envelope received by the session subscriber.

    Returns:
        A JSON-serialisable dict with a ``type`` field, or ``None`` to skip.
    """
    subject = envelope.subject
    payload = envelope.payload
    query_id = envelope.correlation_id

    if subject == GENERATION_THINKING:
        return {
            "type": "thinking_chunk",
            "chunk": payload.get("chunk", ""),
            "query_id": query_id,
        }

    if subject.startswith(_TOOL_CALL_PREFIX):
        tool_name = subject[len(_TOOL_CALL_PREFIX):]
        return {
            "type": "tool_start",
            "tool": tool_name,
            "args": payload.get("args", {}),
            "ts": envelope.timestamp_utc,
            "query_id": query_id,
        }

    if subject.startswith(_TOOL_RESULT_PREFIX):
        tool_name = subject[len(_TOOL_RESULT_PREFIX):]
        return {
            "type": "tool_result",
            "tool": tool_name,
            "result": payload.get("result", ""),
            "sources": payload.get("sources", []),
            "ts": envelope.timestamp_utc,
            "query_id": query_id,
        }

    if subject == RESPONSE_GENERATION:
        return {
            "type": "response",
            "answer": payload.get("answer", ""),
            "thinking": payload.get("thinking", ""),
            "tool_calls": payload.get("tool_calls", []),
            "session_id": payload.get("session_id", ""),
            "query_id": payload.get("query_id") or query_id,
            "prompt_tokens": payload.get("prompt_tokens", 0),
            "model": payload.get("model", ""),
        }

    if subject == CRITIQUE:
        return {
            "type": "critique",
            "score": payload.get("score"),
            "feedback": payload.get("feedback", ""),
            "query_id": payload.get("query_id") or query_id,
        }

    if subject == LIBRARY_INGEST_STARTED:
        return {
            "type": "library_ingest_started",
            "filename": payload.get("filename", ""),
            "collection": payload.get("collection", ""),
        }

    if subject == TOOL_TRANSITION:
        return {
            "type": "tool_transition",
            "tool": payload.get("tool", ""),
            "from_state": payload.get("from_state", ""),
            "action": payload.get("action", ""),
            "to": payload.get("to", ""),
            "error": payload.get("error", ""),
            "query_id": query_id,
        }

    if subject == LIBRARY_INGEST_COMPLETE:
        error = payload.get("error", "")
        return {
            "type": "library_ingested",
            "filename": payload.get("filename", ""),
            "collection": payload.get("collection", ""),
            "chunks": payload.get("chunk_count", 0),
            "error": error,
        }

    return None
