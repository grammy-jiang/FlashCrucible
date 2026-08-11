"""Small-file workload engine for TFQA."""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from tfqa.core.errors import ArgumentError
from tfqa.core.logging import emit_event
from tfqa.core.models import RunContext, TestResult, TestStatus

FileWriteFn = Callable[[Path, int], int]
FileReadFn = Callable[[Path], int]
FileDeleteFn = Callable[[Path], None]
EventEmitter = Callable[[str, dict[str, Any], Path | None], Path]


@dataclass(frozen=True)
class SmallFileWorkloadConfig:
    """Configuration for a small-file workload test."""

    file_count: int = 256
    file_size_bytes: int = 1024
    working_dir: Path | None = None
    delete_after: bool = True
    read_after_write: bool = True
    file_prefix: str = "tfqa-smallfile"

    def with_overrides(self, **overrides: Any) -> "SmallFileWorkloadConfig":
        if not overrides:
            return self
        return replace(self, **overrides)


def _default_write_file(path: Path, size: int) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes([0xA5]) * size)
    return size


def _default_read_file(path: Path) -> int:
    data = path.read_bytes()
    return len(data)


def _default_delete_file(path: Path) -> None:
    path.unlink(missing_ok=True)


def _ensure_working_dir(config: SmallFileWorkloadConfig) -> tuple[Path, bool]:
    if config.working_dir:
        config.working_dir.mkdir(parents=True, exist_ok=True)
        return config.working_dir, False
    temp_dir = Path(tempfile.mkdtemp(prefix="tfqa-smallfiles-"))
    return temp_dir, True


def _process_file_operations(
    file_path: Path,
    config: SmallFileWorkloadConfig,
    write_file: FileWriteFn,
    read_file: FileReadFn,
    delete_file: FileDeleteFn,
) -> tuple[dict[str, int], str, str, bool]:
    metrics: dict[str, int] = {"bytes_written": 0, "bytes_read": 0, "errors": 0}
    status = "ok"
    message = f"Processed {file_path.name}."
    deleted_successfully = False

    try:
        bytes_written = write_file(file_path, config.file_size_bytes)
        metrics["bytes_written"] = bytes_written
    except Exception as exc:  # pragma: no cover - defensive
        metrics["errors"] += 1
        status = "warning"
        message = f"Failed to write {file_path.name}: {exc}"
        return metrics, status, message, deleted_successfully

    if config.read_after_write:
        try:
            bytes_read = read_file(file_path)
            metrics["bytes_read"] = bytes_read
        except Exception as exc:  # pragma: no cover - defensive
            metrics["errors"] += 1
            status = "warning"
            message = f"Failed to read {file_path.name}: {exc}"
            return metrics, status, message, deleted_successfully

    if config.delete_after:
        try:
            delete_file(file_path)
            deleted_successfully = True
        except Exception as exc:  # pragma: no cover - defensive
            metrics["errors"] += 1
            status = "warning"
            message = f"Failed to delete {file_path.name}: {exc}"

    return metrics, status, message, deleted_successfully


def validate_config(config: SmallFileWorkloadConfig) -> None:
    """Reject a config the engine cannot run.

    Split out from the engine so the CLI can apply the same rules before
    emitting a dry-run plan; otherwise `--dry-run` advertises a plan that the
    real invocation would refuse.
    """

    if config.file_count <= 0:
        raise ArgumentError(
            message="file_count must be positive",
            details={"file_count": config.file_count},
        )
    if config.file_size_bytes <= 0:
        raise ArgumentError(
            message="file_size_bytes must be positive",
            details={"file_size_bytes": config.file_size_bytes},
        )


def run_small_file_workload(
    ctx: RunContext,
    config: SmallFileWorkloadConfig,
    *,
    write_file: FileWriteFn = _default_write_file,
    read_file: FileReadFn = _default_read_file,
    delete_file: FileDeleteFn = _default_delete_file,
    event_emitter: EventEmitter = emit_event,
) -> TestResult:
    """Execute the small-file workload test and emit structured events."""

    validate_config(config)

    working_dir, cleanup_dir = _ensure_working_dir(config)
    total_bytes_written = 0
    total_bytes_read = 0
    files_created = 0
    files_read = 0
    files_deleted = 0
    total_errors = 0
    last_log_path: Path | None = None
    start_at = ctx.started_at

    try:
        for index in range(1, config.file_count + 1):
            file_path = working_dir / f"{config.file_prefix}_{index:04d}.bin"
            metrics, status, message, deleted = _process_file_operations(
                file_path, config, write_file, read_file, delete_file
            )

            total_bytes_written += metrics["bytes_written"]
            total_bytes_read += metrics["bytes_read"]
            files_created += int(metrics["bytes_written"] > 0)
            files_read += int(metrics["bytes_read"] > 0)
            files_deleted += int(deleted)
            total_errors += metrics["errors"]

            event_type = "progress" if status == "ok" else "error"
            last_log_path = event_emitter(
                ctx.run_id,
                {
                    "phase": "workload",
                    "stage": "smallfiles",
                    "event_type": event_type,
                    "device_path": ctx.device.path,
                    "file_path": str(file_path),
                    "status": status,
                    "metrics": metrics,
                    "message": message,
                },
                ctx.log_dir,
            )
    finally:
        if cleanup_dir:
            shutil.rmtree(working_dir, ignore_errors=True)

    finished_at = datetime.now(timezone.utc)
    duration_seconds = (finished_at - start_at).total_seconds()
    result_status: TestStatus = "warning" if total_errors else "ok"
    warnings: list[str] = []
    if total_errors:
        warnings.append("Some file operations failed during the workload.")

    return TestResult(
        name="workload.smallfiles",
        status=result_status,
        started_at=start_at,
        finished_at=finished_at,
        duration_seconds=duration_seconds,
        metrics={
            "files_created": files_created,
            "files_read": files_read,
            "files_deleted": files_deleted,
            "total_bytes_written": total_bytes_written,
            "total_bytes_read": total_bytes_read,
            "total_errors": total_errors,
            "duration_seconds": duration_seconds,
        },
        details={
            "config": {
                "file_count": config.file_count,
                "file_size_bytes": config.file_size_bytes,
                "working_dir": str(config.working_dir) if config.working_dir else None,
                "delete_after": config.delete_after,
                "read_after_write": config.read_after_write,
            },
            "files": files_created,
        },
        warnings=warnings,
        logs_path=last_log_path,
    )
