"""Story runner — executes a YAML story against the live LoCAL2 API.

Usage:
    PYTHONPATH=src python tests/run_story.py tests/stories/s1_basic_qa.yaml
    PYTHONPATH=src python tests/run_story.py tests/stories/s2_multi_turn.yaml --api http://localhost:8000
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx
import yaml


def _check(label: str, passed: bool, detail: str = "") -> bool:
    mark = "  PASS" if passed else "  FAIL"
    print(f"{mark}  {label}" + (f" — {detail}" if detail else ""))
    return passed


def run_story(story_path: str, api_base: str) -> bool:
    story = yaml.safe_load(Path(story_path).read_text())
    story_id = story.get("story_id", "?")
    title = story.get("title", "")
    print(f"\n{'='*60}")
    print(f"Story {story_id}: {title}")
    print(f"{'='*60}")

    session_id: str | None = None
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
            session_id = None

        payload: dict = {"query": query, "timeout": 120.0}
        if session_id:
            payload["session_id"] = session_id

        try:
            resp = httpx.post(f"{api_base}/query", json=payload, timeout=130.0)
        except Exception as exc:
            print(f"  ERROR  HTTP request failed: {exc}")
            return False

        ok = _check("HTTP 200", resp.status_code == 200, f"got {resp.status_code}")
        all_passed = all_passed and ok
        if not ok:
            print(f"  body: {resp.text[:200]}")
            continue

        data = resp.json()
        session_id = data.get("session_id")   # carry forward for multi-turn

        answer: str = data.get("answer") or ""
        thinking: str = data.get("thinking") or ""
        tool_calls: list = data.get("tool_calls") or []

        print(f"  answer   : {answer[:120]}{'…' if len(answer) > 120 else ''}")
        print(f"  thinking : {len(thinking)} chars")
        print(f"  tool_calls: {len(tool_calls)}")

        # Per-turn content checks
        for expected in turn.get("expected_content", []):
            ok = _check(f"answer contains {expected!r}", expected.lower() in answer.lower())
            all_passed = all_passed and ok

        for banned in turn.get("must_not_contain", []):
            ok = _check(f"answer excludes {banned!r}", banned.lower() not in answer.lower())
            all_passed = all_passed and ok

        # Per-turn checks override story-level checks where specified
        turn_rg_checks = {**rg_checks, **turn.get("response_generation_checks", {})}

        # response_generation_checks apply to every turn (merged with per-turn)
        rg_checks = story.get("response_generation_checks", {})  # reset to story-level for next turn
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
    parser.add_argument("story", help="Path to story YAML file")
    parser.add_argument("--api", default="http://localhost:8000", metavar="URL")
    args = parser.parse_args()

    passed = run_story(args.story, args.api)
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
