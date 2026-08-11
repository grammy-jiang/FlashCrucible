"""Image flash and verify helpers for FlashCrucible."""

from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from tfqa.core.errors import RuntimeIOError, TimeoutError, ToolNotFoundError

DD_TOOL = "dd"
CMP_TOOL = "cmp"

_DEFAULT_BLOCK_SIZE = "4M"


@dataclass(frozen=True)
class ImageCommandResult:
    returncode: int
    duration_seconds: float
    stdout: str
    stderr: str
    command: list[str]

    def model_dump(self) -> dict[str, object]:
        return {
            "returncode": self.returncode,
            "duration_seconds": self.duration_seconds,
            "stdout": self.stdout.strip(),
            "stderr": self.stderr.strip(),
            "command": self.command,
        }


def _ensure_tool(tool_name: str) -> None:
    if shutil.which(tool_name) is None:
        raise ToolNotFoundError(tool_name)


def _run_command(command: list[str], timeout_seconds: float) -> ImageCommandResult:
    start = time.monotonic()
    try:
        proc = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(
            message=f"{command[0]} timed out",
            timeout_seconds=timeout_seconds,
            details={
                "command": command,
                "device": command[-1],
                "error": str(exc),
            },
        )
    except OSError as exc:
        raise RuntimeIOError(
            message=f"Failed to execute {' '.join(command)}",
            details={"command": command, "error": str(exc)},
        )
    duration = time.monotonic() - start
    return ImageCommandResult(
        returncode=proc.returncode,
        duration_seconds=duration,
        stdout=proc.stdout or "",
        stderr=proc.stderr or "",
        command=command,
    )


def _build_dd_command(
    image_path: Path, device_path: str, block_size: str, conv_flags: Iterable[str]
) -> list[str]:
    args = [DD_TOOL, f"if={str(image_path)}", f"of={device_path}", f"bs={block_size}"]
    for flag in conv_flags:
        args.append(f"conv={flag}")
    args.append("status=progress")
    return args


def _build_cmp_command(
    image_path: Path, device_path: str, block_size: str
) -> list[str]:
    return [CMP_TOOL, "-n", block_size, str(image_path), device_path]


def run_image_flash(
    image_path: str,
    device_path: str,
    *,
    block_size: str = _DEFAULT_BLOCK_SIZE,
    conv_flags: Iterable[str] = ("fsync",),
    write_timeout: float = 600.0,
    verify_timeout: float = 300.0,
    verify: bool = True,
) -> dict[str, object]:
    """Flash an image via dd and optionally verify it with cmp."""

    image_file = Path(image_path)
    if not image_file.exists():
        raise RuntimeIOError(
            message="Image file not found",
            details={"image_path": image_path},
        )

    _ensure_tool(DD_TOOL)
    dd_command = _build_dd_command(image_file, device_path, block_size, conv_flags)
    write_result = _run_command(dd_command, write_timeout)

    verify_result = None
    if verify:
        _ensure_tool(CMP_TOOL)
        cmp_command = _build_cmp_command(image_file, device_path, block_size)
        verify_result = _run_command(cmp_command, verify_timeout)

    status = (
        "failed"
        if (
            write_result.returncode != 0
            or (verify_result and verify_result.returncode != 0)
        )
        else "ok"
    )

    metrics: dict[str, float | int] = {
        "write_duration_seconds": write_result.duration_seconds,
        "write_returncode": write_result.returncode,
    }
    details: dict[str, object] = {
        "write": write_result.model_dump(),
    }

    if verify_result:
        metrics["verify_duration_seconds"] = verify_result.duration_seconds
        metrics["verify_returncode"] = verify_result.returncode
        details["verify"] = verify_result.model_dump()
        details["verify_match"] = verify_result.returncode == 0
    else:
        details["verify"] = None
        details["verify_match"] = None

    return {
        "status": status,
        "metrics": metrics,
        "details": details,
    }
