#!/usr/bin/env python3
"""CLI document ingestion script for LoCAL2 RAG knowledge base.

Usage:
  python scripts/ingest.py file.pdf [file2.txt ...]
  python scripts/ingest.py --list
  python scripts/ingest.py --delete filename.pdf

Runs without the full stack (no ZMQ, no Ollama chat endpoint).
Requires: nomic-embed-text pulled in Ollama (ollama pull nomic-embed-text).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from local.services.document_service import DocumentService


def cmd_ingest(paths: list[str], svc: DocumentService) -> None:
    for path in paths:
        p = Path(path)
        if not p.exists():
            print(f"  ERROR: file not found: {path}")
            continue
        try:
            n = svc.ingest_file(str(p))
            print(f"  {p.name}: {n} chunks ingested")
        except ValueError as exc:
            print(f"  ERROR: {exc}")
        except Exception as exc:
            print(f"  ERROR ingesting {p.name}: {exc}")


def cmd_list(svc: DocumentService) -> None:
    sources = svc.list_sources()
    if not sources:
        print("  Knowledge base is empty.")
        return
    total = svc.count()
    print(f"  {len(sources)} source(s), {total} total chunks:\n")
    for name in sources:
        print(f"    {name}")


def cmd_delete(source_file: str, svc: DocumentService) -> None:
    n = svc.delete_source(source_file)
    if n:
        print(f"  Deleted {n} chunks for '{source_file}'")
    else:
        print(f"  '{source_file}' not found in knowledge base")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest documents into the LoCAL2 RAG knowledge base"
    )
    parser.add_argument("files", nargs="*", help="Files to ingest (PDF, txt, md, …)")
    parser.add_argument("--list", action="store_true", help="List ingested sources")
    parser.add_argument("--delete", metavar="FILENAME", help="Delete all chunks for a source file")
    args = parser.parse_args()

    svc = DocumentService()

    if args.list:
        cmd_list(svc)
    elif args.delete:
        cmd_delete(args.delete, svc)
    elif args.files:
        cmd_ingest(args.files, svc)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
