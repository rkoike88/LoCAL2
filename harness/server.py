"""Harness server — FastAPI app for the LoCAL2 comparison harness.

Endpoints:
  WS  /arm_a/{session_id}     — proxy to LoCAL2 gateway (injects user_id)
  WS  /arm_b/{session_id}     — bare model tool-call loop
  POST /api/runs              — create a run
  GET  /api/runs              — list runs
  GET  /api/runs/{run_id}     — run detail + items
  POST /api/judgments         — save a verdict
  GET  /api/judgments/{run_id}— list judgments for a run
  GET  /api/aggregate/{run_id}— win-rate stats
  GET  /                      — serve index.html
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
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
from harness.arm_b import ArmBClient
from harness.judge import PairwiseJudge

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).parent / "config.yaml"
_STATIC_DIR  = Path(__file__).parent / "static"

_cfg = yaml.safe_load(_CONFIG_PATH.read_text())
_LOCAL2_URL: str = _cfg.get("local2_url", "ws://localhost:3000")

app = FastAPI(title="LoCAL2 Harness")
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

_arm_b = ArmBClient()
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
                        run_id = data.pop("run_id", "")
                        data["user_id"] = f"arm_a_{run_id}" if run_id else "arm_a_default"
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
# Arm B — bare model tool-call loop
# ---------------------------------------------------------------------------

@app.websocket("/arm_b/{session_id}")
async def ws_arm_b(websocket: WebSocket, session_id: str) -> None:
    """Run Arm B tool-call loop, streaming events to the browser."""
    await websocket.accept()
    loop = asyncio.get_event_loop()

    try:
        while True:
            data = await websocket.receive_json()
            query: str = data.get("query", "").strip()
            if not query:
                continue

            queue: asyncio.Queue = asyncio.Queue()

            def _run() -> None:
                def _emit(event: dict) -> None:
                    asyncio.run_coroutine_threadsafe(queue.put(event), loop)
                try:
                    _arm_b.stream(session_id, query, _emit)
                except Exception as exc:
                    asyncio.run_coroutine_threadsafe(
                        queue.put({"type": "error", "content": str(exc)}), loop
                    )
                finally:
                    asyncio.run_coroutine_threadsafe(queue.put(None), loop)

            # daemon=True so Ctrl-C doesn't wait for in-flight Ollama calls to finish
            threading.Thread(target=_run, daemon=True).start()

            while True:
                event = await queue.get()
                if event is None:
                    break
                await websocket.send_json(event)

    except (WebSocketDisconnect, asyncio.CancelledError):
        pass


# ---------------------------------------------------------------------------
# Runs API
# ---------------------------------------------------------------------------

class RunRequest(BaseModel):
    arm_b_model: Optional[str] = None
    arm_b_backend: Optional[str] = None


@app.post("/api/runs")
async def create_run(body: RunRequest) -> JSONResponse:
    arm_b_cfg = _cfg.get("arm_b", {})
    model   = body.arm_b_model   or arm_b_cfg.get("model", "")
    backend = body.arm_b_backend or arm_b_cfg.get("backend", "ollama")
    run_id = db.create_run(model, backend, arm_b_cfg)
    return JSONResponse({"run_id": run_id, "arm_b_model": model, "arm_b_backend": backend})


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
    if body.verdict not in ("a_better", "tie", "b_better"):
        return JSONResponse({"error": "verdict must be a_better, tie, or b_better"}, status_code=422)
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
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        lambda: _judge.judge(item["prompt"], item["arm_a_output"], item["arm_b_output"]),
    )
    db.save_judgment(
        run_id=item["run_id"],
        item_id=item_id,
        verdict=result["verdict"],
        rationale=result["feedback"],
        judge_type="prometheus",
    )
    return JSONResponse(result)


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
    logger.info("Harness starting on port %d  arm_b=%s", port, cfg.get("arm_b", {}).get("model", "?"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")


if __name__ == "__main__":
    main()
