"""Device discovery and classification.

Discovers block devices on the system and determines their properties:
  - Removable devices (USB, SD cards)
  - System disks (mounted at /, /boot, etc.)
  - Mount points and filesystem types
  - Device model, vendor, serial, size, transport
"""

from __future__ import annotations

from pathlib import Path

from tfqa.core.errors import RuntimeIOError
from tfqa.core.models import DeviceInfo


def _read_sysfs_attr(sys_path: Path, attr: str) -> str | None:
    """Read a sysfs attribute file safely.

    Args:
        sys_path: Path object pointing to device directory in /sys/block
        attr: Attribute name (e.g., 'size', 'removable', 'vendor', 'model')

    Returns:
        Attribute value as string, or None if not found
    """
    attr_file = sys_path / attr
    if not attr_file.exists():
        return None
    try:
        return attr_file.read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        return None


def _get_device_mountpoints(device_path: str) -> list[dict[str, str]]:
    """Get mountpoints for a device.

    Parses /proc/mounts to find all filesystems mounted on the device.

    Args:
        device_path: Device path (e.g., '/dev/sdb1')

    Returns:
        List of dicts with 'mountpoint' and 'fstype' keys
    """
    mountpoints: list[dict[str, str]] = []
    try:
        mounts_file = Path("/proc/mounts")
        if not mounts_file.exists():
            return mountpoints

        content = mounts_file.read_text(encoding="utf-8")
        for line in content.split("\n"):
            if not line.strip():
                continue
            parts = line.split()
            if len(parts) >= 3 and parts[0].startswith(device_path):
                mountpoints.append({"mountpoint": parts[1], "fstype": parts[2]})
    except (OSError, ValueError):
        pass

    return mountpoints


def _is_system_disk_heuristic(
    device_path: str, mountpoints: list[dict[str, str]]
) -> bool:
    """Determine if device is likely a system disk using heuristics.

    Checks if device is mounted at critical system locations.

    Args:
        device_path: Device path (e.g., '/dev/sda')
        mountpoints: List of mountpoint dicts

    Returns:
        True if likely system disk, False otherwise
    """
    # Check for critical mountpoints
    critical_mounts = {"/", "/boot", "/etc", "/usr", "/var", "/home"}
    for mp_dict in mountpoints:
        if mp_dict.get("mountpoint") in critical_mounts:
            return True

    # Check if device is root device (heuristic)
    try:
        # Try to read /proc/cmdline for root device
        proc_cmdline = Path("/proc/cmdline").read_text(encoding="utf-8")
        if device_path in proc_cmdline:
            return True
    except (OSError, ValueError):
        pass

    return False


def _get_device_size_bytes(sys_path: Path) -> int | None:
    """Get device size in bytes from sysfs.

    Args:
        sys_path: Path object pointing to device directory in /sys/block

    Returns:
        Size in bytes, or None if not found
    """
    size_sectors_str = _read_sysfs_attr(sys_path, "size")
    if not size_sectors_str:
        return None
    try:
        size_sectors = int(size_sectors_str.strip())
        return size_sectors * 512
    except ValueError:
        return None


def _get_device_serial(sys_path: Path) -> str | None:
    """Get device serial number from sysfs.

    Args:
        sys_path: Path object pointing to device directory in /sys/block

    Returns:
        Serial number, or None if not found
    """
    serial_path = sys_path / "device" / "serial"
    if serial_path.exists():
        try:
            return serial_path.read_text(encoding="utf-8").strip()
        except (OSError, ValueError):
            pass
    return None


def _process_device(device_dir: Path) -> DeviceInfo | None:
    """Extract device info from a /sys/block/{device} directory.

    Args:
        device_dir: Path to device directory in /sys/block

    Returns:
        DeviceInfo object or None if device should be skipped
    """
    device_name = device_dir.name
    device_path = f"/dev/{device_name}"

    # Skip loop, ram devices
    if device_name.startswith(("loop", "ram")):
        return None

    # Get device size
    size_bytes = _get_device_size_bytes(device_dir)
    if not size_bytes:
        return None

    # Get removable flag
    removable_str = _read_sysfs_attr(device_dir, "removable")
    is_removable = removable_str is not None and removable_str.strip() == "1"

    # Get vendor and model
    vendor = _read_sysfs_attr(device_dir, "device/vendor")
    if vendor:
        vendor = vendor.strip()
    model = _read_sysfs_attr(device_dir, "device/model")
    if model:
        model = model.strip()

    # Get serial
    serial = _get_device_serial(device_dir)

    # Get transport
    transport = _read_sysfs_attr(device_dir, "device/transport")
    if transport:
        transport = transport.strip()

    # Get mountpoints
    mountpoints = _get_device_mountpoints(device_path)

    # Determine if system disk
    is_system = _is_system_disk_heuristic(device_path, mountpoints)

    return DeviceInfo(
        path=device_path,
        name=device_name,
        model=model,
        vendor=vendor,
        serial=serial,
        size_bytes=size_bytes,
        is_removable=is_removable,
        is_system_disk=is_system,
        mountpoints=mountpoints,
        transport=transport,
    )


def discover_devices() -> list[DeviceInfo]:
    """Discover all block devices on the system.

    Scans /sys/block for all block devices and collects metadata
    including: size, removable flag, vendor, model, serial, mountpoints.

    Returns:
        List of DeviceInfo objects

    Raises:
        RuntimeIOError: If device discovery fails catastrophically
    """
    sys_block_path = Path("/sys/block")

    if not sys_block_path.exists():
        raise RuntimeIOError(
            "Cannot discover devices: /sys/block not found",
            {"reason": "Not running on Linux or /sys not mounted"},
        )

    devices: list[DeviceInfo] = []

    try:
        for device_dir in sorted(sys_block_path.iterdir()):
            if not device_dir.is_dir():
                continue

            device = _process_device(device_dir)
            if device:
                devices.append(device)

    except OSError as e:
        raise RuntimeIOError(
            f"Error discovering devices: {e}",
            {"error": str(e)},
        ) from e

    return devices


def get_device(device_path: str) -> DeviceInfo:
    """Get information about a specific device.

    Args:
        device_path: Device path (e.g., '/dev/sdb')

    Returns:
        DeviceInfo object

    Raises:
        RuntimeIOError: If device not found or inaccessible
    """
    # Normalize device path
    if not device_path.startswith("/dev/"):
        device_path = f"/dev/{device_path}"

    # Extract device name
    device_name = Path(device_path).name

    # Try to get from discovered devices
    devices = discover_devices()
    for dev in devices:
        if dev.path == device_path or dev.name == device_name:
            return dev

    raise RuntimeIOError(
        f"Device not found: {device_path}",
        {"device_path": device_path},
    )
