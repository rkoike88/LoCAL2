"""ZeroMQ PUB/SUB transport adapters."""

from dataclasses import asdict
import json
from typing import TYPE_CHECKING, Optional, Union
import zmq

from local.protocol.envelope import MessageEnvelope

if TYPE_CHECKING:
    from local.protocol.messages import BusMessage


class ZmqPublisher:
    """Publishes MessageEnvelope objects over a ZeroMQ PUB socket."""

    def __init__(self, address: str, bind: bool = True, sender_id: str = ""):
        """
        Args:
            address: ZeroMQ address string (e.g. ``"tcp://127.0.0.1:5570"``).
            bind: If ``True``, socket binds to the address (server side). Bus
                participants always connect (``bind=False``); the proxy binds.
            sender_id: Used when publishing BusMessage objects that need a sender.
        """
        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.PUB)
        self._sender_id = sender_id
        if bind:
            self.socket.bind(address)
        else:
            self.socket.connect(address)

    def publish(self, message: "Union[MessageEnvelope, BusMessage]", *, sender_id: str = "", correlation_id: str = "", session_id: str = "", user_id: str = "") -> None:
        from local.protocol.messages import BusMessage
        if isinstance(message, BusMessage):
            envelope = message.to_envelope(
                sender_id=sender_id or self._sender_id,
                correlation_id=correlation_id,
                session_id=session_id,
            )
            if user_id:
                envelope.metadata["user_id"] = user_id
        else:
            envelope = message
        self.socket.send_multipart([
            envelope.subject.encode(),
            json.dumps(asdict(envelope)).encode(),
        ])

    def close(self) -> None:
        self.socket.close()
        self.context.term()


class ZmqSubscriber:
    """Subscribes to one or more subjects over a ZeroMQ SUB socket."""

    def __init__(self, address: str, subscriptions: list[str], bind: bool = False):
        """
        Args:
            address: ZeroMQ address string for the XPUB proxy backend.
            subscriptions: Subject prefixes to subscribe to. ZMQ
                prefix-matches on the topic frame, so ``"tool.result"``
                matches all subjects starting with that string.
            bind: If ``True``, socket binds (server side). Participants
                always connect (default ``False``).
        """
        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.SUB)
        if bind:
            self.socket.bind(address)
        else:
            self.socket.connect(address)

        for subject in subscriptions:
            self.socket.setsockopt_string(zmq.SUBSCRIBE, subject)

    def receive(self) -> MessageEnvelope:
        """Block until a valid 2-part message arrives and return it as a MessageEnvelope."""
        while True:
            parts = self.socket.recv_multipart()
            if len(parts) != 2:
                continue
            topic_bytes, payload_bytes = parts
            topic = topic_bytes.decode()
            message_dict = json.loads(payload_bytes.decode())
            message = MessageEnvelope(**message_dict)
            if topic != message.subject:
                raise ValueError(
                    f"Topic/envelope mismatch: topic='{topic}', envelope.subject='{message.subject}'"
                )
            return message

    def receive_with_timeout(self, timeout_ms: int) -> Optional[MessageEnvelope]:
        """Wait up to timeout_ms milliseconds for a message; return None on timeout."""
        self.socket.setsockopt(zmq.RCVTIMEO, timeout_ms)
        try:
            parts = self.socket.recv_multipart()
            if len(parts) != 2:
                return None
            topic_bytes, payload_bytes = parts
            topic = topic_bytes.decode()
            message_dict = json.loads(payload_bytes.decode())
            message = MessageEnvelope(**message_dict)
            if topic != message.subject:
                raise ValueError(
                    f"Topic/envelope mismatch: topic='{topic}', envelope.subject='{message.subject}'"
                )
            return message
        except zmq.error.Again:
            return None

    def close(self) -> None:
        self.socket.close()
        self.context.term()
