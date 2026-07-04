"""Unit tests for CriticAgent — OllamaBackend and bus are fully mocked."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from local.agents.critic_agent import CriticAgent
from local.agents.critic_actions import CriticAction
from local.agents.critic_states import CriticState
from local.agents.critic_transitions import CriticStateMachine


def _make_agent(llm_response: str = "", gatekeeper_response: str = "NO") -> tuple[CriticAgent, MagicMock]:
    mock_llm = MagicMock()
    mock_llm.chat.return_value = (llm_response, "")
    mock_gatekeeper = MagicMock()
    mock_gatekeeper.chat.return_value = (gatekeeper_response, "")
    with patch("local.agents.critic_agent.make_participant_bus", return_value=(MagicMock(), MagicMock())):
        agent = CriticAgent(llm=mock_llm, gatekeeper=mock_gatekeeper)
    return agent, mock_llm


# ------------------------------------------------------------------
# State machine
# ------------------------------------------------------------------

class TestStateMachine:
    def test_initial_state_is_idle(self):
        sm = CriticStateMachine()
        assert sm.state == CriticState.IDLE

    def test_receive_transitions_to_receiving(self):
        sm = CriticStateMachine()
        sm.transition(CriticAction.RECEIVE)
        assert sm.state == CriticState.RECEIVING

    def test_full_happy_path(self):
        sm = CriticStateMachine()
        sm.transition(CriticAction.RECEIVE)
        sm.transition(CriticAction.START_GRADE)
        sm.transition(CriticAction.PUBLISH)
        sm.transition(CriticAction.RESET)
        assert sm.state == CriticState.IDLE

    def test_fail_path_resets_to_idle(self):
        sm = CriticStateMachine()
        sm.transition(CriticAction.RECEIVE)
        sm.transition(CriticAction.START_GRADE)
        sm.transition(CriticAction.FAIL)
        sm.transition(CriticAction.RESET)
        assert sm.state == CriticState.IDLE

    def test_invalid_transition_raises(self):
        sm = CriticStateMachine()
        with pytest.raises(ValueError, match="Illegal transition"):
            sm.transition(CriticAction.PUBLISH)  # invalid from IDLE


# ------------------------------------------------------------------
# _grade: score parsing
# ------------------------------------------------------------------

class TestGradeScoreParsing:
    def test_parses_score_5(self):
        agent, _ = _make_agent("Feedback: Excellent answer. [RESULT] 5")
        score, _ = agent._grade("Q", "A")
        assert score == 5

    def test_parses_score_4(self):
        agent, _ = _make_agent("Feedback: Good but minor gaps. [RESULT] 4")
        score, _ = agent._grade("Q", "A")
        assert score == 4

    def test_parses_score_3(self):
        agent, _ = _make_agent("Feedback: Partially correct. [RESULT] 3")
        score, _ = agent._grade("Q", "A")
        assert score == 3

    def test_parses_score_2(self):
        agent, _ = _make_agent("Feedback: Mostly wrong. [RESULT] 2")
        score, _ = agent._grade("Q", "A")
        assert score == 2

    def test_parses_score_1(self):
        agent, _ = _make_agent("Feedback: Incorrect. [RESULT] 1")
        score, _ = agent._grade("Q", "A")
        assert score == 1

    def test_returns_none_score_when_result_tag_missing(self):
        agent, _ = _make_agent("Feedback: Some text with no result tag.")
        score, _ = agent._grade("Q", "A")
        assert score is None

    def test_returns_none_score_on_empty_llm_response(self):
        agent, _ = _make_agent("")
        score, feedback = agent._grade("Q", "A")
        assert score is None
        assert feedback == ""

    def test_rejects_out_of_range_score(self):
        # 6 is outside [1-5] — regex [1-5] won't match
        agent, _ = _make_agent("Feedback: Too high. [RESULT] 6")
        score, _ = agent._grade("Q", "A")
        assert score is None


# ------------------------------------------------------------------
# _grade: feedback extraction
# ------------------------------------------------------------------

class TestGradeFeedbackExtraction:
    def test_strips_result_tag_from_feedback(self):
        agent, _ = _make_agent("Feedback: Great explanation. [RESULT] 5")
        _, feedback = agent._grade("Q", "A")
        assert "[RESULT]" not in feedback
        assert "5" not in feedback

    def test_strips_feedback_prefix(self):
        agent, _ = _make_agent("Feedback: Good answer. [RESULT] 4")
        _, feedback = agent._grade("Q", "A")
        assert not feedback.lower().startswith("feedback:")
        assert "Good answer" in feedback

    def test_feedback_empty_on_empty_response(self):
        agent, _ = _make_agent("")
        _, feedback = agent._grade("Q", "A")
        assert feedback == ""


# ------------------------------------------------------------------
# _grade: prompt construction
# ------------------------------------------------------------------

class TestGradePromptConstruction:
    def test_prompt_includes_query_and_answer(self):
        agent, mock_llm = _make_agent("Feedback: OK. [RESULT] 3")
        agent._grade("my question", "my answer")
        prompt = mock_llm.chat.call_args.args[0][0]["content"]
        assert "my question" in prompt
        assert "my answer" in prompt

    def test_prompt_includes_rubric(self):
        agent, mock_llm = _make_agent("Feedback: OK. [RESULT] 3")
        agent._rubric = "Score 5: Perfect."
        agent._grade("Q", "A")
        prompt = mock_llm.chat.call_args.args[0][0]["content"]
        assert "Score 5: Perfect." in prompt


# ------------------------------------------------------------------
# _handle_generation: tool call skip
# ------------------------------------------------------------------

class TestHandleGenerationToolSkip:
    def _make_envelope(self, tool_calls=None) -> MagicMock:
        env = MagicMock()
        env.subject = "response.generation"
        env.correlation_id = "corr-1"
        env.payload = {
            "query": "how do I like my eggs?",
            "answer": "Based on your memory, scrambled.",
            "session_id": "s1",
            "query_id": "q1",
            "tool_calls": tool_calls or [],
        }
        return env

    def test_gatekeeper_skips_prometheus_when_live_data(self):
        agent, mock_llm = _make_agent("Feedback: OK. [RESULT] 4", gatekeeper_response="YES")
        env = self._make_envelope(tool_calls=[{"name": "web_search", "result": "Bitcoin is $94,200"}])
        agent._handle_generation(env)
        mock_llm.chat.assert_not_called()

    def test_gatekeeper_publishes_skip_reason_when_live_data(self):
        from local.protocol.messages import CriticSkipped
        agent, mock_llm = _make_agent("Feedback: OK. [RESULT] 4", gatekeeper_response="YES")
        env = self._make_envelope(tool_calls=[{"name": "web_search", "result": "Bitcoin is $94,200"}])
        agent._handle_generation(env)
        published = [
            call.args[0] for call in agent._pub.publish.call_args_list
            if isinstance(call.args[0], CriticSkipped)
        ]
        assert len(published) == 1
        assert "gatekeeper" in published[0].reason

    def test_grades_when_no_tool_calls(self):
        agent, mock_llm = _make_agent("Feedback: OK. [RESULT] 4")
        env = self._make_envelope(tool_calls=[])
        agent._handle_generation(env)
        mock_llm.chat.assert_called_once()
