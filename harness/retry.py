"""Retry skipped items — rerun only the LoCAL2 arm and re-judge.

A "skipped" item is one that has arm_a_output='' (LoCAL2 arm failed or timed out)
and no judgment row. The native arm output is already stored; only arm_a needs
to be rerun. After a successful arm_a response the pair is double-judged and
the result is inserted into judgments.

Usage:
    python -m harness.retry --run-id pref-04
    python -m harness.retry --run-id pref-04 --dry-run   # list items only
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sqlite3
import time
from pathlib import Path

import yaml

from harness import db
from harness.judge import PairwiseJudge
from harness.runner import _query_arm, _arm_timing_summary

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).parent / "config.yaml"


def _get_skipped(run_id: str) -> list[dict]:
    """Return items with no arm_a output and no judgment for run_id."""
    with db._conn() as con:
        con.row_factory = sqlite3.Row
        rows = con.execute("""
            SELECT
              i.item_id,
              i.prompt_id,
              i.prompt,
              i.session_id,
              i.arm_b_output,
              i.arm_b_thinking,
              i.arm_b_tool_calls,
              i.arm_b_t_submit,
              i.arm_b_t_first_event,
              i.arm_b_t_complete,
              i.arm_b_timed_out
            FROM items i
            LEFT JOIN judgments j
                   ON j.item_id = i.item_id AND j.run_id = i.run_id
            WHERE i.run_id = ?
              AND (i.arm_a_output IS NULL OR i.arm_a_output = '')
              AND j.verdict IS NULL
            ORDER BY i.timestamp
        """, (run_id,)).fetchall()
    return [dict(r) for r in rows]


def _get_by_ids(run_id: str, item_ids: list[str]) -> list[dict]:
    """Return specific items by item_id regardless of judgment status."""
    with db._conn() as con:
        con.row_factory = sqlite3.Row
        placeholders = ",".join("?" * len(item_ids))
        rows = con.execute(f"""
            SELECT
              i.item_id,
              i.prompt_id,
              i.prompt,
              i.session_id,
              i.arm_b_output,
              i.arm_b_thinking,
              i.arm_b_tool_calls,
              i.arm_b_t_submit,
              i.arm_b_t_first_event,
              i.arm_b_t_complete,
              i.arm_b_timed_out
            FROM items i
            WHERE i.run_id = ? AND i.item_id IN ({placeholders})
            ORDER BY i.timestamp
        """, (run_id, *item_ids)).fetchall()
    return [dict(r) for r in rows]


def _delete_judgment(run_id: str, item_id: str) -> None:
    with db._conn() as con:
        con.execute(
            "DELETE FROM judgments WHERE run_id=? AND item_id=?",
            (run_id, item_id),
        )


def _get_rubric(prompt_id: str) -> tuple[str, str]:
    """Return (reference_answer, score_rubric) for a prompt_id."""
    p = db.get_prompt(prompt_id)
    if not p:
        return "", ""
    return p.get("reference_answer", ""), p.get("score_rubric", "")


async def _retry_item(
    item: dict,
    run_id: str,
    judge: PairwiseJudge,
    local2_url: str,
    user_id: str,
    replace: bool = False,
) -> str:
    """Rerun LoCAL2 arm for one skipped item. Returns verdict or 'skipped'."""
    item_id   = item["item_id"]
    prompt_id = item["prompt_id"]
    query     = item["prompt"]

    # Reconstruct the original session id used for this item's LoCAL2 arm.
    local2_session = f"{run_id}_{prompt_id[:8]}_l"

    logger.info("  retrying item=%s  q=%s", item_id[:8], query[:60].replace("\n", " "))

    local2_result = await _query_arm(local2_url, local2_session, user_id, query, native=False)

    logger.info("%s", _arm_timing_summary(local2_result, "local2"))
    logger.info("  local2=%d chars", len(local2_result["answer"]))

    # Upsert the item, preserving the existing arm_b data.
    db.upsert_item(
        run_id=run_id,
        session_id=local2_session,
        turn_idx=0,
        prompt=query,
        prompt_id=prompt_id,
        item_id=item_id,
        arm_a_output=local2_result["answer"],
        arm_a_thinking=local2_result["thinking"],
        arm_a_tool_calls=local2_result["tool_calls"],
        arm_a_capsules=local2_result["capsules"],
        arm_a_candidates=local2_result["candidates"],
        arm_a_t_submit=local2_result.get("t_submit"),
        arm_a_t_first_event=local2_result.get("t_first_event"),
        arm_a_t_complete=local2_result.get("t_complete"),
        arm_a_timed_out=local2_result.get("timed_out", False),
        # Preserve existing arm_b data.
        arm_b_output=item["arm_b_output"] or "",
        arm_b_thinking=item["arm_b_thinking"] or "",
        arm_b_tool_calls=json.loads(item["arm_b_tool_calls"] or "[]"),
        arm_b_t_submit=item.get("arm_b_t_submit"),
        arm_b_t_first_event=item.get("arm_b_t_first_event"),
        arm_b_t_complete=item.get("arm_b_t_complete"),
        arm_b_timed_out=bool(item.get("arm_b_timed_out", 0)),
    )

    local2_answer = local2_result["answer"]
    native_answer = item["arm_b_output"] or ""

    if not local2_answer or not native_answer:
        logger.info("  still empty after retry — skipping judge")
        return "skipped"

    reference_answer, rubric = _get_rubric(prompt_id)

    if replace:
        _delete_judgment(run_id, item_id)
        logger.info("  deleted existing judgment for item=%s", item_id[:8])

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        lambda: judge.judge_both(query, local2_answer, native_answer, reference_answer, rubric or None),
    )
    verdict = result["verdict"]
    t_judge_end = time.time()
    judge_secs = t_judge_end - result["t_judge_start"]

    db.save_judgment(
        run_id=run_id,
        item_id=item_id,
        verdict=verdict,
        rubric_scores={"double_judge": True, "retry": True},
        rationale=result["feedback_pass1"],
        judge_type="prometheus",
        reference_answer=reference_answer,
        rubric=rubric,
        t_judge_start=result["t_judge_start"],
        verdict_pass1=result["verdict_pass1"],
        feedback_pass2=result["feedback_pass2"],
        verdict_pass2=result["verdict_pass2"],
        t_judge_start_pass2=result["t_judge_start_pass2"],
    )

    logger.info(
        "  verdict=%s (p1=%s p2=%s)  judge=%.1fs",
        verdict, result["verdict_pass1"], result["verdict_pass2"], judge_secs,
    )
    return verdict


async def _run_retries(
    run_id: str, user_id: str, cfg: dict, dry_run: bool,
    item_ids: list[str] | None = None,
) -> None:
    db.init_db(cfg.get("db_path", "harness.db"))

    if item_ids:
        items = _get_by_ids(run_id, item_ids)
        replace = True  # delete existing judgment before re-judging
        logger.info("Targeting %d specific item(s) for run_id=%s", len(items), run_id)
    else:
        items = _get_skipped(run_id)
        replace = False
        logger.info("Found %d skipped items for run_id=%s", len(items), run_id)

    if not items:
        logger.info("Nothing to retry.")
        return

    for i, item in enumerate(items, 1):
        logger.info(
            "  [%d/%d] item=%s  q=%s",
            i, len(items), item["item_id"][:8],
            item["prompt"][:60].replace("\n", " "),
        )

    if dry_run:
        logger.info("Dry run — exiting without retrying.")
        return

    local2_url = cfg.get("local2_url", "ws://localhost:8000")
    judge = PairwiseJudge()
    verdicts: dict[str, int] = {"local2_better": 0, "tie": 0, "native_better": 0, "skipped": 0}

    for i, item in enumerate(items, 1):
        logger.info("[%d/%d]", i, len(items))
        try:
            verdict = await _retry_item(item, run_id, judge, local2_url, user_id, replace=replace)
        except Exception as exc:
            logger.error("  FAILED: %s", exc, exc_info=True)
            verdict = "skipped"
        verdicts[verdict] = verdicts.get(verdict, 0) + 1

    logger.info(
        "=== Retry complete ===  l2=%d  nat=%d  tie=%d  still_skipped=%d",
        verdicts["local2_better"], verdicts["native_better"],
        verdicts["tie"], verdicts["skipped"],
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = yaml.safe_load(_CONFIG_PATH.read_text())

    ap = argparse.ArgumentParser(description="Retry skipped harness items (LoCAL2 arm only)")
    ap.add_argument("--run-id",   required=True)
    ap.add_argument("--user-id",  default="harness")
    ap.add_argument("--item-id",  nargs="+", metavar="ITEM_ID", help="Specific item(s) to rerun and replace")
    ap.add_argument("--dry-run",  action="store_true", help="List items without retrying")
    ap.add_argument("--db",       default=cfg.get("db_path", "harness.db"))
    args = ap.parse_args()

    cfg["db_path"] = args.db
    asyncio.run(_run_retries(args.run_id, args.user_id, cfg, args.dry_run, args.item_id))


if __name__ == "__main__":
    main()
