"""Story runner — executes a YAML story against the live LoCAL2 API.

Usage:
    PYTHONPATH=src python tests/run_story.py tests/stories/s1_basic_qa.yaml
    PYTHONPATH=src python tests/run_story.py tests/stories/s2_multi_turn.yaml --api http://localhost:8000
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid
from pathlib import Path

import websockets
import yaml


def _check(label: str, passed: bool, detail: str = "") -> bool:
    mark = "  PASS" if passed else "  FAIL"
    print(f"{mark}  {label}" + (f" — {detail}" if detail else ""))
    return passed


async def _ws_query(ws_base: str, session_id: str, query: str, timeout: float = 120.0) -> dict:
    """Send one query over WebSocket and collect until type=response arrives."""
    uri = f"{ws_base}/ws/chat/{session_id}"
    async with websockets.connect(uri) as ws:
        await ws.send(json.dumps({"query": query}))
        tool_calls: list[dict] = []
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                return {"answer": "[timeout]", "thinking": "", "tool_calls": tool_calls, "session_id": session_id}
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
            except asyncio.TimeoutError:
                return {"answer": "[timeout]", "thinking": "", "tool_calls": tool_calls, "session_id": session_id}
            msg = json.loads(raw)
            t = msg.get("type")
            if t == "tool_result":
                tool_calls.append({"tool": msg.get("tool", ""), "result": msg.get("result", "")})
            elif t == "response":
                return {
                    "answer": msg.get("answer", ""),
                    "thinking": msg.get("thinking", ""),
                    "tool_calls": msg.get("tool_calls") or tool_calls,
                    "session_id": msg.get("session_id") or session_id,
                }


def run_story(story_path: str, api_base: str) -> bool:
    story = yaml.safe_load(Path(story_path).read_text())
    story_id = story.get("story_id", "?")
    title = story.get("title", "")
    print(f"\n{'='*60}")
    print(f"Story {story_id}: {title}")
    print(f"{'='*60}")

    ws_base = api_base.replace("http://", "ws://").replace("https://", "wss://")
    session_id: str = str(uuid.uuid4())[:8]
    all_passed = True
    rg_checks = story.get("response_generation_checks", {})
    inter_turn_delay: float = story.get("inter_turn_delay_secs", 0.0)

    for i, turn in enumerate(story.get("turns", []), 1):
        query = turn["query"]
        print(f"\n--- Turn {i}: {query!r} ---")

        if i > 1 and inter_turn_delay > 0:
            print(f"  [waiting {inter_turn_delay}s for background agents…]")
            time.sleep(inter_turn_delay)

        if turn.get("new_session"):
            session_id = str(uuid.uuid4())[:8]

        try:
            data = asyncio.run(_ws_query(ws_base, session_id, query))
        except Exception as exc:
            print(f"  ERROR  WebSocket request failed: {exc}")
            return False

        session_id = data.get("session_id") or session_id

        answer: str = data.get("answer") or ""
        thinking: str = data.get("thinking") or ""
        tool_calls: list = data.get("tool_calls") or []

        print(f"  answer   : {answer[:120]}{'…' if len(answer) > 120 else ''}")
        print(f"  thinking : {len(thinking)} chars")
        print(f"  tool_calls: {len(tool_calls)}")

        for expected in turn.get("expected_content", []):
            # "|" means OR — any alternative must appear in the answer
            alts = [a.strip() for a in expected.split("|")]
            matched = any(a.lower() in answer.lower() for a in alts)
            label = f"answer contains {expected!r}" if len(alts) == 1 else f"answer contains one of {alts}"
            ok = _check(label, matched)
            all_passed = all_passed and ok

        for banned in turn.get("must_not_contain", []):
            ok = _check(f"answer excludes {banned!r}", banned.lower() not in answer.lower())
            all_passed = all_passed and ok

        turn_rg_checks = {**rg_checks, **turn.get("response_generation_checks", {})}
        rg_checks = story.get("response_generation_checks", {})

        if turn_rg_checks.get("answer_not_empty"):
            ok = _check("answer not empty", bool(answer.strip()))
            all_passed = all_passed and ok

        if turn_rg_checks.get("thinking_not_empty"):
            ok = _check("thinking not empty", bool(thinking.strip()),
                        "check think=True reaches ollama" if not thinking.strip() else "")
            all_passed = all_passed and ok

        if turn_rg_checks.get("tool_calls_empty"):
            ok = _check("no tool calls", len(tool_calls) == 0, str(tool_calls) if tool_calls else "")
            all_passed = all_passed and ok

        if turn_rg_checks.get("tool_calls_not_empty"):
            ok = _check("at least one tool called", len(tool_calls) > 0)
            all_passed = all_passed and ok

        called_names = {tc.get("tool") for tc in tool_calls}
        for required_tool in turn_rg_checks.get("tool_names_include", []):
            ok = _check(f"tool {required_tool!r} called", required_tool in called_names,
                        f"called: {sorted(called_names)}" if called_names else "no tools fired")
            all_passed = all_passed and ok

    print(f"\n{'='*60}")
    verdict = "PASS" if all_passed else "FAIL"
    print(f"Story {story_id}: {verdict}")
    print(f"{'='*60}\n")
    return all_passed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("story", nargs="?", help="Path to story YAML (omit to run all)")
    parser.add_argument("--api", default="http://localhost:8000", metavar="URL")
    args = parser.parse_args()

    if args.story:
        passed = run_story(args.story, args.api)
        sys.exit(0 if passed else 1)
    else:
        stories_dir = Path(__file__).parent / "stories"
        files = sorted(stories_dir.glob("*.yaml"))
        results = {}
        for f in files:
            results[f.name] = run_story(str(f), args.api)
        print("\n=== Summary ===")
        for name, ok in results.items():
            print(f"  {'PASS' if ok else 'FAIL'}  {name}")
        sys.exit(0 if all(results.values()) else 1)


if __name__ == "__main__":
    main()
