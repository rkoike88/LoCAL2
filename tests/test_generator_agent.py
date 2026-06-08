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
    """Return a one-item iterable that mimics ollama.chat(stream=True).

    Each item is a chunk with .message.content, .message.thinking,
    .message.tool_calls matching what the streaming API yields.
    A single chunk with all content simulates a complete response.
    """
    chunk = MagicMock()
    chunk.message.content = content
    chunk.message.thinking = thinking or None
    chunk.message.tool_calls = tool_calls or None
    return [chunk]


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
        agent = GeneratorAgent(model=model, conversation_service=ConversationService(persist_path=":memory:"))
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

    def test_system_prompt_added_even_with_history(self):
        agent = _make_agent(system_prompt="You are helpful.")
        agent._conv.append_turn("s1", "prior", "answer")
        msgs = agent._build_messages("follow-up", session_id="s1")
        assert msgs[0] == {"role": "system", "content": "You are helpful."}
        assert msgs[1] == {"role": "user", "content": "prior"}
        assert msgs[-1] == {"role": "user", "content": "follow-up"}

    def test_no_session_id_no_history(self):
        agent = _make_agent()
        msgs = agent._build_messages("hello", session_id=None)
        assert msgs == [{"role": "user", "content": "hello"}]

    def test_no_attachments_unchanged(self):
        agent = _make_agent()
        msgs = agent._build_messages("hello", session_id=None, attachments=[])
        assert msgs == [{"role": "user", "content": "hello"}]

    def test_text_attachment_prepended(self):
        agent = _make_agent()
        att = {"type": "text", "name": "notes.txt", "data": "secret phrase xyz"}
        msgs = agent._build_messages("What does the file say?", session_id=None, attachments=[att])
        user_msg = msgs[-1]
        assert user_msg["role"] == "user"
        assert "[Attached: notes.txt]" in user_msg["content"]
        assert "secret phrase xyz" in user_msg["content"]
        assert "What does the file say?" in user_msg["content"]
        assert "images" not in user_msg

    def test_text_attachment_truncated_to_max_chars(self):
        agent = _make_agent()
        long_text = "x" * 20000
        att = {"type": "text", "name": "big.txt", "data": long_text}
        with patch("local.agents.generator_agent.get_config") as mock_cfg:
            mock_cfg.return_value = {"max_attachment_chars": 100}
            msgs = agent._build_messages("query", session_id=None, attachments=[att])
        content = msgs[-1]["content"]
        assert "x" * 100 in content
        assert "x" * 101 not in content

    def test_image_attachment_goes_to_images_field(self):
        agent = _make_agent()
        att = {"type": "image", "name": "photo.png", "data": "base64data=="}
        msgs = agent._build_messages("What do you see?", session_id=None, attachments=[att])
        user_msg = msgs[-1]
        assert user_msg["images"] == ["base64data=="]
        assert user_msg["content"] == "What do you see?"

    def test_mixed_text_and_image_attachments(self):
        agent = _make_agent()
        atts = [
            {"type": "text",  "name": "notes.txt", "data": "some context"},
            {"type": "image", "name": "diagram.png", "data": "imgdata=="},
        ]
        msgs = agent._build_messages("Explain this", session_id=None, attachments=atts)
        user_msg = msgs[-1]
        assert "[Attached: notes.txt]" in user_msg["content"]
        assert "some context" in user_msg["content"]
        assert "Explain this" in user_msg["content"]
        assert user_msg["images"] == ["imgdata=="]

    def test_error_attachments_skipped(self):
        agent = _make_agent()
        atts = [{"type": "error", "name": "bad.zip"}]
        msgs = agent._build_messages("hello", session_id=None, attachments=atts)
        user_msg = msgs[-1]
        assert user_msg["content"] == "hello"
        assert "images" not in user_msg


# ---------------------------------------------------------------------------
# _generate (mocked ollama.chat)
# ---------------------------------------------------------------------------

class TestGenerate:
    def test_simple_answer(self):
        agent = _make_agent()
        mock_resp = _make_ollama_response("Paris", thinking="France → Paris")
        with patch("ollama.chat", return_value=mock_resp):
            answer, thinking, tool_calls, _ = agent._generate(
                [{"role": "user", "content": "Capital of France?"}], "corr-1"
            )
        assert answer == "Paris"
        assert thinking == "France → Paris"
        assert tool_calls == []

    def test_thinking_empty_when_none(self):
        agent = _make_agent()
        # thinking=None in model_dump() — raw_msg.get("thinking") returns None
        mock_resp = _make_ollama_response("42", thinking="")
        with patch("ollama.chat", return_value=mock_resp):
            _, thinking, _, _ = agent._generate(
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

    def test_tool_call_exchange_saved_in_history(self):
        """Tool calls and results from turn 1 must appear in history for turn 2."""
        agent = _make_agent()
        tc = [{"function": {"name": "web_search", "arguments": {"query": "Jane Austen"}}}]
        tool_resp = _make_ollama_response("", tool_calls=tc)
        final_resp = _make_ollama_response("Jane Austen was born in 1775.")
        with patch("ollama.chat", side_effect=[tool_resp, final_resp]):
            with patch.object(agent, "_execute_tool", return_value="result: Jane Austen born 1775"):
                agent._handle_query(self._make_query_envelope("When was Jane Austen born?", session_id="s-tool"))
        history = agent._conv.get_history("s-tool")
        roles = [m["role"] for m in history]
        # Must contain: user, assistant (tool call), tool result, assistant (final answer)
        assert roles == ["user", "assistant", "tool", "assistant"]
        assert history[0]["content"] == "When was Jane Austen born?"
        assert history[-1]["content"] == "Jane Austen was born in 1775."
        # Thinking must be stripped from stored assistant turns
        assert all("thinking" not in m for m in history)

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
