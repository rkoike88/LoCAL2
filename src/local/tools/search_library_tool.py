"""SearchLibraryTool — semantic search over the user's persistent document library."""
from __future__ import annotations

import logging

from local.config_loader import ConfigManager, get_config
from local.protocol.envelope import MessageEnvelope
from local.protocol.subjects import (
    TOOL_ACTIVITY_SEARCH_DOCUMENTS,
    TOOL_REQUEST_SEARCH_DOCUMENTS,
    TOOL_RESULT_SEARCH_DOCUMENTS,
    TOOL_SCHEMA,
    TOOL_SCHEMA_REQUEST,
)
from local.services.document_service import DocumentService
from local.transport.bus_config import make_participant_bus

logger = logging.getLogger(__name__)

CONFIG_NAME = "documents"
TOOL_NAME = "search_library"


class SearchLibraryTool:
    TOOL_ID = "search_library_tool"

    def __init__(self, document_service: DocumentService | None = None) -> None:
        self._docs = document_service or DocumentService()
        self._pub, self._sub = make_participant_bus(
            [TOOL_REQUEST_SEARCH_DOCUMENTS, TOOL_SCHEMA_REQUEST]
        )

    def run(self) -> None:
        self._announce_schema()
        print("[search_library_tool] ready")
        while True:
            try:
                envelope = self._sub.receive()
            except Exception as exc:
                logger.error("SearchLibraryTool: receive error: %s", exc)
                continue
            if envelope.subject == TOOL_SCHEMA_REQUEST:
                ConfigManager.invalidate(CONFIG_NAME)
                self._announce_schema()
            elif envelope.subject == TOOL_REQUEST_SEARCH_DOCUMENTS:
                self._handle_request(envelope)

    def _build_schema(self) -> dict:
        cfg = get_config(CONFIG_NAME) or {}
        topic = cfg.get("topic", "").strip()

        if topic:
            description = (
                f"Searches the user's personal library of {topic}. "
                f"Call this tool when the user asks about {topic}. "
                f"Do not use this for general web searches or academic papers — "
                f"use web_search or search_papers for those."
            )
        else:
            description = (
                "Searches the user's personal document library. "
                "Call this when the user asks about content from documents they have added. "
                "Do not use this for general research — use web_search or search_papers for those."
            )

        param_query = cfg.get("param_query",
            "What to look for in the library. Use specific terms likely to appear "
            "in the source documents."
        ).strip()

        return {
            "type": "function",
            "function": {
                "name": TOOL_NAME,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": param_query},
                    },
                    "required": ["query"],
                },
            },
        }

    def _announce_schema(self) -> None:
        self._pub.publish(MessageEnvelope.create(
            message_type="tool_schema",
            subject=TOOL_SCHEMA,
            sender_id=self.TOOL_ID,
            payload={"schema": self._build_schema()},
        ))

    def _handle_request(self, envelope: MessageEnvelope) -> None:
        args: dict = envelope.payload.get("args", {})
        query: str = (args.get("query") or "").strip()
        correlation_id = envelope.correlation_id

        self._pub.publish(MessageEnvelope.create(
            message_type="tool_activity",
            subject=TOOL_ACTIVITY_SEARCH_DOCUMENTS,
            sender_id=self.TOOL_ID,
            payload={"event": "request", "tool": TOOL_NAME, "query": query},
            correlation_id=correlation_id,
        ))

        try:
            result = self._search(query)
        except Exception as exc:
            logger.error("SearchLibraryTool: search failed: %s", exc)
            result = f"[search_library error: {exc}]"

        self._pub.publish(MessageEnvelope.create(
            message_type="tool_activity",
            subject=TOOL_ACTIVITY_SEARCH_DOCUMENTS,
            sender_id=self.TOOL_ID,
            payload={"event": "result", "tool": TOOL_NAME, "result": result},
            correlation_id=correlation_id,
        ))
        self._pub.publish(MessageEnvelope.create(
            message_type="tool_result",
            subject=TOOL_RESULT_SEARCH_DOCUMENTS,
            sender_id=self.TOOL_ID,
            payload={"result": result, "tool": TOOL_NAME},
            correlation_id=correlation_id,
        ))

    def _search(self, query: str) -> str:
        if not query:
            return "[search_library: query is required]"
        if self._docs.count() == 0:
            return "[Library is empty — add documents via the library window or: python scripts/ingest.py <file>]"

        hits = self._docs.search(query)
        if not hits:
            return "[No relevant passages found in the library]"

        cfg = get_config(CONFIG_NAME) or {}
        topic = cfg.get("topic", "library")
        lines = [f'[Library results for "{query}"]\n']
        for i, hit in enumerate(hits, 1):
            source = hit["source_file"]
            page = f"p.{hit['page']}, " if "page" in hit else ""
            lines.append(f"{i}. {source} ({page}chunk {hit['chunk_index']})")
            lines.append(f"   {hit['content'].strip()}")
            lines.append("")
        return "\n".join(lines).rstrip()
