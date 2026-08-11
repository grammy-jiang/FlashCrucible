"""Simulated random I/O performance runner for Phase 2 benchmarks."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import logging
from typing import Literal, TypedDict

from tfqa.core.models import DeviceInfo
from tfqa.core.errors import ToolNotFoundError
from tfqa.ext import fio
from tfqa.tests.health import snapshot as health_snapshot
from tfqa.tests.health.snapshot import HealthSnapshot


@dataclass(frozen=True)
class RandomPerformanceMetrics:
    """Metrics captured when running a random I/O benchmark."""

    random_read_mbps: float
    random_write_mbps: float
    io_depth: int
    duration_seconds: float
    block_size: str
    rw_mix: str
    random_read_percentage: int


class RandomPerformanceResult(TypedDict):
    status: Literal["ok", "fail"]
    metrics: dict[str, object]
    device: dict[str, str]
    details: dict[str, object]


def _parse_block_size(size: str) -> int:
    normalized = size.strip().lower()
    multiplier = 1
    if normalized.endswith("k"):
        multiplier = 1024
        normalized = normalized[:-1]
    elif normalized.endswith("m"):
        multiplier = 1024 * 1024
        normalized = normalized[:-1]
    elif normalized.endswith("g"):
        multiplier = 1024 * 1024 * 1024
        normalized = normalized[:-1]

    if not normalized:
        return 4096

    try:
        value = int(normalized)
    except ValueError:
        return 4096

    return max(1, value * multiplier)


def _clamp_percentage(value: int) -> int:
    return max(0, min(100, value))


def _compute_base_throughput(device: DeviceInfo) -> float:
    return 120.0 if device.is_removable else 220.0


_LOGGER = logging.getLogger(__name__)


def _collect_health_snapshot(device: DeviceInfo) -> HealthSnapshot | None:
    try:
        return health_snapshot.run_health_snapshot(device)
    except Exception as exc:  # pragma: no cover
        _LOGGER.debug("Health snapshot failed for %s: %s", device.path, exc)
        return None


def run_random_performance(
    device: DeviceInfo,
    *,
    duration_seconds: float = 30.0,
    block_size: str = "4k",
    io_depth: int = 32,
    rw_mix: str = "randrw",
    random_read_percentage: int = 50,
) -> RandomPerformanceResult:
    """Simulate random I/O performance metrics."""

    block_bytes = _parse_block_size(block_size)
    read_percentage = _clamp_percentage(random_read_percentage)
    try:
        extra_args = ["--rwmixread", str(read_percentage)]
        fio_result = fio.run_fio_job(
            device.path,
            "tfqa-random",
            rw=rw_mix,
            bs=block_size,
            iodepth=io_depth,
            runtime=duration_seconds,
            extra_args=extra_args,
        )
        health_data = _collect_health_snapshot(device)
        details: dict[str, object] = {
            "sampling": "random",
            "block_size": block_size,
            "rw_mix": rw_mix,
            "duration_seconds": duration_seconds,
            "mode": "fio",
            "fio_job": fio_result,
        }
        if health_data:
            details["health_snapshot"] = health_data

        metrics = RandomPerformanceMetrics(
            random_read_mbps=round(fio_result["read_bw_kbps"] / 1024, 2),
            random_write_mbps=round(fio_result["write_bw_kbps"] / 1024, 2),
            io_depth=fio_result["iodepth"],
            duration_seconds=fio_result["runtime"],
            block_size=block_size,
            rw_mix=rw_mix,
            random_read_percentage=read_percentage,
        )
    except ToolNotFoundError:
        _LOGGER.debug("fio unavailable, falling back to simulated random metrics")
        base = _compute_base_throughput(device)
        depth_factor = min(2.0, max(0.5, io_depth / 32))
        block_factor = max(0.5, 4096 / block_bytes)

        read_mbps = round(
            base * depth_factor * block_factor * (read_percentage / 100), 2
        )
        write_mbps = round(
            base * depth_factor * block_factor * ((100 - read_percentage) / 100), 2
        )

        metrics = RandomPerformanceMetrics(
            random_read_mbps=read_mbps,
            random_write_mbps=write_mbps,
            io_depth=io_depth,
            duration_seconds=duration_seconds,
            block_size=block_size,
            rw_mix=rw_mix,
            random_read_percentage=read_percentage,
        )
        details = {
            "sampling": "random",
            "block_size": block_size,
            "rw_mix": rw_mix,
            "duration_seconds": duration_seconds,
            "mode": "simulated",
            "reason": "fio missing",
        }
        health_data = _collect_health_snapshot(device)
        if health_data:
            details["health_snapshot"] = health_data

    result: RandomPerformanceResult = {
        "status": "ok",
        "metrics": asdict(metrics),
        "device": {"path": device.path},
        "details": details,
    }

    return result
