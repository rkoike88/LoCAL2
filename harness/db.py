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
            CREATE TABLE IF NOT EXISTS prompts (
                prompt_id        TEXT PRIMARY KEY,
                source_idx       INTEGER,
                instruction      TEXT UNIQUE,
                reference_answer TEXT DEFAULT '',
                criteria         TEXT DEFAULT '',
                score_rubric     TEXT DEFAULT '',
                created_at       REAL
            );
            CREATE TABLE IF NOT EXISTS runs (
                run_id       TEXT PRIMARY KEY,
                created_at   REAL,
                arm_b_model  TEXT,
                arm_b_backend TEXT,
                config_json  TEXT
            );
            CREATE TABLE IF NOT EXISTS items (
                item_id            TEXT PRIMARY KEY,
                run_id             TEXT,
                session_id         TEXT,
                turn_idx           INTEGER,
                prompt             TEXT,
                prompt_id          TEXT DEFAULT '',
                arm_a_output       TEXT DEFAULT '',
                arm_a_thinking     TEXT DEFAULT '',
                arm_a_tool_calls   TEXT DEFAULT '[]',
                arm_b_output       TEXT DEFAULT '',
                arm_b_thinking     TEXT DEFAULT '',
                arm_b_tool_calls   TEXT DEFAULT '[]',
                arm_a_critic_score REAL,
                timestamp          REAL
            );
            CREATE TABLE IF NOT EXISTS judgments (
                judgment_id      TEXT PRIMARY KEY,
                run_id           TEXT,
                item_id          TEXT,
                verdict          TEXT,
                rubric_scores_json TEXT DEFAULT '{}',
                rationale        TEXT DEFAULT '',
                reference_answer TEXT DEFAULT '',
                rubric           TEXT DEFAULT '',
                judge_type       TEXT DEFAULT 'human',
                timestamp        REAL
            );
        """)
        # Idempotent migration for existing DBs — add new columns if absent
        _add_column_if_missing(con, "items", "prompt_id", "TEXT DEFAULT ''")
        _add_column_if_missing(con, "items", "arm_a_thinking", "TEXT DEFAULT ''")
        _add_column_if_missing(con, "items", "arm_a_tool_calls", "TEXT DEFAULT '[]'")
        _add_column_if_missing(con, "items", "arm_a_capsules", "TEXT DEFAULT '[]'")
        _add_column_if_missing(con, "items", "arm_a_candidates", "TEXT DEFAULT '[]'")
        _add_column_if_missing(con, "items", "arm_b_thinking", "TEXT DEFAULT ''")
        _add_column_if_missing(con, "items", "arm_a_t_submit", "REAL")
        _add_column_if_missing(con, "items", "arm_a_t_first_event", "REAL")
        _add_column_if_missing(con, "items", "arm_a_t_complete", "REAL")
        _add_column_if_missing(con, "items", "arm_a_timed_out", "INTEGER DEFAULT 0")
        _add_column_if_missing(con, "items", "arm_b_t_submit", "REAL")
        _add_column_if_missing(con, "items", "arm_b_t_first_event", "REAL")
        _add_column_if_missing(con, "items", "arm_b_t_complete", "REAL")
        _add_column_if_missing(con, "items", "arm_b_timed_out", "INTEGER DEFAULT 0")
        _add_column_if_missing(con, "judgments", "reference_answer", "TEXT DEFAULT ''")
        _add_column_if_missing(con, "judgments", "rubric", "TEXT DEFAULT ''")
        _add_column_if_missing(con, "judgments", "t_judge_start", "REAL")
        _add_column_if_missing(con, "judgments", "verdict_pass1", "TEXT DEFAULT ''")
        _add_column_if_missing(con, "judgments", "feedback_pass2", "TEXT DEFAULT ''")
        _add_column_if_missing(con, "judgments", "verdict_pass2", "TEXT DEFAULT ''")
        _add_column_if_missing(con, "judgments", "t_judge_start_pass2", "REAL")


def _add_column_if_missing(con: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    cols = {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


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
# Prompts
# ---------------------------------------------------------------------------

def insert_prompt(
    prompt_id: str,
    source_idx: int,
    instruction: str,
    reference_answer: str,
    criteria: str,
    score_rubric: str,
) -> None:
    with _conn() as con:
        con.execute(
            "INSERT OR IGNORE INTO prompts VALUES (?,?,?,?,?,?,?)",
            (prompt_id, source_idx, instruction, reference_answer, criteria, score_rubric, time.time()),
        )


def get_prompts(limit: int = 100, offset: int = 0) -> list[dict]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM prompts ORDER BY source_idx LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    return [dict(r) for r in rows]


def count_prompts() -> int:
    with _conn() as con:
        return con.execute("SELECT COUNT(*) FROM prompts").fetchone()[0]


def get_prompt(prompt_id: str) -> dict | None:
    with _conn() as con:
        row = con.execute("SELECT * FROM prompts WHERE prompt_id=?", (prompt_id,)).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Items
# ---------------------------------------------------------------------------

def upsert_item(
    run_id: str,
    session_id: str,
    turn_idx: int,
    prompt: str,
    arm_a_output: str = "",
    arm_a_thinking: str = "",
    arm_a_tool_calls: list | None = None,
    arm_a_capsules: list | None = None,
    arm_a_candidates: list | None = None,
    arm_b_output: str = "",
    arm_b_thinking: str = "",
    arm_b_tool_calls: list | None = None,
    arm_a_critic_score: float | None = None,
    prompt_id: str = "",
    item_id: str | None = None,
    arm_a_t_submit: float | None = None,
    arm_a_t_first_event: float | None = None,
    arm_a_t_complete: float | None = None,
    arm_a_timed_out: bool = False,
    arm_b_t_submit: float | None = None,
    arm_b_t_first_event: float | None = None,
    arm_b_t_complete: float | None = None,
    arm_b_timed_out: bool = False,
) -> str:
    iid = item_id or str(uuid.uuid4())
    with _conn() as con:
        con.execute(
            """INSERT INTO items
               (item_id, run_id, session_id, turn_idx, prompt, prompt_id,
                arm_a_output, arm_a_thinking, arm_a_tool_calls, arm_a_capsules, arm_a_candidates,
                arm_b_output, arm_b_thinking, arm_b_tool_calls,
                arm_a_critic_score, timestamp,
                arm_a_t_submit, arm_a_t_first_event, arm_a_t_complete, arm_a_timed_out,
                arm_b_t_submit, arm_b_t_first_event, arm_b_t_complete, arm_b_timed_out)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(item_id) DO UPDATE SET
                 arm_a_output=excluded.arm_a_output,
                 arm_a_thinking=excluded.arm_a_thinking,
                 arm_a_tool_calls=excluded.arm_a_tool_calls,
                 arm_a_capsules=excluded.arm_a_capsules,
                 arm_a_candidates=excluded.arm_a_candidates,
                 arm_b_output=excluded.arm_b_output,
                 arm_b_thinking=excluded.arm_b_thinking,
                 arm_b_tool_calls=excluded.arm_b_tool_calls,
                 arm_a_critic_score=excluded.arm_a_critic_score,
                 arm_a_t_submit=excluded.arm_a_t_submit,
                 arm_a_t_first_event=excluded.arm_a_t_first_event,
                 arm_a_t_complete=excluded.arm_a_t_complete,
                 arm_a_timed_out=excluded.arm_a_timed_out,
                 arm_b_t_submit=excluded.arm_b_t_submit,
                 arm_b_t_first_event=excluded.arm_b_t_first_event,
                 arm_b_t_complete=excluded.arm_b_t_complete,
                 arm_b_timed_out=excluded.arm_b_timed_out
            """,
            (
                iid, run_id, session_id, turn_idx, prompt, prompt_id,
                arm_a_output, arm_a_thinking, json.dumps(arm_a_tool_calls or []),
                json.dumps(arm_a_capsules or []), json.dumps(arm_a_candidates or []),
                arm_b_output, arm_b_thinking, json.dumps(arm_b_tool_calls or []),
                arm_a_critic_score, time.time(),
                arm_a_t_submit, arm_a_t_first_event, arm_a_t_complete, int(arm_a_timed_out),
                arm_b_t_submit, arm_b_t_first_event, arm_b_t_complete, int(arm_b_timed_out),
            ),
        )
    return iid


def get_item(item_id: str) -> dict | None:
    with _conn() as con:
        row = con.execute("SELECT * FROM items WHERE item_id=?", (item_id,)).fetchone()
    return dict(row) if row else None


def get_item_judgment(item_id: str, judge_type: str = "prometheus") -> dict | None:
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM judgments WHERE item_id=? AND judge_type=? ORDER BY timestamp DESC LIMIT 1",
            (item_id, judge_type),
        ).fetchone()
    return dict(row) if row else None


def get_items(run_id: str, session_id: str | None = None) -> list[dict]:
    with _conn() as con:
        if session_id:
            rows = con.execute(
                "SELECT * FROM items WHERE run_id=? AND session_id=? ORDER BY turn_idx",
                (run_id, session_id),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM items WHERE run_id=? ORDER BY timestamp, turn_idx",
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
    reference_answer: str = "",
    rubric: str = "",
    t_judge_start: float | None = None,
    verdict_pass1: str = "",
    feedback_pass2: str = "",
    verdict_pass2: str = "",
    t_judge_start_pass2: float | None = None,
) -> str:
    jid = str(uuid.uuid4())
    with _conn() as con:
        con.execute(
            """INSERT INTO judgments
               (judgment_id, run_id, item_id, verdict, rubric_scores_json,
                rationale, reference_answer, rubric, judge_type, timestamp, t_judge_start,
                verdict_pass1, feedback_pass2, verdict_pass2, t_judge_start_pass2)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                jid, run_id, item_id, verdict,
                json.dumps(rubric_scores or {}), rationale,
                reference_answer, rubric, judge_type,
                time.time(), t_judge_start,
                verdict_pass1, feedback_pass2, verdict_pass2, t_judge_start_pass2,
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
