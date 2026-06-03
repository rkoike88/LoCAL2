"""LocationTool — returns the user's current location.

Priority:
  1. config/location.yaml — static override (set this to pin a location)
  2. Live IP geolocation via ipinfo.io — used when config is empty/absent
  3. Graceful error string — if both fail (offline, misconfigured)

Live results are cached for CACHE_TTL seconds to avoid hitting the API on every call.
"""
from __future__ import annotations

import logging
import time

import httpx

from local.config_loader import get_config
from local.protocol.envelope import MessageEnvelope
from local.protocol.subjects import (
    TOOL_ACTIVITY_GET_LOCATION,
    TOOL_REQUEST_GET_LOCATION,
    TOOL_RESULT_GET_LOCATION,
    TOOL_SCHEMA,
    TOOL_SCHEMA_REQUEST,
)
from local.transport.bus_config import make_participant_bus

logger = logging.getLogger(__name__)

TOOL_NAME = "get_location"
CACHE_TTL = 300  # seconds — re-fetch live location after 5 minutes

_cache: tuple[float, str] | None = None  # (monotonic_time, result)

_SCHEMA = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": (
            "Returns the user's current location (city, state/region, country, timezone, "
            "and coordinates). Call this tool for any question that depends on the user's "
            "physical location — weather, nearby restaurants or places, local events, "
            "travel distances, or when you need to know where the user is before calling "
            "another tool such as web_search."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
}


def _format_coords(loc_str: str) -> str:
    """Convert ipinfo "lat,lon" string to "37.7749° N, 122.4194° W"."""
    try:
        lat_s, lon_s = loc_str.split(",")
        lat, lon = float(lat_s), float(lon_s)
        return (
            f"{abs(lat):.4f}° {'N' if lat >= 0 else 'S'}, "
            f"{abs(lon):.4f}° {'E' if lon >= 0 else 'W'}"
        )
    except Exception:
        return loc_str


def _from_config() -> str | None:
    """Return location string from config/location.yaml, or None if not configured."""
    cfg = get_config("location") or {}
    parts = [cfg.get("city", ""), cfg.get("state", ""), cfg.get("country", "")]
    location = ", ".join(p for p in parts if p)
    if not location:
        return None
    extras = []
    if cfg.get("timezone"):
        extras.append(cfg["timezone"])
    if cfg.get("coordinates"):
        extras.append(cfg["coordinates"])
    if extras:
        location += f" ({', '.join(extras)})"
    return location


def _from_ip() -> str:
    """Fetch location from ipinfo.io. Raises on network error."""
    resp = httpx.get("https://ipinfo.io/json", timeout=5.0)
    resp.raise_for_status()
    data = resp.json()
    parts = [data.get("city", ""), data.get("region", ""), data.get("country", "")]
    location = ", ".join(p for p in parts if p)
    extras = []
    if data.get("timezone"):
        extras.append(data["timezone"])
    if data.get("loc"):
        extras.append(_format_coords(data["loc"]))
    if extras:
        location += f" ({', '.join(extras)})"
    return location


def _get_location() -> str:
    global _cache

    # 1. Static config override
    config_loc = _from_config()
    if config_loc:
        return config_loc

    # 2. Live — serve from cache if fresh
    if _cache is not None and time.monotonic() - _cache[0] < CACHE_TTL:
        return _cache[1]

    # 3. Fetch live
    try:
        result = _from_ip()
        _cache = (time.monotonic(), result)
        logger.info("LocationTool: live location: %s", result)
        return result
    except Exception as exc:
        logger.warning("LocationTool: live geolocation failed: %s", exc)
        return "Location unavailable — check network or set config/location.yaml"


class LocationTool:
    TOOL_ID = "location_tool"

    def __init__(self) -> None:
        self._pub, self._sub = make_participant_bus([TOOL_REQUEST_GET_LOCATION, TOOL_SCHEMA_REQUEST])

    def run(self) -> None:
        self._announce_schema()
        print("[location_tool] ready")
        while True:
            try:
                envelope = self._sub.receive()
            except Exception as exc:
                logger.error("LocationTool: receive error: %s", exc)
                continue
            if envelope.subject == TOOL_SCHEMA_REQUEST:
                self._announce_schema()
            elif envelope.subject == TOOL_REQUEST_GET_LOCATION:
                self._handle_request(envelope)

    def _announce_schema(self) -> None:
        self._pub.publish(MessageEnvelope.create(
            message_type="tool_schema",
            subject=TOOL_SCHEMA,
            sender_id=self.TOOL_ID,
            payload={"schema": _SCHEMA},
        ))

    def _handle_request(self, envelope: MessageEnvelope) -> None:
        result = _get_location()
        self._pub.publish(MessageEnvelope.create(
            message_type="tool_activity",
            subject=TOOL_ACTIVITY_GET_LOCATION,
            sender_id=self.TOOL_ID,
            payload={"request": {}, "result": result},
            correlation_id=envelope.correlation_id,
        ))
        self._pub.publish(MessageEnvelope.create(
            message_type="tool_result",
            subject=TOOL_RESULT_GET_LOCATION,
            sender_id=self.TOOL_ID,
            payload={"result": result},
            correlation_id=envelope.correlation_id,
        ))


if __name__ == "__main__":
    LocationTool().run()
