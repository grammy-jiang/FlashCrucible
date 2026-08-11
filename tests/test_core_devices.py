"""Unit tests for tfqa.core.devices module."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from tfqa.core.devices import discover_devices, get_device
from tfqa.core.errors import RuntimeIOError
from tfqa.core.models import DeviceInfo


class DiscoverDevicesTest(unittest.TestCase):
    def test_discover_devices_returns_list(self) -> None:
        devices = discover_devices()

        self.assertIsInstance(devices, list)

    def test_discover_devices_non_empty_on_linux(self) -> None:
        devices = discover_devices()

        self.assertGreater(len(devices), 0)

    def test_discover_devices_device_info_structure(self) -> None:
        devices = discover_devices()

        if devices:
            dev = devices[0]
            self.assertTrue(dev.path.startswith("/dev/"))
            self.assertNotEqual(dev.name, "")
            self.assertGreater(dev.size_bytes, 0)
            self.assertIsInstance(dev.is_removable, bool)
            self.assertIsInstance(dev.is_system_disk, bool)
            self.assertIsInstance(dev.mountpoints, list)

    def test_discover_devices_sys_block_not_found(self) -> None:
        with patch("tfqa.core.devices.Path.exists", return_value=False):
            with self.assertRaises(RuntimeIOError) as exc_info:
                discover_devices()

        self.assertIn("Cannot discover devices", str(exc_info.exception))

    def test_discover_devices_os_error(self) -> None:
        with patch("tfqa.core.devices.Path.exists", return_value=True):
            with patch(
                "tfqa.core.devices.Path.iterdir",
                side_effect=OSError("Permission denied"),
            ):
                with self.assertRaises(RuntimeIOError) as exc_info:
                    discover_devices()

        self.assertIn("Error discovering devices", str(exc_info.exception))

    def test_discover_devices_no_loop_devices(self) -> None:
        devices = discover_devices()

        self.assertTrue(all(not d.name.startswith("loop") for d in devices))
        self.assertTrue(all(not d.name.startswith("ram") for d in devices))


class GetDeviceTest(unittest.TestCase):
    def test_get_device_by_path(self) -> None:
        mock_device = DeviceInfo(
            path="/dev/sdb",
            name="sdb",
            size_bytes=128_000_000_000,
            is_removable=True,
            is_system_disk=False,
        )

        with patch(
            "tfqa.core.devices.discover_devices",
            return_value=[mock_device],
        ):
            device = get_device("/dev/sdb")

        self.assertEqual(device.path, "/dev/sdb")
        self.assertEqual(device.name, "sdb")

    def test_get_device_by_name_without_dev_prefix(self) -> None:
        mock_device = DeviceInfo(
            path="/dev/sdb",
            name="sdb",
            size_bytes=128_000_000_000,
            is_removable=True,
            is_system_disk=False,
        )

        with patch(
            "tfqa.core.devices.discover_devices",
            return_value=[mock_device],
        ):
            device = get_device("sdb")

        self.assertEqual(device.path, "/dev/sdb")

    def test_get_device_finds_by_name(self) -> None:
        mock_device = DeviceInfo(
            path="/dev/sdb",
            name="sdb",
            size_bytes=128_000_000_000,
            is_removable=True,
            is_system_disk=False,
        )

        with patch(
            "tfqa.core.devices.discover_devices",
            return_value=[mock_device],
        ):
            device = get_device("sdb")

        self.assertEqual(device.name, "sdb")

    def test_get_device_not_found(self) -> None:
        with patch(
            "tfqa.core.devices.discover_devices",
            return_value=[],
        ):
            with self.assertRaises(RuntimeIOError) as exc_info:
                get_device("/dev/nonexistent")

        self.assertIn("Device not found", str(exc_info.exception))

    def test_get_device_error_includes_path(self) -> None:
        device_path = "/dev/nonexistent"

        with patch(
            "tfqa.core.devices.discover_devices",
            return_value=[],
        ):
            with self.assertRaises(RuntimeIOError) as exc_info:
                get_device(device_path)

        self.assertIn(device_path, str(exc_info.exception))

    def test_get_device_returns_device_info(self) -> None:
        mock_device = DeviceInfo(
            path="/dev/sdb",
            name="sdb",
            size_bytes=128_000_000_000,
            is_removable=True,
            is_system_disk=False,
        )

        with patch(
            "tfqa.core.devices.discover_devices",
            return_value=[mock_device],
        ):
            device = get_device("/dev/sdb")

        self.assertIsInstance(device, DeviceInfo)
