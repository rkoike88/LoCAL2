"""LocationTool — returns the user's current location.

Priority:
  1. config/location.yaml — static override (set this to pin a location)
  2. Live IP geolocation via ipinfo.io — used when config is empty/absent
  3. Graceful error string — if both fail (offline, misconfigured)

Live results are cached for cache_ttl seconds to avoid hitting the API on every call.
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
)
from local.tools.base_tool import BaseTool

logger = logging.getLogger(__name__)


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
    """Fetch location from ipinfo.io with ip-api.com fallback. Raises if both fail."""
    # Primary: ipinfo.io
    try:
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
    except Exception as exc:
        logger.warning("LocationTool: ipinfo.io failed (%s), trying ip-api.com", exc)

    # Fallback: ip-api.com
    resp = httpx.get("http://ip-api.com/json", timeout=5.0)
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != "success":
        raise RuntimeError(f"ip-api.com returned status={data.get('status')}")
    parts = [data.get("city", ""), data.get("regionName", ""), data.get("country", "")]
    location = ", ".join(p for p in parts if p)
    extras = []
    if data.get("timezone"):
        extras.append(data["timezone"])
    lat, lon = data.get("lat"), data.get("lon")
    if lat is not None and lon is not None:
        extras.append(_format_coords(f"{lat},{lon}"))
    if extras:
        location += f" ({', '.join(extras)})"
    return location


class LocationTool(BaseTool):
    CONFIG_NAME = "location"
    TOOL_NAME = "get_location"
    ACTIVITY_SUBJECT = TOOL_ACTIVITY_GET_LOCATION
    RESULT_SUBJECT = TOOL_RESULT_GET_LOCATION

    def __init__(self) -> None:
        cfg = get_config("location") or {}
        self._cache_ttl: float = float(cfg.get("cache_ttl", 300))
        self._cache: tuple[float, str] | None = None  # (monotonic_time, result)
        super().__init__(TOOL_REQUEST_GET_LOCATION)

    def _build_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.TOOL_NAME,
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

    def _get_location(self) -> str:
        # 1. Static config override
        config_loc = _from_config()
        if config_loc:
            return config_loc

        # 2. Live — serve from cache if fresh
        if self._cache is not None and time.monotonic() - self._cache[0] < self._cache_ttl:
            return self._cache[1]

        # 3. Fetch live
        try:
            result = _from_ip()
            self._cache = (time.monotonic(), result)
            logger.info("LocationTool: live location: %s", result)
            return result
        except Exception as exc:
            logger.warning("LocationTool: live geolocation failed: %s", exc)
            return "Location unavailable — check network or set config/location.yaml"

    def _handle_request(self, envelope: MessageEnvelope) -> None:
        correlation_id = envelope.correlation_id
        self._publish_activity("request", {}, correlation_id)
        result = self._get_location()
        self._publish_activity("result", {"result": result}, correlation_id)
        self._publish_result(result, correlation_id)


if __name__ == "__main__":
    LocationTool().run()
