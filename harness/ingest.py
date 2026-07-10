"""Ingest prometheus-eval/Preference-Collection into the harness prompts table.

The Preference Collection is designed for pairwise evaluation. Each row has:
  orig_instruction, orig_reference_answer, orig_criteria, orig_preference (A/B winner).

Usage:
    python -m harness.ingest --max 5000
    python -m harness.ingest --max 500 --filter "emotional"
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


def _iter_hf() -> Iterator[tuple[int, dict]]:
    """Stream Preference-Collection from HuggingFace (requires datasets library)."""
    from datasets import load_dataset  # noqa: PLC0415
    logger.info("Streaming prometheus-eval/Preference-Collection from HuggingFace…")
    ds = load_dataset("prometheus-eval/Preference-Collection", split="train", streaming=True)
    yield from enumerate(ds)


def ingest(
    db_path: str,
    max_prompts: int | None = None,
    filter_keyword: str | None = None,
) -> None:
    db.init_db(db_path)

    source = _iter_hf()

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

        db.insert_prompt(
            prompt_id=str(uuid.uuid4()),
            source_idx=idx,
            instruction=instruction,
            reference_answer=reference_answer,
            criteria=criteria,
            score_rubric=criteria,  # pairwise uses short criterion only
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

    ap = argparse.ArgumentParser(description="Ingest Preference-Collection into harness DB")
    ap.add_argument("--db", default=cfg.get("db_path", "harness.db"))
    ap.add_argument("--max", type=int, default=None, help="Max unique prompts to ingest")
    ap.add_argument("--filter", default=None, help="Keyword filter on orig_criteria")
    args = ap.parse_args()

    ingest(args.db, max_prompts=args.max, filter_keyword=args.filter)


if __name__ == "__main__":
    main()
