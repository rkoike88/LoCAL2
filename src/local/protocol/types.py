"""Shared protocol types for LoCAL2.

These are cross-cutting data structures that originate in the UI/API layer and
are consumed by agents. Using typed objects instead of raw dicts makes the
shape explicit and removes string-key access at call sites.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Attachment:
    """A file or clipboard item attached to a user query.

    Attributes:
        type: ``"text"``, ``"image"``, or ``"error"``.
        name: Display filename (e.g. ``"notes.txt"``, ``"clipboard.png"``).
        data: Text content or base64-encoded image bytes. Empty for ``"error"`` type.
    """
    type: str
    name: str
    data: str = field(default="")

    @classmethod
    def from_dict(cls, d: dict) -> "Attachment":
        return cls(type=d.get("type", ""), name=d.get("name", ""), data=d.get("data") or "")
