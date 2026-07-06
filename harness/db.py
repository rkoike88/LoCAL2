"""SQLite store for harness runs, items, and judgments."""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

_DB_PATH: Path | None = None


def init_db(path: str) -> None:
    global _DB_PATH
    _DB_PATH = Path(path)
    with _conn() as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS runs (
                run_id       TEXT PRIMARY KEY,
                created_at   REAL,
                arm_b_model  TEXT,
                arm_b_backend TEXT,
                config_json  TEXT
            );
            CREATE TABLE IF NOT EXISTS items (
                item_id           TEXT PRIMARY KEY,
                run_id            TEXT,
                session_id        TEXT,
                turn_idx          INTEGER,
                prompt            TEXT,
                arm_a_output      TEXT DEFAULT '',
                arm_b_output      TEXT DEFAULT '',
                arm_a_critic_score REAL,
                arm_b_tool_calls  TEXT DEFAULT '[]',
                timestamp         REAL
            );
            CREATE TABLE IF NOT EXISTS judgments (
                judgment_id      TEXT PRIMARY KEY,
                run_id           TEXT,
                item_id          TEXT,
                verdict          TEXT,
                rubric_scores_json TEXT DEFAULT '{}',
                rationale        TEXT DEFAULT '',
                judge_type       TEXT DEFAULT 'human',
                timestamp        REAL
            );
        """)


def _conn() -> sqlite3.Connection:
    if _DB_PATH is None:
        raise RuntimeError("db not initialised — call init_db() first")
    con = sqlite3.connect(str(_DB_PATH))
    con.row_factory = sqlite3.Row
    return con


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------

def create_run(arm_b_model: str, arm_b_backend: str, config: dict) -> str:
    run_id = str(uuid.uuid4())[:8]
    with _conn() as con:
        con.execute(
            "INSERT INTO runs VALUES (?,?,?,?,?)",
            (run_id, time.time(), arm_b_model, arm_b_backend, json.dumps(config)),
        )
    return run_id


def list_runs() -> list[dict]:
    with _conn() as con:
        rows = con.execute("SELECT * FROM runs ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Items
# ---------------------------------------------------------------------------

def upsert_item(
    run_id: str,
    session_id: str,
    turn_idx: int,
    prompt: str,
    arm_a_output: str = "",
    arm_b_output: str = "",
    arm_a_critic_score: float | None = None,
    arm_b_tool_calls: list | None = None,
    item_id: str | None = None,
) -> str:
    iid = item_id or str(uuid.uuid4())
    with _conn() as con:
        con.execute(
            """INSERT INTO items
               (item_id, run_id, session_id, turn_idx, prompt,
                arm_a_output, arm_b_output, arm_a_critic_score, arm_b_tool_calls, timestamp)
               VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(item_id) DO UPDATE SET
                 arm_a_output=excluded.arm_a_output,
                 arm_b_output=excluded.arm_b_output,
                 arm_a_critic_score=excluded.arm_a_critic_score,
                 arm_b_tool_calls=excluded.arm_b_tool_calls
            """,
            (
                iid, run_id, session_id, turn_idx, prompt,
                arm_a_output, arm_b_output, arm_a_critic_score,
                json.dumps(arm_b_tool_calls or []),
                time.time(),
            ),
        )
    return iid


def get_items(run_id: str, session_id: str | None = None) -> list[dict]:
    with _conn() as con:
        if session_id:
            rows = con.execute(
                "SELECT * FROM items WHERE run_id=? AND session_id=? ORDER BY turn_idx",
                (run_id, session_id),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM items WHERE run_id=? ORDER BY session_id, turn_idx",
                (run_id,),
            ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Judgments
# ---------------------------------------------------------------------------

def save_judgment(
    run_id: str,
    item_id: str,
    verdict: str,
    rubric_scores: dict | None = None,
    rationale: str = "",
    judge_type: str = "human",
) -> str:
    jid = str(uuid.uuid4())
    with _conn() as con:
        con.execute(
            "INSERT INTO judgments VALUES (?,?,?,?,?,?,?,?)",
            (
                jid, run_id, item_id, verdict,
                json.dumps(rubric_scores or {}), rationale, judge_type,
                time.time(),
            ),
        )
    return jid


def get_judgments(run_id: str) -> list[dict]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM judgments WHERE run_id=? ORDER BY timestamp",
            (run_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_aggregate(run_id: str) -> dict[str, Any]:
    items = get_items(run_id)
    judgments = get_judgments(run_id)
    judged = {j["item_id"]: j for j in judgments}

    total = len(judgments)
    a_wins = sum(1 for j in judgments if j["verdict"] == "a_better")
    b_wins = sum(1 for j in judgments if j["verdict"] == "b_better")
    ties   = sum(1 for j in judgments if j["verdict"] == "tie")

    return {
        "run_id": run_id,
        "total_items": len(items),
        "judged": total,
        "a_wins": a_wins,
        "b_wins": b_wins,
        "ties": ties,
        "a_win_rate": round(a_wins / total, 3) if total else None,
        "b_win_rate": round(b_wins / total, 3) if total else None,
    }
