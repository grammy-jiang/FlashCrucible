import unittest
from unittest.mock import patch

from typer.testing import CliRunner

from tfqa.cli.main import app
from tfqa.core.models import CLIResponse, DeviceInfo
from tfqa.tests.performance.basic import PerformanceResult
from tfqa.tests.performance.random import RandomPerformanceResult


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


class PerformanceCLI(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()

    def test_performance_json(self) -> None:
        device = make_device("/dev/sdb")

        def get_device(_: str) -> DeviceInfo:
            return device

        payload: PerformanceResult = {
            "status": "ok",
            "metrics": {
                "sequential_read_mbps": 250.0,
                "sequential_write_mbps": 210.0,
                "io_depth": 32,
                "duration_seconds": 30.0,
                "platform": "usb",
            },
            "device": {"path": device.path},
            "details": {"sampled_at": device.path, "duration_seconds": 30.0},
        }

        def run_seq_performance_stub(
            _: DeviceInfo, *, duration_seconds: float = 30.0
        ) -> PerformanceResult:
            self.assertAlmostEqual(duration_seconds, 30.0, delta=1e-6)
            return payload

        with (
            patch("tfqa.core.devices.get_device", get_device),
            patch(
                "tfqa.tests.performance.basic.run_seq_performance",
                run_seq_performance_stub,
            ),
        ):
            result = self.runner.invoke(
                app,
                [
                    "performance",
                    "--device",
                    "/dev/sdb",
                    "--duration",
                    "30",
                    "--output",
                    "json",
                ],
            )

        self.assertEqual(result.exit_code, 0)
        resp = CLIResponse.model_validate_json(result.stdout)
        read_val = float(resp.data["metrics"]["sequential_read_mbps"])
        self.assertAlmostEqual(read_val, 250.0, delta=1e-6)
        self.assertEqual(resp.status, "ok")

    def test_performance_runtime_error(self) -> None:
        device = make_device("/dev/sdb")

        def get_device(_: str) -> DeviceInfo:
            return device

        with (
            patch("tfqa.core.devices.get_device", get_device),
            patch(
                "tfqa.tests.performance.basic.run_seq_performance",
                side_effect=RuntimeError("boom"),
            ),
        ):
            result = self.runner.invoke(
                app,
                [
                    "performance",
                    "--device",
                    "/dev/sdb",
                    "--duration",
                    "30",
                    "--output",
                    "json",
                ],
            )

        self.assertNotEqual(result.exit_code, 0)
        resp = CLIResponse.model_validate_json(result.stdout)
        self.assertEqual(resp.status, "error")
        self.assertEqual(resp.error_code, "INTERNAL_ERROR")
        self.assertIn("boom", resp.message)

    def test_performance_random_mode(self) -> None:
        device = make_device("/dev/sdb")

        def get_device(_: str) -> DeviceInfo:
            return device

        payload: RandomPerformanceResult = {
            "status": "ok",
            "metrics": {
                "random_read_mbps": 85.0,
                "random_write_mbps": 70.0,
                "io_depth": 64,
                "duration_seconds": 30.0,
                "block_size": "8k",
                "rw_mix": "randrw",
            },
            "device": {"path": device.path},
            "details": {"sampling": "random"},
        }

        def run_random_performance_stub(
            _: DeviceInfo,
            *,
            duration_seconds: float = 30.0,
            block_size: str = "4k",
            io_depth: int = 32,
            rw_mix: str = "randrw",
            random_read_percentage: int = 50,
        ) -> RandomPerformanceResult:
            self.assertAlmostEqual(duration_seconds, 30.0, delta=1e-6)
            self.assertEqual(block_size, "8k")
            self.assertEqual(io_depth, 64)
            self.assertEqual(rw_mix, "randrw")
            self.assertEqual(random_read_percentage, 70)
            return payload

        with (
            patch("tfqa.core.devices.get_device", get_device),
            patch(
                "tfqa.tests.performance.random.run_random_performance",
                run_random_performance_stub,
            ),
        ):
            result = self.runner.invoke(
                app,
                [
                    "performance",
                    "--device",
                    "/dev/sdb",
                    "--mode",
                    "random",
                    "--random-bs",
                    "8k",
                    "--random-iodepth",
                    "64",
                    "--random-read-percentage",
                    "70",
                    "--output",
                    "json",
                ],
            )

        self.assertEqual(result.exit_code, 0)
        resp = CLIResponse.model_validate_json(result.stdout)
        self.assertEqual(resp.status, "ok")
        self.assertEqual(resp.data["metrics"]["random_read_mbps"], 85.0)
