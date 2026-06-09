"""YAML-based config loader for LoCAL2 agents and services."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


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
        """Load ``config/<name>.yaml``, caching the result in-process.

        Args:
            name: Config file stem (e.g. ``"generator"`` loads
                ``config/generator.yaml``).

        Returns:
            Parsed YAML as a dict, or ``{}`` if the file is absent.
        """
        if name in cls._configs:
            return cls._configs[name]
        config_path = _repo_root() / "config" / f"{name}.yaml"
        try:
            with config_path.open("r", encoding="utf-8") as f:
                config = yaml.safe_load(f) or {}
        except FileNotFoundError:
            config = {}
        cls._configs[name] = config
        return config

    @classmethod
    def save(cls, name: str, config: dict[str, Any]) -> None:
        """Write config back to config/<name>.yaml and update cache."""
        root = _repo_root() / "config"
        root.mkdir(parents=True, exist_ok=True)
        config_path = root / f"{name}.yaml"
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
    """Return the config dict for ``config/<name>.yaml``, caching on first read.

    Args:
        name: Config file stem (e.g. ``"generator"`` → ``config/generator.yaml``).

    Returns:
        Parsed YAML as a dict, or ``{}`` if the file is absent.
    """
    return ConfigManager.load(name)
