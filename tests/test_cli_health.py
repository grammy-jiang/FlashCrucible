import unittest
from typing import Callable

from unittest.mock import patch
from typer.testing import CliRunner

from tfqa.cli.main import app
from tfqa.core.models import CLIResponse, DeviceInfo
from tfqa.tests.health.snapshot import run_health_snapshot


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


def _fake_snapshot(device: DeviceInfo) -> dict[str, object]:
    return dict(run_health_snapshot(device))


class HealthCLI(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()

    def test_health_json(self) -> None:
        device = make_device("/dev/sdb")
        with (
            patch("tfqa.core.devices.get_device", _fake_get_device(device, self)),
            patch(
                "tfqa.tests.health.snapshot.run_health_snapshot",
                _fake_snapshot,
            ),
        ):
            result = self.runner.invoke(
                app, ["health", "--device", "/dev/sdb", "--output", "json"]
            )
        self.assertEqual(result.exit_code, 0)

        resp = CLIResponse.model_validate_json(result.stdout)
        self.assertEqual(resp.status, "ok")
        self.assertEqual(resp.command, "health")
        self.assertIn("snapshot", resp.data)
        self.assertEqual(resp.data["snapshot"].get("source"), "tfqa.ext.mmc")

    def test_health_human_output(self) -> None:
        device = make_device("/dev/sdb")
        with (
            patch("tfqa.core.devices.get_device", _fake_get_device(device, self)),
            patch(
                "tfqa.tests.health.snapshot.run_health_snapshot",
                _fake_snapshot,
            ),
        ):
            result = self.runner.invoke(app, ["health", "--device", "/dev/sdb"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Health snapshot", result.stdout)

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
