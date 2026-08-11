import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch
from typer.testing import CliRunner

from tfqa.cli.main import app
from tfqa.core.models import CLIResponse, DeviceInfo


def make_device(path: str, name: str = "sdX") -> DeviceInfo:
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


class QuickTestCLI(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()

    def test_quick_test_json_success(self):
        device = make_device("/dev/sdb")

        def get_device(_: str) -> DeviceInfo:
            return device

        def run_quick_capacity_stub(*_: object, **__: object) -> dict[str, object]:
            return {
                "fake_detected": False,
                "coverage_percent": 92.0,
                "duration_seconds": 120.0,
                "estimated_real_size_bytes": 1024,
                "throughput_mbps": 150.0,
            }

        with (
            patch("tfqa.core.devices.get_device", get_device),
            patch(
                "tfqa.tests.capacity.quick.run_quick_capacity", run_quick_capacity_stub
            ),
        ):
            result = self.runner.invoke(
                app, ["quick-test", "--device", "/dev/sdb", "--output", "json"]
            )
        self.assertEqual(result.exit_code, 0)

        resp = CLIResponse.model_validate_json(result.stdout)
        self.assertEqual(resp.status, "ok")
        self.assertEqual(resp.command, "quick-test")
        self.assertEqual(resp.device, {"path": "/dev/sdb"})
        self.assertFalse(resp.data["fake_detected"])
        self.assertIn("coverage_percent", resp.data)

    def test_quick_test_detects_fake(self):
        device = make_device("/dev/sdb")

        def get_device(_: str) -> DeviceInfo:
            return device

        def fake_run(*_: object, **__: object) -> dict[str, object]:
            return {"fake_detected": True, "coverage_percent": 85.0}

        with (
            patch("tfqa.core.devices.get_device", get_device),
            patch("tfqa.tests.capacity.quick.run_quick_capacity", fake_run),
        ):
            result = self.runner.invoke(
                app, ["quick-test", "--device", "/dev/sdb", "--output", "json"]
            )
        resp = CLIResponse.model_validate_json(result.stdout)

        self.assertEqual(resp.status, "fail")
        self.assertIn("fake", resp.message.lower())
        self.assertTrue(resp.data["fake_detected"])

    def test_quick_test_dry_run(self):
        device = make_device("/dev/sdb")

        def get_device(_: str) -> DeviceInfo:
            return device

        with patch("tfqa.core.devices.get_device", get_device):
            result = self.runner.invoke(
                app,
                ["quick-test", "--device", "/dev/sdb", "--dry-run", "--output", "json"],
            )
        resp = CLIResponse.model_validate_json(result.stdout)
        self.assertEqual(resp.status, "ok")
        self.assertIsNone(resp.run_id)
        self.assertEqual(resp.data["plan"]["device"], "/dev/sdb")

    def test_quick_test_emits_float_metrics(self):
        device = make_device("/dev/sdb")

        def get_device(_: str) -> DeviceInfo:
            return device

        def run_quick_capacity_stub(*_: object, **__: object) -> dict[str, object]:
            return {
                "status": "ok",
                "fake_detected": False,
                "coverage_percent": 92,
                "duration_seconds": 120,
                "estimated_real_size_bytes": 1024,
                "throughput_mbps": 150,
                "details": {"free_space_only": False},
            }

        emitted: dict[str, Any] = {}

        def emit_event_stub(
            run_id: str, event: dict[str, Any], log_dir: Path | None = None
        ) -> Path:
            emitted["metrics"] = event["metrics"]
            emitted["details"] = event["details"]
            return Path("/tmp/mock-run.jsonl")

        with (
            patch("tfqa.core.devices.get_device", get_device),
            patch(
                "tfqa.tests.capacity.quick.run_quick_capacity",
                run_quick_capacity_stub,
            ),
            patch("tfqa.core.logging.emit_event", emit_event_stub),
        ):
            result = self.runner.invoke(
                app, ["quick-test", "--device", "/dev/sdb", "--output", "json"]
            )
        resp = CLIResponse.model_validate_json(result.stdout)
        self.assertEqual(resp.status, "ok")
        self.assertIn("metrics", emitted)
        self.assertIsInstance(emitted["details"], dict)
        for value in emitted["metrics"].values():
            self.assertIsInstance(value, float)
