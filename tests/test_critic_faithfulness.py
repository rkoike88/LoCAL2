"""Tests for the two critic fixes: heading sanitization and the optional faithfulness pass.

Background (reproduced live against prometheus-7b on 2026-09-28): Prometheus's
grading template uses "###" as its section delimiter, so a Markdown "### Heading"
inside the answer ends the "Response to evaluate" section early. The same Gemma
answer scored 1/5 with headings intact and 4/5 with headings rewritten as bold.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from local.agents.critic_agent import CriticAgent, _parse_grade, sanitize_for_grader
from local.protocol.messages import CritiqueResult


# ------------------------------------------------------------------
# sanitize_for_grader
# ------------------------------------------------------------------

class TestSanitizeForGrader:
    def test_rewrites_atx_headings_as_bold(self):
        text = "Intro\n### What Cuttlefish Is\n* a\n## Host OS\ntext\n# Title #"
        out = sanitize_for_grader(text)
        assert "**What Cuttlefish Is**" in out
        assert "**Host OS**" in out
        assert "**Title**" in out
        assert not any(line.lstrip().startswith("#") for line in out.splitlines())

    def test_keeps_body_text_and_lists(self):
        text = "### H\n* item one\n* item two\nplain"
        out = sanitize_for_grader(text)
        assert "* item one" in out and "* item two" in out and "plain" in out

    def test_leaves_inline_hash_alone(self):
        text = "issue #42 and C# are fine"
        assert sanitize_for_grader(text) == text

    def test_drops_bare_hash_lines(self):
        assert "#" not in sanitize_for_grader("a\n###\nb")

    def test_empty_passthrough(self):
        assert sanitize_for_grader("") == ""


class TestParseGrade:
    def test_strips_think_block(self):
        score, fb = _parse_grade("<think>lots of\nreasoning</think>Feedback: fine. [RESULT] 4")
        assert score == 4 and fb == "fine."

    def test_none_when_missing(self):
        assert _parse_grade("no score here")[0] is None


# ------------------------------------------------------------------
# CriticAgent wiring
# ------------------------------------------------------------------

def _make_agent(llm_response="Feedback: OK. [RESULT] 4", faith_response=None, faith_cfg=None):
    mock_llm = MagicMock()
    mock_llm.chat.return_value = (llm_response, "")
    faith_llm = None
    if faith_response is not None:
        faith_llm = MagicMock()
        faith_llm.chat.return_value = (faith_response, "")
    from local.config_loader import get_config
    real_cfg = dict(get_config("critic"))
    if faith_cfg is not None:
        real_cfg["faithfulness"] = {**(real_cfg.get("faithfulness") or {}), **faith_cfg}
    with patch("local.agents.critic_agent.make_participant_bus", return_value=(MagicMock(), MagicMock())), \
         patch("local.agents.critic_agent.get_config", return_value=real_cfg):
        agent = CriticAgent(llm=mock_llm, faithfulness_llm=faith_llm)
    return agent, mock_llm, faith_llm


def _envelope(answer="Based on your memory, scrambled.", tool_calls=None):
    env = MagicMock()
    env.subject = "response.generation"
    env.correlation_id = "corr-1"
    env.payload = {
        "query": "how do I like my eggs?",
        "answer": answer,
        "session_id": "s1",
        "query_id": "q1",
        "tool_calls": tool_calls or [],
    }
    return env


def _published(agent):
    return [c.args[0] for c in agent._pub.publish.call_args_list if isinstance(c.args[0], CritiqueResult)]


class TestHeadingSanitizationInPrompt:
    def test_prometheus_prompt_has_no_markdown_headings_from_answer(self):
        agent, mock_llm, _ = _make_agent()
        agent._handle_generation(_envelope(answer="Cuttlefish is a VM.\n\n### Host OS\nLinux only."))
        prompt = mock_llm.chat.call_args.args[0][0]["content"]
        assert "### Host OS" not in prompt
        assert "**Host OS**" in prompt
        # the template's own delimiters must survive
        assert "###Response to evaluate:" in prompt
        assert "Linux only." in prompt

    def test_query_is_sanitized_too(self):
        agent, mock_llm, _ = _make_agent()
        env = _envelope()
        env.payload["query"] = "# explain this\nwhat is cuttlefish?"
        agent._handle_generation(env)
        prompt = mock_llm.chat.call_args.args[0][0]["content"]
        assert "# explain this" not in prompt and "**explain this**" in prompt


class TestFaithfulnessPass:
    WEB = [{"tool": "web_search", "args": {"query": "cuttlefish"}, "result": "[1] Host OS: Linux"}]

    def test_disabled_by_default_publishes_single_critique(self):
        agent, mock_llm, _ = _make_agent(faith_response="Feedback: x [RESULT] 1", faith_cfg={"enabled": False})
        agent._handle_generation(_envelope(tool_calls=self.WEB))
        assert len(_published(agent)) == 1
        assert _published(agent)[0].kind == "quality"

    def test_enabled_publishes_second_critique_with_reference(self):
        agent, mock_llm, faith_llm = _make_agent(
            faith_response="Feedback: claims macOS; ref [1] says Linux. [RESULT] 1",
            faith_cfg={"enabled": True, "model": "qwen3.5:27b"},
        )
        agent._handle_generation(_envelope(answer="Runs on macOS.", tool_calls=self.WEB))
        pubs = _published(agent)
        assert [p.kind for p in pubs] == ["quality", "faithfulness"]
        f = pubs[1]
        assert f.score == 1 and "macOS" in f.feedback
        assert f.rubric_name == "faithfulness" and f.judge_model == "qwen3.5:27b"
        assert f.query_id == "q1" and f.session_id == "s1"
        prompt = faith_llm.chat.call_args.args[0][0]["content"]
        assert "[web_search]" in prompt and "Host OS: Linux" in prompt
        assert "Runs on macOS." in prompt
        # primary grader untouched
        assert mock_llm.chat.call_count == 1

    def test_no_reference_tools_means_no_second_pass(self):
        agent, _, faith_llm = _make_agent(faith_response="Feedback: x [RESULT] 5", faith_cfg={"enabled": True})
        agent._handle_generation(_envelope(tool_calls=[{"tool": "persona", "args": {"name": "analytic"}, "result": "ok"}]))
        assert len(_published(agent)) == 1
        faith_llm.chat.assert_not_called()

    def test_empty_tool_result_skipped(self):
        agent, _, faith_llm = _make_agent(faith_response="Feedback: x [RESULT] 5", faith_cfg={"enabled": True})
        agent._handle_generation(_envelope(tool_calls=[{"tool": "web_search", "args": {}, "result": ""}]))
        faith_llm.chat.assert_not_called()

    def test_reference_truncated(self):
        agent, _, faith_llm = _make_agent(faith_response="Feedback: x [RESULT] 5", faith_cfg={"enabled": True, "max_reference_chars": 50})
        agent._handle_generation(_envelope(tool_calls=[{"tool": "web_fetch", "args": {}, "result": "x" * 500}]))
        prompt = faith_llm.chat.call_args.args[0][0]["content"]
        assert "[... reference truncated ...]" in prompt
        assert "x" * 100 not in prompt

    def test_judge_failure_publishes_none_score(self):
        agent, _, faith_llm = _make_agent(faith_response="", faith_cfg={"enabled": True})
        faith_llm.chat.side_effect = RuntimeError("ollama down")
        agent._handle_generation(_envelope(tool_calls=self.WEB))
        pubs = _published(agent)
        assert len(pubs) == 2 and pubs[1].kind == "faithfulness" and pubs[1].score is None

    def test_think_flag_forwarded(self):
        agent, _, faith_llm = _make_agent(faith_response="Feedback: x [RESULT] 5", faith_cfg={"enabled": True, "think": True})
        agent._handle_generation(_envelope(tool_calls=self.WEB))
        assert faith_llm.chat.call_args.kwargs["think"] is True

    def test_enabled_without_prompt_disables(self):
        agent, _, _ = _make_agent(faith_response="x", faith_cfg={"enabled": True, "grade_prompt": ""})
        assert agent._faith_enabled is False


class TestCritiqueResultRoundTrip:
    def test_kind_defaults_to_quality(self):
        env = MagicMock()
        env.payload = {"score": 4, "feedback": "", "query": "q", "answer": "a", "session_id": "s", "query_id": "id"}
        assert CritiqueResult.from_envelope(env).kind == "quality"

    def test_faithfulness_fields_round_trip(self):
        env = MagicMock()
        env.payload = {"score": 1, "feedback": "f", "query": "q", "answer": "a", "session_id": "s",
                       "query_id": "id", "kind": "faithfulness", "judge_model": "qwen3.5:27b"}
        m = CritiqueResult.from_envelope(env)
        assert m.kind == "faithfulness" and m.judge_model == "qwen3.5:27b"
