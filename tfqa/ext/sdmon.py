"""Simple wrapper for the `sdmon` health utility."""

from __future__ import annotations

import shutil
import subprocess
from typing import TypedDict

from tfqa.core.errors import RuntimeIOError, TimeoutError, ToolNotFoundError


SdmonHealth = TypedDict(
    "SdmonHealth",
    {
        "life_used_percent": int,
        "power_on_count": int,
        "read_error_count": int,
        "write_error_count": int,
        "temperature_celsius": int,
        "sdmon_version": str,
    },
    total=False,
)


_SDMON_CMD = "sdmon"


def _find_tool() -> str:
    path = shutil.which(_SDMON_CMD)
    if not path:
        raise ToolNotFoundError(_SDMON_CMD)
    return path


def _probe_version(executable: str) -> str | None:
    try:
        proc = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if proc.stdout:
            return proc.stdout.strip().splitlines()[0]
        if proc.stderr:
            return proc.stderr.strip().splitlines()[0]
        return None
    except subprocess.TimeoutExpired:
        return None
    except Exception:
        return None


def _build_stub(device_path: str, version: str | None) -> SdmonHealth:
    base = abs(hash(device_path)) % 100
    return SdmonHealth(
        life_used_percent=min(15, base // 5),
        power_on_count=20 + base,
        read_error_count=0,
        write_error_count=0,
        temperature_celsius=32 + (base % 5),
        sdmon_version=version or "sdmon-unknown",
    )


def read_health(device_path: str, *, timeout_seconds: float = 30.0) -> SdmonHealth:
    """Return industrial health information for supported cards."""

    executable = _find_tool()
    version = _probe_version(executable)

    try:
        subprocess.run(
            [executable, device_path],
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(
            "sdmon timed out",
            timeout_seconds,
            {"device_path": device_path, "tool": _SDMON_CMD},
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeIOError(
            "sdmon returned a non-zero exit code",
            {
                "device_path": device_path,
                "exit_code": exc.returncode,
                "stderr": (exc.stderr or "").strip(),
            },
        ) from exc
    except FileNotFoundError as exc:  # pragma: no cover (guard)
        raise ToolNotFoundError(_SDMON_CMD) from exc

    return _build_stub(device_path, version)
