"""Unit tests for GeneratorAgent — no live Ollama required."""

from unittest.mock import MagicMock, patch
import pytest

from local.agents.generator_agent import GeneratorAgent
from local.agents.generator_states import GeneratorState
from local.services.conversation_service import ConversationService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ollama_response(content: str, thinking: str = "", tool_calls=None):
    """Build a minimal mock that mimics ollama.ChatResponse."""
    msg = MagicMock()
    msg.model_dump.return_value = {
        "role": "assistant",
        "content": content,
        "tool_calls": tool_calls,
    }
    msg.content = content
    msg.tool_calls = tool_calls or []

    response = MagicMock()
    response.message = msg
    response.thinking = thinking
    return response


def _make_agent(model="test-model", system_prompt="", tool_schemas=None) -> GeneratorAgent:
    """Create a GeneratorAgent with patched bus connections."""
    with patch("local.agents.generator_agent.make_participant_bus") as mock_bus, \
         patch("local.agents.generator_agent.get_config") as mock_cfg:
        mock_cfg.return_value = {
            "model": model,
            "num_ctx": 8192,
            "temperature": 0.7,
            "max_tool_iterations": 5,
            "system_prompt": system_prompt,
            "tools": tool_schemas or [],
        }
        mock_pub = MagicMock()
        mock_sub = MagicMock()
        mock_bus.return_value = (mock_pub, mock_sub)
        agent = GeneratorAgent(model=model)
        agent._pub = mock_pub
        agent._sub = mock_sub
    return agent


# ---------------------------------------------------------------------------
# _build_messages
# ---------------------------------------------------------------------------

class TestBuildMessages:
    def test_first_turn_no_system(self):
        agent = _make_agent()
        msgs = agent._build_messages("hello", session_id="s1")
        assert msgs == [{"role": "user", "content": "hello"}]

    def test_first_turn_with_system_prompt(self):
        agent = _make_agent(system_prompt="You are helpful.")
        msgs = agent._build_messages("hello", session_id="s1")
        assert msgs[0] == {"role": "system", "content": "You are helpful."}
        assert msgs[-1] == {"role": "user", "content": "hello"}

    def test_multi_turn_history_included(self):
        agent = _make_agent()
        agent._conv.append_turn("s1", "what is 2+2?", "4")
        msgs = agent._build_messages("and times 3?", session_id="s1")
        assert msgs[0] == {"role": "user", "content": "what is 2+2?"}
        assert msgs[1] == {"role": "assistant", "content": "4"}
        assert msgs[-1] == {"role": "user", "content": "and times 3?"}

    def test_system_prompt_not_added_when_history_present(self):
        agent = _make_agent(system_prompt="You are helpful.")
        agent._conv.append_turn("s1", "prior", "answer")
        msgs = agent._build_messages("follow-up", session_id="s1")
        assert msgs[0]["role"] != "system"

    def test_no_session_id_no_history(self):
        agent = _make_agent()
        msgs = agent._build_messages("hello", session_id=None)
        assert msgs == [{"role": "user", "content": "hello"}]


# ---------------------------------------------------------------------------
# _generate (mocked ollama.chat)
# ---------------------------------------------------------------------------

class TestGenerate:
    def test_simple_answer(self):
        agent = _make_agent()
        mock_resp = _make_ollama_response("Paris", thinking="France → Paris")
        with patch("ollama.chat", return_value=mock_resp):
            answer, thinking, tool_calls = agent._generate(
                [{"role": "user", "content": "Capital of France?"}], "corr-1"
            )
        assert answer == "Paris"
        assert thinking == "France → Paris"
        assert tool_calls == []

    def test_thinking_empty_when_none(self):
        agent = _make_agent()
        mock_resp = _make_ollama_response("42")
        mock_resp.thinking = None
        with patch("ollama.chat", return_value=mock_resp):
            _, thinking, _ = agent._generate(
                [{"role": "user", "content": "?"}], "corr-2"
            )
        assert thinking == ""

    def test_state_machine_returns_to_generating_after_generate(self):
        agent = _make_agent()
        agent._sm._state = GeneratorState.GENERATING
        mock_resp = _make_ollama_response("hello")
        with patch("ollama.chat", return_value=mock_resp):
            agent._generate([{"role": "user", "content": "hi"}], "c")
        assert agent._sm.state == GeneratorState.GENERATING


# ---------------------------------------------------------------------------
# _handle_query (integration — mocked ollama + bus)
# ---------------------------------------------------------------------------

class TestHandleQuery:
    def _make_query_envelope(self, query, session_id="sess-1", query_id="qid-1"):
        from local.protocol.envelope import MessageEnvelope
        return MessageEnvelope.create(
            message_type="query",
            subject="query.received",
            sender_id="test",
            payload={"query": query, "session_id": session_id, "query_id": query_id},
            correlation_id=query_id,
            metadata={"session_id": session_id},
        )

    def test_publishes_response_generation(self):
        agent = _make_agent()
        mock_resp = _make_ollama_response("4", thinking="trivial")
        with patch("ollama.chat", return_value=mock_resp):
            agent._handle_query(self._make_query_envelope("what is 2+2?"))

        publish_calls = agent._pub.publish.call_args_list
        subjects = [c.args[0].subject for c in publish_calls]
        assert "response.generation" in subjects
        assert "answer.dialog" in subjects

    def test_response_generation_payload(self):
        agent = _make_agent()
        mock_resp = _make_ollama_response("4", thinking="2+2=4")
        with patch("ollama.chat", return_value=mock_resp):
            agent._handle_query(self._make_query_envelope("what is 2+2?"))

        rg_call = next(
            c for c in agent._pub.publish.call_args_list
            if c.args[0].subject == "response.generation"
        )
        payload = rg_call.args[0].payload
        assert payload["answer"] == "4"
        assert payload["thinking"] == "2+2=4"
        assert payload["tool_calls"] == []

    def test_conversation_history_updated(self):
        agent = _make_agent()
        mock_resp = _make_ollama_response("Jane Austen")
        with patch("ollama.chat", return_value=mock_resp):
            agent._handle_query(self._make_query_envelope(
                "Who wrote Pride and Prejudice?", session_id="s-hist"
            ))
        history = agent._conv.get_history("s-hist")
        assert history[0] == {"role": "user", "content": "Who wrote Pride and Prejudice?"}
        assert history[1] == {"role": "assistant", "content": "Jane Austen"}

    def test_state_machine_returns_to_idle(self):
        agent = _make_agent()
        mock_resp = _make_ollama_response("ok")
        with patch("ollama.chat", return_value=mock_resp):
            agent._handle_query(self._make_query_envelope("hi"))
        assert agent._sm.state == GeneratorState.IDLE

    def test_error_resets_state_machine(self):
        agent = _make_agent()
        with patch("ollama.chat", side_effect=RuntimeError("connection refused")):
            agent._handle_query(self._make_query_envelope("hi"))
        assert agent._sm.state == GeneratorState.IDLE

    def test_error_publishes_response_generation(self):
        agent = _make_agent()
        with patch("ollama.chat", side_effect=RuntimeError("timeout")):
            agent._handle_query(self._make_query_envelope("hi"))
        subjects = [c.args[0].subject for c in agent._pub.publish.call_args_list]
        assert "response.generation" in subjects
