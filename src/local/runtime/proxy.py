"""ZMQ XPUB/XSUB proxy for the LoCAL2 message bus.

Run this process first before starting any participants.
Participants publish to PROXY_FRONTEND and subscribe from PROXY_BACKEND.
"""

from __future__ import annotations

import zmq

from local.transport.bus_config import PROXY_FRONTEND_BIND_ADDR, PROXY_BACKEND_BIND_ADDR


def run_proxy() -> None:
    """Block and forward messages until the process is killed."""
    context = zmq.Context()
    frontend = context.socket(zmq.XSUB)
    frontend.bind(PROXY_FRONTEND_BIND_ADDR)
    backend = context.socket(zmq.XPUB)
    backend.bind(PROXY_BACKEND_BIND_ADDR)
    print(f"[proxy] XSUB {PROXY_FRONTEND_BIND_ADDR}  XPUB {PROXY_BACKEND_BIND_ADDR}")
    try:
        zmq.proxy(frontend, backend)
    finally:
        frontend.close()
        backend.close()
        context.term()


if __name__ == "__main__":
    run_proxy()
