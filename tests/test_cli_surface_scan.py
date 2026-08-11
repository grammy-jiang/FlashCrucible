from __future__ import annotations

from pathlib import Path
from typing import Callable
from unittest.mock import patch
import unittest

from typer.testing import CliRunner

from tfqa.cli import main as cli_main
from tfqa.core.models import CLIResponse, DeviceInfo
from tfqa.tests.surface.scan import SurfaceScanResult


def make_device(path: str) -> DeviceInfo:
    return DeviceInfo(
        path=path,
        name="sdX",
        model="Model",
        vendor="Vendor",
        serial="SN",
        size_bytes=64 * 1024,
        is_removable=True,
        is_system_disk=False,
        mountpoints=[],
        transport="usb",
    )


def _fake_get_device(
    device: DeviceInfo, test_case: unittest.TestCase | None = None
) -> Callable[[str], DeviceInfo]:
    def inner(path: str) -> DeviceInfo:
        if test_case:
            test_case.assertEqual(path, device.path)
        elif path != device.path:
            raise ValueError(f"Expected path {device.path}, got {path}")
        return device

    return inner


class SurfaceScanCLI(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()

    def test_surface_scan_readonly_json(self) -> None:
        device = make_device("/dev/sdc")
        payload: SurfaceScanResult = {
            "status": "ok",
            "metrics": {
                "pass_count": 1,
                "coverage_percent": 98.5,
                "read_errors": 0,
                "duration_seconds": 45.0,
                "average_latency_ms": 2.1,
            },
            "device": {"path": device.path},
            "details": {"mode": "readonly", "tool": "badblocks"},
        }

        def run_surface_scan_stub(
            _: DeviceInfo,
            *,
            pass_count: int,
            duration_seconds: float,
            mode: str,
            block_size: int,
        ) -> SurfaceScanResult:
            self.assertEqual(pass_count, 1)
            self.assertEqual(mode, "readonly")
            return payload

        with (
            patch("tfqa.core.devices.get_device", _fake_get_device(device, self)),
            patch(
                "tfqa.tests.surface.scan.run_surface_scan",
                run_surface_scan_stub,
            ),
            patch(
                "tfqa.core.logging.emit_event",
                return_value=Path("/tmp/run-surface.jsonl"),
            ),
        ):
            result = self.runner.invoke(
                cli_main.app,
                [
                    "surface-scan",
                    "--device",
                    device.path,
                    "--passes",
                    "1",
                    "--duration",
                    "45",
                    "--output",
                    "json",
                ],
            )

        self.assertEqual(result.exit_code, 0)
        resp = CLIResponse.model_validate_json(result.stdout)
        self.assertEqual(resp.status, "ok")
        self.assertEqual(resp.data["metrics"]["coverage_percent"], 98.5)
        self.assertEqual(resp.data["details"]["tool"], "badblocks")

    def test_surface_scan_destructive_requires_force(self) -> None:
        device = make_device("/dev/sdd")
        with patch("tfqa.core.devices.get_device", _fake_get_device(device, self)):
            result = self.runner.invoke(
                cli_main.app,
                [
                    "surface-scan",
                    "--device",
                    device.path,
                    "--mode",
                    "destructive",
                    "--passes",
                    "1",
                    "--duration",
                    "60",
                    "--output",
                    "json",
                ],
            )

        self.assertNotEqual(result.exit_code, 0)
        resp = CLIResponse.model_validate_json(result.stdout)
        self.assertEqual(resp.status, "error")
        self.assertIn("Destructive scans require --force", resp.message)
