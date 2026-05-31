"""Session-scoped conversation history for multi-turn Ollama chat calls.

History is stored as a messages array in Ollama chat format.
Callers must pass clean assistant text — thinking tokens must be stripped
before calling append_turn().
"""

from __future__ import annotations

from collections import OrderedDict

_MAX_SESSIONS = 50
_MAX_TURNS_PER_SESSION = 20


class ConversationService:
    def __init__(self) -> None:
        self._sessions: OrderedDict[str, list[dict]] = OrderedDict()

    def get_history(self, session_id: str | None) -> list[dict]:
        """Return a copy of the messages array for session_id, or [] if unknown."""
        if not session_id:
            return []
        turns = self._sessions.get(session_id)
        if not turns:
            return []
        self._sessions.move_to_end(session_id)
        return [dict(turn) for turn in turns]

    def append_turn(self, session_id: str | None, user: str, assistant: str) -> None:
        """Append a user+assistant exchange to session history.

        assistant must be the clean response text with thinking tokens already
        removed — never pass raw Ollama response content here.
        """
        if not session_id:
            return
        if session_id not in self._sessions:
            if len(self._sessions) >= _MAX_SESSIONS:
                self._sessions.popitem(last=False)
            self._sessions[session_id] = []

        turns = self._sessions[session_id]
        turns.append({"role": "user", "content": user})
        turns.append({"role": "assistant", "content": assistant})
        max_entries = _MAX_TURNS_PER_SESSION * 2
        if len(turns) > max_entries:
            self._sessions[session_id] = turns[-max_entries:]
        self._sessions.move_to_end(session_id)

    def clear(self, session_id: str | None) -> None:
        """Remove all history for a session."""
        if session_id:
            self._sessions.pop(session_id, None)
