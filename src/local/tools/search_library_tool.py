"""SearchLibraryTool — semantic search over the user's persistent document library."""
from __future__ import annotations

import logging

from local.config_loader import get_config
from local.protocol.envelope import MessageEnvelope
from local.protocol.subjects import (
    TOOL_ACTIVITY_SEARCH_DOCUMENTS,
    TOOL_CALL_SEARCH_DOCUMENTS,
    TOOL_RESULT_SEARCH_DOCUMENTS,
)
from local.services.document_service import DocumentService
from local.tools.base_tool import BaseTool

logger = logging.getLogger(__name__)

CONFIG_NAME = "documents"
TOOL_NAME = "search_library"


class SearchLibraryTool(BaseTool):
    TOOL_NAME = TOOL_NAME
    ACTIVITY_SUBJECT = TOOL_ACTIVITY_SEARCH_DOCUMENTS
    RESULT_SUBJECT = TOOL_RESULT_SEARCH_DOCUMENTS
    CONFIG_NAME = CONFIG_NAME

    def __init__(self, document_service: DocumentService | None = None) -> None:
        self._docs = document_service or DocumentService()
        super().__init__(TOOL_CALL_SEARCH_DOCUMENTS)

    def _build_schema(self) -> dict:
        cfg = get_config(CONFIG_NAME) or {}
        collections = cfg.get("collections") or []

        if len(collections) == 0:
            description = (
                "Searches the user's personal document library. "
                "Call this when the user asks about content from documents they have added. "
                "Do not use this for general research — use web_search or search_papers for those."
            )
            parameters: dict = {
                "type": "object",
                "properties": {
                    "query": {"type": "string",
                              "description": "What to look for. Use specific terms likely to appear in the documents."},
                },
                "required": ["query"],
            }

        elif len(collections) == 1:
            col = collections[0]
            desc = col.get("description", "")
            display = col.get("display_name", col.get("name", ""))
            description = (
                f"Search {display}: {desc} "
                f"Call this tool when the user asks about {display}. "
                f"Do not use this for general web searches or academic papers — "
                f"use web_search or search_papers for those."
            )
            parameters = {
                "type": "object",
                "properties": {
                    "query": {"type": "string",
                              "description": "What to look for. Use specific terms likely to appear in the documents."},
                },
                "required": ["query"],
            }

        else:
            enum_desc_parts = []
            for col in collections:
                name = col.get("name", "")
                desc = col.get("description", "")
                display = col.get("display_name", name)
                enum_desc_parts.append(f"{name}: {display} — {desc}")

            description = (
                "Search the document library. Choose the collection that best matches your query."
            )
            parameters = {
                "type": "object",
                "properties": {
                    "query": {"type": "string",
                              "description": "What to look for. Use specific terms likely to appear in the documents."},
                    "collection": {
                        "type": "string",
                        "enum": [col.get("name", "") for col in collections],
                        "description": " | ".join(enum_desc_parts),
                    },
                },
                "required": ["query"],
            }

        return {
            "type": "function",
            "function": {
                "name": self.TOOL_NAME,
                "description": description,
                "parameters": parameters,
            },
        }

    def _handle_request(self, envelope: MessageEnvelope) -> None:
        args: dict = envelope.payload.get("args", {})
        query: str = (args.get("query") or "").strip()
        collection: str | None = args.get("collection") or None
        correlation_id = envelope.correlation_id

        self._publish_activity("request", {"query": query, "collection": collection}, correlation_id)

        try:
            result, sources = self._search_with_sources(query, collection)
        except Exception as exc:
            logger.error("SearchLibraryTool: search failed: %s", exc)
            result, sources = f"[search_library error: {exc}]", []

        self._publish_activity("result", {"result": result, "sources": sources}, correlation_id)
        self._publish_result(result, correlation_id, sources=sources)

    def _search(self, query: str, collection: str | None = None) -> str:
        result, _ = self._search_with_sources(query, collection)
        return result

    def _search_with_sources(self, query: str, collection: str | None = None) -> tuple[str, list[dict]]:
        if not query:
            return "[search_library: query is required]", []
        if self._docs.count() == 0:
            return "[Library is empty — add documents via the library window]", []

        hits = self._docs.search(query, collection=collection)
        if not hits:
            return "[No relevant passages found in the library]", []

        cfg = get_config(CONFIG_NAME) or {}
        collections_cfg = cfg.get("collections") or []
        col_display = collection or "library"
        for col in collections_cfg:
            if col.get("name") == collection:
                col_display = col.get("display_name", collection)
                break

        sources = [
            {
                "type": "library",
                "source_file": h["source_file"],
                "chunk_index": h.get("chunk_index"),
                "page": h.get("page"),
                "snippet": (h["content"] or "").strip()[:80],
            }
            for h in hits
        ]

        lines = [f'[{col_display} — results for "{query}"]\n']
        for i, hit in enumerate(hits, 1):
            source = hit["source_file"]
            page = f"p.{hit['page']}, " if "page" in hit else ""
            lines.append(f"{i}. {source} ({page}chunk {hit['chunk_index']})")
            lines.append(f"   {hit['content'].strip()}")
            lines.append("")
        return "\n".join(lines).rstrip(), sources
