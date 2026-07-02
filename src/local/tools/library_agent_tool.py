"""LibraryAgentTool — LLM-powered librarian callable by Gemma via consult_librarian.

Gemma delegates library management tasks (list, add, delete, reorganise) to this
agent in a single tool call. The librarian handles categorisation, collection creation,
and async ingestion — the conversation is never blocked by embedding.

Wire format:
  Call:   tool.call.consult_librarian  {instruction, filename?}
  Result: tool.result.consult_librarian {tool, result, sources}
  Async:  library.ingest.complete       {filename, collection, chunk_count, error}
"""
from __future__ import annotations

import json
import logging
import re
import threading
import uuid

import ollama

from local.config_loader import get_config
from local.data_dir import get_data_dir
from local.protocol.envelope import MessageEnvelope
from local.protocol.messages import LibraryCollectionCreated, LibraryIngestComplete, LibraryIngestStarted
from local.protocol.subjects import (
    TOOL_ACTIVITY_CONSULT_LIBRARIAN,
    TOOL_CALL_CONSULT_LIBRARIAN,
    TOOL_RESULT_CONSULT_LIBRARIAN,
)
from local.services.document_service import DocumentService
from local.tools.base_tool import BaseTool

logger = logging.getLogger(__name__)

TOOL_NAME = "consult_librarian"


class LibraryAgentTool(BaseTool):
    TOOL_NAME = TOOL_NAME
    ACTIVITY_SUBJECT = TOOL_ACTIVITY_CONSULT_LIBRARIAN
    RESULT_SUBJECT = TOOL_RESULT_CONSULT_LIBRARIAN
    CONFIG_NAME = "librarian"

    def __init__(self, document_service: DocumentService | None = None) -> None:
        self._docs = document_service or DocumentService()
        self._clear_uploads()
        super().__init__(TOOL_CALL_CONSULT_LIBRARIAN)

    def _clear_uploads(self) -> None:
        uploads_dir = get_data_dir() / "uploads"
        if uploads_dir.exists():
            for f in uploads_dir.iterdir():
                try:
                    f.unlink()
                except OSError:
                    pass

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _build_schema(self) -> dict:
        cfg = get_config("librarian") or {}
        return {
            "type": "function",
            "function": {
                "name": TOOL_NAME,
                "description": (cfg.get("description") or "").strip(),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["search", "list", "add", "delete", "reorganise"],
                            "description": (cfg.get("param_action") or "").strip(),
                        },
                        "instruction": {
                            "type": "string",
                            "description": (cfg.get("param_instruction") or "").strip(),
                        },
                        "filename": {
                            "type": "string",
                            "description": (cfg.get("param_filename") or "").strip(),
                        },
                        "collection": {
                            "type": "string",
                            "description": (cfg.get("param_collection") or "").strip(),
                        },
                    },
                    "required": ["action", "instruction"],
                },
            },
        }

    # ------------------------------------------------------------------
    # Request handler
    # ------------------------------------------------------------------

    def _handle_request(self, envelope: MessageEnvelope) -> None:
        args = envelope.payload.get("args") or {}
        action: str = args.get("action", "search")
        instruction: str = args.get("instruction", "")
        filename: str = args.get("filename", "")
        collection: str = args.get("collection", "")
        correlation_id = envelope.correlation_id or str(uuid.uuid4())

        self._publish_activity(
            "request",
            {"action": action, "instruction": instruction, "filename": filename},
            correlation_id=correlation_id,
        )

        sources: list = []
        if action == "list":
            result = self._handle_list()
        elif action == "delete":
            result = self._handle_delete(filename)
        elif action == "add":
            result = self._handle_add(filename, correlation_id)
        elif action == "reorganise":
            result = self._handle_reorganise(instruction, correlation_id)
        else:
            result, sources = self._handle_search(instruction, collection or None)

        self._publish_activity(
            "result",
            {"result": result, "instruction": instruction},
            correlation_id=correlation_id,
        )
        self._publish_result(result, correlation_id, sources=sources or None)

    # ------------------------------------------------------------------
    # List
    # ------------------------------------------------------------------

    def _handle_list(self) -> str:
        collections = self._docs.list_collections()
        if not collections:
            return "The library is empty — no collections have been created yet."
        lines = ["**Library collections:**\n"]
        for col in collections:
            col_name = col.get("name", "")
            display = col.get("display_name") or col_name
            desc = col.get("description", "")
            chunks = col.get("chunk_count", 0)
            files = self._docs.list_sources(col_name)
            file_str = ", ".join(files) if files else "(empty)"
            lines.append(f"- **{display}**: {desc}")
            lines.append(f"  Files: {file_str} ({chunks} chunks)")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def _handle_search(self, instruction: str, collection: str | None = None) -> tuple[str, list]:
        cfg = get_config("librarian") or {}
        raw = self._docs.search(instruction, collection=collection, n=cfg.get("n_results", 8))
        if not raw:
            return "No relevant documents found in the library.", []

        sources = [
            {
                "type": "library",
                "source_file": r.get("source_file", ""),
                "chunk_index": r.get("chunk_index", 0),
                "page": r.get("page"),
                "score": r.get("score"),
                "snippet": r.get("content", "")[:200],
                "collection": r.get("collection", ""),
            }
            for r in raw
        ]

        passages = "\n\n".join(
            f"[{r.get('source_file', '')} p.{r.get('page') or r.get('chunk_index', '')}]\n{r.get('content', '')}"
            for r in raw[:5]
        )
        result = f"Library search results:\n\n{passages}"
        return result, sources

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    def _handle_delete(self, filename: str) -> str:
        if not filename:
            return "Please specify the filename to delete, e.g. 'delete OrganizationalBehavior.pdf'."

        collections = self._docs.list_collections()
        total_deleted = 0
        deleted_from = []
        for col in collections:
            col_name = col.get("name", "")
            count = self._docs.delete_source(filename, col_name)
            if count:
                total_deleted += count
                deleted_from.append(col.get("display_name") or col_name)

        if total_deleted:
            return f"Deleted {total_deleted} chunk(s) for **{filename}** from: {', '.join(deleted_from)}."
        return f"No chunks found for **{filename}** in any collection."

    # ------------------------------------------------------------------
    # Add
    # ------------------------------------------------------------------

    def _handle_add(self, filename: str, correlation_id: str) -> str:
        if not filename:
            return (
                "Please attach a file and include its filename in the instruction, "
                "e.g. 'add paper.pdf to the library'."
            )

        upload_path = get_data_dir() / "uploads" / filename
        if not upload_path.exists():
            uploads_dir = get_data_dir() / "uploads"
            available = [f.name for f in uploads_dir.iterdir()] if uploads_dir.exists() else []
            if available:
                return (
                    f"File **{filename}** not found in uploads. "
                    f"Available: {', '.join(available)}. Please check the filename and re-upload if needed."
                )
            return f"No uploaded files found. Please attach a file using the 📎 button first."

        collection_name, is_new, description = self._pick_collection(filename)

        if is_new:
            self._docs.create_collection(collection_name, description)
            self._pub.publish(
                LibraryCollectionCreated(name=collection_name, description=description),
                sender_id=self.id,
                correlation_id=correlation_id,
            )
            logger.info("LibraryAgentTool: created collection %s", collection_name)

        display_name = collection_name.replace("_", " ").title()

        def _ingest() -> None:
            with self._thread_publisher() as pub:
                pub.publish(
                    LibraryIngestStarted(filename=filename, collection=collection_name),
                    sender_id=self.id,
                    correlation_id=correlation_id,
                )
                try:
                    chunks = self._docs.ingest_file(str(upload_path), collection_name)
                    try:
                        upload_path.unlink()
                    except OSError:
                        pass
                    pub.publish(
                        LibraryIngestComplete(
                            filename=filename,
                            collection=collection_name,
                            chunk_count=chunks,
                        ),
                        sender_id=self.id,
                        correlation_id=correlation_id,
                    )
                    logger.info(
                        "LibraryAgentTool: ingested %s → %s (%d chunks)",
                        filename, collection_name, chunks,
                    )
                except Exception as exc:
                    logger.error("LibraryAgentTool: ingest failed for %s: %s", filename, exc)
                    pub.publish(
                        LibraryIngestComplete(
                            filename=filename,
                            collection=collection_name,
                            chunk_count=0,
                            error=str(exc),
                        ),
                        sender_id=self.id,
                        correlation_id=correlation_id,
                    )

        t = threading.Thread(target=_ingest, daemon=True, name=f"ingest-{filename}")
        t.start()

        return json.dumps({
            "status": "processing",
            "file": filename,
            "collection": display_name,
            "message": "Ingestion started in background. File is not yet available in the library.",
        })

    def _call_llm(self, prompt: str, num_ctx_key: str, cfg: dict | None = None) -> str:
        if cfg is None:
            cfg = get_config("librarian") or {}
        try:
            response = ollama.chat(
                model=cfg.get("model", ""),
                messages=[{"role": "user", "content": prompt}],
                stream=False,
                options={
                    "temperature": cfg.get("temperature", 0.0),
                    "num_ctx": cfg.get(num_ctx_key, 512),
                },
            )
            return (response.message.content or "").strip()
        except Exception as exc:
            logger.warning("LibraryAgentTool: LLM call failed: %s", exc)
            return ""

    def _pick_collection(self, filename: str) -> tuple[str, bool, str]:
        collections = self._docs.list_collections()

        if not collections:
            collection_name = self._slugify(filename.rsplit(".", 1)[0])
            return collection_name, True, f"Documents related to {filename}"

        cfg = get_config("librarian") or {}
        col_summary = "\n".join(
            f"- {c['name']}: {c.get('description', '')}" for c in collections
        )
        prompt = (cfg.get("prompt_categorise") or "").format(
            col_summary=col_summary, filename=filename
        )
        reply = self._call_llm(prompt, "num_ctx_categorise", cfg=cfg)

        if reply.startswith("EXISTING:"):
            name = reply.split(":", 1)[1].strip()
            if any(c["name"] == name for c in collections):
                return name, False, ""
        if reply.startswith("NEW:"):
            rest = reply.split(":", 1)[1].strip()
            if "|" in rest:
                name_part, desc_part = rest.split("|", 1)
                name = self._slugify(name_part.strip())
                desc = desc_part.strip()
                return name, True, desc

        # Fallback: use first collection or derive from filename
        if collections:
            return collections[0]["name"], False, ""
        name = self._slugify(filename.rsplit(".", 1)[0])
        return name, True, f"Documents related to {filename}"

    @staticmethod
    def _slugify(s: str) -> str:
        s = s.lower().strip()
        s = re.sub(r"[^a-z0-9]+", "_", s)
        return s.strip("_") or "general"

    # ------------------------------------------------------------------
    # Reorganise
    # ------------------------------------------------------------------

    def _handle_reorganise(self, instruction: str, correlation_id: str) -> str:
        collections = self._docs.list_collections()
        if not collections:
            return "The library is empty — nothing to reorganise."

        cfg = get_config("librarian") or {}
        col_lines = []
        for col in collections:
            sources = self._docs.list_sources(col["name"])
            col_lines.append(
                f"- {col['name']} ({col.get('description','')}): {', '.join(sources) or '(empty)'}"
            )
        col_summary = "\n".join(col_lines)

        prompt = (cfg.get("prompt_reorganise") or "").format(
            instruction=instruction, col_summary=col_summary
        )
        raw = self._call_llm(prompt, "num_ctx_reorganise", cfg=cfg)
        if not raw:
            return "Sorry, I couldn't plan the reorganisation right now."

        try:
            plan = json.loads(raw)
        except json.JSONDecodeError:
            return f"Sorry, the reorganisation plan was not valid JSON.\n\nResponse:\n{raw}"

        new_collections = plan.get("new_collections") or []
        moves = plan.get("moves") or []

        if not moves:
            return "No changes needed based on the current structure."

        for col in new_collections:
            name, desc = col.get("name", ""), col.get("description", "")
            if name:
                self._docs.create_collection(name, desc)
                self._pub.publish(
                    LibraryCollectionCreated(name=name, description=desc),
                    sender_id=self.id,
                    correlation_id=correlation_id,
                )

        results = []
        for move in moves:
            filename = move.get("file", "")
            from_col = move.get("from", "")
            to_col = move.get("to", "")
            try:
                moved = self._docs.move_source(filename, from_col, to_col)
                results.append(f"Moved **{filename}** → **{to_col}** ({moved} chunks re-embedded)")
            except Exception as exc:
                results.append(f"Failed to move **{filename}**: {exc}")

        return "\n".join(results)
