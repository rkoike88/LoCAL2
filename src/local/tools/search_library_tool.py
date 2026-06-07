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
            # Build enum description: "name: description; name2: description2"
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
                "name": TOOL_NAME,
                "description": description,
                "parameters": parameters,
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
        collection: str | None = args.get("collection") or None
        correlation_id = envelope.correlation_id

        self._pub.publish(MessageEnvelope.create(
            message_type="tool_activity",
            subject=TOOL_ACTIVITY_SEARCH_DOCUMENTS,
            sender_id=self.TOOL_ID,
            payload={"event": "request", "tool": TOOL_NAME, "query": query,
                     "collection": collection},
            correlation_id=correlation_id,
        ))

        try:
            result = self._search(query, collection)
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

    def _search(self, query: str, collection: str | None = None) -> str:
        if not query:
            return "[search_library: query is required]"
        if self._docs.count() == 0:
            return "[Library is empty — add documents via the library window]"

        hits = self._docs.search(query, collection=collection)
        if not hits:
            return "[No relevant passages found in the library]"

        # Label header with collection scope
        cfg = get_config(CONFIG_NAME) or {}
        collections_cfg = cfg.get("collections") or []
        col_display = collection or "library"
        for col in collections_cfg:
            if col.get("name") == collection:
                col_display = col.get("display_name", collection)
                break

        lines = [f'[{col_display} — results for "{query}"]\n']
        for i, hit in enumerate(hits, 1):
            source = hit["source_file"]
            page = f"p.{hit['page']}, " if "page" in hit else ""
            lines.append(f"{i}. {source} ({page}chunk {hit['chunk_index']})")
            lines.append(f"   {hit['content'].strip()}")
            lines.append("")
        return "\n".join(lines).rstrip()
