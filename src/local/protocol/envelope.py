"""Envelope schema for LoCAL2 inter-participant messages."""

from dataclasses import asdict, dataclass, field
from typing import Optional
import uuid
import datetime
import json


@dataclass
class MessageEnvelope:
    """Generic message wrapper used on the bus."""
    message_id: str
    message_type: str
    subject: str
    sender_id: str
    payload: dict
    correlation_id: Optional[str] = None
    recipient_id: Optional[str] = None
    metadata: dict = field(default_factory=dict)
    timestamp_utc: str = field(
        default_factory=lambda: datetime.datetime.utcnow().isoformat()
    )

    @staticmethod
    def create(message_type: str, subject: str, sender_id: str, payload: dict, **kwargs):
        """Create a new MessageEnvelope with a generated message_id."""
        return MessageEnvelope(
            message_id=str(uuid.uuid4()),
            message_type=message_type,
            subject=subject,
            sender_id=sender_id,
            payload=payload,
            **kwargs
        )


def envelope_debug_dict(message: MessageEnvelope) -> dict:
    """Return a debug-friendly dict including local timestamp."""
    data = asdict(message)
    timestamp = datetime.datetime.fromisoformat(message.timestamp_utc).replace(
        tzinfo=datetime.timezone.utc
    )
    data["timestamp_local"] = timestamp.astimezone().isoformat()
    return data


def format_envelope_debug(message: MessageEnvelope) -> str:
    """Return a pretty JSON string for debug logging."""
    return json.dumps(envelope_debug_dict(message), indent=2)
