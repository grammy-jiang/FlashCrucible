from __future__ import annotations

import time
from typing import Any, Dict

from tfqa.core.capabilities import check_tool
from tfqa.core.errors import ToolNotFoundError
from tfqa.core.models import DeviceInfo, ToolCapability
from tfqa.ext.f3 import run_f3probe


def _prepare_probe_command(device: DeviceInfo) -> tuple[ToolCapability, list[str]]:
    tool: ToolCapability = check_tool("f3probe")
    if not tool.available:
        raise ToolNotFoundError("f3probe")
    tool_path = tool.path or "f3probe"
    return tool, [tool_path, device.path]


def describe_probe_command(device: DeviceInfo) -> list[str]:
    """Return the concrete command that will run f3probe for this device."""
    _, command = _prepare_probe_command(device)
    return command


def run_quick_capacity(
    device: DeviceInfo, *, free_space_only: bool = True, timeout_seconds: float = 120.0
) -> dict[str, Any]:
    """Run a quick capacity/authenticity check against the device."""

    tool, command = _prepare_probe_command(device)

    start = time.monotonic()
    parsed: Dict[str, Any] = run_f3probe(device.path, timeout_seconds=timeout_seconds)
    duration_seconds = time.monotonic() - start

    real_size = parsed.get("real_size_bytes") or device.size_bytes
    coverage_percent = (
        min(100.0, (real_size / device.size_bytes) * 100) if device.size_bytes else 0.0
    )
    coverage_percent = round(coverage_percent, 1)
    throughput_mbps = (
        real_size / duration_seconds / (1024 * 1024) if duration_seconds > 0 else 0.0
    )

    status = "fail" if parsed.get("fake_detected") else "ok"
    details: Dict[str, Any] = {
        "free_space_only": free_space_only,
        "tool": parsed.get("tool", tool.name),
        "stdout": parsed.get("stdout", ""),
        "stderr": parsed.get("stderr", ""),
        "probe_command": parsed.get("command") or command,
        "timeout_seconds": parsed.get("timeout_seconds") or timeout_seconds,
    }

    return {
        "status": status,
        "fake_detected": bool(parsed.get("fake_detected")),
        "estimated_real_size_bytes": real_size,
        "test_size_bytes": real_size,
        "coverage_percent": coverage_percent,
        "duration_seconds": round(duration_seconds, 3),
        "throughput_mbps": round(throughput_mbps, 2),
        "details": details,
    }
