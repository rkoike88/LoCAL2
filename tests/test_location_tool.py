"""Unit tests for LocationTool — no live bus or network required."""

from unittest.mock import MagicMock, patch

import pytest

from local.tools.location_tool import LocationTool, _format_coords, _from_config, _from_ip


# ---------------------------------------------------------------------------
# _format_coords
# ---------------------------------------------------------------------------

class TestFormatCoords:
    def test_north_west(self):
        assert _format_coords("37.7749,-122.4194") == "37.7749° N, 122.4194° W"

    def test_south_east(self):
        assert _format_coords("-33.8688,151.2093") == "33.8688° S, 151.2093° E"

    def test_invalid_returns_original(self):
        assert _format_coords("bad") == "bad"


# ---------------------------------------------------------------------------
# _from_config
# ---------------------------------------------------------------------------

class TestFromConfig:
    def test_returns_none_when_empty(self):
        with patch("local.tools.location_tool.get_config", return_value={}):
            assert _from_config() is None

    def test_returns_none_when_config_none(self):
        with patch("local.tools.location_tool.get_config", return_value=None):
            assert _from_config() is None

    def test_returns_location_when_set(self):
        with patch("local.tools.location_tool.get_config", return_value={
            "city": "Cupertino", "state": "California", "country": "United States",
        }):
            result = _from_config()
        assert result == "Cupertino, California, United States"

    def test_includes_timezone_and_coords(self):
        with patch("local.tools.location_tool.get_config", return_value={
            "city": "Cupertino", "timezone": "America/Los_Angeles",
            "coordinates": "37.32° N, 122.03° W",
        }):
            result = _from_config()
        assert "America/Los_Angeles" in result
        assert "37.32° N" in result


# ---------------------------------------------------------------------------
# _from_ip (mocked httpx)
# ---------------------------------------------------------------------------

class TestFromIp:
    def _mock_response(self, data: dict):
        resp = MagicMock()
        resp.json.return_value = data
        return resp

    def test_parses_city_region_country(self):
        with patch("httpx.get", return_value=self._mock_response({
            "city": "San Francisco", "region": "California", "country": "US",
            "timezone": "America/Los_Angeles", "loc": "37.7749,-122.4194",
        })):
            result = _from_ip()
        assert "San Francisco" in result
        assert "California" in result
        assert "America/Los_Angeles" in result
        assert "37.7749° N" in result

    def test_raises_on_network_error(self):
        with patch("httpx.get", side_effect=Exception("timeout")):
            with pytest.raises(Exception):
                _from_ip()


# ---------------------------------------------------------------------------
# _get_location — priority logic
# ---------------------------------------------------------------------------

class TestGetLocation:
    def _make_tool(self):
        with patch("local.tools.base_tool.make_participant_bus",
                   return_value=(MagicMock(), MagicMock())):
            with patch("local.tools.location_tool.get_config", return_value={}):
                return LocationTool()

    def test_config_takes_precedence_over_live(self):
        tool = self._make_tool()
        with patch("local.tools.location_tool.get_config", return_value={"city": "Cupertino"}):
            with patch("local.tools.location_tool._from_ip") as mock_ip:
                result = tool._get_location()
        assert "Cupertino" in result
        mock_ip.assert_not_called()

    def test_live_used_when_config_empty(self):
        tool = self._make_tool()
        with patch("local.tools.location_tool.get_config", return_value={}):
            with patch("local.tools.location_tool._from_ip", return_value="San Francisco, CA"):
                result = tool._get_location()
        assert result == "San Francisco, CA"

    def test_cache_prevents_second_api_call(self):
        tool = self._make_tool()
        with patch("local.tools.location_tool.get_config", return_value={}):
            with patch("local.tools.location_tool._from_ip", return_value="Austin, TX") as mock_ip:
                tool._get_location()
                tool._get_location()
        mock_ip.assert_called_once()

    def test_graceful_error_when_live_fails(self):
        tool = self._make_tool()
        with patch("local.tools.location_tool.get_config", return_value={}):
            with patch("local.tools.location_tool._from_ip", side_effect=Exception("offline")):
                result = tool._get_location()
        assert "unavailable" in result.lower()

    def test_cache_expires_after_ttl(self):
        import time
        tool = self._make_tool()
        with patch("local.tools.location_tool.get_config", return_value={}):
            with patch("local.tools.location_tool._from_ip", return_value="Austin, TX") as mock_ip:
                tool._get_location()
                tool._cache = (time.monotonic() - tool._cache_ttl - 1, "Austin, TX")
                tool._get_location()
        assert mock_ip.call_count == 2


# ---------------------------------------------------------------------------
# LocationTool bus behaviour
# ---------------------------------------------------------------------------

class TestLocationToolBus:
    def _make_tool(self):
        with patch("local.tools.base_tool.make_participant_bus") as mock_bus:
            mock_pub, mock_sub = MagicMock(), MagicMock()
            mock_bus.return_value = (mock_pub, mock_sub)
            tool = LocationTool()
            tool._pub = mock_pub
            tool._sub = mock_sub
        return tool

    def test_announce_schema_publishes_tool_schema(self):
        tool = self._make_tool()
        tool._announce_schema()
        envelope = tool._pub.publish.call_args.args[0]
        assert envelope.subject == "tool.schema"
        assert envelope.schema["function"]["name"] == "get_location"

    def test_handle_request_publishes_result_and_activity(self):
        tool = self._make_tool()
        envelope = MagicMock()
        envelope.correlation_id = "corr-1"
        with patch.object(tool, "_get_location", return_value="Austin, TX"):
            tool._handle_request(envelope)
        subjects = [c.args[0].subject for c in tool._pub.publish.call_args_list]
        assert "tool.result.get_location" in subjects
        assert "tool.activity.get_location" in subjects
