from __future__ import annotations

from dataclasses import asdict, dataclass
import logging
from typing import Literal, TypedDict

from tfqa.core.errors import ToolNotFoundError
from tfqa.core.models import DeviceInfo
from tfqa.ext import fio
from tfqa.tests.health import snapshot as health_snapshot
from tfqa.tests.health.snapshot import HealthSnapshot

_LOGGER = logging.getLogger(__name__)


def _collect_health_snapshot(device: DeviceInfo) -> HealthSnapshot | None:
    try:
        return health_snapshot.run_health_snapshot(device)
    except Exception as exc:  # pragma: no cover
        _LOGGER.debug("Health snapshot failed for %s: %s", device.path, exc)
        return None


@dataclass(frozen=True)
class PerformanceMetrics:
    sequential_read_mbps: float
    sequential_write_mbps: float
    io_depth: int
    duration_seconds: float
    platform: str


class PerformanceResult(TypedDict):
    status: Literal["ok", "fail"]
    metrics: dict[str, object]
    device: dict[str, str]
    details: dict[str, object]


def run_seq_performance(
    device: DeviceInfo, *, duration_seconds: float = 30.0
) -> PerformanceResult:
    """Simulate a sequential performance test."""

    details: dict[str, object] = {
        "sampled_at": device.path,
        "duration_seconds": duration_seconds,
    }

    try:
        read_job = fio.run_fio_job(
            device.path,
            "tfqa-seq-read",
            rw="read",
            bs="1m",
            iodepth=32,
            runtime=duration_seconds,
        )
        write_job = fio.run_fio_job(
            device.path,
            "tfqa-seq-write",
            rw="write",
            bs="1m",
            iodepth=32,
            runtime=duration_seconds,
        )
        details.update({"mode": "fio", "read_job": read_job, "write_job": write_job})
        health_data = _collect_health_snapshot(device)
        if health_data:
            details["health_snapshot"] = health_data

        metrics = PerformanceMetrics(
            sequential_read_mbps=round(read_job["read_bw_kbps"] / 1024, 2),
            sequential_write_mbps=round(write_job["write_bw_kbps"] / 1024, 2),
            io_depth=32,
            duration_seconds=duration_seconds,
            platform=device.transport or "unknown",
        )
    except ToolNotFoundError:
        _LOGGER.debug("fio unavailable, falling back to simulated sequential metrics")
        throughput = 240.0 if device.is_removable else 520.0
        write_penalty = 0.85 if device.is_removable else 0.95
        metrics = PerformanceMetrics(
            sequential_read_mbps=throughput,
            sequential_write_mbps=throughput * write_penalty,
            io_depth=32,
            duration_seconds=duration_seconds,
            platform=device.transport or "unknown",
        )
        details["mode"] = "simulated"
        details["reason"] = "fio missing"
        health_data = _collect_health_snapshot(device)
        if health_data:
            details["health_snapshot"] = health_data

    result: PerformanceResult = {
        "status": "ok",
        "metrics": asdict(metrics),
        "device": {"path": device.path},
        "details": details,
    }
    return result
