import unittest
from typing import cast
from unittest.mock import patch

from tfqa.core.models import DeviceInfo
from tfqa.tests.health.snapshot import run_health_snapshot


def make_device(path: str, name: str = "sdX") -> DeviceInfo:
    return DeviceInfo(
        path=path,
        name=name,
        model="Model",
        vendor="Vendor",
        serial="SN",
        size_bytes=16 * 1024,
        is_removable=True,
        is_system_disk=False,
        mountpoints=[],
        transport="usb",
    )


class HealthSnapshotTest(unittest.TestCase):
    def test_health_snapshot_uses_mmc(self):
        device = make_device("/dev/sdz")

        def fake_read_cid(path: str) -> dict[str, object]:
            self.assertEqual(path, device.path)
            return {
                "product_name": "StubCard",
                "serial_number": "0xABC",
            }

        def fake_read_health(path: str) -> dict[str, object]:
            self.assertEqual(path, device.path)
            return {
                "life_used_percent": 1,
                "power_on_count": 5,
            }

        with (
            patch("tfqa.ext.mmc.read_cid", fake_read_cid),
            patch("tfqa.ext.mmc.read_health", fake_read_health),
        ):
            snapshot = run_health_snapshot(device)

        self.assertEqual(snapshot["source"], "tfqa.ext.mmc")
        self.assertEqual(snapshot["cid"].get("product_name"), "StubCard")
        self.assertEqual(snapshot["cid"].get("serial_number"), "0xABC")
        self.assertEqual(snapshot["health"].get("power_on_count"), 5)
        self.assertEqual(snapshot["details"]["cid_provider"], "mmc")
        self.assertEqual(snapshot["details"]["device_path"], device.path)

    def test_health_snapshot_includes_sdmon_when_available(self):
        device = make_device("/dev/sdz")

        def fake_read_cid(path: str) -> dict[str, object]:
            self.assertEqual(path, device.path)
            return {"product_name": "StubCard"}

        def fake_read_health(path: str) -> dict[str, object]:
            self.assertEqual(path, device.path)
            return {"life_used_percent": 2}

        def fake_sdmon(path: str) -> dict[str, object]:
            self.assertEqual(path, device.path)
            return {"life_used_percent": 3, "sdmon_version": "sdmon 1.1"}

        with (
            patch("tfqa.ext.mmc.read_cid", fake_read_cid),
            patch("tfqa.ext.mmc.read_health", fake_read_health),
            patch("tfqa.ext.sdmon.read_health", fake_sdmon),
        ):
            snapshot = run_health_snapshot(device)

        self.assertEqual(snapshot["source"], "mmc-utils+sdmon")
        self.assertTrue(snapshot["details"].get("sdmon_available"))
        sdmon_details = cast(
            dict[str, object], snapshot["details"].get("sdmon_health", {})
        )
        self.assertEqual(sdmon_details.get("sdmon_version"), "sdmon 1.1")
