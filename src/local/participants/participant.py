"""Participant — root base class for all LoCAL2 bus participants."""
from __future__ import annotations

from abc import ABC
from typing import ClassVar

from local.config_loader import get_config

_ID_UNSET = "[set id in yaml]"


class Participant(ABC):
    """Root base for every LoCAL2 bus participant.

    Participation contract:
      - Declare CONFIG_NAME pointing to the participant's own yaml
      - Set id: in that yaml

    If id: is absent, self.id returns "[set id in yaml]" — no startup failure.
    """

    CONFIG_NAME: ClassVar[str]

    @property
    def id(self) -> str:
        cfg = get_config(self.CONFIG_NAME) or {}
        return cfg.get("id") or _ID_UNSET
