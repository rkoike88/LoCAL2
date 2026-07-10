"""Timing probe — connect to LoCAL2 via WebSocket and break down latency per phase.

Usage:
    python -m harness.timing_probe
    python -m harness.timing_probe --query "your query here"
    python -m harness.timing_probe --url ws://localhost:8000
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
import uuid

import websockets

DEFAULT_QUERY = (
    "Research the latest breakthroughs in fusion energy from the past month "
    "and give me a comprehensive summary of where things stand."
)
DEFAULT_URL = "ws://localhost:8000"


def _fmt(seconds: float) -> str:
    return f"{seconds:.2f}s"


async def probe(url: str, query: str, user_id: str = "probe") -> None:
    session_id = f"probe_{uuid.uuid4().hex[:8]}"
    ws_url = f"{url}/ws/chat/{session_id}"

    print(f"\nQuery   : {query[:80]}{'…' if len(query) > 80 else ''}")
    print(f"Session : {session_id}")
    print(f"URL     : {ws_url}\n")

    t0 = time.perf_counter()
    timestamps: list[tuple[float, str, str]] = []  # (elapsed, event_type, detail)

    def record(ev_type: str, detail: str = "") -> float:
        elapsed = time.perf_counter() - t0
        timestamps.append((elapsed, ev_type, detail))
        return elapsed

    async with websockets.connect(ws_url, open_timeout=10) as ws:
        record("connected")
        await ws.send(json.dumps({"query": query, "user_id": user_id}))
        record("query_sent")

        thinking_chunks = 0
        token_chunks = 0
        tool_events: list[dict] = []

        async for raw in ws:
            ev = json.loads(raw)
            ev_type = ev.get("type", "")

            if ev_type == "thinking_chunk":
                if thinking_chunks == 0:
                    record("first_thinking_chunk")
                thinking_chunks += 1
                record("last_thinking_chunk")  # overwrites each time; final value = last chunk

            elif ev_type == "token":
                if token_chunks == 0:
                    record("first_token")
                token_chunks += 1

            elif ev_type == "tool_start":
                tool = ev.get("tool", "")
                elapsed = record("tool_start", tool)
                tool_events.append({"tool": tool, "start": elapsed, "end": None})
                print(f"  [{_fmt(elapsed)}] tool_start  → {tool}")

            elif ev_type == "tool_result":
                tool = ev.get("tool", "")
                elapsed = record("tool_result", tool)
                result_len = len(ev.get("result", ""))
                for te in reversed(tool_events):
                    if te["tool"] == tool and te["end"] is None:
                        te["end"] = elapsed
                        break
                print(f"  [{_fmt(elapsed)}] tool_result ← {tool} ({result_len} chars)")

            elif ev_type == "response":
                answer_len = len(ev.get("answer", ""))
                record("response", f"{answer_len} chars")
                break  # done

    total = time.perf_counter() - t0

    # --- Phase breakdown ---
    def ts(name: str, last: bool = False) -> float | None:
        matches = [t for t, ev, _ in timestamps if ev == name]
        if not matches:
            return None
        return matches[-1] if last else matches[0]

    t_connected     = ts("connected")
    t_query_sent    = ts("query_sent")
    t_first_think   = ts("first_thinking_chunk")
    t_last_think    = ts("last_thinking_chunk", last=True)
    t_first_token   = ts("first_token")
    t_response      = ts("response")

    print("\n" + "─" * 56)
    print("PHASE BREAKDOWN")
    print("─" * 56)

    def phase(label: str, start: float | None, end: float | None) -> None:
        if start is not None and end is not None:
            print(f"  {label:<32} {_fmt(end - start):>8}")
        else:
            print(f"  {label:<32} {'—':>8}")

    phase("WS connect → query sent",      t_connected,   t_query_sent)
    phase("Query sent → first thinking",  t_query_sent,  t_first_think)
    phase("Thinking duration",            t_first_think, t_last_think)

    # Tool phases
    for te in tool_events:
        label = f"Tool: {te['tool']}"
        phase(label, te["start"], te["end"])

    last_before_token = tool_events[-1]["end"] if tool_events else t_last_think
    phase("Last event → first token",     last_before_token, t_first_token)
    phase("First token → response",       t_first_token, t_response)

    print("─" * 56)
    print(f"  {'TOTAL':<32} {_fmt(total):>8}")
    print(f"\n  thinking chunks : {thinking_chunks}")
    print(f"  token chunks    : {token_chunks}  {'(stack needs restart for tokens)' if token_chunks == 0 else ''}")
    print(f"  tool calls      : {len(tool_events)}")
    print()


def main() -> None:
    ap = argparse.ArgumentParser(description="Time a LoCAL2 request phase by phase")
    ap.add_argument("--query",   default=DEFAULT_QUERY, help="Query to send")
    ap.add_argument("--url",     default=DEFAULT_URL,   help="LoCAL2 WebSocket base URL")
    ap.add_argument("--user-id", default="probe",       help="user_id for the request")
    args = ap.parse_args()
    asyncio.run(probe(args.url, args.query, args.user_id))


if __name__ == "__main__":
    main()
