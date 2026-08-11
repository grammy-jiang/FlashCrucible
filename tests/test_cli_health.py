import unittest
from typing import Callable

from unittest.mock import patch
from typer.testing import CliRunner

from tfqa.cli.main import app
from tfqa.core.models import CLIResponse, DeviceInfo


def make_device(path: str, name: str = "sdb") -> DeviceInfo:
    return DeviceInfo(
        path=path,
        name=name,
        model="Model",
        vendor="Vendor",
        serial="SN",
        size_bytes=1024,
        is_removable=True,
        is_system_disk=False,
        mountpoints=[],
        transport="usb",
    )


def _fake_get_device(
    device: DeviceInfo, test_case: unittest.TestCase | None = None
) -> Callable[[str], DeviceInfo]:
    def _inner(path: str) -> DeviceInfo:
        if test_case:
            test_case.assertEqual(path, device.path)
        elif path != device.path:
            raise ValueError(f"Expected path {device.path}, got {path}")
        return device

    return _inner


def _snapshot(available: bool = True) -> dict[str, object]:
    """A snapshot in the shape the readers now produce."""

    sdmon_missing: dict[str, object] = {
        "available": False,
        "error_code": "EXT_TOOL_MISSING",
        "reason": "Required tool not found: sdmon",
    }
    if available:
        cid: dict[str, object] = {"product_name": "SD32G", "is_card_identity": True}
        health: dict[str, object] = {"life_used_percent": 30}
        extcsd: dict[str, object] = {"available": True}
        details: dict[str, object] = {"device_path": "/dev/sdb"}
    else:
        cid = {"product_name": "Generic- USB3.0 CRW", "is_card_identity": False}
        health = {}
        extcsd = {
            "available": False,
            "error_code": "RUNTIME_IO_ERROR",
            "reason": "Could not read EXT_CSD from /dev/sdb",
        }
        details = {"device_path": "/dev/sdb", "identity_is_not_card_cid": True}

    return {
        "source": "sysfs:mmc+mmc-utils" if available else "sysfs:scsi",
        "available": available,
        "cid": cid,
        "health": health,
        "sources": {
            "sysfs": {"available": True},
            "mmc-extcsd": extcsd,
            "sdmon": sdmon_missing,
        },
        "details": details,
    }


class HealthCLI(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()

    def _invoke(self, snapshot: dict[str, object], *extra: str):
        device = make_device("/dev/sdb")
        with (
            patch("tfqa.core.devices.get_device", _fake_get_device(device, self)),
            patch(
                "tfqa.tests.health.snapshot.run_health_snapshot",
                lambda _device: snapshot,
            ),
        ):
            return self.runner.invoke(app, ["health", "--device", "/dev/sdb", *extra])

    def test_health_json(self) -> None:
        result = self._invoke(_snapshot(), "--output", "json")
        self.assertEqual(result.exit_code, 0)

        resp = CLIResponse.model_validate_json(result.stdout)
        self.assertEqual(resp.status, "ok")
        self.assertEqual(resp.command, "health")
        self.assertIn("snapshot", resp.data)
        self.assertEqual(resp.data["snapshot"].get("source"), "sysfs:mmc+mmc-utils")
        self.assertTrue(resp.data["snapshot"].get("available"))

    def test_health_json_reports_unavailable_without_inventing_values(self) -> None:
        result = self._invoke(_snapshot(available=False), "--output", "json")
        self.assertEqual(result.exit_code, 0)

        resp = CLIResponse.model_validate_json(result.stdout)
        snapshot = resp.data["snapshot"]
        self.assertFalse(snapshot["available"])
        self.assertEqual(snapshot["health"], {})
        self.assertIn("No health data available", resp.message)

    def test_health_human_output(self) -> None:
        result = self._invoke(_snapshot())
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Health snapshot", result.stdout)
        self.assertIn("life_used_percent: 30", result.stdout)

    def test_health_human_output_names_each_source(self) -> None:
        # An absent reading used to look identical to a real one.
        result = self._invoke(_snapshot(available=False))
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Health: no data available", result.stdout)
        self.assertIn("sdmon: unavailable", result.stdout)
        self.assertIn("Required tool not found: sdmon", result.stdout)

    def test_health_human_output_flags_non_card_identity(self) -> None:
        # `is_card_identity` is False for any SCSI-style device, so the wording
        # must not claim it is specifically a USB reader.
        result = self._invoke(_snapshot(available=False))
        self.assertIn("Device identity (no MMC CID available)", result.stdout)
        self.assertIn("not the card's CID register", result.stdout)
        self.assertNotIn("Reader identity", result.stdout)

    def test_health_runtime_failure(self) -> None:
        device = make_device("/dev/sdb")
        with (
            patch("tfqa.core.devices.get_device", _fake_get_device(device, self)),
            patch(
                "tfqa.tests.health.snapshot.run_health_snapshot",
                side_effect=RuntimeError("boom"),
            ),
        ):
            result = self.runner.invoke(
                app, ["health", "--device", "/dev/sdb", "--output", "json"]
            )

        self.assertNotEqual(result.exit_code, 0)
        resp = CLIResponse.model_validate_json(result.stdout)
        self.assertEqual(resp.status, "error")
        self.assertEqual(resp.error_code, "INTERNAL_ERROR")
        self.assertIn("boom", resp.message)
