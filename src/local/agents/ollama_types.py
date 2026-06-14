"""Typed wrappers and message factories for Ollama API structures.

The Ollama streaming API returns SDK objects (with attribute access); deserialized
history uses plain dicts. OllamaToolCall.from_any() handles both so callers never
need to branch on the type.

Message factory functions (make_assistant_msg, make_tool_result_msg) keep
Ollama-specific role/key strings out of the generator.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class OllamaToolCall:
    """Normalized tool call from an Ollama chat response.

    Attributes:
        name: The tool function name.
        arguments: The tool arguments dict.
    """
    name: str
    arguments: dict

    @classmethod
    def from_any(cls, tc) -> "OllamaToolCall":
        """Parse from an Ollama SDK ToolCall object or a plain dict."""
        if isinstance(tc, dict):
            fn = tc.get("function") or {}
            return cls(name=fn.get("name", ""), arguments=fn.get("arguments") or {})
        fn = getattr(tc, "function", None)
        return cls(
            name=getattr(fn, "name", "") or "",
            arguments=getattr(fn, "arguments", {}) or {},
        )

    def to_dict(self) -> dict:
        return {"function": {"name": self.name, "arguments": self.arguments}}


def make_assistant_msg(content: str, thinking: str | None, tool_calls: list | None) -> dict:
    """Build an assistant turn dict for the Ollama messages array."""
    msg: dict = {"role": "assistant", "content": content}
    if thinking:
        msg["thinking"] = thinking
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return msg


def make_tool_result_msg(name: str, result: str) -> dict:
    """Build a tool-result turn dict for the Ollama messages array."""
    return {"role": "tool", "content": result, "name": name}


def clean_for_history(m: dict) -> dict:
    """Strip thinking and normalize tool_calls before saving a message to history.

    Removes the thinking key (must not be passed back to the model in history)
    and converts Ollama SDK ToolCall objects to plain JSON-serializable dicts.
    """
    result = {k: v for k, v in m.items() if k != "thinking"}
    tool_calls = result.get("tool_calls")
    if not tool_calls:
        result.pop("tool_calls", None)
    else:
        result["tool_calls"] = [OllamaToolCall.from_any(tc).to_dict() for tc in tool_calls]
    return result
