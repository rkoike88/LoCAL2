"""User data directory resolution for LoCAL2.

The data directory holds user-editable config files, the ChromaDB store, and
other mutable runtime data.  It is separate from the installed package so that
``pip install --upgrade local2`` never clobbers user config.

Resolution order:
  1. ``LOCAL2_DATA_DIR`` environment variable (full path)
  2. ``~/.local2/`` (default for all platforms)
"""
from __future__ import annotations

import os
from pathlib import Path


def get_data_dir() -> Path:
    """Return the LoCAL2 user data directory, creating it if absent.

    Returns:
        Absolute path to the data directory.
    """
    env = os.environ.get("LOCAL2_DATA_DIR")
    if env:
        path = Path(env).expanduser().resolve()
    else:
        path = Path.home() / ".local2"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_defaults_dir() -> Path:
    """Return the directory containing bundled default YAML configs.

    Returns:
        Absolute path to ``src/local/defaults/`` inside the installed package.
    """
    return Path(__file__).resolve().parent / "defaults"
