"""RewardService — routes user.feedback to reward.event and annotates engrams."""

from __future__ import annotations

import logging

from local.protocol.envelope import MessageEnvelope
from local.protocol.subjects import REWARD_EVENT, USER_FEEDBACK
from local.services.memory_service import MemoryService
from local.transport.bus_config import make_participant_bus

logger = logging.getLogger(__name__)

SERVICE_ID = "reward_service"


class RewardService:
    def __init__(self, memory_service: MemoryService | None = None) -> None:
        self._memory = memory_service or MemoryService()
        self._pub, self._sub = make_participant_bus([USER_FEEDBACK])

    def run(self) -> None:
        print("[reward_service] ready")
        while True:
            try:
                envelope = self._sub.receive()
            except Exception as exc:
                logger.error("RewardService: receive error: %s", exc)
                continue
            if envelope.subject == USER_FEEDBACK:
                try:
                    self._handle_feedback(envelope)
                except Exception as exc:
                    logger.error("RewardService: unhandled error: %s", exc, exc_info=True)

    def _handle_feedback(self, envelope: MessageEnvelope) -> None:
        payload = envelope.payload
        query_id: str = payload.get("query_id") or ""
        session_id: str = payload.get("session_id") or ""
        sentiment: str = payload.get("sentiment") or ""

        if not query_id or sentiment not in ("positive", "negative"):
            logger.warning("RewardService: invalid feedback payload: %s", payload)
            return

        self._memory.update_engram_sentiment(query_id, sentiment)

        self._pub.publish(MessageEnvelope.create(
            message_type="reward",
            subject=REWARD_EVENT,
            sender_id=SERVICE_ID,
            payload={
                "query_id": query_id,
                "session_id": session_id,
                "sentiment": sentiment,
            },
            correlation_id=envelope.correlation_id or query_id,
            metadata={"session_id": session_id},
        ))
        logger.debug("RewardService: routed %s feedback for query %s", sentiment, query_id)
