"""Abstract message bus interface."""

from abc import ABC, abstractmethod
from typing import Optional
from local.protocol.envelope import MessageEnvelope


class MessageBus(ABC):
    """Message bus interface for pub/sub implementations."""
    @abstractmethod
    def publish(self, message: MessageEnvelope) -> None:
        pass

    @abstractmethod
    def receive(self) -> MessageEnvelope:
        pass

    @abstractmethod
    def receive_with_timeout(self, timeout_ms: int) -> Optional[MessageEnvelope]:
        pass

    @abstractmethod
    def close(self) -> None:
        pass
