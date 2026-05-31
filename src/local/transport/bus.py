"""ZMQ-backed MessageBus implementation."""

from typing import Optional

from local.protocol.envelope import MessageEnvelope
from local.transport.base import MessageBus
from local.transport.zmq_pubsub import ZmqPublisher, ZmqSubscriber


class ZmqMessageBus(MessageBus):
    """Message bus backed by one ZeroMQ publisher and one ZeroMQ subscriber."""

    def __init__(self, publisher: ZmqPublisher, subscriber: ZmqSubscriber):
        self._publisher = publisher
        self._subscriber = subscriber

    def publish(self, message: MessageEnvelope) -> None:
        self._publisher.publish(message)

    def receive(self) -> MessageEnvelope:
        return self._subscriber.receive()

    def receive_with_timeout(self, timeout_ms: int) -> Optional[MessageEnvelope]:
        return self._subscriber.receive_with_timeout(timeout_ms)

    def close(self) -> None:
        self._subscriber.close()
        self._publisher.close()
