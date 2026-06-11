"""BaseAgent — common behaviour for all LoCAL2 system-triggered agents."""
from __future__ import annotations

import logging
import uuid
from abc import abstractmethod
from typing import Any

from local.participants.participant import Participant
from local.protocol.envelope import MessageEnvelope
from local.protocol.subjects import AGENT_TRANSITION

logger = logging.getLogger(__name__)


class BaseAgent(Participant):
    """Abstract base for all system-triggered LoCAL2 agents.

    Subclasses must:
    - declare ``CONFIG_NAME`` as a class variable (required by Participant)
    - set ``self._pub``, ``self._sub``, and ``self._sm`` in ``__init__``
    - implement ``_dispatch()`` to route envelopes to their handlers

    ``run()`` provides the standard receive loop. ``GeneratorAgent`` overrides
    it for startup sequencing; other agents inherit it directly.
    """

    _pub: Any
    _sub: Any
    _sm: Any

    def run(self) -> None:
        """Block on the bus and dispatch each envelope; log receive errors."""
        logger.info("%s ready", self.id)
        while True:
            try:
                envelope = self._sub.receive()
            except Exception as exc:
                logger.error("%s: receive error: %s", self.__class__.__name__, exc)
                continue
            self._dispatch(envelope)

    @abstractmethod
    def _dispatch(self, envelope: MessageEnvelope) -> None:
        """Route an incoming envelope to the appropriate handler."""
        ...

    def _do_transition(self, action: Any) -> None:
        """Execute a state machine transition and publish AGENT_TRANSITION.

        Wrapped in try/except — transition logging must never propagate and
        interrupt the agent's main work. Calls ``_after_transition()`` after
        a successful transition so subclasses can add side-effects (e.g.
        ``GeneratorAgent`` uses it to publish a status snapshot).
        """
        from_state = self._sm.state
        to_state = self._sm.transition(action)
        try:
            self._pub.publish(MessageEnvelope.create(
                message_type="agent_transition",
                subject=AGENT_TRANSITION,
                sender_id=self.id,
                payload={
                    "agent":  self.id,
                    "from":   from_state.value,
                    "action": action.value,
                    "to":     to_state.value,
                },
                correlation_id=str(uuid.uuid4()),
            ))
        except Exception:
            pass
        self._after_transition()

    def _after_transition(self) -> None:
        """Hook called after every transition. Default: no-op."""
