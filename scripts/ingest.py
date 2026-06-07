#!/usr/bin/env python3
"""CLI document ingestion script for LoCAL2 RAG knowledge base.

Usage:
  python scripts/ingest.py --collection mba file.pdf [file2.txt ...]
  python scripts/ingest.py --list
  python scripts/ingest.py --list --collection mba
  python scripts/ingest.py --delete filename.pdf --collection mba

Runs without the full stack (no ZMQ, no Ollama chat endpoint).
Requires: nomic-embed-text pulled in Ollama (ollama pull nomic-embed-text).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from local.services.document_service import DocumentService


def cmd_ingest(paths: list[str], collection: str, svc: DocumentService) -> None:
    for path in paths:
        p = Path(path)
        if not p.exists():
            print(f"  ERROR: file not found: {path}")
            continue
        try:
            n = svc.ingest_file(str(p), collection)
            print(f"  {p.name}: {n} chunks ingested into '{collection}'")
        except ValueError as exc:
            print(f"  ERROR: {exc}")
        except Exception as exc:
            print(f"  ERROR ingesting {p.name}: {exc}")


def cmd_list(svc: DocumentService, collection: str | None) -> None:
    if collection:
        sources = svc.list_sources_detail(collection)
        if not sources:
            print(f"  Collection '{collection}' is empty.")
            return
        total = sum(s["chunk_count"] for s in sources)
        print(f"  '{collection}': {len(sources)} source(s), {total} chunks:\n")
        for s in sources:
            print(f"    {s['source_file']}  ({s['chunk_count']} chunks)")
    else:
        collections = svc.list_collections()
        if not collections:
            print("  No collections configured (see config/documents.yaml).")
            return
        for col in collections:
            print(f"  [{col['display_name']}]  {col['source_count']} sources, "
                  f"{col['chunk_count']} chunks")
            if col["source_count"]:
                for s in svc.list_sources_detail(col["name"]):
                    print(f"    {s['source_file']}  ({s['chunk_count']} chunks)")


def cmd_delete(source_file: str, collection: str, svc: DocumentService) -> None:
    n = svc.delete_source(source_file, collection)
    if n:
        print(f"  Deleted {n} chunks for '{source_file}' from '{collection}'")
    else:
        print(f"  '{source_file}' not found in collection '{collection}'")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest documents into the LoCAL2 RAG knowledge base"
    )
    parser.add_argument("files", nargs="*", help="Files to ingest")
    parser.add_argument("--collection", metavar="NAME",
                        help="Collection name (required for ingest and delete)")
    parser.add_argument("--list", action="store_true",
                        help="List sources (all collections, or --collection for one)")
    parser.add_argument("--delete", metavar="FILENAME",
                        help="Delete all chunks for a source file (requires --collection)")
    args = parser.parse_args()

    svc = DocumentService()

    if args.list:
        cmd_list(svc, args.collection)
    elif args.delete:
        if not args.collection:
            print("ERROR: --delete requires --collection")
            sys.exit(1)
        cmd_delete(args.delete, args.collection, svc)
    elif args.files:
        if not args.collection:
            print("ERROR: ingestion requires --collection NAME")
            sys.exit(1)
        cmd_ingest(args.files, args.collection, svc)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
