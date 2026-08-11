from __future__ import annotations

from typing import Literal, TypedDict

from tfqa.core.models import DeviceInfo


class FullCapacityResult(TypedDict):
    status: Literal["ok", "fail"]
    message: str
    coverage_percent: float
    duration_seconds: float
    throughput_mbps: float
    issues: list[str]
    details: dict[str, object]


def run_full_capacity(
    device: DeviceInfo, *, force: bool, yes: bool
) -> FullCapacityResult:
    """Return a safe stub result for a destructive full capacity test."""

    return FullCapacityResult(
        status="ok",
        message="Simulated full capacity test completed successfully.",
        coverage_percent=100.0,
        duration_seconds=600.0,
        throughput_mbps=120.0,
        issues=[],
        details={
            "device_path": device.path,
            "force_override": force,
            "confirmation": yes,
        },
    )
