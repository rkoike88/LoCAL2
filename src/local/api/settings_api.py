"""Config YAML read/write helpers for the settings REST API.

Exposes a fixed set of allowed section names that map to config/*.yaml files.
All reads and writes go through this module to prevent path traversal.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

# Resolve config/ relative to this file's location in src/local/api/.
_CONFIG_DIR = Path(__file__).resolve().parent.parent.parent.parent / "config"


class _BlockDumper(yaml.Dumper):
    """YAML dumper that uses literal block scalar (|) for multiline strings."""


def _str_representer(dumper: yaml.Dumper, data: str) -> yaml.ScalarNode:
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


_BlockDumper.add_representer(str, _str_representer)

_ALLOWED_SECTIONS = frozenset({
    "generator",
    "critic",
    "memory",
    "web_search",
    "location",
    "documents",
    "search_memory",
    "semantic_scholar",
    "web_fetch",
})


def list_sections() -> list[str]:
    """Return the sorted list of configurable section names."""
    return sorted(_ALLOWED_SECTIONS)


def read_section(name: str) -> dict[str, Any]:
    """Return the parsed YAML for a config section.

    Args:
        name: One of the allowed section names.

    Returns:
        The parsed config dict, or {} if the file does not exist.

    Raises:
        ValueError: If name is not in the allowed set.
    """
    _validate(name)
    path = _CONFIG_DIR / f"{name}.yaml"
    if not path.exists():
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


def write_section(name: str, data: dict[str, Any]) -> None:
    """Write data to a config section YAML atomically.

    Args:
        name: One of the allowed section names.
        data: Dict to serialise. Must be YAML-safe.

    Raises:
        ValueError: If name is not in the allowed set.
    """
    _validate(name)
    path = _CONFIG_DIR / f"{name}.yaml"
    tmp = str(path) + ".tmp"
    with open(tmp, "w") as f:
        yaml.dump(data, f, Dumper=_BlockDumper, default_flow_style=False,
                  allow_unicode=True, sort_keys=True)
    os.replace(tmp, path)


def _validate(name: str) -> None:
    if name not in _ALLOWED_SECTIONS:
        raise ValueError(f"Unknown config section: {name!r}")
