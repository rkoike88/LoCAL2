"""Session-scoped conversation history for multi-turn Ollama chat calls.

History is stored as a messages array in Ollama chat format.
Callers must strip thinking tokens before storing — either pass clean text
to append_turn() or pass pre-cleaned dicts to append_messages().

Persistence: sessions are written to .conversation_history.json on every
append so history survives process restarts. The file is loaded at startup.

Storage format:
  {session_id: {messages: [...], started_at: float, last_active: float, title: str}}

Old format ({session_id: [messages]}) is auto-migrated on load.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections import OrderedDict
from pathlib import Path

logger = logging.getLogger(__name__)

_MAX_SESSIONS = 50
_MAX_TURNS_PER_SESSION = 20
_DEFAULT_PERSIST_PATH = ".conversation_history.json"


def _derive_title(messages: list[dict]) -> str:
    for msg in messages:
        if msg.get("role") == "user":
            text = msg.get("content") or ""
            return text[:60].strip()
    return "(no title)"


class ConversationService:
    """Session-scoped conversation history store.

    Stores messages in Ollama chat format (``{role, content}``). Persists to
    ``.conversation_history.json`` on every write so history survives process
    restarts.

    Caps: ``_MAX_SESSIONS`` (50) total sessions; ``_MAX_TURNS_PER_SESSION``
    (20 user+assistant pairs) per session. Oldest entries are evicted when
    caps are reached.
    """

    def __init__(self, persist_path: str | None = None) -> None:
        """Initialize and load any existing history from disk.

        Args:
            persist_path: Path to the JSON persistence file. Pass
                ``":memory:"`` to disable disk I/O entirely (used in tests).
                Defaults to ``.conversation_history.json`` in the working dir.
        """
        self._sessions: OrderedDict[str, dict] = OrderedDict()
        self._biscuits: dict[str, dict[str, dict]] = {}  # session_id → {query_id → biscuit}
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
            now = time.time()
            for sid, value in data.items():
                if isinstance(value, list):
                    # Migrate old format: {session_id: [messages]}
                    self._sessions[sid] = {
                        "messages": value,
                        "started_at": now,
                        "last_active": now,
                        "title": _derive_title(value),
                    }
                else:
                    self._sessions[sid] = value
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

    def _ensure_session(self, session_id: str) -> dict:
        if session_id not in self._sessions:
            if len(self._sessions) >= _MAX_SESSIONS:
                self._sessions.popitem(last=False)
            self._sessions[session_id] = {
                "messages": [],
                "started_at": time.time(),
                "last_active": time.time(),
                "title": "",
                "token_count": 0,
            }
        return self._sessions[session_id]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_history(self, session_id: str | None) -> list[dict]:
        """Return a copy of the messages array for session_id, or [] if unknown."""
        if not session_id:
            return []
        entry = self._sessions.get(session_id)
        if not entry:
            return []
        self._sessions.move_to_end(session_id)
        return [dict(msg) for msg in entry["messages"]]

    def append_turn(self, session_id: str | None, user: str, assistant: str) -> None:
        """Append a user+assistant exchange to session history."""
        if not session_id:
            return
        entry = self._ensure_session(session_id)
        msgs = entry["messages"]
        msgs.append({"role": "user", "content": user})
        msgs.append({"role": "assistant", "content": assistant})
        max_entries = _MAX_TURNS_PER_SESSION * 2
        if len(msgs) > max_entries:
            entry["messages"] = msgs[-max_entries:]
        if not entry["title"]:
            entry["title"] = user[:60].strip()
        entry["last_active"] = time.time()
        self._sessions.move_to_end(session_id)
        self._save()

    def append_messages(self, session_id: str | None, messages: list[dict]) -> None:
        """Append a pre-cleaned list of messages (user/assistant/tool) to session history."""
        if not session_id or not messages:
            return
        entry = self._ensure_session(session_id)
        msgs = entry["messages"]
        msgs.extend(messages)
        max_entries = _MAX_TURNS_PER_SESSION * 2
        if len(msgs) > max_entries:
            entry["messages"] = msgs[-max_entries:]
        if not entry["title"]:
            entry["title"] = _derive_title(entry["messages"])
        entry["last_active"] = time.time()
        self._sessions.move_to_end(session_id)
        self._save()

    def list_sessions(self) -> list[dict]:
        """Return session metadata sorted by last_active descending.

        Each item: {session_id, title, message_count, started_at, last_active}
        """
        result = []
        for sid, entry in self._sessions.items():
            result.append({
                "session_id": sid,
                "title": entry.get("title") or "(no title)",
                "message_count": len(entry.get("messages", [])),
                "started_at": entry.get("started_at", 0.0),
                "last_active": entry.get("last_active", 0.0),
            })
        result.sort(key=lambda x: x["last_active"], reverse=True)
        return result

    def delete_session(self, session_id: str) -> None:
        """Remove a session entirely."""
        self._sessions.pop(session_id, None)
        self._save()

    def set_token_count(self, session_id: str | None, count: int) -> None:
        """Store the prompt_eval_count from the last Ollama generation turn."""
        if not session_id:
            return
        entry = self._sessions.get(session_id)
        if entry is not None:
            entry["token_count"] = count
            self._save()

    def get_token_count(self, session_id: str | None) -> int:
        """Return the last stored token count for a session, or 0 if unknown."""
        if not session_id:
            return 0
        entry = self._sessions.get(session_id)
        if entry is None:
            return 0
        return entry.get("token_count", 0)

    def replace_messages(self, session_id: str | None, messages: list[dict]) -> None:
        """Atomically replace the message list for a session (used by compaction)."""
        if not session_id:
            return
        entry = self._ensure_session(session_id)
        entry["messages"] = list(messages)
        entry["last_active"] = time.time()
        self._sessions.move_to_end(session_id)
        self._save()

    def clear(self, session_id: str | None) -> None:
        """Remove all history for a session."""
        if session_id:
            self._sessions.pop(session_id, None)
            self._save()

    # ------------------------------------------------------------------
    # Context biscuit — per-turn provenance (capsules, persona)
    # ------------------------------------------------------------------

    def write_context_biscuit(self, query_id: str, biscuit: dict, session_id: str = "") -> None:
        """Store context metadata for a query turn.

        In-memory only (ephemeral across restarts) — used for UI transparency.

        Args:
            query_id: The query's ID, matches ResponseGeneration.query_id.
            biscuit: Dict with keys ``capsules`` (list) and ``persona`` (str|None).
            session_id: Session the turn belongs to. Falls back to ``""`` bucket.
        """
        self._biscuits.setdefault(session_id, {})[query_id] = biscuit

    def get_context_biscuit(self, query_id: str, session_id: str = "") -> dict | None:
        """Return the context biscuit for query_id within session_id, or None."""
        return self._biscuits.get(session_id, {}).get(query_id)

    def get_context_log(self, session_id: str) -> dict[str, dict]:
        """Return all context biscuits for a session, keyed by query_id."""
        return dict(self._biscuits.get(session_id, {}))
