"""Safety guardrails for destructive operations.

Prevents accidental data loss by verifying device safety before allowing
destructive tests (full capacity, surface scan, etc.).

Key responsibilities:
  - Detect system disks
  - Verify device is not mounted
  - Support force override with explicit flags
"""

from __future__ import annotations

from typing import Any

from tfqa.core.errors import DeviceUnsafeError
from tfqa.core.models import DeviceInfo


def is_system_disk(device: DeviceInfo) -> bool:
    """Check if device appears to be a system disk.

    Args:
        device: DeviceInfo object

    Returns:
        True if device is likely a system disk, False otherwise
    """
    return device.is_system_disk


def is_device_mounted(device: DeviceInfo) -> bool:
    """Check if device has active mountpoints.

    Args:
        device: DeviceInfo object

    Returns:
        True if device has any mountpoints, False otherwise
    """
    return len(device.mountpoints) > 0


def assert_safe_for_destructive(
    device: DeviceInfo,
    force: bool = False,
    yes: bool = False,
) -> None:
    """Verify device is safe for destructive operations.

    Checks for safety conditions and raises DeviceUnsafeError if unsafe.
    Can be overridden with force flag, but requires explicit yes confirmation.

    Args:
        device: DeviceInfo object to verify
        force: Allow override of safety checks (requires yes=True)
        yes: Confirm destructive operation (required with force=True)

    Raises:
        DeviceUnsafeError: If device is unsafe (even with force, if yes=False)
    """
    unsafe_reasons: list[str] = []
    extra_details: dict[str, Any] = {}

    # Check if system disk
    if is_system_disk(device):
        unsafe_reasons.append("appears to be a system disk")
        extra_details["is_system_disk"] = True

    # Check if mounted
    if is_device_mounted(device):
        unsafe_reasons.append("has active mountpoints")
        extra_details["mountpoints"] = device.mountpoints

    if not unsafe_reasons:
        return

    if force and yes:
        # Force override allowed, log warning but allow
        extra_details["forced_override"] = True
        return

    if force and not yes:
        # Force specified but no confirmation
        extra_details["requires_confirmation"] = True

    # Raise error with reasons
    reason = "; ".join(unsafe_reasons)
    if not (force and not yes):
        reason += ". Use --force --yes --non-interactive to override (expert mode only)"

    raise DeviceUnsafeError(
        device.path,
        reason,
        extra_details,
    )
