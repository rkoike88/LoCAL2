"""ZeroMQ PUB/SUB transport adapters."""

from dataclasses import asdict
import json
from typing import Optional
import zmq

from local.protocol.envelope import MessageEnvelope


class ZmqPublisher:
    """Publishes MessageEnvelope objects over a ZeroMQ PUB socket."""

    def __init__(self, address: str, bind: bool = True):
        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.PUB)
        if bind:
            self.socket.bind(address)
        else:
            self.socket.connect(address)

    def publish(self, message: MessageEnvelope) -> None:
        self.socket.send_multipart([
            message.subject.encode(),
            json.dumps(asdict(message)).encode(),
        ])

    def close(self) -> None:
        self.socket.close()
        self.context.term()


class ZmqSubscriber:
    """Subscribes to one or more subjects over a ZeroMQ SUB socket."""

    def __init__(self, address: str, subscriptions: list[str], bind: bool = False):
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
