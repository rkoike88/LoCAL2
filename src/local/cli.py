"""LoCAL2 command-line entry point.

Usage::

    local2                       # web UI, opens browser
    local2 --headless            # web server only
    local2 --panels              # web UI + Qt observer panels
    local2 --desktop             # legacy PySide6 UI
    local2 setup                 # first-run setup: init config + pull Ollama models
    local2 setup --models-only   # re-pull models without touching config
    local2 setup --config-only   # re-init config without pulling models
    local2 searxng up            # start SearXNG in Docker (background)
    local2 searxng down          # stop SearXNG
    local2 searxng status        # show container status
"""
from __future__ import annotations

import sys


def _cmd_setup(models_only: bool = False, config_only: bool = False) -> None:
    """First-run initialisation: write user config, SearXNG files, and pull Ollama models."""
    import os
    import shutil
    import subprocess
    from pathlib import Path

    from local.data_dir import get_data_dir, get_defaults_dir

    data_dir = get_data_dir()
    config_dir = data_dir / "config"
    defaults = get_defaults_dir()

    if not models_only:
        # --- YAML configs ---
        config_dir.mkdir(parents=True, exist_ok=True)
        copied = []
        for src in sorted(defaults.glob("*.yaml")):
            dst = config_dir / src.name
            if not dst.exists():
                shutil.copy2(src, dst)
                copied.append(src.name)
        if copied:
            print(f"[setup] Wrote default configs to {config_dir}:")
            for name in copied:
                print(f"        {name}")
        else:
            print(f"[setup] Config already initialised at {config_dir}")

        # --- SearXNG docker-compose + settings ---
        compose_src = defaults / "docker-compose.yml"
        compose_dst = data_dir / "docker-compose.yml"
        searxng_src = defaults / "searxng"
        searxng_dst = data_dir / "searxng"

        if compose_src.exists() and not compose_dst.exists():
            shutil.copy2(compose_src, compose_dst)
            shutil.copytree(searxng_src, searxng_dst, dirs_exist_ok=True)
            print(f"[setup] Wrote SearXNG files to {data_dir}")

        # --- .env for SearXNG secret ---
        env_file = data_dir / ".env"
        if not env_file.exists():
            secret = os.urandom(32).hex()
            env_file.write_text(f"MY_SEARX_SECRET={secret}\n")
            print(f"[setup] Generated SearXNG secret in {env_file}")

    if not config_only:
        models = ["gemma4:e4b", "nomic-embed-text"]
        for model in models:
            print(f"[setup] Pulling {model} …")
            result = subprocess.run(["ollama", "pull", model], check=False)
            if result.returncode != 0:
                print(f"[setup] Warning: 'ollama pull {model}' failed (exit {result.returncode})")
                print("        Make sure Ollama is installed: https://ollama.com/download")

    print("[setup] Done. Run 'local2' to start.")
    print("        For web search: run 'local2 searxng up' (requires Docker Desktop)")


def _cmd_searxng(action: str) -> None:
    """Manage the SearXNG Docker container."""
    import subprocess
    from local.data_dir import get_data_dir

    data_dir = get_data_dir()
    compose_file = data_dir / "docker-compose.yml"

    if not compose_file.exists():
        print("[searxng] docker-compose.yml not found. Run 'local2 setup' first.")
        sys.exit(1)

    if action == "up":
        env_file = data_dir / ".env"
        if not env_file.exists():
            import os
            secret = os.urandom(32).hex()
            env_file.write_text(f"MY_SEARX_SECRET={secret}\n")
        result = subprocess.run(
            ["docker", "compose", "--env-file", str(env_file), "-f", str(compose_file), "up", "-d"],
            check=False,
        )
    elif action == "down":
        result = subprocess.run(
            ["docker", "compose", "-f", str(compose_file), "down"],
            check=False,
        )
    elif action == "status":
        result = subprocess.run(
            ["docker", "compose", "-f", str(compose_file), "ps"],
            check=False,
        )
    else:
        print(f"[searxng] Unknown action '{action}'. Use: up, down, status")
        sys.exit(1)

    if result.returncode != 0 and action != "status":
        print("[searxng] Command failed. Is Docker Desktop running?")
        sys.exit(result.returncode)


def main() -> None:  # noqa: C901
    """Unified LoCAL2 entry point."""
    args = sys.argv[1:]

    # --- setup subcommand ---------------------------------------------------
    if args and args[0] == "setup":
        import argparse
        p = argparse.ArgumentParser(prog="local2 setup")
        p.add_argument("--models-only", action="store_true")
        p.add_argument("--config-only", action="store_true")
        opts = p.parse_args(args[1:])
        _cmd_setup(models_only=opts.models_only, config_only=opts.config_only)
        return

    # --- searxng subcommand -------------------------------------------------
    if args and args[0] == "searxng":
        action = args[1] if len(args) > 1 else "status"
        _cmd_searxng(action)
        return

    # --- delegate to run_local main() ---------------------------------------
    try:
        from local import run
        run.main()
    except ImportError:
        import importlib.util
        from pathlib import Path

        run_local = Path(__file__).resolve().parents[2] / "run_local.py"
        spec = importlib.util.spec_from_file_location("run_local", run_local)
        mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        mod.main()
