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
    TOOL_REQUEST_GET_DATETIME,
    TOOL_REQUEST_GET_LOCATION,
    TOOL_REQUEST_SEARCH_DOCUMENTS,
    TOOL_REQUEST_SEARCH_MEMORY,
    TOOL_REQUEST_SEARCH_PAPERS,
    TOOL_REQUEST_WEB_FETCH,
    TOOL_REQUEST_WEB_SEARCH,
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
    TOOL_REQUEST_WEB_SEARCH,
    TOOL_RESULT_WEB_SEARCH,
    TOOL_REQUEST_WEB_FETCH,
    TOOL_RESULT_WEB_FETCH,
    TOOL_REQUEST_SEARCH_MEMORY,
    TOOL_RESULT_SEARCH_MEMORY,
    TOOL_REQUEST_GET_DATETIME,
    TOOL_RESULT_GET_DATETIME,
    TOOL_REQUEST_GET_LOCATION,
    TOOL_RESULT_GET_LOCATION,
    TOOL_REQUEST_SEARCH_PAPERS,
    TOOL_RESULT_SEARCH_PAPERS,
    TOOL_REQUEST_SEARCH_DOCUMENTS,
    TOOL_RESULT_SEARCH_DOCUMENTS,
    RESPONSE_GENERATION,
    ANSWER_DIALOG,
    CRITIQUE,
]

# Window after RESPONSE_GENERATION before the stream closes — captures
# ANSWER_DIALOG and any late-arriving events (critique, etc.).
_TRAIL_SECONDS = 2.0


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
        timeout: float = 120.0,
    ) -> Iterator[MessageEnvelope]:
        """Publish query.received; yield bus events until RESPONSE_GENERATION + trail.

        Terminates after RESPONSE_GENERATION plus a _TRAIL_SECONDS window that
        captures ANSWER_DIALOG and any late-arriving events. Adapters iterate
        with a plain for-loop; no sentinel is needed.
        """
        query_id = query_id or str(uuid.uuid4())
        envelope = MessageEnvelope.create(
            message_type="query",
            subject=QUERY_RECEIVED,
            sender_id="local-session",
            payload={
                "query": query,
                "session_id": self.session_id,
                "query_id": query_id,
            },
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
                    trail_deadline = time.time() + _TRAIL_SECONDS
                    while time.time() < trail_deadline:
                        trail = sub.receive_with_timeout(200)
                        if trail is not None and trail.correlation_id == query_id:
                            yield trail
                    return
        finally:
            sub.close()
