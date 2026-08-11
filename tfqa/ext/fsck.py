"""Filesystem integrity wrapper around fsck for FlashCrucible."""

from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Any

from tfqa.core.errors import (
    PermissionError as TFQAPermissionError,
    RuntimeIOError,
    TimeoutError,
    ToolNotFoundError,
)

FSCK_TOOL_NAME = "fsck"

_SAFE_EXIT_CODES = {0, 1}


@dataclass(frozen=True)
class FsckResult:
    returncode: int
    status: str
    clean: bool
    errors_fixed: bool
    needs_reboot: bool
    fsck_error: bool
    operational_error: bool
    duration_seconds: float
    stdout: str
    stderr: str
    command: list[str]

    def model_dump(self) -> dict[str, Any]:
        return {
            "returncode": self.returncode,
            "status": self.status,
            "clean": self.clean,
            "errors_fixed": self.errors_fixed,
            "needs_reboot": self.needs_reboot,
            "fsck_error": self.fsck_error,
            "operational_error": self.operational_error,
            "duration_seconds": self.duration_seconds,
            "stdout": self.stdout.strip(),
            "stderr": self.stderr.strip(),
            "command": self.command,
        }


def _build_args(
    device_path: str, *, read_only: bool = True, force: bool = False
) -> list[str]:
    args: list[str] = [FSCK_TOOL_NAME]
    if read_only:
        args.append("-n")
    elif force:
        args.append("-f")
    args.extend(["-V", device_path])
    return args


def _map_status(returncode: int) -> str:
    if returncode in _SAFE_EXIT_CODES:
        return "ok"
    if returncode & 4 or returncode & 8:
        return "error"
    return "warning"


def run_fsck(
    device_path: str,
    *,
    read_only: bool = True,
    force: bool = False,
    timeout_seconds: float = 120.0,
) -> FsckResult:
    """Execute fsck against a block device without modifying scratches."""

    if shutil.which(FSCK_TOOL_NAME) is None:
        raise ToolNotFoundError(FSCK_TOOL_NAME)

    args = _build_args(device_path, read_only=read_only, force=force)
    start = time.monotonic()
    proc: subprocess.CompletedProcess[str] | None = None
    try:
        proc = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(
            message="fsck timed out",
            timeout_seconds=timeout_seconds,
            details={
                "device_path": device_path,
                "command": exc.cmd or args,
            },
        )
    except PermissionError as exc:
        raise TFQAPermissionError(
            message="Unable to execute fsck, insufficient privileges",
            details={"device_path": device_path, "error": str(exc)},
        )
    except OSError as exc:
        raise RuntimeIOError(
            message="fsck execution failed",
            details={"device_path": device_path, "error": str(exc)},
        )
    finally:
        duration = time.monotonic() - start

    returncode = proc.returncode if proc else 255
    clean = returncode == 0
    errors_fixed = bool(returncode & 1)
    needs_reboot = bool(returncode & 2)
    fsck_error = bool(returncode & 4)
    operational_error = bool(returncode & 8)
    status = _map_status(returncode)

    return FsckResult(
        returncode=returncode,
        status=status,
        clean=clean,
        errors_fixed=errors_fixed,
        needs_reboot=needs_reboot,
        fsck_error=fsck_error,
        operational_error=operational_error,
        duration_seconds=duration,
        stdout=proc.stdout if proc else "",
        stderr=proc.stderr if proc else "",
        command=args,
    )
