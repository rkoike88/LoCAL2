"""Ingest prometheus-eval/Feedback-Collection into the harness prompts table.

Usage:
    # From local JSON file (fast):
    python -m harness.ingest --file ~/LoCAL2/new_feedback_collection.json
    python -m harness.ingest --file ~/LoCAL2/new_feedback_collection.json --max 500
    python -m harness.ingest --file ~/LoCAL2/new_feedback_collection.json --filter "code"

    # Stream from HuggingFace (slow, ~5 min first run):
    python -m harness.ingest
    python -m harness.ingest --max 500
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import uuid
from pathlib import Path
from typing import Iterator

import yaml

from harness import db

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).parent / "config.yaml"


def _build_score_rubric(item: dict) -> str:
    criteria = item.get("orig_criteria", "")
    lines = [f"[{criteria}]"]
    for i in range(1, 6):
        desc = (item.get(f"orig_score{i}_description") or "").strip()
        if desc:
            lines.append(f"Score {i}: {desc}")
    return "\n".join(lines)


def _iter_local(file_path: Path) -> Iterator[tuple[int, dict]]:
    """Iterate a local JSON file (list of dicts)."""
    logger.info("Loading %s …", file_path)
    with open(file_path, encoding="utf-8") as f:
        data = json.load(f)
    logger.info("Loaded %d rows from local file.", len(data))
    yield from enumerate(data)


def _iter_hf() -> Iterator[tuple[int, dict]]:
    """Stream from HuggingFace (requires datasets library)."""
    from datasets import load_dataset  # noqa: PLC0415
    logger.info("Streaming prometheus-eval/Feedback-Collection from HuggingFace…")
    ds = load_dataset("prometheus-eval/Feedback-Collection", split="train", streaming=True)
    yield from enumerate(ds)


def ingest(
    db_path: str,
    file_path: Path | None = None,
    max_prompts: int | None = None,
    filter_keyword: str | None = None,
) -> None:
    db.init_db(db_path)

    source = _iter_local(file_path) if file_path else _iter_hf()

    seen: set[str] = set()
    ingested = skipped_dup = skipped_filter = 0

    for idx, item in source:
        instruction = (item.get("orig_instruction") or "").strip()
        if not instruction:
            continue

        key = hashlib.md5(instruction.encode()).hexdigest()
        if key in seen:
            skipped_dup += 1
            continue
        seen.add(key)

        criteria = (item.get("orig_criteria") or "").strip()
        if filter_keyword and filter_keyword.lower() not in criteria.lower():
            skipped_filter += 1
            continue

        reference_answer = (item.get("orig_reference_answer") or "").strip()
        score_rubric = _build_score_rubric(item)

        db.insert_prompt(
            prompt_id=str(uuid.uuid4()),
            source_idx=idx,
            instruction=instruction,
            reference_answer=reference_answer,
            criteria=criteria,
            score_rubric=score_rubric,
        )
        ingested += 1

        if ingested % 1000 == 0:
            logger.info("  %d unique prompts ingested (row %d, %d dups skipped)", ingested, idx, skipped_dup)

        if max_prompts and ingested >= max_prompts:
            break

    logger.info("Done. ingested=%d  dups_skipped=%d  filter_skipped=%d", ingested, skipped_dup, skipped_filter)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    cfg = yaml.safe_load(_CONFIG_PATH.read_text())

    ap = argparse.ArgumentParser(description="Ingest Feedback-Collection into harness DB")
    ap.add_argument("--db", default=cfg.get("db_path", "harness.db"))
    ap.add_argument("--file", default=None, help="Path to local new_feedback_collection.json")
    ap.add_argument("--max", type=int, default=None, help="Max unique prompts to ingest")
    ap.add_argument("--filter", default=None, help="Keyword filter on orig_criteria")
    args = ap.parse_args()

    file_path = Path(args.file).expanduser() if args.file else None
    ingest(args.db, file_path=file_path, max_prompts=args.max, filter_keyword=args.filter)


if __name__ == "__main__":
    main()
