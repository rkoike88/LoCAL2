"""Arm B — bare model with tool-call loop (no LoCAL2 bus).

Maintains per-session conversation history. Executes web_search and web_fetch
directly via HTTP — same search backend as LoCAL2, no bus involvement.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any, Callable

import httpx
import ollama
import yaml
from bs4 import BeautifulSoup
from pathlib import Path

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).parent / "config.yaml"

_WEB_SEARCH_SCHEMA = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "Search the web for current information, news, or facts not in your training data. "
            "Call this when the user asks about recent events or wants live information. "
            "Follow up with web_fetch to retrieve the full content of a specific result."
        ),
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "The search query."}},
            "required": ["query"],
        },
    },
}

_WEB_FETCH_SCHEMA = {
    "type": "function",
    "function": {
        "name": "web_fetch",
        "description": (
            "Fetch and extract the text content of a specific URL. "
            "Call this after web_search to read the full content of a result, "
            "or when the user provides a URL to read."
        ),
        "parameters": {
            "type": "object",
            "properties": {"url": {"type": "string", "description": "The full URL to fetch."}},
            "required": ["url"],
        },
    },
}

_USER_AGENT = "Mozilla/5.0 (compatible; LoCAL2-Harness/1.0)"


class ArmBClient:
    """Bare model client with web tool-call loop.

    Conversation history is maintained per session_id in memory.
    Tool calls are executed directly via HTTP — no LoCAL2 bus involved.
    """

    def __init__(self) -> None:
        self._cfg = yaml.safe_load(_CONFIG_PATH.read_text())
        arm_b = self._cfg.get("arm_b", {})
        self._model: str = arm_b.get("model", "gemma4:e4b-mlx")
        self._temperature: float = arm_b.get("temperature", 0.7)
        self._system_prompt: str = arm_b.get("system_prompt", "")
        self._tools_cfg: dict = arm_b.get("tools", {})

        search_cfg = self._cfg.get("search", {})
        self._searxng_url: str = search_cfg.get("searxng_url", "http://localhost:8080")
        self._search_max_results: int = search_cfg.get("max_results", 5)
        self._search_timeout: float = search_cfg.get("timeout", 10)

        fetch_cfg = self._cfg.get("fetch", {})
        self._fetch_max_chars: int = fetch_cfg.get("max_chars", 12000)
        self._fetch_timeout: float = fetch_cfg.get("timeout", 15)

        self._histories: dict[str, list[dict]] = {}

        self._tool_schemas: list[dict] = []
        if self._tools_cfg.get("web_search"):
            self._tool_schemas.append(_WEB_SEARCH_SCHEMA)
        if self._tools_cfg.get("web_fetch"):
            self._tool_schemas.append(_WEB_FETCH_SCHEMA)

    def reset_session(self, session_id: str) -> None:
        self._histories.pop(session_id, None)

    def stream(
        self,
        session_id: str,
        query: str,
        emit: Callable[[dict], None],
        max_iters: int = 6,
    ) -> str:
        """Run the tool-call loop for one query turn.

        Calls emit() with event dicts as they arrive. Returns the final answer.
        Events:
          {"type": "tool_call",   "tool": str, "args": dict}
          {"type": "tool_result", "tool": str, "result": str}
          {"type": "token",       "content": str}
          {"type": "done",        "content": str, "tool_calls": list}
        """
        history = self._histories.setdefault(session_id, [])
        if self._system_prompt and not history:
            history.append({"role": "system", "content": self._system_prompt})

        history.append({"role": "user", "content": query})

        tool_call_log: list[dict] = []
        answer = ""

        for _ in range(max_iters):
            iter_content = ""
            iter_tool_calls = None

            for chunk in ollama.chat(
                model=self._model,
                messages=history,
                tools=self._tool_schemas or None,
                stream=True,
                options={"temperature": self._temperature},
            ):
                thinking = getattr(chunk.message, "thinking", None)
                if thinking:
                    emit({"type": "thinking_chunk", "chunk": thinking})
                if chunk.message.content:
                    iter_content += chunk.message.content
                    emit({"type": "token", "content": chunk.message.content})
                if chunk.message.tool_calls:
                    iter_tool_calls = chunk.message.tool_calls

            assistant_msg: dict = {"role": "assistant", "content": iter_content}
            if iter_tool_calls:
                assistant_msg["tool_calls"] = [
                    {
                        "function": {
                            "name": tc.function.name,
                            "arguments": dict(tc.function.arguments or {}),
                        }
                    }
                    for tc in iter_tool_calls
                ]
            history.append(assistant_msg)

            if not iter_tool_calls:
                answer = iter_content.strip()
                break

            for tc in iter_tool_calls:
                name = tc.function.name
                args = dict(tc.function.arguments or {})
                emit({"type": "tool_call", "tool": name, "args": args})
                result = self._execute_tool(name, args)
                emit({"type": "tool_result", "tool": name, "result": result})
                tool_call_log.append({"tool": name, "args": args, "result": result})
                history.append({"role": "tool", "content": result, "name": name})

        emit({"type": "done", "content": answer, "tool_calls": tool_call_log})
        return answer

    def _execute_tool(self, name: str, args: dict) -> str:
        if name == "web_search":
            return self._web_search(args.get("query", ""))
        if name == "web_fetch":
            return self._web_fetch(args.get("url", ""))
        return f"[unknown tool: {name}]"

    def _web_search(self, query: str) -> str:
        try:
            resp = httpx.get(
                f"{self._searxng_url}/search",
                params={"q": query, "format": "json"},
                timeout=self._search_timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results", [])[:self._search_max_results]
            if not results:
                return f"[{date.today()}] No results found for: {query}"
            today = date.today().isoformat()
            lines = [f"[{today}] Web search: {query}\n"]
            for i, r in enumerate(results, 1):
                title = r.get("title", "")
                url = r.get("url", "")
                content = r.get("content", "")
                lines.append(f"{i}. {title}\n   URL: {url}\n   {content}")
            return "\n\n".join(lines)
        except Exception as exc:
            return f"[web_search error: {exc}]"

    def _web_fetch(self, url: str) -> str:
        try:
            resp = httpx.get(
                url,
                timeout=self._fetch_timeout,
                headers={"User-Agent": _USER_AGENT},
                follow_redirects=True,
            )
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            for tag in soup(["script", "style", "nav", "footer", "header"]):
                tag.decompose()
            text = soup.get_text(separator="\n", strip=True)
            lines = [line for line in text.splitlines() if line.strip()]
            content = "\n".join(lines)
            if len(content) > self._fetch_max_chars:
                content = content[:self._fetch_max_chars] + f"\n[truncated at {self._fetch_max_chars} chars]"
            return content
        except Exception as exc:
            return f"[web_fetch error: {exc}]"
