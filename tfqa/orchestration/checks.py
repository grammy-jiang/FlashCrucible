from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from tfqa.core.logging import emit_event
from tfqa.core.models import DeviceInfo
from tfqa.tests.capacity import quick as quick_capacity
from tfqa.tests.health import snapshot as health_snapshot


@dataclass(frozen=True)
class CheckResult:
    payload: dict[str, object]
    log_path: Path | None


def quick_test_check(
    run_id: str,
    device: DeviceInfo,
    log_dir: Path | None,
    *,
    label: Literal["pre", "post"],
    free_space_only: bool = False,
) -> CheckResult:
    payload = quick_capacity.run_quick_capacity(device, free_space_only=free_space_only)
    log_path = emit_event(
        run_id,
        {
            "phase": f"check.quick-test.{label}",
            "device_path": device.path,
            "metrics": {
                k: v for k, v in payload.items() if isinstance(v, (int, float))
            },
            "details": payload.get("details"),
        },
        log_dir=log_dir,
    )
    return CheckResult(payload=payload, log_path=log_path)


def health_snapshot_check(
    run_id: str,
    device: DeviceInfo,
    log_dir: Path | None,
    *,
    label: Literal["pre", "post"],
) -> CheckResult:
    snapshot = health_snapshot.run_health_snapshot(device)
    log_path = emit_event(
        run_id,
        {
            "phase": f"check.health.{label}",
            "device_path": device.path,
            "metrics": {
                k: v
                for k, v in snapshot.get("health", {}).items()
                if isinstance(v, (int, float))
            },
            "details": snapshot,
        },
        log_dir=log_dir,
    )
    # Cast TypedDict to dict[str, object] to satisfy CheckResult
    payload: dict[str, object] = dict(snapshot)
    return CheckResult(payload=payload, log_path=log_path)
