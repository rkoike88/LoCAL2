"""BaseService — lightweight bus participant with a subscribe-and-dispatch loop."""
from __future__ import annotations

import logging
from abc import abstractmethod

from local.participants.participant import Participant

logger = logging.getLogger(__name__)


class BaseService(Participant):
    """Participant with a simple subscribe-and-dispatch run loop.

    Subclasses implement _handle(envelope) and set up self._sub in __init__.
    """

    def run(self) -> None:
        logger.info("%s ready", self.id)
        while True:
            try:
                envelope = self._sub.receive()
            except Exception as exc:
                logger.error("%s: receive error: %s", self.id, exc)
                continue
            try:
                self._handle(envelope)
            except Exception as exc:
                logger.error("%s: handler error: %s", self.id, exc, exc_info=True)

    @abstractmethod
    def _handle(self, envelope) -> None: ...
