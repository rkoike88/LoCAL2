"""ZMQ address configuration for the LoCAL2 message bus.

All participants publish to PROXY_FRONTEND_ADDR (XSUB side of proxy).
All participants subscribe from PROXY_BACKEND_ADDR (XPUB side of proxy).
"""

import os

from local.config_loader import ConfigManager
from local.transport.zmq_pubsub import ZmqPublisher, ZmqSubscriber


def _load_bus_addresses() -> tuple[str, dict]:
    cfg = ConfigManager.load("bus")
    proxy_host = os.environ.get("LOCAL2_PROXY_HOST") or cfg.get("proxy_host", "127.0.0.1")
    ports: dict = cfg.get("ports", {})
    return proxy_host, ports


_proxy_host, _ports = _load_bus_addresses()

_proxy_frontend_port = _ports.get("proxy_frontend", 5570)
_proxy_backend_port = _ports.get("proxy_backend", 5571)

# Participants connect their PUB socket here to publish via the proxy.
PROXY_FRONTEND_ADDR = f"tcp://{_proxy_host}:{_proxy_frontend_port}"
PROXY_FRONTEND_BIND_ADDR = f"tcp://0.0.0.0:{_proxy_frontend_port}"

# Participants connect their SUB socket here to receive all bus traffic.
PROXY_BACKEND_ADDR = f"tcp://{_proxy_host}:{_proxy_backend_port}"
PROXY_BACKEND_BIND_ADDR = f"tcp://0.0.0.0:{_proxy_backend_port}"


def make_participant_bus(subscriptions: list[str]) -> tuple[ZmqPublisher, ZmqSubscriber]:
    """Return (publisher, subscriber) wired to the proxy for a bus participant."""
    publisher = ZmqPublisher(PROXY_FRONTEND_ADDR, bind=False)
    subscriber = ZmqSubscriber(PROXY_BACKEND_ADDR, subscriptions=subscriptions, bind=False)
    return publisher, subscriber
