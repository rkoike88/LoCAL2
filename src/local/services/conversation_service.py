"""Session-scoped conversation history for multi-turn Ollama chat calls.

History is stored as a messages array in Ollama chat format.
Callers must strip thinking tokens before storing — either pass clean text
to append_turn() or pass pre-cleaned dicts to append_messages().

Persistence: sessions are written to .conversation_history.json on every
append so history survives process restarts. The file is loaded at startup.
"""

from __future__ import annotations

import json
import logging
import os
from collections import OrderedDict
from pathlib import Path

logger = logging.getLogger(__name__)

_MAX_SESSIONS = 50
_MAX_TURNS_PER_SESSION = 20
_DEFAULT_PERSIST_PATH = ".conversation_history.json"


class ConversationService:
    def __init__(self, persist_path: str | None = None) -> None:
        self._sessions: OrderedDict[str, list[dict]] = OrderedDict()
        # ":memory:" sentinel disables disk I/O (useful in tests)
        raw = persist_path or _DEFAULT_PERSIST_PATH
        self._persist_path: Path | None = None if raw == ":memory:" else Path(raw)
        self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        if self._persist_path is None or not self._persist_path.exists():
            return
        try:
            with open(self._persist_path) as f:
                data = json.load(f)
            for sid, msgs in data.items():
                self._sessions[sid] = msgs
            # Enforce session cap on load
            while len(self._sessions) > _MAX_SESSIONS:
                self._sessions.popitem(last=False)
            logger.debug("ConversationService: loaded %d sessions from %s", len(self._sessions), self._persist_path)
        except Exception as exc:
            logger.warning("ConversationService: could not load history: %s", exc)

    def _save(self) -> None:
        if self._persist_path is None:
            return
        try:
            tmp = str(self._persist_path) + ".tmp"
            with open(tmp, "w") as f:
                json.dump(dict(self._sessions), f)
            os.replace(tmp, self._persist_path)
        except Exception as exc:
            logger.warning("ConversationService: could not save history: %s", exc)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

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
        """Append a user+assistant exchange to session history."""
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
        self._save()

    def append_messages(self, session_id: str | None, messages: list[dict]) -> None:
        """Append a pre-cleaned list of messages (user/assistant/tool) to session history."""
        if not session_id or not messages:
            return
        if session_id not in self._sessions:
            if len(self._sessions) >= _MAX_SESSIONS:
                self._sessions.popitem(last=False)
            self._sessions[session_id] = []

        turns = self._sessions[session_id]
        turns.extend(messages)
        max_entries = _MAX_TURNS_PER_SESSION * 2
        if len(turns) > max_entries:
            self._sessions[session_id] = turns[-max_entries:]
        self._sessions.move_to_end(session_id)
        self._save()

    def clear(self, session_id: str | None) -> None:
        """Remove all history for a session."""
        if session_id:
            self._sessions.pop(session_id, None)
            self._save()
