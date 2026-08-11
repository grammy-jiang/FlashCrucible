import unittest
from unittest.mock import patch
from typer.testing import CliRunner

from tfqa.cli.main import app
from tfqa.core.errors import DeviceUnsafeError
from tfqa.core.models import CLIResponse, DeviceInfo
from tfqa.tests.capacity.full import FullCapacityResult


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


class FullCapacityCLI(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()

    def test_full_capacity_json_success(self):
        device = make_device("/dev/sdb")

        def get_device(_: str) -> DeviceInfo:
            return device

        payload: FullCapacityResult = {
            "status": "ok",
            "message": "Simulated full test.",
            "coverage_percent": 100.0,
            "duration_seconds": 600.0,
            "throughput_mbps": 120.0,
            "issues": [],
            "details": {},
        }

        def run_full_capacity_stub(
            _: DeviceInfo, *, force: bool, yes: bool, **options: object
        ) -> FullCapacityResult:
            self.assertTrue(force)
            self.assertTrue(yes)
            # The CLI now forwards the pattern/IO options to the engine.
            self.assertIn("block_size", options)
            self.assertIn("seed", options)
            return payload

        with (
            patch("tfqa.core.devices.get_device", get_device),
            patch("tfqa.tests.capacity.full.run_full_capacity", run_full_capacity_stub),
        ):
            result = self.runner.invoke(
                app,
                [
                    "full-capacity-test",
                    "--device",
                    "/dev/sdb",
                    "--force",
                    "--yes",
                    "--output",
                    "json",
                ],
            )
        self.assertEqual(result.exit_code, 0)

        resp = CLIResponse.model_validate_json(result.stdout)
        self.assertEqual(resp.status, "ok")
        coverage_value = float(resp.data["coverage_percent"])
        self.assertAlmostEqual(coverage_value, 100.0, delta=1e-6)
        self.assertEqual(resp.device, {"path": "/dev/sdb"})
        self.assertIsNotNone(resp.run_id)

    def test_full_capacity_human_failure(self):
        device = make_device("/dev/sdc")

        def get_device(_: str) -> DeviceInfo:
            return device

        payload: FullCapacityResult = {
            "status": "fail",
            "message": "Simulated failure detected.",
            "coverage_percent": 100.0,
            "duration_seconds": 720.0,
            "throughput_mbps": 80.0,
            "issues": [
                "Bad sectors encountered",
            ],
            "details": {},
        }

        def run_full_capacity_failure(
            _: DeviceInfo, *, force: bool, yes: bool, **options: object
        ) -> FullCapacityResult:
            return payload

        with (
            patch("tfqa.core.devices.get_device", get_device),
            patch(
                "tfqa.tests.capacity.full.run_full_capacity", run_full_capacity_failure
            ),
        ):
            result = self.runner.invoke(
                app,
                [
                    "full-capacity-test",
                    "--device",
                    "/dev/sdc",
                ],
            )
        self.assertEqual(result.exit_code, 0)
        output = result.stdout
        self.assertIn("Full capacity test FAIL", output)
        self.assertIn("Simulated failure detected.", output)
        self.assertIn("Bad sectors encountered", output)

    def test_bad_options_are_rejected_before_the_dry_run_plan(self):
        # A dry run must not advertise a plan the real invocation would reject,
        # and a negative limit must not surface as an impossible span_bytes.
        device = make_device("/dev/sdb")

        def get_device(_: str) -> DeviceInfo:
            return device

        for flag, value in (("--limit-bytes", "0"), ("--block-size", "8")):
            with self.subTest(flag=flag, value=value):
                with (
                    patch("tfqa.core.devices.get_device", get_device),
                    patch("tfqa.tests.capacity.full.run_full_capacity") as run_full,
                ):
                    result = self.runner.invoke(
                        app,
                        [
                            "--dry-run",
                            "full-capacity-test",
                            "--device",
                            "/dev/sdb",
                            flag,
                            value,
                            "--output",
                            "json",
                        ],
                    )
                self.assertEqual(result.exit_code, 2, msg=result.stdout)
                resp = CLIResponse.model_validate_json(result.stdout)
                self.assertEqual(resp.error_code, "INVALID_ARGUMENT")
                run_full.assert_not_called()

    def test_full_capacity_requires_safety_override(self):
        device = make_device("/dev/sda")

        def get_device(_: str) -> DeviceInfo:
            return device

        unsafe_error = DeviceUnsafeError(
            device.path,
            "Device unsafe",
            {"is_system_disk": True, "mountpoints": []},
        )

        with (
            patch("tfqa.core.devices.get_device", get_device),
            patch(
                "tfqa.core.safety.assert_safe_for_destructive",
                side_effect=unsafe_error,
            ),
            patch("tfqa.tests.capacity.full.run_full_capacity") as mock_run_full,
        ):
            result = self.runner.invoke(
                app,
                [
                    "full-capacity-test",
                    "--device",
                    "/dev/sda",
                    "--output",
                    "json",
                ],
            )

        self.assertEqual(result.exit_code, 3)
        resp = CLIResponse.model_validate_json(result.stdout)
        self.assertEqual(resp.status, "error")
        self.assertEqual(resp.error_code, "DEVICE_UNSAFE")
        self.assertTrue(resp.data["details"]["is_system_disk"])
        mock_run_full.assert_not_called()
