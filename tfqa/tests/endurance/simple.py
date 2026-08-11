"""Simple endurance/burn-in engine for FlashCrucible."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from tfqa.core.errors import ArgumentError
from tfqa.core.logging import emit_event
from tfqa.core.models import (
    DeviceInfo,
    EnduranceConfig,
    RunContext,
    TestResult,
    TestStatus,
)


def run_simple_endurance(ctx: RunContext, config: EnduranceConfig) -> TestResult:
    """Run a simple endurance simulation and emit structured metrics."""

    if config.duration_seconds <= 0:
        raise ArgumentError(
            message="Endurance duration must be positive",
            details={"duration_seconds": config.duration_seconds},
        )

    if config.pass_count <= 0:
        raise ArgumentError(
            message="Pass count must be at least 1",
            details={"pass_count": config.pass_count},
        )

    pass_history: list[dict[str, Any]] = []
    total_bytes = 0
    total_errors = 0
    throughput_sum = 0.0
    last_log_path = None

    for pass_index in range(1, config.pass_count + 1):
        throughput = _calculate_throughput(ctx.device, pass_index)
        bytes_written = int(throughput * 1_000_000 / 8 * config.duration_seconds)
        errors = _calculate_errors(ctx.device, config.force, pass_index)

        total_bytes += bytes_written
        total_errors += errors
        throughput_sum += throughput

        entry: dict[str, Any] = {
            "pass_index": pass_index,
            "throughput_mbps": round(throughput, 3),
            "bytes_written": bytes_written,
            "errors": errors,
        }
        pass_history.append(entry)

        last_log_path = emit_event(
            ctx.run_id,
            {
                "phase": "endurance",
                "device_path": ctx.device.path,
                "pass_index": pass_index,
                "pass_metrics": entry,
                "duration_seconds": config.duration_seconds,
                "write_pattern": config.write_pattern,
                "force": config.force,
            },
            log_dir=ctx.log_dir,
        )

    finished_at = datetime.now(timezone.utc)
    status: TestStatus = "warning" if total_errors else "ok"
    average_throughput = throughput_sum / config.pass_count
    metrics: dict[str, float | int] = {
        "total_bytes_written": total_bytes,
        "total_errors": total_errors,
        "average_throughput_mbps": round(average_throughput, 3),
        "duration_seconds": config.duration_seconds * config.pass_count,
    }
    warnings: list[str] = []
    if total_errors:
        warnings.append("Errors were observed during endurance passes.")

    return TestResult(
        name="endurance.simple",
        status=status,
        started_at=ctx.started_at,
        finished_at=finished_at,
        duration_seconds=config.duration_seconds * config.pass_count,
        metrics=metrics,
        details={
            "pass_count": config.pass_count,
            "duration_seconds": config.duration_seconds,
            "write_pattern": config.write_pattern,
            "force": config.force,
            "pass_history": pass_history,
        },
        warnings=warnings,
        logs_path=last_log_path,
    )


def _calculate_throughput(device: DeviceInfo, pass_index: int) -> float:
    base = 150.0 if device.is_removable else 300.0
    degradation = 0.01 * (pass_index - 1)
    return max(base * (1.0 - degradation), 1.0)


def _calculate_errors(device: DeviceInfo, force: bool, pass_index: int) -> int:
    if force or device.is_removable:
        return 0
    return pass_index // 2
