"""Harness server — FastAPI app for the LoCAL2 comparison harness.

Endpoints:
  WS  /arm_a/{session_id}     — proxy to LoCAL2 (normal mode: memory + tools)
  WS  /arm_b/{session_id}     — proxy to LoCAL2 (native mode: bare model + web tools)
  POST /api/runs              — create a run
  GET  /api/runs              — list runs
  GET  /api/runs/{run_id}     — run detail + items
  POST /api/judgments         — save a verdict
  GET  /api/judgments/{run_id}— list judgments for a run
  GET  /api/aggregate/{run_id}— win-rate stats
  GET  /                      — serve index.html

arm_a = LoCAL2 (full augmentation), arm_b = Native (bare model). Always.
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Optional

import uvicorn
import websockets
import yaml
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from harness import db
from harness.judge import PairwiseJudge

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).parent / "config.yaml"
_STATIC_DIR  = Path(__file__).parent / "static"

_cfg = yaml.safe_load(_CONFIG_PATH.read_text())
_LOCAL2_URL: str = _cfg.get("local2_url", "ws://localhost:3000")

app = FastAPI(title="LoCAL2 Harness")
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

_judge = PairwiseJudge()


# ---------------------------------------------------------------------------
# Arm A — WebSocket proxy to LoCAL2
# ---------------------------------------------------------------------------

@app.websocket("/arm_a/{session_id}")
async def ws_arm_a(websocket: WebSocket, session_id: str) -> None:
    """Proxy browser WS to LoCAL2, injecting a harness user_id."""
    await websocket.accept()
    local2_ws_url = f"{_LOCAL2_URL}/ws/chat/{session_id}"

    try:
        async with websockets.connect(local2_ws_url) as local2_ws:
            async def browser_to_local2() -> None:
                try:
                    while True:
                        raw = await websocket.receive_text()
                        data = json.loads(raw)
                        data.pop("run_id", "")
                        if "user_id" not in data or not data["user_id"]:
                            data["user_id"] = "arm_a_default"
                        await local2_ws.send(json.dumps(data))
                except (WebSocketDisconnect, asyncio.CancelledError):
                    pass

            async def local2_to_browser() -> None:
                try:
                    async for message in local2_ws:
                        await websocket.send_text(message if isinstance(message, str) else message.decode())
                except (asyncio.CancelledError, Exception):
                    pass

            await asyncio.gather(browser_to_local2(), local2_to_browser())
    except (asyncio.CancelledError, Exception) as exc:
        if not isinstance(exc, asyncio.CancelledError):
            logger.warning("arm_a proxy error: %s", exc)


# ---------------------------------------------------------------------------
# Arm B — native mode proxy to LoCAL2 (no memory/augmentation)
# ---------------------------------------------------------------------------

@app.websocket("/arm_b/{session_id}")
async def ws_arm_b(websocket: WebSocket, session_id: str) -> None:
    """Proxy browser WS to LoCAL2 in native mode (bare model + web tools only)."""
    await websocket.accept()
    local2_ws_url = f"{_LOCAL2_URL}/ws/chat/{session_id}"

    try:
        async with websockets.connect(local2_ws_url) as local2_ws:
            async def browser_to_local2() -> None:
                try:
                    while True:
                        raw = await websocket.receive_text()
                        data = json.loads(raw)
                        data.pop("run_id", "")
                        data["native"] = True
                        if "user_id" not in data or not data["user_id"]:
                            data["user_id"] = "arm_b_default"
                        await local2_ws.send(json.dumps(data))
                except (WebSocketDisconnect, asyncio.CancelledError):
                    pass

            async def local2_to_browser() -> None:
                try:
                    async for message in local2_ws:
                        await websocket.send_text(message if isinstance(message, str) else message.decode())
                except (asyncio.CancelledError, Exception):
                    pass

            await asyncio.gather(browser_to_local2(), local2_to_browser())
    except (asyncio.CancelledError, Exception) as exc:
        if not isinstance(exc, asyncio.CancelledError):
            logger.warning("arm_b (native) proxy error: %s", exc)


# ---------------------------------------------------------------------------
# Runs API
# ---------------------------------------------------------------------------

class RunRequest(BaseModel):
    native_model: Optional[str] = None


@app.post("/api/runs")
async def create_run(body: RunRequest) -> JSONResponse:
    generator_cfg = _cfg.get("generator", {})
    model = body.native_model or generator_cfg.get("model", "native")
    run_id = db.create_run(model, "local2-gateway", _cfg)
    return JSONResponse({"run_id": run_id, "native_model": model})


@app.get("/api/runs")
async def list_runs() -> JSONResponse:
    return JSONResponse(db.list_runs())


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str) -> JSONResponse:
    items = db.get_items(run_id)
    return JSONResponse({"run_id": run_id, "items": items})


# ---------------------------------------------------------------------------
# Items API
# ---------------------------------------------------------------------------

class ItemRequest(BaseModel):
    run_id: str
    session_id: str
    turn_idx: int
    prompt: str
    prompt_id: str = ""
    arm_a_output: str = ""
    arm_a_thinking: str = ""
    arm_a_tool_calls: list = []
    arm_a_capsules: list = []
    arm_a_candidates: list = []
    arm_b_output: str = ""
    arm_b_thinking: str = ""
    arm_b_tool_calls: list = []
    arm_a_critic_score: Optional[float] = None
    item_id: Optional[str] = None


@app.post("/api/items")
async def upsert_item(body: ItemRequest) -> JSONResponse:
    iid = db.upsert_item(
        run_id=body.run_id,
        session_id=body.session_id,
        turn_idx=body.turn_idx,
        prompt=body.prompt,
        prompt_id=body.prompt_id,
        arm_a_output=body.arm_a_output,
        arm_a_thinking=body.arm_a_thinking,
        arm_a_tool_calls=body.arm_a_tool_calls,
        arm_a_capsules=body.arm_a_capsules,
        arm_a_candidates=body.arm_a_candidates,
        arm_b_output=body.arm_b_output,
        arm_b_thinking=body.arm_b_thinking,
        arm_b_tool_calls=body.arm_b_tool_calls,
        arm_a_critic_score=body.arm_a_critic_score,
        item_id=body.item_id,
    )
    return JSONResponse({"item_id": iid})


# ---------------------------------------------------------------------------
# Judgments API
# ---------------------------------------------------------------------------

class JudgmentRequest(BaseModel):
    run_id: str
    item_id: str
    verdict: str          # a_better | tie | b_better
    rubric_scores: dict = {}
    rationale: str = ""
    judge_type: str = "human"


@app.post("/api/judgments")
async def save_judgment(body: JudgmentRequest) -> JSONResponse:
    if body.verdict not in ("local2_better", "tie", "native_better"):
        return JSONResponse({"error": "verdict must be local2_better, tie, or native_better"}, status_code=422)
    jid = db.save_judgment(
        run_id=body.run_id,
        item_id=body.item_id,
        verdict=body.verdict,
        rubric_scores=body.rubric_scores,
        rationale=body.rationale,
        judge_type=body.judge_type,
    )
    return JSONResponse({"judgment_id": jid})


@app.get("/api/judgments/{run_id}")
async def get_judgments(run_id: str) -> JSONResponse:
    return JSONResponse(db.get_judgments(run_id))


@app.get("/api/aggregate/{run_id}")
async def get_aggregate(run_id: str) -> JSONResponse:
    return JSONResponse(db.get_aggregate(run_id))


# ---------------------------------------------------------------------------
# Auto-judge (Prometheus pairwise)
# ---------------------------------------------------------------------------

@app.post("/api/judge/{item_id}")
async def auto_judge(item_id: str) -> JSONResponse:
    item = db.get_item(item_id)
    if not item:
        return JSONResponse({"error": "item not found"}, status_code=404)
    if not item.get("arm_a_output") or not item.get("arm_b_output"):
        return JSONResponse({"error": "both arms must have output before judging"}, status_code=422)
    prompt_row = db.get_prompt(item.get("prompt_id", "")) if item.get("prompt_id") else None
    reference_answer = (prompt_row or {}).get("reference_answer", "")
    rubric = (prompt_row or {}).get("score_rubric", "")
    loop = asyncio.get_event_loop()
    double_judge: bool = _cfg.get("judge", {}).get("double_judge", False)

    if double_judge:
        result = await loop.run_in_executor(
            None,
            lambda: _judge.judge_both(
                item["prompt"], item["arm_a_output"], item["arm_b_output"],
                reference_answer=reference_answer, rubric=rubric or None,
            ),
        )
        db.save_judgment(
            run_id=item["run_id"],
            item_id=item_id,
            verdict=result["verdict"],
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
    else:
        result = await loop.run_in_executor(
            None,
            lambda: _judge.judge(
                item["prompt"], item["arm_a_output"], item["arm_b_output"],
                reference_answer=reference_answer, rubric=rubric or None,
            ),
        )
        # Translate raw verdict (arm_a=LoCAL2 in UI manual runs)
        verdict = {"a_better": "local2_better", "b_better": "native_better"}.get(result["verdict"], result["verdict"])
        result = {**result, "verdict": verdict}
        db.save_judgment(
            run_id=item["run_id"],
            item_id=item_id,
            verdict=verdict,
            rationale=result["feedback"],
            judge_type="prometheus",
            reference_answer=reference_answer,
            rubric=rubric,
        )

    return JSONResponse({**result, "rubric": rubric})


@app.get("/api/items/{item_id}")
async def get_item(item_id: str) -> JSONResponse:
    item = db.get_item(item_id)
    if not item:
        return JSONResponse({"error": "item not found"}, status_code=404)
    return JSONResponse(item)


@app.get("/api/items/{item_id}/judgment")
async def get_item_judgment(item_id: str) -> JSONResponse:
    j = db.get_item_judgment(item_id, judge_type="prometheus")
    return JSONResponse(j or {})


# ---------------------------------------------------------------------------
# Static / SPA
# ---------------------------------------------------------------------------

@app.get("/")
async def index() -> FileResponse:
    return FileResponse(str(_STATIC_DIR / "index.html"), headers={"Cache-Control": "no-store"})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    logging.basicConfig(level=logging.INFO)
    cfg = yaml.safe_load(_CONFIG_PATH.read_text())
    db.init_db(cfg.get("db_path", "harness.db"))
    port = cfg.get("harness_port", 7001)
    logger.info("Harness starting on port %d  local2=%s", port, cfg.get("local2_url", "?"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")


if __name__ == "__main__":
    main()
