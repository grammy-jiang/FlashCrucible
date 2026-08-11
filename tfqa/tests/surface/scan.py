"""Surface inspection utilities for Phase 2 QA flows."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import logging
from typing import Literal, SupportsFloat, SupportsInt, TypedDict, cast

from tfqa.core.errors import ToolNotFoundError
from tfqa.core.models import DeviceInfo
from tfqa.ext import badblocks
from tfqa.tests.health import snapshot as health_snapshot
from tfqa.tests.health.snapshot import HealthSnapshot


@dataclass(frozen=True)
class SurfaceScanMetrics:
    """Metrics captured for a surface scan."""

    pass_count: int
    coverage_percent: float
    read_errors: int
    duration_seconds: float
    average_latency_ms: float


class SurfaceScanResult(TypedDict):
    status: str
    metrics: dict[str, object]
    device: dict[str, str]
    details: dict[str, object]


SurfaceMode = Literal["readonly", "destructive"]

_LOGGER = logging.getLogger(__name__)


def _simulate_surface_scan(
    device: DeviceInfo,
    *,
    pass_count: int,
    duration_seconds: float,
    mode: SurfaceMode,
) -> SurfaceScanResult:
    base_coverage = 75.0 if device.is_removable else 85.0
    size_factor = min(25.0, device.size_bytes / (128 * 1024**3) * 5)
    coverage = min(100.0, base_coverage + size_factor)
    latency = 2.0 + (0.5 if device.transport == "usb" else 0.0)

    metrics = SurfaceScanMetrics(
        pass_count=pass_count,
        coverage_percent=round(coverage, 2),
        read_errors=0 if mode == "readonly" else 1,
        duration_seconds=duration_seconds,
        average_latency_ms=round(latency, 3),
    )

    details: dict[str, object] = {
        "mode": mode,
        "pass_stats": [
            {
                "pass": idx + 1,
                "coverage_percent": metrics.coverage_percent,
                "read_errors": metrics.read_errors,
            }
            for idx in range(pass_count)
        ],
        "read_only": mode == "readonly",
        "scanned_at": device.path,
        "tool": "simulated",
    }
    health_data = _collect_health_snapshot(device)
    if health_data:
        details["health_snapshot"] = health_data

    return {
        "status": "ok",
        "metrics": asdict(metrics),
        "device": {"path": device.path},
        "details": details,
    }


def _collect_health_snapshot(device: DeviceInfo) -> HealthSnapshot | None:
    try:
        return health_snapshot.run_health_snapshot(device)
    except Exception as exc:  # pragma: no cover (safety)
        _LOGGER.debug("Health snapshot failed for %s: %s", device.path, exc)
        return None


def run_surface_scan(
    device: DeviceInfo,
    *,
    pass_count: int = 1,
    duration_seconds: float = 60.0,
    mode: SurfaceMode = "readonly",
    block_size: int = 4096,
    timeout_seconds: float = 120.0,
) -> SurfaceScanResult:
    """Run a surface scan, preferring badblocks when available."""

    try:
        tool_result = (
            badblocks.run_badblocks_readonly(
                device.path,
                block_size=block_size,
                pass_count=pass_count,
                timeout_seconds=timeout_seconds,
            )
            if mode == "readonly"
            else badblocks.run_badblocks_write(
                device.path,
                block_size=block_size,
                pass_count=pass_count,
                timeout_seconds=timeout_seconds,
            )
        )
        coverage = cast(float, tool_result.get("coverage_percent", 98.0))
        latency = 2.0 if mode == "readonly" else 3.5
        metrics = SurfaceScanMetrics(
            pass_count=cast(int, tool_result["pass_count"]),
            coverage_percent=round(coverage, 2),
            read_errors=int(cast(SupportsInt, tool_result.get("read_errors", 0))),
            duration_seconds=float(
                cast(
                    SupportsFloat,
                    tool_result.get("duration_seconds", duration_seconds),
                )
            ),
            average_latency_ms=float(
                cast(SupportsFloat, tool_result.get("average_latency_ms", latency))
            ),
        )

        details: dict[str, object] = {
            "mode": mode,
            "tool": "badblocks",
            "tool_output": tool_result,
            "scanned_at": device.path,
            "pass_stats": tool_result.get("pass_stats", []),
        }
        health_data = _collect_health_snapshot(device)
        if health_data:
            details["health_snapshot"] = health_data

        return {
            "status": "ok",
            "metrics": asdict(metrics),
            "device": {"path": device.path},
            "details": details,
        }
    except ToolNotFoundError:
        return _simulate_surface_scan(
            device,
            pass_count=pass_count,
            duration_seconds=duration_seconds,
            mode=mode,
        )
