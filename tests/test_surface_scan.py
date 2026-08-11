"""Tests for the surface scan engine.

Without `badblocks` this used to return `_simulate_surface_scan`: a
`coverage_percent` derived from device size and `read_errors = 0 if readonly
else 1` -- a coverage figure and a defect count for a physical surface check
that never touched the card. Both landed in `metrics`, which `trends`
aggregates, while the `tool: "simulated"` marker sat in `details`, which it
does not.

The tests that used to live here pinned that behaviour, one of them named
`test_surface_scan_non_removable_injects_read_error`.
"""

from __future__ import annotations

from typing import cast
from unittest.mock import patch

import pytest

from tfqa.core.errors import ToolNotFoundError
from tfqa.core.models import DeviceInfo
from tfqa.tests.surface.scan import run_surface_scan


def make_device(is_removable: bool = True, transport: str = "usb") -> DeviceInfo:
    return DeviceInfo(
        path="/dev/fake",
        name="fake",
        model="FakeModel",
        vendor="Vendor",
        serial="SN",
        size_bytes=64 * 1024 * 1024 * 1024,
        is_removable=is_removable,
        is_system_disk=False,
        mountpoints=[],
        transport=transport,
    )


TOOL_RESULT = {
    "pass_count": 2,
    "coverage_percent": 99.5,
    "read_errors": 3,
    "duration_seconds": 45.0,
    "average_latency_ms": 2.5,
    "pass_stats": [],
}


class WithoutBadblocks:
    """The tool is missing, so there is nothing to report."""

    @pytest.mark.parametrize("removable", [True, False])
    def test_refuses_rather_than_simulating(self, removable: bool) -> None:
        device = make_device(is_removable=removable)
        with patch(
            "tfqa.ext.badblocks.run_badblocks_readonly",
            side_effect=ToolNotFoundError("badblocks"),
        ):
            with pytest.raises(ToolNotFoundError):
                run_surface_scan(device, pass_count=2, duration_seconds=45.0)

    def test_destructive_mode_also_refuses(self) -> None:
        with patch(
            "tfqa.ext.badblocks.run_badblocks_write",
            side_effect=ToolNotFoundError("badblocks"),
        ):
            with pytest.raises(ToolNotFoundError):
                run_surface_scan(make_device(), mode="destructive")


class WithBadblocks:
    """Real tool output is reported unchanged."""

    def test_reports_what_the_tool_measured(self) -> None:
        with patch(
            "tfqa.ext.badblocks.run_badblocks_readonly", return_value=dict(TOOL_RESULT)
        ):
            result = run_surface_scan(make_device(), pass_count=2)

        metrics = cast(dict[str, object], result["metrics"])
        assert result["status"] == "ok"
        assert metrics["pass_count"] == 2
        assert metrics["coverage_percent"] == 99.5
        assert metrics["read_errors"] == 3
        assert cast(dict[str, object], result["details"])["tool"] == "badblocks"

    def test_read_errors_are_not_invented(self) -> None:
        # The old code returned 1 for a non-removable device in destructive
        # mode regardless of what the surface actually looked like.
        clean = dict(TOOL_RESULT, read_errors=0)
        with patch("tfqa.ext.badblocks.run_badblocks_write", return_value=clean):
            result = run_surface_scan(
                make_device(is_removable=False), mode="destructive"
            )

        assert cast(dict[str, object], result["metrics"])["read_errors"] == 0
