from __future__ import annotations

import math
from typing import cast
from unittest.mock import patch

from tfqa.core.errors import ToolNotFoundError
from tfqa.core.models import DeviceInfo
from tfqa.tests.surface.scan import SurfaceScanMetrics, run_surface_scan


def make_device(is_removable: bool, transport: str = "usb") -> DeviceInfo:
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


def _ensure_surface_metrics(metrics: dict[str, object]) -> SurfaceScanMetrics:
    return SurfaceScanMetrics(
        pass_count=cast(int, metrics["pass_count"]),
        coverage_percent=cast(float, metrics["coverage_percent"]),
        read_errors=cast(int, metrics["read_errors"]),
        duration_seconds=cast(float, metrics["duration_seconds"]),
        average_latency_ms=cast(float, metrics["average_latency_ms"]),
    )


def test_surface_scan_defaults_for_removable_device() -> None:
    device = make_device(is_removable=True, transport="usb")
    with patch(
        "tfqa.ext.badblocks.run_badblocks_readonly",
        side_effect=ToolNotFoundError("badblocks"),
    ):
        result = run_surface_scan(device, pass_count=2, duration_seconds=45.0)

    assert result["status"] == "ok"
    scan_metrics = _ensure_surface_metrics(result["metrics"])
    assert scan_metrics.pass_count == 2
    assert math.isclose(scan_metrics.duration_seconds, 45.0, rel_tol=1e-6)
    assert scan_metrics.read_errors == 0
    assert 75.0 <= scan_metrics.coverage_percent <= 100.0
    assert scan_metrics.average_latency_ms >= 2.0


def test_surface_scan_non_removable_injects_read_error() -> None:
    device = make_device(is_removable=False, transport="sata")
    with patch(
        "tfqa.ext.badblocks.run_badblocks_readonly",
        side_effect=ToolNotFoundError("badblocks"),
    ):
        result = run_surface_scan(device, pass_count=1)

    scan_metrics = _ensure_surface_metrics(result["metrics"])
    assert math.isclose(scan_metrics.average_latency_ms, 2.0, rel_tol=1e-9)
    assert scan_metrics.coverage_percent >= 85.0
    assert scan_metrics.coverage_percent >= 85.0
