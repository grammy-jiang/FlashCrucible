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


def _clamp_percentage(value: int) -> int:
    return max(0, min(100, value))


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
        # Deliberately not caught -- see the note in basic.py. This branch used
        # to compute a throughput figure from block size and queue depth and
        # report it as a measurement.
        raise

    result: RandomPerformanceResult = {
        "status": "ok",
        "metrics": asdict(metrics),
        "device": {"path": device.path},
        "details": details,
    }

    return result
