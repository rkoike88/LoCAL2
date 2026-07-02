#!/usr/bin/env python3
"""
benchmark_inference.py — Inference speed and memory benchmarks.

Measures TTFT, decode tokens/sec, and memory at multiple context lengths.
Works with Ollama now. MLX/TurboQuant support: add --backend mlx once
mlx_lm.server is running (OpenAI-compatible endpoint on port 8080).

Usage:
    python scripts/benchmark_inference.py
    python scripts/benchmark_inference.py --models gemma4:e4b gemma4:31b
    python scripts/benchmark_inference.py --model gemma4:31b --contexts 0 8000 32000 64000
    python scripts/benchmark_inference.py --model gemma4:31b --backend mlx

NOTE: To benchmark KV cache types (q8_0, f16), restart Ollama with the env var set:
    OLLAMA_KV_CACHE_TYPE=q8_0 ollama serve
    Then re-run this script and compare results.
"""

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass, asdict
from typing import Optional

import httpx

OLLAMA_BASE = "http://localhost:11434"
MLX_BASE = "http://localhost:8081"

FILLER_PARA = (
    "The mitochondria are the powerhouse of the cell. "
    "Ribosomes synthesize proteins from messenger RNA templates. "
    "The nucleus contains the cell's genetic material in the form of DNA. "
    "Cell membranes regulate the passage of substances into and out of the cell. "
) * 5  # ~100 tokens per repeat

PROMPTS = {
    "simple": "What is the capital of France? Answer in one word.",
    "reasoning": (
        "A train leaves City A at 60 mph. Another leaves City B, 300 miles away, "
        "at 90 mph toward City A at the same time. When and where do they meet? Show your work."
    ),
    "tool_json": (
        "Respond only with a JSON object representing a tool call to search the web. "
        'Format: {"name": "web_search", "parameters": {"query": "<your query>"}}. '
        "Query: current weather in Tokyo."
    ),
}


@dataclass
class Result:
    model: str
    backend: str
    context_pad_tokens: int
    prompt_name: str
    prompt_tokens: int
    output_tokens: int
    ttft_ms: float
    decode_tps: float
    total_s: float
    ollama_mem_rss_gb: float
    system_mem_used_gb: float
    json_valid: Optional[bool] = None
    error: str = ""


def get_ollama_pids() -> list[int]:
    try:
        out = subprocess.check_output(["pgrep", "-f", "ollama"], text=True)
        return [int(p) for p in out.strip().splitlines()]
    except subprocess.CalledProcessError:
        return []


def get_process_rss_gb(pids: list[int]) -> float:
    if not pids:
        return 0.0
    try:
        pid_args = [str(p) for p in pids]
        out = subprocess.check_output(
            ["ps", "-o", "rss=", "-p", ",".join(pid_args)], text=True
        )
        total_kb = sum(int(x) for x in out.strip().splitlines() if x.strip())
        return total_kb / 1024 / 1024
    except Exception:
        return 0.0


def get_system_mem_used_gb() -> float:
    try:
        out = subprocess.check_output(["vm_stat"], text=True)
        page_size = 16384  # Apple Silicon page size (16KB)
        stats: dict[str, int] = {}
        for line in out.splitlines():
            if ":" in line:
                key, _, val = line.partition(":")
                val = val.strip().rstrip(".")
                if val.isdigit():
                    stats[key.strip()] = int(val)
        active = stats.get("Pages active", 0)
        wired = stats.get("Pages wired down", 0)
        occupied = stats.get("Pages occupied by compressor", 0)
        used_bytes = (active + wired + occupied) * page_size
        return used_bytes / 1024**3
    except Exception:
        return 0.0


def pad_to_tokens(target_tokens: int) -> str:
    if target_tokens <= 0:
        return ""
    repeats = (target_tokens // 100) + 1
    text = (FILLER_PARA * repeats)[: target_tokens * 4]
    return text


def run_ollama(model: str, messages: list[dict], num_ctx: int) -> dict:
    with httpx.Client(timeout=300) as client:
        resp = client.post(
            f"{OLLAMA_BASE}/api/chat",
            json={
                "model": model,
                "messages": messages,
                "stream": False,
                "options": {"num_ctx": num_ctx, "temperature": 0.1},
            },
        )
        resp.raise_for_status()
        return resp.json()


def run_mlx(model: str, messages: list[dict]) -> tuple[str, int, int, float, float]:
    """Returns (content, prompt_tokens, output_tokens, ttft_ms, decode_tps)."""
    t0 = time.perf_counter()
    first_token_time: Optional[float] = None
    content = ""
    output_tokens = 0

    with httpx.Client(timeout=300) as client:
        with client.stream(
            "POST",
            f"{MLX_BASE}/v1/chat/completions",
            json={
                "model": model,
                "messages": messages,
                "stream": True,
                "temperature": 0.1,
                "max_tokens": 512,
            },
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                    delta = chunk["choices"][0]["delta"].get("content", "")
                    if delta:
                        if first_token_time is None:
                            first_token_time = time.perf_counter()
                        content += delta
                        output_tokens += 1
                except (json.JSONDecodeError, KeyError):
                    pass

    total_s = time.perf_counter() - t0
    ttft_ms = (first_token_time - t0) * 1000 if first_token_time else 0.0
    decode_s = total_s - ttft_ms / 1000
    decode_tps = output_tokens / decode_s if decode_s > 0 else 0.0
    return content, 0, output_tokens, ttft_ms, decode_tps


def benchmark_one(
    model: str,
    backend: str,
    prompt_name: str,
    prompt_text: str,
    context_pad_tokens: int,
    num_ctx: int,
) -> Result:
    pids = get_ollama_pids()
    sys_mem_before = get_system_mem_used_gb()

    messages: list[dict] = []
    if context_pad_tokens > 0:
        pad = pad_to_tokens(context_pad_tokens)
        messages.append({"role": "user", "content": pad})
        messages.append({"role": "assistant", "content": "Understood, I have read the context."})
    messages.append({"role": "user", "content": prompt_text})

    t0 = time.perf_counter()
    error = ""
    ttft_ms = decode_tps = prompt_tokens = output_tokens = 0.0
    content = ""
    json_valid: Optional[bool] = None

    try:
        if backend == "ollama":
            data = run_ollama(model, messages, num_ctx)
            msg = data.get("message", {})
            content = msg.get("content", "")
            prompt_tokens = data.get("prompt_eval_count", 0)
            output_tokens = data.get("eval_count", 0)
            prompt_eval_ns = data.get("prompt_eval_duration", 0)
            eval_ns = data.get("eval_duration", 1)
            ttft_ms = prompt_eval_ns / 1e6
            decode_tps = output_tokens / (eval_ns / 1e9) if eval_ns > 0 else 0.0
        else:
            content, prompt_tokens, output_tokens, ttft_ms, decode_tps = run_mlx(
                model, messages
            )
    except Exception as e:
        error = str(e)

    total_s = time.perf_counter() - t0
    sys_mem_after = get_system_mem_used_gb()
    ollama_rss = get_process_rss_gb(pids)

    if prompt_name == "tool_json" and content:
        try:
            parsed = json.loads(content.strip())
            json_valid = isinstance(parsed, dict) and "name" in parsed
        except json.JSONDecodeError:
            json_valid = False

    return Result(
        model=model,
        backend=backend,
        context_pad_tokens=context_pad_tokens,
        prompt_name=prompt_name,
        prompt_tokens=int(prompt_tokens),
        output_tokens=int(output_tokens),
        ttft_ms=round(ttft_ms, 1),
        decode_tps=round(decode_tps, 2),
        total_s=round(total_s, 2),
        ollama_mem_rss_gb=round(ollama_rss, 2),
        system_mem_used_gb=round(sys_mem_after - sys_mem_before, 2),
        json_valid=json_valid,
        error=error,
    )


def print_table(results: list[Result]) -> None:
    cols = [
        ("Model", 18),
        ("Backend", 8),
        ("Prompt", 10),
        ("Ctx pad", 8),
        ("In tok", 7),
        ("Out tok", 7),
        ("TTFT ms", 9),
        ("Tok/s", 7),
        ("Total s", 8),
        ("RSS GB", 7),
        ("JSON", 5),
        ("Error", 20),
    ]

    header = "  ".join(f"{name:<{w}}" for name, w in cols)
    print("\n" + "=" * len(header))
    print(header)
    print("=" * len(header))

    for r in results:
        json_str = "" if r.json_valid is None else ("✓" if r.json_valid else "✗")
        row = [
            r.model,
            r.backend,
            r.prompt_name,
            str(r.context_pad_tokens),
            str(r.prompt_tokens),
            str(r.output_tokens),
            f"{r.ttft_ms:.0f}",
            f"{r.decode_tps:.1f}",
            f"{r.total_s:.1f}",
            f"{r.ollama_mem_rss_gb:.2f}",
            json_str,
            r.error[:20],
        ]
        print("  ".join(f"{val:<{w}}" for val, (_, w) in zip(row, cols)))

    print("=" * len(header) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="LoCAL2 inference benchmark")
    parser.add_argument(
        "--models", nargs="+", default=["gemma4:e4b"], help="Ollama model names"
    )
    parser.add_argument(
        "--backend", choices=["ollama", "mlx"], default="ollama"
    )
    parser.add_argument(
        "--contexts",
        nargs="+",
        type=int,
        default=[0, 4000, 16000, 32000],
        help="Context padding sizes in tokens",
    )
    parser.add_argument(
        "--prompts",
        nargs="+",
        choices=list(PROMPTS.keys()),
        default=["simple", "reasoning", "tool_json"],
    )
    parser.add_argument(
        "--num-ctx", type=int, default=128000, help="Ollama num_ctx setting"
    )
    parser.add_argument("--output", help="Save results to JSON file")
    parser.add_argument("--runs", type=int, default=1, help="Runs per configuration")
    parser.add_argument("--mlx-port", type=int, default=8081, help="Port for mlx_lm.server")
    args = parser.parse_args()

    global MLX_BASE
    MLX_BASE = f"http://localhost:{args.mlx_port}"

    configs = [
        (model, backend, prompt_name, ctx)
        for model in args.models
        for backend in [args.backend]
        for prompt_name in args.prompts
        for ctx in args.contexts
    ]

    total = len(configs) * args.runs
    print(f"\nRunning {total} benchmark(s)...")
    print("Models:", args.models)
    print("Contexts:", args.contexts)
    print("Prompts:", args.prompts)
    print()

    all_results: list[Result] = []
    for i, (model, backend, prompt_name, ctx) in enumerate(configs, 1):
        for run in range(args.runs):
            label = f"[{i}/{total}] {model} | {prompt_name} | ctx={ctx}"
            print(f"  {label}...", end="", flush=True)
            result = benchmark_one(
                model=model,
                backend=backend,
                prompt_name=prompt_name,
                prompt_text=PROMPTS[prompt_name],
                context_pad_tokens=ctx,
                num_ctx=args.num_ctx,
            )
            all_results.append(result)
            status = f"  {result.decode_tps:.1f} tok/s  TTFT {result.ttft_ms:.0f}ms"
            if result.error:
                status = f"  ERROR: {result.error[:40]}"
            print(status)

    print_table(all_results)

    if args.output:
        with open(args.output, "w") as f:
            json.dump([asdict(r) for r in all_results], f, indent=2)
        print(f"Results saved to {args.output}")

    print("Tips:")
    print("  Compare KV cache types: restart Ollama with OLLAMA_KV_CACHE_TYPE=q8_0, re-run, diff results.")
    print("  TurboQuant: start mlx_lm.server, use --backend mlx --models <mlx-model-name>.")


if __name__ == "__main__":
    main()
