"""Unit tests for MemoryAgent — MemoryService, OllamaBackend, and bus are fully mocked."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, call, patch

import pytest

from local.agents.memory_agent import MemoryAgent
from local.agents.memory_agent_actions import MemoryAgentAction
from local.agents.memory_agent_states import MemoryAgentState
from local.agents.memory_agent_transitions import MemoryAgentStateMachine


def _make_agent() -> tuple[MemoryAgent, MagicMock, MagicMock]:
    mock_memory = MagicMock()
    mock_llm = MagicMock()
    with patch("local.agents.memory_agent.make_participant_bus", return_value=(MagicMock(), MagicMock())):
        agent = MemoryAgent(memory_service=mock_memory, llm=mock_llm)
    return agent, mock_memory, mock_llm


def _make_envelope(query: str, answer: str, error: bool = False, query_id: str = "") -> MagicMock:
    env = MagicMock()
    env.subject = "response.generation"
    env.payload = {"query": query, "answer": answer, "error": error, "query_id": query_id}
    return env


# ------------------------------------------------------------------
# State machine
# ------------------------------------------------------------------

class TestStateMachine:
    def test_initial_state_is_idle(self):
        sm = MemoryAgentStateMachine()
        assert sm.state == MemoryAgentState.IDLE

    def test_start_ingest_transitions_to_ingesting(self):
        sm = MemoryAgentStateMachine()
        sm.transition(MemoryAgentAction.START_INGEST)
        assert sm.state == MemoryAgentState.INGESTING

    def test_complete_transitions_back_to_idle(self):
        sm = MemoryAgentStateMachine()
        sm.transition(MemoryAgentAction.START_INGEST)
        sm.transition(MemoryAgentAction.COMPLETE)
        assert sm.state == MemoryAgentState.IDLE

    def test_invalid_transition_raises(self):
        sm = MemoryAgentStateMachine()
        with pytest.raises(ValueError, match="Illegal transition"):
            sm.transition(MemoryAgentAction.COMPLETE)  # invalid from IDLE

    def test_update_score_path(self):
        sm = MemoryAgentStateMachine()
        sm.transition(MemoryAgentAction.UPDATE_SCORE)
        assert sm.state == MemoryAgentState.UPDATING_SCORE
        sm.transition(MemoryAgentAction.COMPLETE)
        assert sm.state == MemoryAgentState.IDLE


# ------------------------------------------------------------------
# Ingest happy path
# ------------------------------------------------------------------

class TestIngestHappyPath:
    def test_calls_write_episodic_with_query_and_answer(self):
        agent, mock_memory, mock_llm = _make_agent()
        mock_llm.chat.return_value = ('{"intent": "fact", "entities": ["Python"]}', "")
        agent._handle_generation(_make_envelope("what is Python?", "A language."))
        mock_memory.write_episodic.assert_called_once()
        args = mock_memory.write_episodic.call_args
        assert args.args[0] == "what is Python?"
        assert args.args[1] == "A language."

    def test_metadata_includes_intent_and_entities(self):
        agent, mock_memory, mock_llm = _make_agent()
        mock_llm.chat.return_value = ('{"intent": "fact", "entities": ["Python", "Alice"]}', "")
        agent._handle_generation(_make_envelope("Q", "A"))
        meta = mock_memory.write_episodic.call_args.kwargs.get("metadata") or \
               mock_memory.write_episodic.call_args.args[2]
        assert meta["intent"] == "fact"
        assert "Python" in meta["entities"]

    def test_state_returns_to_idle_after_ingest(self):
        agent, mock_memory, mock_llm = _make_agent()
        mock_llm.chat.return_value = ('{"intent": "fact", "entities": []}', "")
        agent._handle_generation(_make_envelope("Q", "A"))
        assert agent._sm.state == MemoryAgentState.IDLE

    def test_passes_query_id_to_write_episodic(self):
        agent, mock_memory, mock_llm = _make_agent()
        mock_llm.chat.return_value = ('{"intent": "fact", "entities": []}', "")
        agent._handle_generation(_make_envelope("Q", "A", query_id="test-qid-abc"))
        kwargs = mock_memory.write_episodic.call_args.kwargs
        assert kwargs.get("query_id") == "test-qid-abc"


# ------------------------------------------------------------------
# Classification failures — engram still written
# ------------------------------------------------------------------

class TestClassificationFallback:
    def test_writes_engram_when_llm_returns_empty(self):
        agent, mock_memory, mock_llm = _make_agent()
        mock_llm.chat.return_value = ("", "")
        agent._handle_generation(_make_envelope("Q", "A"))
        mock_memory.write_episodic.assert_called_once()

    def test_writes_engram_when_json_is_malformed(self):
        agent, mock_memory, mock_llm = _make_agent()
        mock_llm.chat.return_value = ("not json at all", "")
        agent._handle_generation(_make_envelope("Q", "A"))
        mock_memory.write_episodic.assert_called_once()

    def test_empty_metadata_on_bad_classification(self):
        agent, mock_memory, mock_llm = _make_agent()
        mock_llm.chat.return_value = ("", "")
        agent._handle_generation(_make_envelope("Q", "A"))
        meta = mock_memory.write_episodic.call_args.args[2] \
               if len(mock_memory.write_episodic.call_args.args) > 2 \
               else mock_memory.write_episodic.call_args.kwargs.get("metadata", {})
        assert meta == {}

    def test_invalid_intent_value_is_cleared(self):
        agent, _, mock_llm = _make_agent()
        mock_llm.chat.return_value = ('{"intent": "unknown_garbage", "entities": []}', "")
        result = agent._classify("Q", "A")
        assert result["intent"] == ""

    def test_state_returns_to_idle_after_write_failure(self):
        agent, mock_memory, mock_llm = _make_agent()
        mock_llm.chat.return_value = ('{"intent": "fact", "entities": []}', "")
        mock_memory.write_episodic.side_effect = RuntimeError("db down")
        agent._handle_generation(_make_envelope("Q", "A"))
        assert agent._sm.state == MemoryAgentState.IDLE


# ------------------------------------------------------------------
# Skip conditions
# ------------------------------------------------------------------

def _make_critique_envelope(query_id: str = "qid-1", score=4) -> MagicMock:
    env = MagicMock()
    env.subject = "critique.result"
    env.payload = {"query_id": query_id, "score": score, "feedback": "Good answer."}
    return env


# ------------------------------------------------------------------
# _handle_critique
# ------------------------------------------------------------------

class TestHandleCritique:
    def test_calls_update_engram_score_with_correct_args(self):
        agent, mock_memory, _ = _make_agent()
        agent._handle_critique(_make_critique_envelope("qid-42", score=4))
        mock_memory.update_engram_score.assert_called_once_with("qid-42", 4)

    def test_skips_when_score_is_none(self):
        agent, mock_memory, _ = _make_agent()
        agent._handle_critique(_make_critique_envelope("qid-1", score=None))
        mock_memory.update_engram_score.assert_not_called()

    def test_skips_when_query_id_missing(self):
        agent, mock_memory, _ = _make_agent()
        agent._handle_critique(_make_critique_envelope(query_id="", score=3))
        mock_memory.update_engram_score.assert_not_called()

    def test_state_returns_to_idle_after_update(self):
        agent, _, _ = _make_agent()
        agent._handle_critique(_make_critique_envelope())
        assert agent._sm.state == MemoryAgentState.IDLE

    def test_state_returns_to_idle_on_update_failure(self):
        agent, mock_memory, _ = _make_agent()
        mock_memory.update_engram_score.side_effect = RuntimeError("db error")
        agent._handle_critique(_make_critique_envelope())
        assert agent._sm.state == MemoryAgentState.IDLE


class TestSkipConditions:
    def test_skips_error_responses(self):
        agent, mock_memory, mock_llm = _make_agent()
        agent._handle_generation(_make_envelope("Q", "A", error=True))
        mock_memory.write_episodic.assert_not_called()
        mock_llm.chat.assert_not_called()

    def test_skips_empty_query(self):
        agent, mock_memory, _ = _make_agent()
        agent._handle_generation(_make_envelope("", "A"))
        mock_memory.write_episodic.assert_not_called()

    def test_skips_empty_answer(self):
        agent, mock_memory, _ = _make_agent()
        agent._handle_generation(_make_envelope("Q", ""))
        mock_memory.write_episodic.assert_not_called()


# ------------------------------------------------------------------
# Classification logic
# ------------------------------------------------------------------

class TestClassify:
    def test_extracts_intent_and_entities(self):
        agent, _, mock_llm = _make_agent()
        mock_llm.chat.return_value = ('{"intent": "procedure", "entities": ["Docker", "FastAPI"]}', "")
        result = agent._classify("how do I run this?", "Use docker compose up.")
        assert result["intent"] == "procedure"
        assert "Docker" in result["entities"]

    def test_handles_json_embedded_in_prose(self):
        agent, _, mock_llm = _make_agent()
        mock_llm.chat.return_value = ('Here is the result: {"intent": "fact", "entities": []} done.', "")
        result = agent._classify("Q", "A")
        assert result["intent"] == "fact"

    def test_returns_empty_dict_on_no_json(self):
        agent, _, mock_llm = _make_agent()
        mock_llm.chat.return_value = ("I cannot classify this.", "")
        result = agent._classify("Q", "A")
        assert result == {}
