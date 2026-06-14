"""Typed wrappers for Ollama API structures.

The Ollama streaming API returns SDK objects (with attribute access); deserialized
history uses plain dicts. OllamaToolCall.from_any() handles both so callers never
need to branch on the type.
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
