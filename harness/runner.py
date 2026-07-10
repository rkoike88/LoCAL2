"""Batch runner — evaluate N prompts from the DB: LoCAL2 vs Native.

Both arms connect to the same LoCAL2 gateway WebSocket:
  - local2 arm: normal query (memory, tools, stage setting all active)
  - native arm: query with native=true (bare model + web tools only, no memory injection)

arm_a in the DB = LoCAL2, always.
arm_b in the DB = Native, always.

Prometheus sees Response A and B according to harness/config.yaml judge.position:
  local2_first  — LoCAL2 is A, Native is B
  native_first  — Native is A, LoCAL2 is B

Usage:
    python -m harness.runner --n 50
    python -m harness.runner --n 50 --run-id stage_28
    python -m harness.runner --n 50 --skip-local2
    python -m harness.runner --n 200 --offset 50
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

import websockets
import yaml

from harness import db
from harness.judge import PairwiseJudge

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).parent / "config.yaml"

_IDLE_TIMEOUT = 120  # seconds of silence on either arm before considered hung


# ---------------------------------------------------------------------------
# Single-arm async WS query
# ---------------------------------------------------------------------------

async def _query_arm(
    local2_url: str,
    session_id: str,
    user_id: str,
    query: str,
    native: bool = False,
) -> dict[str, Any]:
    """Connect to LoCAL2 gateway and run one query turn.

    Args:
        local2_url: WebSocket base URL, e.g. ws://localhost:8000
        session_id: Unique session identifier for this prompt/run combination
        user_id: User ID passed to LoCAL2 (harness user)
        query: The prompt text
        native: If True, sends native=true to bypass memory/augmentation

    Returns:
        Dict with 'answer', 'thinking', 'tool_calls', 'capsules', 'candidates'.
        Empty dict on connection failure.
    """
    ws_url = f"{local2_url}/ws/chat/{session_id}"
    acc: dict[str, Any] = {
        "answer": "", "thinking": "", "tool_calls": [],
        "capsules": [], "candidates": [],
        "t_submit": None, "t_first_event": None, "t_complete": None, "timed_out": False,
    }
    arm_label = "native" if native else "local2"

    try:
        async with websockets.connect(ws_url, open_timeout=10) as ws:
            payload = {"query": query, "user_id": user_id}
            if native:
                payload["native"] = True
            acc["t_submit"] = time.time()
            await ws.send(json.dumps(payload))

            last_event = time.monotonic()
            while True:
                time_left = _IDLE_TIMEOUT - (time.monotonic() - last_event)
                if time_left <= 0:
                    logger.warning("%s idle timeout (%ds) session=%s", arm_label, _IDLE_TIMEOUT, session_id)
                    acc["timed_out"] = True
                    break
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=time_left)
                except asyncio.TimeoutError:
                    logger.warning("%s idle timeout (%ds) session=%s", arm_label, _IDLE_TIMEOUT, session_id)
                    acc["timed_out"] = True
                    break

                ev = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
                ev_type = ev.get("type", "")

                if ev_type in ("thinking_chunk", "token", "tool_start", "tool_result"):
                    now = time.time()
                    if acc["t_first_event"] is None:
                        acc["t_first_event"] = now
                    last_event = time.monotonic()
                    if ev_type == "thinking_chunk":
                        acc["thinking"] += ev.get("chunk", "")
                    elif ev_type == "tool_start":
                        acc["tool_calls"].append({
                            "tool": ev.get("tool", ""),
                            "args": ev.get("args", {}),
                            "result": "",
                        })
                    elif ev_type == "tool_result":
                        tool_name = ev.get("tool", "")
                        for tc in reversed(acc["tool_calls"]):
                            if tc["tool"] == tool_name and tc["result"] == "":
                                tc["result"] = ev.get("result", "")
                                break

                elif ev_type == "response":
                    acc["t_complete"] = time.time()
                    acc["answer"] = ev.get("answer", "").strip()
                    if ev.get("thinking"):
                        acc["thinking"] = ev["thinking"]
                    if ev.get("tool_calls"):
                        acc["tool_calls"] = ev["tool_calls"]
                    acc["capsules"] = ev.get("capsules") or []
                    acc["candidates"] = ev.get("candidates") or []
                    break  # done — critique arrives after but we don't need it

    except (OSError, websockets.WebSocketException) as exc:
        logger.warning("%s connect failed (%s): %s", arm_label, ws_url, exc)

    return acc


# ---------------------------------------------------------------------------
# Timing helpers
# ---------------------------------------------------------------------------

def _arm_timing_summary(result: dict, label: str) -> str:
    t_sub = result.get("t_submit")
    t_fe  = result.get("t_first_event")
    t_com = result.get("t_complete")
    timed_out = result.get("timed_out", False)

    if t_sub is None:
        return f"  {label}: CONNECTION_FAILED"

    parts = [f"  {label}:"]
    if t_fe is not None:
        parts.append(f"ttfe={t_fe - t_sub:.1f}s")
    else:
        parts.append("ttfe=—")

    if t_com is not None:
        parts.append(f"gen={t_com - (t_fe or t_sub):.1f}s")
        parts.append(f"total={t_com - t_sub:.1f}s")
    elif timed_out:
        parts.append(f"TIMED_OUT({_IDLE_TIMEOUT}s idle)")
    else:
        parts.append("total=—(no response event)")

    return "  ".join(parts)


# ---------------------------------------------------------------------------
# Single-prompt evaluation
# ---------------------------------------------------------------------------

async def _eval_prompt(
    prompt: dict,
    run_id: str,
    judge: PairwiseJudge,
    local2_url: str,
    judge_position: str,
    skip_local2: bool,
    user_id: str,
    double_judge: bool = False,
) -> str:
    """Evaluate one prompt against both arms, save item + judgment. Returns verdict."""
    prompt_id: str = prompt["prompt_id"]
    query: str = prompt["instruction"]
    reference_answer: str = prompt.get("reference_answer", "")
    rubric: str = prompt.get("score_rubric", "")

    # Session IDs must be unique per arm so LoCAL2 doesn't share conversation history.
    local2_session = f"{run_id}_{prompt_id[:8]}_l"
    native_session = f"{run_id}_{prompt_id[:8]}_n"

    loop = asyncio.get_event_loop()

    # Run local2 first, then native — sequential to avoid Ollama queue contention.
    if skip_local2:
        local2_result: dict[str, Any] = {
            "answer": "", "thinking": "", "tool_calls": [], "capsules": [], "candidates": [],
            "t_submit": None, "t_first_event": None, "t_complete": None, "timed_out": False,
        }
    else:
        local2_result = await _query_arm(local2_url, local2_session, user_id, query, native=False)

    native_result = await _query_arm(local2_url, native_session, user_id, query, native=True)

    local2_answer: str = local2_result["answer"]
    native_answer: str = native_result["answer"]

    logger.info("%s", _arm_timing_summary(local2_result, "local2"))
    logger.info("%s", _arm_timing_summary(native_result, "native"))
    logger.info(
        "  local2=%d chars  native=%d chars",
        len(local2_answer), len(native_answer),
    )

    # arm_a = LoCAL2, arm_b = Native — always, every run.
    item_id = db.upsert_item(
        run_id=run_id,
        session_id=local2_session,
        turn_idx=0,
        prompt=query,
        prompt_id=prompt_id,
        arm_a_output=local2_answer,
        arm_a_thinking=local2_result["thinking"],
        arm_a_tool_calls=local2_result["tool_calls"],
        arm_a_capsules=local2_result["capsules"],
        arm_a_candidates=local2_result["candidates"],
        arm_b_output=native_answer,
        arm_b_thinking=native_result["thinking"],
        arm_b_tool_calls=native_result["tool_calls"],
        arm_a_t_submit=local2_result.get("t_submit"),
        arm_a_t_first_event=local2_result.get("t_first_event"),
        arm_a_t_complete=local2_result.get("t_complete"),
        arm_a_timed_out=local2_result.get("timed_out", False),
        arm_b_t_submit=native_result.get("t_submit"),
        arm_b_t_first_event=native_result.get("t_first_event"),
        arm_b_t_complete=native_result.get("t_complete"),
        arm_b_timed_out=native_result.get("timed_out", False),
    )

    if not local2_answer or not native_answer:
        logger.info("  skipping judge (one or both arms empty)")
        return "skipped"

    if double_judge:
        result = await loop.run_in_executor(
            None,
            lambda: judge.judge_both(query, local2_answer, native_answer, reference_answer, rubric or None),
        )
        verdict = result["verdict"]
        t_judge_end = time.time()
        db.save_judgment(
            run_id=run_id,
            item_id=item_id,
            verdict=verdict,
            rubric_scores={"double_judge": True},
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
        judge_secs = t_judge_end - result["t_judge_start"]
        logger.info(
            "  verdict=%s (p1=%s p2=%s)  judge=%.1fs",
            verdict, result["verdict_pass1"], result["verdict_pass2"], judge_secs,
        )
    else:
        # Single-pass: position config determines which arm is A.
        if judge_position == "native_first":
            prom_a, prom_b = native_answer, local2_answer
        else:
            prom_a, prom_b = local2_answer, native_answer

        t_judge_start = time.time()
        result = await loop.run_in_executor(
            None,
            lambda: judge.judge(query, prom_a, prom_b, reference_answer, rubric or None),
        )
        t_judge_end = time.time()
        raw_verdict = result["verdict"]
        if judge_position == "native_first":
            verdict = {"a_better": "native_better", "b_better": "local2_better"}.get(raw_verdict, raw_verdict)
        else:
            verdict = {"a_better": "local2_better", "b_better": "native_better"}.get(raw_verdict, raw_verdict)

        db.save_judgment(
            run_id=run_id,
            item_id=item_id,
            verdict=verdict,
            rubric_scores={"judge_position": judge_position},
            rationale=result["feedback"],
            judge_type="prometheus",
            reference_answer=reference_answer,
            rubric=rubric,
            t_judge_start=t_judge_start,
        )
        logger.info(
            "  verdict=%s (position=%s)  judge=%.1fs",
            verdict, judge_position, t_judge_end - t_judge_start,
        )

    return verdict


# ---------------------------------------------------------------------------
# Main runner loop
# ---------------------------------------------------------------------------

def _ensure_run(run_id: str, model: str, backend: str, config: dict) -> None:
    existing = {r["run_id"] for r in db.list_runs()}
    if run_id in existing:
        logger.info("Resuming run: %s", run_id)
        return
    with db._conn() as con:
        con.execute(
            "INSERT OR IGNORE INTO runs VALUES (?,?,?,?,?)",
            (run_id, time.time(), model, backend, json.dumps(config)),
        )
    logger.info("Created run: %s", run_id)


async def _run(
    run_id: str,
    n: int,
    filter_kw: str | None,
    skip_local2: bool,
    user_id: str,
    cfg: dict,
    offset: int = 0,
) -> None:
    db.init_db(cfg.get("db_path", "harness.db"))

    judge_cfg = cfg.get("judge", {})
    judge_position: str = judge_cfg.get("position", "local2_first")
    double_judge: bool = judge_cfg.get("double_judge", False)
    local2_url: str = cfg.get("local2_url", "ws://localhost:8000")

    _ensure_run(run_id, "native-mode", "local2-gateway", cfg)

    done_items = db.get_items(run_id)
    done_prompt_ids = {it["prompt_id"] for it in done_items if it.get("prompt_id")}
    if done_prompt_ids:
        logger.info("Already processed: %d items (will skip)", len(done_prompt_ids))

    fetch_limit = n + len(done_prompt_ids) + 100
    all_prompts = db.get_prompts(limit=fetch_limit, offset=offset)
    if offset:
        logger.info("Prompt offset: %d", offset)

    if filter_kw:
        kw = filter_kw.lower()
        all_prompts = [p for p in all_prompts if kw in (p.get("criteria") or "").lower()]

    remaining = [p for p in all_prompts if p["prompt_id"] not in done_prompt_ids][:n]

    logger.info(
        "DB has %d prompts total. Processing %d this run (run_id=%s, position=%s, user_id=%s)",
        db.count_prompts(), len(remaining), run_id, judge_position, user_id,
    )

    if not remaining:
        logger.info("Nothing to process.")
        return

    judge = PairwiseJudge()
    verdicts: dict[str, int] = {"local2_better": 0, "tie": 0, "native_better": 0, "skipped": 0}
    t0 = time.time()

    for i, prompt in enumerate(remaining, 1):
        elapsed = time.time() - t0
        avg = elapsed / max(i - 1, 1)
        eta = avg * (len(remaining) - i + 1)
        logger.info(
            "[%d/%d] %s  eta=%.0fs  q=%s",
            i, len(remaining), prompt["prompt_id"], eta,
            (prompt.get("instruction") or "")[:60].replace("\n", " "),
        )
        try:
            verdict = await _eval_prompt(
                prompt, run_id, judge, local2_url, judge_position, skip_local2, user_id, double_judge,
            )
        except Exception as exc:
            logger.error("  FAILED: %s", exc, exc_info=True)
            verdict = "skipped"

        verdicts[verdict] = verdicts.get(verdict, 0) + 1

        if i % 10 == 0 or i == len(remaining):
            total_judged = verdicts["local2_better"] + verdicts["tie"] + verdicts["native_better"]
            l2_rate = verdicts["local2_better"] / total_judged if total_judged else 0.0
            nat_rate = verdicts["native_better"] / total_judged if total_judged else 0.0
            logger.info(
                "  --- [%d/%d] local2=%.0f%%  native=%.0f%%  tie=%d  skip=%d ---",
                i, len(remaining),
                l2_rate * 100, nat_rate * 100, verdicts["tie"], verdicts["skipped"],
            )

    total = time.time() - t0
    total_judged = verdicts["local2_better"] + verdicts["tie"] + verdicts["native_better"]
    logger.info("=== Done in %.0fs ===", total)
    logger.info(
        "  local2 wins: %d  native wins: %d  ties: %d  skipped: %d",
        verdicts["local2_better"], verdicts["native_better"], verdicts["tie"], verdicts["skipped"],
    )
    if total_judged:
        logger.info(
            "  local2 win-rate: %.1f%%  native win-rate: %.1f%%",
            verdicts["local2_better"] / total_judged * 100,
            verdicts["native_better"] / total_judged * 100,
        )
    logger.info("  run_id=%s  position=%s  db=%s", run_id, judge_position, cfg.get("db_path", "harness.db"))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = yaml.safe_load(_CONFIG_PATH.read_text())

    ap = argparse.ArgumentParser(description="Batch-evaluate prompts: LoCAL2 vs Native")
    ap.add_argument("--db",           default=cfg.get("db_path", "harness.db"))
    ap.add_argument("--n",            type=int, default=50)
    ap.add_argument("--run-id",       default=None)
    ap.add_argument("--filter",       default=None)
    ap.add_argument("--skip-local2",  action="store_true", help="Skip LoCAL2 arm (Native + judge only)")
    ap.add_argument("--user-id",      default="harness")
    ap.add_argument("--offset",       type=int, default=0)
    args = ap.parse_args()

    cfg["db_path"] = args.db
    run_id = args.run_id or str(uuid.uuid4())[:8]

    asyncio.run(
        _run(
            run_id=run_id,
            n=args.n,
            filter_kw=args.filter,
            skip_local2=args.skip_local2,
            user_id=args.user_id,
            cfg=cfg,
            offset=args.offset,
        )
    )


if __name__ == "__main__":
    main()
