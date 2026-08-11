from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest import TestCase
from unittest.mock import patch

from typer.testing import CliRunner

from tfqa.cli.main import app
from tfqa.core.models import (
    CLIResponse,
    DeviceInfo,
    EnduranceConfig,
    TestStatus,
    TestResult as ResultModel,
)
from tfqa.orchestration.profile import EnduranceProfile


def _make_device(path: str, removable: bool = True) -> DeviceInfo:
    return DeviceInfo(
        path=path,
        name=Path(path).name,
        model="TestModel",
        vendor="Vendor",
        serial="SN",
        size_bytes=128 * 1024**3,
        is_removable=removable,
        is_system_disk=False,
        mountpoints=[],
        transport="usb",
    )


def _build_result(status: TestStatus, errors: int = 0) -> ResultModel:
    now = datetime.now(timezone.utc)
    return ResultModel(
        name="endurance.simple",
        status=status,
        started_at=now,
        finished_at=now,
        duration_seconds=5.0,
        metrics={
            "total_bytes_written": 1024,
            "average_throughput_mbps": 150.0,
            "total_errors": errors,
            "duration_seconds": 5.0,
        },
        details={
            "duration_seconds": 5.0,
            "pass_count": 1,
            "write_pattern": "sequential",
            "force": False,
            "pass_history": [],
        },
        warnings=["Warnings"] if errors else [],
        logs_path=Path("/tmp/run-endurance.jsonl"),
    )


class EnduranceCLITest(TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()

    def test_endurance_json_success(self) -> None:
        device = _make_device("/dev/sdx")
        profile_obj = EnduranceProfile(
            name="cli-profile",
            description="CLI profile",
            duration_seconds=2.0,
            pass_count=2,
            force=False,
            write_pattern="sequential",
        )
        result = _build_result("ok")

        with (
            patch("tfqa.core.devices.get_device", return_value=device),
            patch("tfqa.orchestration.profile.load_profile", return_value=profile_obj),
            patch(
                "tfqa.tests.endurance.simple.run_simple_endurance", return_value=result
            ),
        ):
            invocation = self.runner.invoke(
                app,
                [
                    "endurance",
                    "--device",
                    "/dev/sdx",
                    "--profile",
                    "cli-profile",
                    "--output",
                    "json",
                ],
            )

        self.assertEqual(invocation.exit_code, 0)
        response = CLIResponse.model_validate_json(invocation.stdout)
        self.assertEqual(response.status, "ok")
        self.assertEqual(response.data["profile"], "cli-profile")
        self.assertEqual(str(response.log_path), str(result.logs_path))

    def test_human_output_names_the_passes_and_the_stop_reason(self) -> None:
        # One summary line hides whether the card slowed down or started
        # failing partway through, which is the whole result.
        device = _make_device("/dev/sdz")
        result = _build_result("ok", errors=0)
        result.details = {
            "summary": "Ran 2 of 2 pass(es) over 100 bytes; stopped because "
            "pass count reached.",
            "passes": [
                {
                    "index": 0,
                    "bytes_written": 100,
                    "write_throughput_mbps": 9.5,
                    "mismatches": 0,
                },
                {
                    "index": 1,
                    "bytes_written": 100,
                    "write_throughput_mbps": 4.1,
                    "mismatches": 2,
                },
            ],
        }
        result.warnings = ["No wear data: nothing answered"]

        with (
            patch("tfqa.core.devices.get_device", return_value=device),
            patch(
                "tfqa.tests.endurance.simple.run_simple_endurance",
                return_value=result,
            ),
        ):
            invocation = self.runner.invoke(
                app, ["endurance", "--device", device.path, "--force", "--yes"]
            )

        self.assertEqual(invocation.exit_code, 0, invocation.stdout)
        self.assertIn("stopped because pass count reached", invocation.stdout)
        self.assertIn("9.5 MB/s", invocation.stdout)
        self.assertIn("4.1 MB/s", invocation.stdout)
        self.assertIn("2 mismatch(es)", invocation.stdout)
        self.assertIn("Warnings:", invocation.stdout)

    def test_the_output_no_longer_calls_it_a_simulation(self) -> None:
        # It writes to the card now. Calling a destructive run a simulation is
        # the kind of wrong that gets a card wiped by someone being careful.
        device = _make_device("/dev/sdz")
        result = _build_result("ok", errors=0)
        result.details = {"summary": "Ran 1 pass.", "passes": []}
        with (
            patch("tfqa.core.devices.get_device", return_value=device),
            patch(
                "tfqa.tests.endurance.simple.run_simple_endurance",
                return_value=result,
            ),
        ):
            invocation = self.runner.invoke(
                app, ["endurance", "--device", device.path, "--force", "--yes"]
            )

        self.assertNotIn("simulation", invocation.stdout.lower())

    def test_endurance_cli_overrides_pass_config(self) -> None:
        device = _make_device("/dev/sdy", removable=False)
        profile_obj = EnduranceProfile(
            name="base",
            description="Base",
            duration_seconds=10.0,
            pass_count=5,
            force=False,
            write_pattern="sequential",
        )
        result = _build_result("warning", errors=2)
        captured: dict[str, EnduranceConfig] = {}

        def fake_run(
            ctx: Any, config: EnduranceConfig, progress: Any = None
        ) -> ResultModel:
            captured["config"] = config
            return result

        with (
            patch("tfqa.core.devices.get_device", return_value=device),
            patch("tfqa.orchestration.profile.load_profile", return_value=profile_obj),
            patch("tfqa.tests.endurance.simple.run_simple_endurance", fake_run),
        ):
            invocation = self.runner.invoke(
                app,
                [
                    "endurance",
                    "--device",
                    "/dev/sdy",
                    "--duration",
                    "3",
                    "--passes",
                    "2",
                    "--force",
                    "--yes",
                    "--output",
                    "json",
                ],
            )

        self.assertEqual(invocation.exit_code, 0, invocation.stdout)
        response = CLIResponse.model_validate_json(invocation.stdout)
        self.assertEqual(response.status, "fail")
        config = captured["config"]
        self.assertIsInstance(config, EnduranceConfig)
        self.assertEqual(config.duration_seconds, 3.0)
        self.assertEqual(config.pass_count, 2)
        self.assertTrue(config.force)
