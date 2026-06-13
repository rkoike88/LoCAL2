"""SemanticScholarTool — searches the Semantic Scholar Graph API for academic papers.

Free API, no key required for basic use (100 req/5min rate limit).
Optional API key via SEMANTIC_SCHOLAR_API_KEY env var for higher limits.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import date

import httpx

from local.config_loader import get_config
from local.protocol.envelope import MessageEnvelope
from local.protocol.subjects import (
    TOOL_ACTIVITY_SEARCH_PAPERS,
    TOOL_CALL_SEARCH_PAPERS,
    TOOL_RESULT_SEARCH_PAPERS,
)
from local.tools.base_tool import BaseTool

logger = logging.getLogger(__name__)

_API_BASE = "https://api.semanticscholar.org/graph/v1/paper/search"
_DEFAULT_FIELDS = "title,authors,year,abstract,citationCount,url,externalIds"

# Rate limiter — Semantic Scholar unauthenticated limit is ~1 req/sec.
# Enforce minimum gap between requests to avoid 429s from rapid consecutive calls.
_rate_lock = threading.Lock()
_last_request_at: float = 0.0


def _throttled_get(params: dict, headers: dict, timeout: float, min_gap: float) -> httpx.Response:
    global _last_request_at
    with _rate_lock:
        elapsed = time.monotonic() - _last_request_at
        if elapsed < min_gap:
            time.sleep(min_gap - elapsed)
        resp = httpx.get(_API_BASE, params=params, headers=headers, timeout=timeout)
        _last_request_at = time.monotonic()
    # Retry once on 429 after a brief pause (e.g. dual-respondent back-to-back calls)
    if resp.status_code == 429:
        time.sleep(2.0)
        with _rate_lock:
            resp = httpx.get(_API_BASE, params=params, headers=headers, timeout=timeout)
            _last_request_at = time.monotonic()
    resp.raise_for_status()
    return resp


def _search_papers(query: str, limit: int) -> str:
    cfg = get_config("semantic_scholar") or {}
    max_results = cfg.get("max_results", 5)
    timeout = cfg.get("timeout", 15)
    fields = cfg.get("fields", _DEFAULT_FIELDS)
    abstract_max = cfg.get("abstract_max_chars", 300)
    min_gap = cfg.get("min_request_gap", 1.2)

    limit = max(1, min(limit, max_results))

    headers = {}
    api_key = os.environ.get("SEMANTIC_SCHOLAR_API_KEY", "")
    if api_key:
        headers["x-api-key"] = api_key

    resp = _throttled_get(
        params={"query": query, "limit": limit, "fields": fields},
        headers=headers,
        timeout=timeout,
        min_gap=min_gap,
    )
    data = resp.json()

    papers = data.get("data", [])
    if not papers:
        return f'No papers found for "{query}".'

    today = date.today().isoformat()
    lines = [f'[{today}] Papers: "{query}"\n']
    for i, paper in enumerate(papers, 1):
        title = paper.get("title") or "Untitled"
        year = paper.get("year") or "n.d."
        authors = paper.get("authors") or []
        author_str = ", ".join(a.get("name", "") for a in authors[:3])
        if len(authors) > 3:
            author_str += " et al."
        citations = paper.get("citationCount")
        citation_str = f"Citations: {citations:,}" if citations is not None else ""
        url = paper.get("url") or ""
        arxiv_id = (paper.get("externalIds") or {}).get("ArXiv")
        arxiv_url = f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else ""
        abstract = (paper.get("abstract") or "").strip()
        if len(abstract) > abstract_max:
            abstract = abstract[:abstract_max].rstrip() + "…"

        lines.append(f"{i}. {title} ({year}) — {author_str}")
        meta_parts = [citation_str, arxiv_url or url]
        meta = "   " + "  |  ".join(filter(None, meta_parts))
        if meta.strip():
            lines.append(meta)
        if abstract:
            lines.append(f"   {abstract}")
        lines.append("")

    return "\n".join(lines).rstrip()


class SemanticScholarTool(BaseTool):
    CONFIG_NAME = "semantic_scholar"
    TOOL_NAME = "search_papers"
    ACTIVITY_SUBJECT = TOOL_ACTIVITY_SEARCH_PAPERS
    RESULT_SUBJECT = TOOL_RESULT_SEARCH_PAPERS

    def __init__(self) -> None:
        super().__init__(TOOL_CALL_SEARCH_PAPERS)

    def _build_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.TOOL_NAME,
                "description": (
                    "Searches the Semantic Scholar academic paper database and returns ranked "
                    "results with titles, authors, years, citation counts, abstracts, and URLs. "
                    "Call this tool for any question about research papers, scientific studies, "
                    "academic literature, or when the user asks to find papers on a topic. "
                    "Do not guess at citations or paper details — always call this tool."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Research topic, keywords, or paper title to search for.",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of papers to return (default 5, max 10).",
                        },
                    },
                    "required": ["query"],
                },
            },
        }

    def _handle_request(self, envelope: MessageEnvelope) -> None:
        args = envelope.payload.get("args") or {}
        query = args.get("query", "")
        limit = int(args.get("limit") or 5)
        correlation_id = envelope.correlation_id

        self._publish_activity("request", {"query": query, "limit": limit}, correlation_id)

        try:
            result = _search_papers(query, limit)
        except Exception as exc:
            logger.warning("SemanticScholarTool: search failed: %s", exc)
            result = f"Search failed: {exc}"

        self._publish_activity("result", {"result": result}, correlation_id)
        self._publish_result(result, correlation_id)


if __name__ == "__main__":
    SemanticScholarTool().run()
