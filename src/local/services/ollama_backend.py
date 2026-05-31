"""Shared Ollama chat backend for LoCAL2 agents.

Used by Critic and other non-generator agents that need simple prompt→text
generation via the /api/chat endpoint. GeneratorAgent does NOT use this —
it calls ollama.chat() directly to retain the raw response["message"] dict
for conversation history appending and tool call inspection.

Contract: chat() NEVER raises. Returns empty string on any failure so callers
apply their own policy.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


def _ollama_debug() -> bool:
    from local.config_loader import get_config
    return bool(get_config("system").get("ollama_debug", False))


class OllamaBackend:
    """Minimal Ollama /api/chat wrapper for non-generator agents (e.g. Critic).

    Args:
        model:      Ollama model name (e.g. "prometheus:7b").
        host:       Optional backend URL override. Empty → ollama library default.
        timeout:    Request timeout seconds. None → ollama library default.
        agent_name: Used in debug log lines.
    """

    def __init__(
        self,
        model: str,
        host: str = "",
        timeout: Optional[int] = None,
        agent_name: str = "",
    ) -> None:
        self.model = model
        self._host = host
        self._timeout = timeout
        self._agent_name = agent_name or "?"

    def chat(
        self,
        messages: list[dict],
        *,
        think: bool = False,
        options: Optional[dict] = None,
    ) -> tuple[str, str]:
        """Call ollama /api/chat and return (response_text, thinking_text).

        Both strings are empty on failure — never raises.

        Args:
            messages: Ollama chat messages array.
            think:    Request extended thinking tokens.
            options:  Extra ollama options dict (e.g. {"num_ctx": 32000}).
        """
        try:
            client = self._make_client()
        except Exception as exc:
            logger.warning("OllamaBackend: client construction failed: %s", exc)
            return "", ""

        chat_kwargs: dict = {"model": self.model, "messages": messages, "think": think}
        if options:
            chat_kwargs["options"] = options

        import time as _time
        _t0 = _time.time()
        if _ollama_debug():
            _total_len = sum(len(m.get("content", "") or "") for m in messages)
            print(
                f"[ollama.chat START] {_time.strftime('%H:%M:%S')} "
                f"agent={self._agent_name} model={self.model} "
                f"messages={len(messages)} total_chars={_total_len}"
            )

        try:
            response = client.chat(**chat_kwargs)
            if _ollama_debug():
                print(f"[ollama.chat OK] elapsed={_time.time()-_t0:.1f}s agent={self._agent_name}")
        except BaseException as exc:
            if _ollama_debug():
                print(f"[ollama.chat FAIL] elapsed={_time.time()-_t0:.1f}s agent={self._agent_name} error={exc}")
            logger.warning("OllamaBackend: chat failed for model=%s: %s", self.model, exc)
            return "", ""

        text = (getattr(response.message, "content", None) or "").strip()
        thinking = (getattr(response, "thinking", None) or "").strip()
        return text, thinking

    def _make_client(self):
        import ollama
        client_kwargs: dict = {}
        host = os.environ.get("OLLAMA_HOST") or self._host
        if host:
            client_kwargs["host"] = host
        if self._timeout is not None:
            client_kwargs["timeout"] = self._timeout
        try:
            return ollama.Client(**client_kwargs)
        except TypeError:
            client_kwargs.pop("timeout", None)
            return ollama.Client(**client_kwargs)
