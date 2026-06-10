"""YAML-based config loader for LoCAL2 agents and services."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _config_search_paths(name: str) -> list[Path]:
    """Return candidate config paths in priority order.

    1. User data dir (``~/.local2/config/``) — user-editable, takes precedence.
    2. Repo ``config/`` dir — present in dev checkouts.
    3. Bundled package defaults (``src/local/defaults/``) — fallback for installed package.
    """
    from local.data_dir import get_data_dir, get_defaults_dir

    filename = f"{name}.yaml"
    return [
        get_data_dir() / "config" / filename,
        _repo_root() / "config" / filename,
        get_defaults_dir() / filename,
    ]


class ConfigManager:
    """In-process cache of YAML configs loaded from ``config/<name>.yaml``.

    All agents and services call ``get_config(name)`` rather than reading
    YAML directly. Cache is process-wide (class-level dict). Call
    ``invalidate(name)`` to force a fresh read on next access — used by
    ``BaseTool`` when it receives a ``TOOL_SCHEMA_REQUEST``.
    """

    _configs: dict[str, dict[str, Any]] = {}

    @classmethod
    def load(cls, name: str) -> dict[str, Any]:
        """Load ``<name>.yaml``, searching user data dir then repo then package defaults.

        Args:
            name: Config file stem (e.g. ``"generator"`` loads
                ``config/generator.yaml``).

        Returns:
            Parsed YAML as a dict, or ``{}`` if not found anywhere.
        """
        if name in cls._configs:
            return cls._configs[name]
        config: dict[str, Any] = {}
        for path in _config_search_paths(name):
            try:
                with path.open("r", encoding="utf-8") as f:
                    config = yaml.safe_load(f) or {}
                break
            except FileNotFoundError:
                continue
        cls._configs[name] = config
        return config

    @classmethod
    def save(cls, name: str, config: dict[str, Any]) -> None:
        """Write config to the user data dir and update cache.

        Always writes to ``~/.local2/config/<name>.yaml`` so that the installed
        package files are never modified by user edits.

        Args:
            name: Config file stem.
            config: Dict to serialise.
        """
        from local.data_dir import get_data_dir

        config_dir = get_data_dir() / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_path = config_dir / f"{name}.yaml"
        with config_path.open("w", encoding="utf-8") as f:
            yaml.dump(dict(config), f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        cls._configs[name] = dict(config)

    @classmethod
    def invalidate(cls, name: str | None = None) -> None:
        """Clear cached config(s). Pass None to clear all."""
        if name is None:
            cls._configs.clear()
        else:
            cls._configs.pop(name, None)


def get_config(name: str) -> dict[str, Any]:
    """Return the config dict for ``<name>.yaml``, caching on first read.

    Args:
        name: Config file stem (e.g. ``"generator"`` → ``config/generator.yaml``).

    Returns:
        Parsed YAML as a dict, or ``{}`` if the file is absent everywhere.
    """
    return ConfigManager.load(name)
