#!/usr/bin/env python3
"""
benchmark_turboquant.py — TurboQuant vs standard KV cache benchmark.

Run from the mlx_env environment (has mlx, mlx_lm, turboquant installed).
Tests TQ-V2 3-bit, TQ-V2 4-bit, and standard float16 KV cache across
multiple context lengths and prompts, producing output comparable to
the Ollama benchmark table.

Usage:
    cd /Users/rkoike/LoCAL2
    conda activate mlx_env  # or source .venv/bin/activate
    python scripts/benchmark_turboquant.py
    python scripts/benchmark_turboquant.py --model mlx-community/gemma-4-27b-it-4bit
    python scripts/benchmark_turboquant.py --contexts 0 4000 16000 32000
"""

import argparse
import sys
import time
from dataclasses import dataclass, asdict
from typing import Optional
import json

import sys
from pathlib import Path

# turboquant lives as a local package in turboquant-mlx/, not installed
_tq_root = Path(__file__).parent.parent / "turboquant-mlx"
if str(_tq_root) not in sys.path:
    sys.path.insert(0, str(_tq_root))

import mlx.core as mx
import mlx_lm
from mlx_lm.generate import generate_step
from mlx_lm.models.cache import make_prompt_cache

import turboquant.patch as tq_patch
tq_patch.apply()
from turboquant.cache_v2 import TurboQuantKVCacheV2

FILLER_PARA = (
    "The mitochondria are the powerhouse of the cell. "
    "Ribosomes synthesize proteins from messenger RNA templates. "
    "The nucleus contains the cell's genetic material in the form of DNA. "
    "Cell membranes regulate the passage of substances into and out of the cell. "
) * 5

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

CACHE_CONFIGS = [
    ("TQ-V2 3-bit", "turboquant", dict(bits=3)),
    ("TQ-V2 4-bit", "turboquant", dict(bits=4)),
    ("Standard f16", "standard",  {}),
]


@dataclass
class Result:
    model: str
    cache_type: str
    prompt_name: str
    context_pad_tokens: int
    prompt_tokens: int
    output_tokens: int
    ttft_ms: float
    decode_tps: float
    total_s: float
    cache_bytes: int
    cache_compression: float  # vs fp16; 1.0 for standard
    json_valid: Optional[bool] = None
    error: str = ""


def make_tq_cache(model, bits=3, group_size=64):
    from mlx_lm.models.cache import make_prompt_cache

    # Collect head_dim per layer — mixed architectures (e.g. Gemma 4) have
    # sliding_attention layers (small head_dim) and full_attention layers (large head_dim).
    head_dims = []
    for layer in model.layers:
        try:
            head_dims.append(layer.self_attn.head_dim)
        except AttributeError:
            head_dims.append(head_dims[0] if head_dims else 64)

    base_head_dim = head_dims[0]
    uniform = len(set(head_dims)) == 1

    if uniform:
        # Llama-style: apply TurboQuant to all layers
        return [
            TurboQuantKVCacheV2(head_dim=base_head_dim, bits=bits, group_size=group_size, seed=42 + i)
            for i in range(len(model.layers))
        ]

    # Mixed architecture (Gemma 4): apply TurboQuant only to full_attention layers
    # (larger head_dim). Sliding layers use standard cache — already bounded by window.
    std_caches = make_prompt_cache(model)
    caches = []
    tq_count = 0
    for i, (hd, std_cache) in enumerate(zip(head_dims, std_caches)):
        if hd > base_head_dim:
            caches.append(TurboQuantKVCacheV2(head_dim=hd, bits=bits, group_size=group_size, seed=42 + i))
            tq_count += 1
        else:
            caches.append(std_cache)
    print(f"    [TurboQuant applied to {tq_count}/{len(model.layers)} full-attention layers]", flush=True)
    return caches


def cache_bytes(cache) -> tuple[int, int]:
    """Returns (actual_bytes, fp16_equivalent_bytes)."""
    actual = 0
    fp16_equiv = 0
    for c in cache:
        if hasattr(c, "nbytes") and hasattr(c, "nbytes_equivalent_fp16"):
            actual += c.nbytes
            fp16_equiv += c.nbytes_equivalent_fp16
        elif hasattr(c, "keys") and c.keys is not None:
            kb = c.keys.nbytes if hasattr(c.keys, "nbytes") else 0
            vb = c.values.nbytes if hasattr(c.values, "nbytes") else 0
            actual += kb + vb
            fp16_equiv += kb + vb  # standard cache: compression = 1.0
    return actual, fp16_equiv


def pad_to_tokens(target: int) -> str:
    if target <= 0:
        return ""
    repeats = (target // 100) + 1
    return (FILLER_PARA * repeats)[: target * 4]


def build_prompt(tokenizer, prompt_text: str, pad_tokens: int) -> mx.array:
    messages = []
    if pad_tokens > 0:
        pad = pad_to_tokens(pad_tokens)
        messages.append({"role": "user", "content": pad})
        messages.append({"role": "assistant", "content": "Understood."})
    messages.append({"role": "user", "content": prompt_text})
    formatted = tokenizer.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True
    )
    return mx.array(tokenizer.encode(formatted))


def run_generation(model, tokenizer, input_ids, cache, max_tokens: int):
    tokens = []
    first_token_time = None
    t0 = time.perf_counter()

    for token, _ in generate_step(
        prompt=input_ids,
        model=model,
        max_tokens=max_tokens,
        prompt_cache=cache,
    ):
        tok = token.item() if hasattr(token, "item") else int(token)
        if tok == tokenizer.eos_token_id:
            break
        if first_token_time is None:
            first_token_time = time.perf_counter()
        tokens.append(tok)

    total_s = time.perf_counter() - t0
    ttft_ms = (first_token_time - t0) * 1000 if first_token_time else total_s * 1000
    decode_s = total_s - ttft_ms / 1000
    decode_tps = (len(tokens) - 1) / decode_s if decode_s > 0 and len(tokens) > 1 else 0.0
    text = tokenizer.decode(tokens)
    return text, tokens, ttft_ms, decode_tps, total_s


def benchmark_one(
    model, tokenizer, model_name, cache_label, cache_kind, cache_kwargs,
    prompt_name, prompt_text, pad_tokens, max_tokens
) -> Result:
    input_ids = build_prompt(tokenizer, prompt_text, pad_tokens)
    prompt_token_count = input_ids.size

    if cache_kind == "turboquant":
        cache = make_tq_cache(model, **cache_kwargs)
    else:
        cache = make_prompt_cache(model)

    error = ""
    text = ""
    output_tokens = ttft_ms = decode_tps = total_s = 0.0
    actual_bytes = fp16_bytes = 0
    json_valid = None

    try:
        text, tokens, ttft_ms, decode_tps, total_s = run_generation(
            model, tokenizer, input_ids, cache, max_tokens
        )
        output_tokens = len(tokens)
        actual_bytes, fp16_bytes = cache_bytes(cache)
    except Exception as e:
        error = str(e)[:60]

    compression = fp16_bytes / actual_bytes if actual_bytes > 0 else 1.0

    if prompt_name == "tool_json" and text:
        try:
            parsed = json.loads(text.strip())
            json_valid = isinstance(parsed, dict) and "name" in parsed
        except json.JSONDecodeError:
            json_valid = False

    return Result(
        model=model_name,
        cache_type=cache_label,
        prompt_name=prompt_name,
        context_pad_tokens=pad_tokens,
        prompt_tokens=int(prompt_token_count),
        output_tokens=int(output_tokens),
        ttft_ms=round(ttft_ms, 1),
        decode_tps=round(decode_tps, 2),
        total_s=round(total_s, 2),
        cache_bytes=actual_bytes,
        cache_compression=round(compression, 2),
        json_valid=json_valid,
        error=error,
    )


def print_table(results: list[Result]) -> None:
    cols = [
        ("Cache", 14),
        ("Prompt", 10),
        ("Ctx pad", 8),
        ("In tok", 7),
        ("Out tok", 7),
        ("TTFT ms", 9),
        ("Tok/s", 7),
        ("Total s", 8),
        ("Cache MB", 9),
        ("Compress", 9),
        ("JSON", 5),
        ("Error", 25),
    ]
    header = "  ".join(f"{n:<{w}}" for n, w in cols)
    sep = "=" * len(header)
    print(f"\n{sep}\n{header}\n{sep}")
    for r in results:
        json_str = "" if r.json_valid is None else ("✓" if r.json_valid else "✗")
        mb = f"{r.cache_bytes / 1024**2:.1f}" if r.cache_bytes else "—"
        compress = f"{r.cache_compression:.1f}x" if r.cache_compression != 1.0 else "1.0x"
        row = [
            r.cache_type, r.prompt_name, str(r.context_pad_tokens),
            str(r.prompt_tokens), str(r.output_tokens),
            f"{r.ttft_ms:.0f}", f"{r.decode_tps:.1f}", f"{r.total_s:.1f}",
            mb, compress, json_str, r.error,
        ]
        print("  ".join(f"{v:<{w}}" for v, (_, w) in zip(row, cols)))
    print(sep + "\n")


def main():
    parser = argparse.ArgumentParser(description="TurboQuant vs standard KV cache benchmark")
    parser.add_argument("--model", default="mlx-community/Llama-3.2-3B-Instruct-4bit")
    parser.add_argument("--contexts", nargs="+", type=int, default=[0, 4000, 16000, 32000])
    parser.add_argument("--prompts", nargs="+", choices=list(PROMPTS.keys()), default=list(PROMPTS.keys()))
    parser.add_argument("--max-tokens", type=int, default=200)
    parser.add_argument("--output", help="Save results to JSON")
    args = parser.parse_args()

    print(f"\nLoading model: {args.model}")
    model, tokenizer = mlx_lm.load(args.model)
    print(f"Model loaded: {len(model.layers)} layers\n")

    configs = [
        (label, kind, kwargs)
        for label, kind, kwargs in CACHE_CONFIGS
        for _ in [None]  # flatten
    ]

    total = len(configs) * len(args.prompts) * len(args.contexts)
    print(f"Running {total} benchmark(s) — model: {args.model}")
    print(f"Contexts: {args.contexts}  |  Prompts: {args.prompts}\n")

    all_results: list[Result] = []
    i = 0
    for prompt_name in args.prompts:
        for pad_tokens in args.contexts:
            for cache_label, cache_kind, cache_kwargs in CACHE_CONFIGS:
                i += 1
                label = f"[{i}/{total}] {cache_label} | {prompt_name} | ctx={pad_tokens}"
                print(f"  {label}...", end="", flush=True)
                result = benchmark_one(
                    model, tokenizer, args.model,
                    cache_label, cache_kind, cache_kwargs,
                    prompt_name, PROMPTS[prompt_name],
                    pad_tokens, args.max_tokens,
                )
                all_results.append(result)
                status = (
                    f"  ERROR: {result.error}" if result.error
                    else f"  {result.decode_tps:.1f} tok/s  TTFT {result.ttft_ms:.0f}ms  "
                         f"cache {result.cache_bytes/1024**2:.1f}MB ({result.cache_compression:.1f}x)"
                )
                print(status)

    print_table(all_results)

    if args.output:
        with open(args.output, "w") as f:
            json.dump([asdict(r) for r in all_results], f, indent=2)
        print(f"Results saved to {args.output}")


if __name__ == "__main__":
    main()
