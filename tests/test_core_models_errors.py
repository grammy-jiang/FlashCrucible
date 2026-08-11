"""Tests for tfqa.core.models and tfqa.core.errors."""

from __future__ import annotations

from datetime import datetime
import unittest

from tfqa.core.errors import (
    ArgumentError,
    DeviceNotFoundError,
    DeviceUnsafeError,
    InterruptedError,
    PermissionError,
    RuntimeIOError,
    TimeoutError,
    ToolNotFoundError,
    TFQAError,
    get_exit_code,
)
from tfqa.core.models import (
    CLIResponse,
    Capabilities,
    DeviceInfo,
    RunContext,
    TestConfig,
    TestResult,
    ToolCapability,
)


class ModelsTest(unittest.TestCase):
    def test_device_info_creation(self) -> None:
        device = DeviceInfo(
            path="/dev/sdb",
            name="sdb",
            model="SanDisk Ultra",
            vendor="SanDisk",
            serial="12345",
            size_bytes=128_000_000_000,
            is_removable=True,
            is_system_disk=False,
        )
        self.assertEqual(device.path, "/dev/sdb")
        self.assertEqual(device.name, "sdb")
        self.assertTrue(device.is_removable)
        self.assertFalse(device.is_system_disk)
        self.assertEqual(device.mountpoints, [])

    def test_device_info_json_serialization(self) -> None:
        device = DeviceInfo(
            path="/dev/sdb",
            name="sdb",
            size_bytes=128_000_000_000,
            is_removable=True,
            is_system_disk=False,
        )
        json_str = device.model_dump_json()
        self.assertIn("sdb", json_str)
        self.assertIn("128000000000", json_str)

    def test_run_context_creation(self) -> None:
        device = DeviceInfo(
            path="/dev/sdb",
            name="sdb",
            size_bytes=128_000_000_000,
            is_removable=True,
            is_system_disk=False,
        )
        ctx = RunContext(
            run_id="2025-11-18T10-15-30Z_9f3a21",
            started_at=datetime.now(),
            device=device,
            mode="human",
        )
        self.assertEqual(ctx.run_id, "2025-11-18T10-15-30Z_9f3a21")
        self.assertEqual(ctx.device.path, "/dev/sdb")
        self.assertFalse(ctx.destructive)

    def test_test_config_creation(self) -> None:
        config = TestConfig(
            name="capacity.quick",
            destructive=False,
            params={"sample_points": 10},
        )
        self.assertEqual(config.name, "capacity.quick")
        self.assertFalse(config.destructive)
        self.assertEqual(config.params["sample_points"], 10)

    def test_test_result_creation(self) -> None:
        now = datetime.now()
        result = TestResult(
            name="capacity.quick",
            status="ok",
            started_at=now,
            finished_at=now,
            duration_seconds=100.5,
            metrics={"throughput_mbps": 265.3},
            details={"fake_detected": False},
        )
        self.assertEqual(result.status, "ok")
        self.assertGreater(result.metrics["throughput_mbps"], 260)

    def test_tool_capability_creation(self) -> None:
        cap = ToolCapability(
            name="f3probe",
            available=True,
            version="8.0",
            path="/usr/bin/f3probe",
        )
        self.assertTrue(cap.available)
        self.assertEqual(cap.version, "8.0")

    def test_capabilities_creation(self) -> None:
        caps = Capabilities(
            version="0.1.0",
            platform="Linux x86_64",
            external_tools={"f3probe": ToolCapability(name="f3probe", available=True)},
            features={"capacity_quick": "wrapper"},
        )
        self.assertEqual(caps.version, "0.1.0")
        self.assertIn("f3probe", caps.external_tools)

    def test_cli_response_success(self) -> None:
        response = CLIResponse(
            status="ok",
            command="detect",
            message="2 devices detected",
            data={"devices": [{"path": "/dev/sdb"}]},
        )
        self.assertEqual(response.status, "ok")
        self.assertIsNone(response.error_code)

    def test_cli_response_error(self) -> None:
        response = CLIResponse(
            status="error",
            command="quick-test",
            error_code="DEVICE_UNSAFE",
            message="Device is system disk",
        )
        self.assertEqual(response.status, "error")
        self.assertEqual(response.error_code, "DEVICE_UNSAFE")


class ErrorsTest(unittest.TestCase):
    def test_get_exit_code_success(self) -> None:
        self.assertEqual(get_exit_code(None), 0)

    def test_get_exit_code_invalid_argument(self) -> None:
        self.assertEqual(get_exit_code("INVALID_ARGUMENT"), 2)

    def test_get_exit_code_device_errors(self) -> None:
        self.assertEqual(get_exit_code("DEVICE_NOT_FOUND"), 3)
        self.assertEqual(get_exit_code("DEVICE_UNSAFE"), 3)
        self.assertEqual(get_exit_code("NO_ROOT_PERMISSION"), 3)

    def test_get_exit_code_runtime_error(self) -> None:
        self.assertEqual(get_exit_code("RUNTIME_IO_ERROR"), 1)
        self.assertEqual(get_exit_code("TIMEOUT"), 1)

    def test_get_exit_code_interrupted(self) -> None:
        self.assertEqual(get_exit_code("INTERRUPTED"), 130)

    def test_tfqa_error_creation(self) -> None:
        err = TFQAError("Something went wrong", "INTERNAL_ERROR", {"detail": "info"})
        self.assertEqual(err.message, "Something went wrong")
        self.assertEqual(err.error_code, "INTERNAL_ERROR")
        self.assertEqual(err.details["detail"], "info")

    def test_argument_error(self) -> None:
        err = ArgumentError("Bad argument")
        self.assertEqual(err.error_code, "INVALID_ARGUMENT")
        self.assertEqual(get_exit_code(err.error_code), 2)

    def test_device_not_found_error(self) -> None:
        err = DeviceNotFoundError("/dev/fake")
        self.assertEqual(err.error_code, "DEVICE_NOT_FOUND")
        self.assertIn("/dev/fake", err.message)

    def test_device_unsafe_error(self) -> None:
        err = DeviceUnsafeError("/dev/sda", "system disk")
        self.assertEqual(err.error_code, "DEVICE_UNSAFE")
        self.assertIn("system disk", err.message)

    def test_permission_error(self) -> None:
        err = PermissionError("Must run as root")
        self.assertEqual(err.error_code, "NO_ROOT_PERMISSION")

    def test_tool_not_found_error(self) -> None:
        err = ToolNotFoundError("f3probe")
        self.assertEqual(err.error_code, "EXT_TOOL_MISSING")
        self.assertIn("f3probe", err.message)

    def test_runtime_io_error(self) -> None:
        err = RuntimeIOError("I/O failed")
        self.assertEqual(err.error_code, "RUNTIME_IO_ERROR")

    def test_timeout_error(self) -> None:
        err = TimeoutError("Operation timed out", 60.0)
        self.assertEqual(err.error_code, "TIMEOUT")
        self.assertGreater(err.details["timeout_seconds"], 50)

    def test_interrupted_error(self) -> None:
        err = InterruptedError()
        self.assertEqual(err.error_code, "INTERRUPTED")
        self.assertEqual(get_exit_code(err.error_code), 130)
