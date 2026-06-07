"""Unit tests for ConversationService — no disk I/O (all :memory:)."""

import time
import pytest

from local.services.conversation_service import ConversationService


def _svc() -> ConversationService:
    return ConversationService(persist_path=":memory:")


# ---------------------------------------------------------------------------
# Basic history API
# ---------------------------------------------------------------------------

class TestGetHistory:
    def test_unknown_session_returns_empty(self):
        svc = _svc()
        assert svc.get_history("no-such-session") == []

    def test_none_session_returns_empty(self):
        svc = _svc()
        assert svc.get_history(None) == []

    def test_append_turn_and_retrieve(self):
        svc = _svc()
        svc.append_turn("s1", "hello", "hi there")
        hist = svc.get_history("s1")
        assert hist == [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
        ]

    def test_append_messages_and_retrieve(self):
        svc = _svc()
        msgs = [{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}]
        svc.append_messages("s1", msgs)
        assert svc.get_history("s1") == msgs

    def test_returns_copy_not_reference(self):
        svc = _svc()
        svc.append_turn("s1", "q", "a")
        hist = svc.get_history("s1")
        hist.clear()
        assert len(svc.get_history("s1")) == 2


# ---------------------------------------------------------------------------
# list_sessions
# ---------------------------------------------------------------------------

class TestListSessions:
    def test_empty_returns_empty(self):
        svc = _svc()
        assert svc.list_sessions() == []

    def test_single_session_listed(self):
        svc = _svc()
        svc.append_turn("s1", "what is 2+2?", "4")
        sessions = svc.list_sessions()
        assert len(sessions) == 1
        s = sessions[0]
        assert s["session_id"] == "s1"
        assert s["title"] == "what is 2+2?"
        assert s["message_count"] == 2

    def test_sorted_newest_first(self):
        svc = _svc()
        svc.append_turn("old", "first question", "answer")
        time.sleep(0.01)
        svc.append_turn("new", "second question", "answer")
        sessions = svc.list_sessions()
        assert sessions[0]["session_id"] == "new"
        assert sessions[1]["session_id"] == "old"

    def test_title_from_first_user_message(self):
        svc = _svc()
        long_q = "a" * 80
        svc.append_turn("s1", long_q, "ok")
        s = svc.list_sessions()[0]
        assert s["title"] == "a" * 60

    def test_metadata_fields_present(self):
        svc = _svc()
        svc.append_turn("s1", "hi", "hello")
        s = svc.list_sessions()[0]
        assert "started_at" in s
        assert "last_active" in s
        assert s["started_at"] > 0
        assert s["last_active"] >= s["started_at"]

    def test_message_count_includes_all_roles(self):
        svc = _svc()
        msgs = [
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "thinking..."},
            {"role": "tool", "content": "result"},
            {"role": "assistant", "content": "final"},
        ]
        svc.append_messages("s1", msgs)
        assert svc.list_sessions()[0]["message_count"] == 4


# ---------------------------------------------------------------------------
# delete_session
# ---------------------------------------------------------------------------

class TestDeleteSession:
    def test_delete_removes_session(self):
        svc = _svc()
        svc.append_turn("s1", "hi", "hello")
        svc.delete_session("s1")
        assert svc.get_history("s1") == []
        assert svc.list_sessions() == []

    def test_delete_nonexistent_is_noop(self):
        svc = _svc()
        svc.delete_session("no-such")  # must not raise

    def test_delete_only_removes_target(self):
        svc = _svc()
        svc.append_turn("s1", "q1", "a1")
        svc.append_turn("s2", "q2", "a2")
        svc.delete_session("s1")
        assert svc.get_history("s1") == []
        assert len(svc.get_history("s2")) == 2


# ---------------------------------------------------------------------------
# Schema migration (old list format)
# ---------------------------------------------------------------------------

class TestSchemaMigration:
    def test_old_format_migrated_on_load(self, tmp_path):
        import json
        hist_file = tmp_path / "history.json"
        old_data = {
            "session-abc": [
                {"role": "user", "content": "what is Python?"},
                {"role": "assistant", "content": "A programming language."},
            ]
        }
        hist_file.write_text(json.dumps(old_data))

        svc = ConversationService(persist_path=str(hist_file))
        msgs = svc.get_history("session-abc")
        assert len(msgs) == 2
        assert msgs[0] == {"role": "user", "content": "what is Python?"}

    def test_old_format_gets_title(self, tmp_path):
        import json
        hist_file = tmp_path / "history.json"
        old_data = {
            "s1": [{"role": "user", "content": "explain gravity"}, {"role": "assistant", "content": "ok"}]
        }
        hist_file.write_text(json.dumps(old_data))

        svc = ConversationService(persist_path=str(hist_file))
        sessions = svc.list_sessions()
        assert sessions[0]["title"] == "explain gravity"

    def test_new_format_loaded_correctly(self, tmp_path):
        import json
        hist_file = tmp_path / "history.json"
        ts = 1700000000.0
        new_data = {
            "s1": {
                "messages": [{"role": "user", "content": "hello"}],
                "started_at": ts,
                "last_active": ts + 60,
                "title": "hello",
            }
        }
        hist_file.write_text(json.dumps(new_data))

        svc = ConversationService(persist_path=str(hist_file))
        s = svc.list_sessions()[0]
        assert s["title"] == "hello"
        assert s["started_at"] == ts
        assert s["last_active"] == ts + 60


# ---------------------------------------------------------------------------
# clear()
# ---------------------------------------------------------------------------

class TestClear:
    def test_clear_removes_history(self):
        svc = _svc()
        svc.append_turn("s1", "hi", "hello")
        svc.clear("s1")
        assert svc.get_history("s1") == []

    def test_clear_none_is_noop(self):
        svc = _svc()
        svc.clear(None)  # must not raise
