"""ToolDispatcher — synchronous bus I/O helper for tool call dispatch.

Owned by GeneratorAgent. Handles subscribe-before-publish ordering,
correlation_id matching, timeout, and tool name normalization.
State transitions are the caller's responsibility.
"""
from __future__ import annotations

import logging
import time

from local.participants.participant import Participant
from local.protocol.messages import ToolCall
from local.transport.bus_config import PROXY_BACKEND_ADDR, PROXY_FRONTEND_ADDR
from local.transport.zmq_pubsub import ZmqPublisher, ZmqSubscriber

logger = logging.getLogger(__name__)


class ToolDispatcher(Participant):
    """Synchronous bus helper for dispatching tool calls mid-generation.

    Has its own ZmqPublisher and opens a short-lived ZmqSubscriber per call.
    No run loop — called on-demand by the generator.
    """

    CONFIG_NAME = "tool_dispatcher"

    def __init__(self, tool_timeout: float) -> None:
        """Initialize the ToolDispatcher.

        Args:
            tool_timeout: Seconds to wait for a tool.result.* response before
                declaring a timeout.
        """
        self._tool_timeout = tool_timeout
        self._pub = ZmqPublisher(PROXY_FRONTEND_ADDR, bind=False)

    def execute(
        self,
        name: str,
        args: dict,
        correlation_id: str,
        schemas: list,
    ) -> tuple[str, bool]:
        """Dispatch a tool call and block for the result.

        Opens a ZmqSubscriber for tool.result.<name> BEFORE publishing
        tool.request.<name> to avoid a race between publish and subscribe.
        Polls until correlation_id matches or tool_timeout expires.

        Args:
            name: Tool function name; normalized via _normalize() first.
            args: Tool arguments dict from the model's tool call.
            correlation_id: Used to match the response envelope.
            schemas: Current tool schema list; used for name normalization.

        Returns:
            (result, timed_out): result is the tool output string;
            timed_out is True if no matching response arrived in time.
        """
        name = self._normalize(name, schemas)
        res_subject = f"tool.result.{name}"

        result_sub = ZmqSubscriber(PROXY_BACKEND_ADDR, subscriptions=[res_subject])
        try:
            self._pub.publish(
                ToolCall(tool=name, args=args, correlation_id=correlation_id),
                sender_id=self.id,
                correlation_id=correlation_id,
            )
            deadline = time.monotonic() + self._tool_timeout
            while time.monotonic() < deadline:
                remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
                msg = result_sub.receive_with_timeout(remaining_ms)
                if msg is None:
                    break
                if msg.correlation_id == correlation_id:
                    return msg.payload.get("result", ""), False
        finally:
            result_sub.close()

        logger.warning("ToolDispatcher: tool %r timed out after %ss", name, self._tool_timeout)
        return f"[tool timeout: {name!r} did not respond within {self._tool_timeout}s]", True

    def _normalize(self, name: str, schemas: list) -> str:
        """Map hallucinated or variant tool names to registered schema names."""
        registered = {s.get("function", {}).get("name") for s in schemas}
        if name in registered:
            return name
        name_lower = name.lower()
        for rname in registered:
            if rname in name_lower or name_lower in rname:
                logger.warning("ToolDispatcher: normalizing tool name %r → %r", name, rname)
                return rname
        return name
