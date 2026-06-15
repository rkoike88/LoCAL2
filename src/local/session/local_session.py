"""LoCALSession — bus I/O core for query submission and event streaming.

All adapters (HTTP API, Qt UI) delegate bus interaction here.
Each adapter owns its I/O protocol; LoCALSession owns the bus contract.

Usage::

    session = LoCALSession(publisher, session_id="...")
    for envelope in session.stream(query):
        if envelope.subject == RESPONSE_GENERATION:
            answer = envelope.payload["answer"]
"""
from __future__ import annotations

import time
import uuid
from typing import Iterator, Optional

from local.protocol.envelope import MessageEnvelope
from local.protocol.subjects import (
    ANSWER_DIALOG,
    CRITIQUE,
    GENERATION_THINKING,
    QUERY_RECEIVED,
    RESPONSE_GENERATION,
    TOOL_CALL_GET_DATETIME,
    TOOL_CALL_GET_LOCATION,
    TOOL_CALL_SEARCH_DOCUMENTS,
    TOOL_CALL_SEARCH_MEMORY,
    TOOL_CALL_SEARCH_PAPERS,
    TOOL_CALL_WEB_FETCH,
    TOOL_CALL_WEB_SEARCH,
    TOOL_RESULT_GET_DATETIME,
    TOOL_RESULT_GET_LOCATION,
    TOOL_RESULT_SEARCH_DOCUMENTS,
    TOOL_RESULT_SEARCH_MEMORY,
    TOOL_RESULT_SEARCH_PAPERS,
    TOOL_RESULT_WEB_FETCH,
    TOOL_RESULT_WEB_SEARCH,
)
from local.transport.bus_config import PROXY_BACKEND_ADDR
from local.transport.zmq_pubsub import ZmqPublisher, ZmqSubscriber

# All bus subjects the session observes.
OBSERVE = [
    QUERY_RECEIVED,
    GENERATION_THINKING,
    TOOL_CALL_WEB_SEARCH,
    TOOL_RESULT_WEB_SEARCH,
    TOOL_CALL_WEB_FETCH,
    TOOL_RESULT_WEB_FETCH,
    TOOL_CALL_SEARCH_MEMORY,
    TOOL_RESULT_SEARCH_MEMORY,
    TOOL_CALL_GET_DATETIME,
    TOOL_RESULT_GET_DATETIME,
    TOOL_CALL_GET_LOCATION,
    TOOL_RESULT_GET_LOCATION,
    TOOL_CALL_SEARCH_PAPERS,
    TOOL_RESULT_SEARCH_PAPERS,
    TOOL_CALL_SEARCH_DOCUMENTS,
    TOOL_RESULT_SEARCH_DOCUMENTS,
    RESPONSE_GENERATION,
    ANSWER_DIALOG,
    CRITIQUE,
]

# Short trail for tool-use responses (no critique expected).
_TRAIL_SECONDS = 2.0
# Extended trail for knowledge responses — waits for Prometheus to finish grading.
_CRITIQUE_TRAIL_SECONDS = 90.0


class LoCALSession:
    """Bus I/O session — one instance per active client connection.

    The caller supplies the publisher so multiple sessions can share a
    single bound socket. LoCALSession never binds a port.
    """

    def __init__(
        self,
        publisher: ZmqPublisher,
        session_id: Optional[str] = None,
    ) -> None:
        self.session_id: str = session_id or str(uuid.uuid4())
        self._publisher = publisher

    def stream(
        self,
        query: str,
        *,
        query_id: Optional[str] = None,
        attachments: Optional[list] = None,
        timeout: float = 120.0,
    ) -> Iterator[MessageEnvelope]:
        """Publish query.received; yield bus events until RESPONSE_GENERATION + trail.

        Terminates after RESPONSE_GENERATION plus a _TRAIL_SECONDS window that
        captures ANSWER_DIALOG and any late-arriving events. Adapters iterate
        with a plain for-loop; no sentinel is needed.
        """
        query_id = query_id or str(uuid.uuid4())
        payload: dict = {
            "query": query,
            "session_id": self.session_id,
            "query_id": query_id,
        }
        if attachments:
            payload["attachments"] = attachments
        envelope = MessageEnvelope.create(
            message_type="query",
            subject=QUERY_RECEIVED,
            sender_id="local-session",
            payload=payload,
            correlation_id=query_id,
            metadata={"session_id": self.session_id},
        )
        sub = ZmqSubscriber(PROXY_BACKEND_ADDR, subscriptions=OBSERVE, bind=False)
        self._publisher.publish(envelope)
        deadline = time.time() + timeout
        try:
            while time.time() < deadline:
                msg = sub.receive_with_timeout(200)
                if msg is None:
                    continue
                if msg.correlation_id != query_id:
                    continue
                yield msg
                if msg.subject == RESPONSE_GENERATION:
                    used_tools = bool(msg.payload.get("tool_calls"))
                    trail_secs = _TRAIL_SECONDS if used_tools else _CRITIQUE_TRAIL_SECONDS
                    trail_deadline = time.time() + trail_secs
                    while time.time() < trail_deadline:
                        trail = sub.receive_with_timeout(200)
                        if trail is not None and trail.correlation_id == query_id:
                            yield trail
                            if trail.subject == CRITIQUE:
                                return
                    return
        finally:
            sub.close()
