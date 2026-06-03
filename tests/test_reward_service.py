"""Unit tests for RewardService — bus and MemoryService are fully mocked."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from local.services.reward_service import RewardService
from local.protocol.subjects import REWARD_EVENT, USER_FEEDBACK


def _make_service() -> tuple[RewardService, MagicMock, MagicMock, MagicMock]:
    mock_memory = MagicMock()
    mock_pub = MagicMock()
    mock_sub = MagicMock()
    with patch("local.services.reward_service.make_participant_bus", return_value=(mock_pub, mock_sub)):
        svc = RewardService(memory_service=mock_memory)
    return svc, mock_memory, mock_pub, mock_sub


def _make_envelope(query_id="qid-1", session_id="s1", sentiment="positive") -> MagicMock:
    env = MagicMock()
    env.subject = USER_FEEDBACK
    env.correlation_id = query_id
    env.payload = {"query_id": query_id, "session_id": session_id, "sentiment": sentiment}
    return env


class TestRewardServiceFeedback:
    def test_positive_feedback_calls_update_sentiment(self):
        svc, mock_memory, _, _ = _make_service()
        svc._handle_feedback(_make_envelope(sentiment="positive"))
        mock_memory.update_engram_sentiment.assert_called_once_with("qid-1", "positive")

    def test_negative_feedback_calls_update_sentiment(self):
        svc, mock_memory, _, _ = _make_service()
        svc._handle_feedback(_make_envelope(sentiment="negative"))
        mock_memory.update_engram_sentiment.assert_called_once_with("qid-1", "negative")

    def test_publishes_reward_event(self):
        svc, _, mock_pub, _ = _make_service()
        svc._handle_feedback(_make_envelope(query_id="qid-42", sentiment="positive"))
        mock_pub.publish.assert_called_once()
        envelope = mock_pub.publish.call_args.args[0]
        assert envelope.subject == REWARD_EVENT
        assert envelope.payload["query_id"] == "qid-42"
        assert envelope.payload["sentiment"] == "positive"

    def test_reward_event_carries_session_id(self):
        svc, _, mock_pub, _ = _make_service()
        svc._handle_feedback(_make_envelope(session_id="sess-99"))
        envelope = mock_pub.publish.call_args.args[0]
        assert envelope.payload["session_id"] == "sess-99"

    def test_skips_invalid_sentiment(self):
        svc, mock_memory, mock_pub, _ = _make_service()
        svc._handle_feedback(_make_envelope(sentiment="maybe"))
        mock_memory.update_engram_sentiment.assert_not_called()
        mock_pub.publish.assert_not_called()

    def test_skips_missing_query_id(self):
        svc, mock_memory, mock_pub, _ = _make_service()
        env = _make_envelope()
        env.payload["query_id"] = ""
        svc._handle_feedback(env)
        mock_memory.update_engram_sentiment.assert_not_called()
        mock_pub.publish.assert_not_called()
