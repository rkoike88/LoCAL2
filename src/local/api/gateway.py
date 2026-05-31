"""FastAPI gateway for LoCAL2.

Endpoints:
  POST /query   — submit a query; returns answer + metadata
  POST /feedback — not yet implemented (Phase 4)
  GET  /health  — liveness check

The shared ZmqPublisher is created at startup via FastAPI lifespan and
stored in app.state.publisher.
"""
from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from local.protocol.subjects import RESPONSE_GENERATION
from local.session.local_session import LoCALSession
from local.transport.bus_config import PROXY_FRONTEND_ADDR
from local.transport.zmq_pubsub import ZmqPublisher


@asynccontextmanager
async def _lifespan(app: FastAPI):
    app.state.publisher = ZmqPublisher(PROXY_FRONTEND_ADDR, bind=False)
    yield
    app.state.publisher.close()


app = FastAPI(title="LoCAL2 API", lifespan=_lifespan)


class QueryRequest(BaseModel):
    query: str
    session_id: Optional[str] = None
    timeout: float = 120.0


class QueryResponse(BaseModel):
    answer: str
    thinking: str
    tool_calls: list
    session_id: str
    query_id: str


@app.post("/query", response_model=QueryResponse)
async def post_query(body: QueryRequest, request: Request):
    publisher: ZmqPublisher = request.app.state.publisher
    session = LoCALSession(publisher, session_id=body.session_id)

    def _run() -> QueryResponse:
        answer = ""
        thinking = ""
        tool_calls: list = []
        query_id = str(uuid.uuid4())
        for envelope in session.stream(body.query, query_id=query_id, timeout=body.timeout):
            if envelope.subject == RESPONSE_GENERATION:
                p = envelope.payload
                answer = p.get("answer") or ""
                thinking = p.get("thinking") or ""
                tool_calls = p.get("tool_calls") or []
                query_id = p.get("query_id") or query_id
        return QueryResponse(
            answer=answer,
            thinking=thinking,
            tool_calls=tool_calls,
            session_id=session.session_id,
            query_id=query_id,
        )

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _run)


@app.post("/feedback")
async def post_feedback():
    raise HTTPException(status_code=501, detail="Feedback endpoint not implemented until Phase 4")


@app.get("/health")
async def health():
    return JSONResponse({"status": "ok"})
