"""Unit tests for tfqa.core.safety module."""

from __future__ import annotations

import unittest
from typing import Any

from tfqa.core.errors import DeviceUnsafeError
from tfqa.core.models import DeviceInfo
from tfqa.core.safety import (
    assert_safe_for_destructive,
    is_device_mounted,
    is_system_disk,
)


def make_device(**overrides: Any) -> DeviceInfo:
    base: dict[str, Any] = {
        "path": "/dev/sdb",
        "name": "sdb",
        "size_bytes": 128_000_000_000,
        "is_removable": True,
        "is_system_disk": False,
        "mountpoints": [],
        "transport": "usb",
    }
    base.update(overrides)
    return DeviceInfo(**base)


class SystemDiskDetectionTest(unittest.TestCase):
    def test_is_system_disk_true(self) -> None:
        device = make_device(
            path="/dev/sda", name="sda", is_removable=False, is_system_disk=True
        )

        self.assertTrue(is_system_disk(device))

    def test_is_system_disk_false(self) -> None:
        device = make_device()

        self.assertFalse(is_system_disk(device))


class MountpointDetectionTest(unittest.TestCase):
    def test_is_device_mounted_true(self) -> None:
        device = make_device(
            mountpoints=[{"mountpoint": "/mnt/usb", "fstype": "exfat"}]
        )

        self.assertTrue(is_device_mounted(device))

    def test_is_device_mounted_multiple(self) -> None:
        device = make_device(
            path="/dev/sda",
            name="sda",
            is_removable=False,
            is_system_disk=True,
            mountpoints=[
                {"mountpoint": "/", "fstype": "ext4"},
                {"mountpoint": "/boot", "fstype": "vfat"},
            ],
        )

        self.assertTrue(is_device_mounted(device))

    def test_is_device_mounted_false(self) -> None:
        device = make_device()

        self.assertFalse(is_device_mounted(device))


class AssertSafeForDestructiveTest(unittest.TestCase):
    def test_assert_safe_removable_device(self) -> None:
        device = make_device()

        assert_safe_for_destructive(device)

    def test_assert_safe_system_disk_raises(self) -> None:
        device = make_device(
            path="/dev/sda", name="sda", is_removable=False, is_system_disk=True
        )

        with self.assertRaises(DeviceUnsafeError) as exc_info:
            assert_safe_for_destructive(device)

        self.assertIn("system disk", str(exc_info.exception).lower())

    def test_assert_safe_mounted_device_raises(self) -> None:
        device = make_device(
            mountpoints=[{"mountpoint": "/mnt/usb", "fstype": "exfat"}]
        )

        with self.assertRaises(DeviceUnsafeError) as exc_info:
            assert_safe_for_destructive(device)

        self.assertIn("mountpoint", str(exc_info.exception).lower())

    def test_assert_safe_mounted_device_details_capture_mountpoints(self) -> None:
        mounts = [{"mountpoint": "/mnt/usb", "fstype": "exfat"}]
        device = make_device(mountpoints=mounts)

        with self.assertRaises(DeviceUnsafeError) as exc_info:
            assert_safe_for_destructive(device)

        self.assertEqual(exc_info.exception.details["mountpoints"], mounts)

    def test_assert_safe_both_unsafe_conditions(self) -> None:
        device = make_device(
            path="/dev/sda",
            name="sda",
            is_removable=False,
            is_system_disk=True,
            mountpoints=[{"mountpoint": "/", "fstype": "ext4"}],
        )

        with self.assertRaises(DeviceUnsafeError) as exc_info:
            assert_safe_for_destructive(device)

        error_msg = str(exc_info.exception).lower()
        self.assertIn("system disk", error_msg)
        self.assertIn("mountpoint", error_msg)

        details = exc_info.exception.details
        self.assertTrue(details.get("is_system_disk"))
        self.assertEqual(details.get("mountpoints"), device.mountpoints)

    def test_assert_safe_force_without_yes_raises(self) -> None:
        device = make_device(
            path="/dev/sda", name="sda", is_removable=False, is_system_disk=True
        )

        with self.assertRaises(DeviceUnsafeError):
            assert_safe_for_destructive(device, force=True, yes=False)

    def test_assert_safe_force_without_yes_requires_confirmation_flag(self) -> None:
        device = make_device(
            path="/dev/sda", name="sda", is_removable=False, is_system_disk=True
        )

        with self.assertRaises(DeviceUnsafeError) as exc_info:
            assert_safe_for_destructive(device, force=True, yes=False)

        self.assertTrue(exc_info.exception.details.get("requires_confirmation"))

    def test_assert_safe_force_with_yes_allows_override(self) -> None:
        device = make_device(
            path="/dev/sda", name="sda", is_removable=False, is_system_disk=True
        )

        assert_safe_for_destructive(device, force=True, yes=True)

    def test_assert_safe_force_with_yes_mounted_device_allows_override(self) -> None:
        device = make_device(
            mountpoints=[{"mountpoint": "/mnt/usb", "fstype": "exfat"}]
        )

        assert_safe_for_destructive(device, force=True, yes=True)

    def test_assert_safe_error_includes_device_path(self) -> None:
        device = make_device(
            path="/dev/sda", name="sda", is_removable=False, is_system_disk=True
        )

        with self.assertRaises(DeviceUnsafeError) as exc_info:
            assert_safe_for_destructive(device)

        self.assertEqual(exc_info.exception.details["device_path"], "/dev/sda")

    def test_assert_safe_error_has_device_unsafe_code(self) -> None:
        device = make_device(
            path="/dev/sda", name="sda", is_removable=False, is_system_disk=True
        )

        with self.assertRaises(DeviceUnsafeError) as exc_info:
            assert_safe_for_destructive(device)

        self.assertEqual(exc_info.exception.error_code, "DEVICE_UNSAFE")

    def test_assert_safe_override_hint_in_error(self) -> None:
        device = make_device(
            path="/dev/sda", name="sda", is_removable=False, is_system_disk=True
        )

        with self.assertRaises(DeviceUnsafeError) as exc_info:
            assert_safe_for_destructive(device, force=False)

        error_msg = str(exc_info.exception).lower()
        self.assertTrue("expert mode" in error_msg or "force" in error_msg)
