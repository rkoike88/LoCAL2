"""RewardService — routes user.feedback to reward.event and annotates engrams."""

from __future__ import annotations

import logging

from local.participants.base_service import BaseService
from local.protocol.envelope import MessageEnvelope
from local.protocol.messages import RewardEvent, UserFeedback as UserFeedbackMsg
from local.protocol.subjects import USER_FEEDBACK
from local.services.memory_service import MemoryService
from local.transport.bus_config import make_participant_bus

logger = logging.getLogger(__name__)


class RewardService(BaseService):
    """Routes user thumbs-up/down feedback to memory and broadcasts reward events.

    Subscribes to ``user.feedback``. On each event, annotates the corresponding
    episodic engram with the sentiment and publishes ``reward.event`` so
    downstream agents can act on the signal.
    """

    CONFIG_NAME = "reward"

    def __init__(self, memory_service: MemoryService | None = None) -> None:
        """Initialize the RewardService.

        Args:
            memory_service: Injected for testing; defaults to a fresh
                ``MemoryService``.
        """
        self._memory = memory_service or MemoryService()
        self._pub, self._sub = make_participant_bus([USER_FEEDBACK])

    def _handle(self, envelope: MessageEnvelope) -> None:
        if envelope.subject == USER_FEEDBACK:
            self._handle_feedback(envelope)

    def _handle_feedback(self, envelope: MessageEnvelope) -> None:
        """Handle a ``user.feedback`` event.

        Args:
            envelope: Payload must contain ``query_id`` (str) and
                ``sentiment`` (``"positive"`` or ``"negative"``). Logs a
                warning and returns if either field is missing or invalid.
        """
        msg = UserFeedbackMsg.from_envelope(envelope)

        if not msg.query_id or msg.sentiment not in ("positive", "negative"):
            logger.warning("RewardService: invalid feedback payload: %s", envelope.payload)
            return

        self._memory.update_engram_sentiment(msg.query_id, msg.sentiment)

        self._pub.publish(
            RewardEvent(query_id=msg.query_id, session_id=msg.session_id, sentiment=msg.sentiment),
            sender_id=self.id,
            correlation_id=envelope.correlation_id or msg.query_id,
            session_id=msg.session_id,
        )
        logger.debug("RewardService: routed %s feedback for query %s", msg.sentiment, msg.query_id)
