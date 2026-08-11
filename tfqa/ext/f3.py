"""Wrapper helpers for the F3 suite (fight flash fraud)."""

from __future__ import annotations

import re
import shutil
import subprocess
from typing import Any, Dict

from tfqa.core.errors import RuntimeIOError, TimeoutError, ToolNotFoundError

_REAL_CAPACITY_REGEX = re.compile(r"\(0x([0-9a-fA-F]+)\s+sectors\)")
_FAKE_CAPACITY_REGEX = re.compile(r"Fake capacity:\s*(YES|NO)", re.IGNORECASE)


def _find_tool(tool_name: str) -> str:
    path = shutil.which(tool_name)
    if not path:
        raise ToolNotFoundError(tool_name)
    return path


def _parse_real_size_bytes(output: str) -> int | None:
    match = _REAL_CAPACITY_REGEX.search(output)
    if not match:
        return None
    sectors = int(match.group(1), 16)
    return sectors * 512


def _parse_fake_detected(output: str) -> bool:
    match = _FAKE_CAPACITY_REGEX.search(output)
    if not match:
        return False
    return match.group(1).upper() == "YES"


def run_f3probe(
    device_path: str,
    timeout_seconds: float = 120.0,
) -> Dict[str, Any]:
    """Run f3probe to inspect a block device and parse its summary output."""

    tool_path = _find_tool("f3probe")
    try:
        proc = subprocess.run(
            [tool_path, device_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(
            "f3probe timed out",
            timeout_seconds,
            {"device_path": device_path, "command": exc.cmd},
        ) from exc

    if proc.returncode != 0:
        raise RuntimeIOError(
            "f3probe failed",
            {
                "device_path": device_path,
                "return_code": proc.returncode,
                "stderr": proc.stderr.strip(),
            },
        )

    stdout = proc.stdout or ""
    parsed: Dict[str, Any] = {
        "tool": "f3probe",
        "device_path": device_path,
        "fake_detected": _parse_fake_detected(stdout),
        "real_size_bytes": _parse_real_size_bytes(stdout),
        "stdout": stdout,
        "stderr": (proc.stderr or "").strip(),
        "exit_code": proc.returncode,
        "command": list(proc.args) if isinstance(proc.args, list) else [proc.args],
        "timeout_seconds": timeout_seconds,
    }
    return parsed
