"""Unit tests for DateTimeTool — no live bus required."""

import time
from unittest.mock import MagicMock, patch

import pytest

from local.tools.datetime_tool import DateTimeTool, _get_datetime


class TestGetDatetime:
    def test_returns_string(self):
        result = _get_datetime()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_contains_day_of_week(self):
        result = _get_datetime()
        days = {"Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"}
        assert any(day in result for day in days)

    def test_contains_date_format(self):
        import re
        result = _get_datetime()
        assert re.search(r"\d{4}-\d{2}-\d{2}", result), f"No YYYY-MM-DD in: {result!r}"

    def test_contains_time_format(self):
        import re
        result = _get_datetime()
        assert re.search(r"\d{2}:\d{2}:\d{2}", result), f"No HH:MM:SS in: {result!r}"

    def test_contains_utc_offset(self):
        result = _get_datetime()
        assert "UTC" in result, f"No UTC offset in: {result!r}"

    def test_changes_over_time(self):
        r1 = _get_datetime()
        time.sleep(1.1)
        r2 = _get_datetime()
        assert r1 != r2, "Result did not change after 1 second — clock may be frozen"


class TestDateTimeToolBus:
    def _make_tool(self):
        with patch("local.tools.base_tool.make_participant_bus") as mock_bus:
            mock_pub = MagicMock()
            mock_sub = MagicMock()
            mock_bus.return_value = (mock_pub, mock_sub)
            tool = DateTimeTool()
            tool._pub = mock_pub
            tool._sub = mock_sub
        return tool

    def test_announce_schema_publishes_tool_schema(self):
        tool = self._make_tool()
        tool._announce_schema()
        call = tool._pub.publish.call_args
        envelope = call.args[0]
        assert envelope.subject == "tool.schema"
        schema = envelope.payload["schema"]
        assert schema["function"]["name"] == "get_datetime"

    def test_handle_request_publishes_result(self):
        tool = self._make_tool()
        envelope = MagicMock()
        envelope.correlation_id = "corr-1"
        tool._handle_request(envelope)
        subjects = [c.args[0].subject for c in tool._pub.publish.call_args_list]
        assert "tool.result.get_datetime" in subjects
        assert "tool.activity.get_datetime" in subjects

    def test_result_payload_contains_datetime_string(self):
        tool = self._make_tool()
        envelope = MagicMock()
        envelope.correlation_id = "corr-2"
        tool._handle_request(envelope)
        result_call = next(
            c for c in tool._pub.publish.call_args_list
            if c.args[0].subject == "tool.result.get_datetime"
        )
        result = result_call.args[0].payload["result"]
        assert isinstance(result, str) and len(result) > 0
