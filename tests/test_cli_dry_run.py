"""Regression tests for the global --dry-run flag.

`--dry-run` was accepted on the top-level callback and stored in the context,
but nothing ever read it, so `tfqa --dry-run pipeline --device /dev/sdX` ran
for real. These tests assert that every command which writes to a device
honours both the global flag and its own, and that no engine is invoked.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest import TestCase
from unittest.mock import patch

from typer.testing import CliRunner

from tfqa.cli.main import app
from tfqa.core.models import CLIResponse, DeviceInfo
from tfqa.orchestration.profile import EnduranceProfile

IMAGE = str(Path(__file__))  # any existing file satisfies exists=True

MOUNTPOINT = {"mountpoint": "/run/media/user/boot", "fstype": "vfat"}


def make_device(
    path: str = "/dev/sdb", *, mountpoints: list[dict[str, str]] | None = None
) -> DeviceInfo:
    return DeviceInfo(
        path=path,
        name=Path(path).name,
        model="Model",
        vendor="Vendor",
        serial="SN",
        size_bytes=1024,
        is_removable=True,
        is_system_disk=False,
        mountpoints=mountpoints or [],
        transport="usb",
    )


SAFE = make_device()
MOUNTED = make_device(mountpoints=[MOUNTPOINT])

PROFILE = EnduranceProfile(
    name="default",
    description="Test profile",
    duration_seconds=1.0,
    pass_count=1,
    force=False,
    write_pattern="sequential",
)

# command argv (after the device flag) -> the engine that must not be called.
CASES: dict[str, tuple[list[str], str]] = {
    "quick-test": ([], "tfqa.tests.capacity.quick.run_quick_capacity"),
    "performance": ([], "tfqa.tests.performance.basic.run_seq_performance"),
    "surface-scan": (
        ["--mode", "destructive", "--force"],
        "tfqa.tests.surface.scan.run_surface_scan",
    ),
    "filesystem-check": (["--force"], "tfqa.cli.main.run_fsck"),
    "image-flash": (["--image-path", IMAGE], "tfqa.cli.main.run_image_flash"),
    "full-capacity-test": ([], "tfqa.tests.capacity.full.run_full_capacity"),
    "endurance": ([], "tfqa.tests.endurance.simple.run_simple_endurance"),
    "pipeline": (
        ["--stages", "detect,quick-test"],
        "tfqa.orchestration.pipeline.run_pipeline",
    ),
    "workload-smallfiles": (
        [],
        "tfqa.tests.workload.smallfiles.run_small_file_workload",
    ),
}


class DryRunTestCase(TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()

    def invoke(
        self,
        command: str,
        engine: str,
        *,
        globals_: list[str] | None = None,
        extra: list[str] | None = None,
        device: DeviceInfo = SAFE,
    ) -> tuple[Any, Any]:
        argv, _ = CASES[command]
        with (
            patch("tfqa.core.devices.get_device", return_value=device),
            patch("tfqa.orchestration.profile.load_profile", return_value=PROFILE),
            patch(engine) as engine_mock,
        ):
            result = self.runner.invoke(
                app,
                [
                    *(globals_ or []),
                    command,
                    "--device",
                    device.path,
                    *argv,
                    *(extra or []),
                    "--output",
                    "json",
                ],
            )
        return result, engine_mock


class GlobalDryRun(DryRunTestCase):
    def test_global_flag_short_circuits_every_writing_command(self):
        for command, (_, engine) in CASES.items():
            with self.subTest(command=command):
                result, engine_mock = self.invoke(
                    command, engine, globals_=["--dry-run"]
                )
                self.assertEqual(result.exit_code, 0, msg=result.stdout)
                resp = CLIResponse.model_validate_json(result.stdout)
                self.assertEqual(resp.status, "ok")
                self.assertEqual(resp.command, command)
                self.assertIn("plan", resp.data)
                self.assertIsNone(resp.run_id)
                engine_mock.assert_not_called()

    def test_per_command_flag_still_works(self):
        for command, (_, engine) in CASES.items():
            with self.subTest(command=command):
                result, engine_mock = self.invoke(command, engine, extra=["--dry-run"])
                self.assertEqual(result.exit_code, 0, msg=result.stdout)
                resp = CLIResponse.model_validate_json(result.stdout)
                self.assertIn("plan", resp.data)
                engine_mock.assert_not_called()

    def test_without_dry_run_the_engine_runs(self):
        # Guards against the flag defaulting to on.
        with (
            patch("tfqa.core.devices.get_device", return_value=SAFE),
            patch(
                "tfqa.tests.capacity.quick.describe_probe_command",
                return_value=["f3probe", SAFE.path],
            ),
            patch("tfqa.tests.capacity.quick.run_quick_capacity") as run_quick,
        ):
            result = self.runner.invoke(
                app, ["quick-test", "--device", SAFE.path, "--output", "json"]
            )
        self.assertEqual(result.exit_code, 0, msg=result.stdout)
        run_quick.assert_called_once()


class DryRunSafetyPreview(DryRunTestCase):
    def _plan(self, result: Any) -> dict[str, Any]:
        return dict(CLIResponse.model_validate_json(result.stdout).data["plan"])

    def test_reports_refusal_for_a_mounted_device(self):
        result, _ = self.invoke(
            "quick-test",
            CASES["quick-test"][1],
            globals_=["--dry-run"],
            device=MOUNTED,
        )
        safety = self._plan(result)["safety"]
        self.assertFalse(safety["would_run"])
        self.assertEqual(safety["error_code"], "DEVICE_UNSAFE")
        self.assertEqual(safety["details"]["mountpoints"], [MOUNTPOINT])

    def test_reports_clearance_for_a_safe_device(self):
        result, _ = self.invoke(
            "quick-test", CASES["quick-test"][1], globals_=["--dry-run"]
        )
        self.assertTrue(self._plan(result)["safety"]["would_run"])

    def test_force_and_yes_shows_as_clearing(self):
        result, _ = self.invoke(
            "quick-test",
            CASES["quick-test"][1],
            globals_=["--dry-run", "--yes"],
            extra=["--force"],
            device=MOUNTED,
        )
        self.assertTrue(self._plan(result)["safety"]["would_run"])

    def test_full_capacity_honours_its_own_yes_in_the_preview(self):
        # This command has a local --yes as well as the global one.
        result, _ = self.invoke(
            "full-capacity-test",
            CASES["full-capacity-test"][1],
            globals_=["--dry-run"],
            extra=["--force", "--yes"],
            device=MOUNTED,
        )
        self.assertTrue(self._plan(result)["safety"]["would_run"])

    def test_exempt_commands_omit_the_safety_block(self):
        # These never clear the unmounted requirement, so previewing a refusal
        # would be misleading.
        cases = [
            ("workload-smallfiles", []),
            ("surface-scan", ["--mode", "readonly"]),
        ]
        for command, extra in cases:
            with self.subTest(command=command):
                argv, engine = CASES[command]
                with (
                    patch("tfqa.core.devices.get_device", return_value=MOUNTED),
                    patch(engine) as engine_mock,
                ):
                    result = self.runner.invoke(
                        app,
                        [
                            "--dry-run",
                            command,
                            "--device",
                            MOUNTED.path,
                            *extra,
                            "--output",
                            "json",
                        ],
                    )
                self.assertEqual(result.exit_code, 0, msg=result.stdout)
                self.assertNotIn("safety", self._plan(result))
                engine_mock.assert_not_called()

    def test_read_only_pipeline_plan_omits_the_safety_block(self):
        with (
            patch("tfqa.core.devices.get_device", return_value=MOUNTED),
            patch("tfqa.orchestration.profile.load_profile", return_value=PROFILE),
            patch("tfqa.orchestration.pipeline.run_pipeline") as run_pipeline,
        ):
            result = self.runner.invoke(
                app,
                [
                    "--dry-run",
                    "pipeline",
                    "--device",
                    MOUNTED.path,
                    "--stages",
                    "detect,health,summary",
                    "--output",
                    "json",
                ],
            )
        plan = self._plan(result)
        self.assertEqual(result.exit_code, 0, msg=result.stdout)
        self.assertFalse(plan["writes_to_device"])
        self.assertNotIn("safety", plan)
        run_pipeline.assert_not_called()


class DryRunPlanContents(DryRunTestCase):
    def test_pipeline_plan_reports_the_negotiated_stages(self):
        result, _ = self.invoke(
            "pipeline", CASES["pipeline"][1], globals_=["--dry-run"]
        )
        plan = dict(CLIResponse.model_validate_json(result.stdout).data["plan"])
        self.assertEqual(plan["stage_plan"], ["detect", "quick-test"])
        self.assertEqual(plan["requested_stages"], ["detect", "quick-test"])
        self.assertTrue(plan["writes_to_device"])

    def test_image_flash_plan_reports_dd_arguments(self):
        result, _ = self.invoke(
            "image-flash",
            CASES["image-flash"][1],
            globals_=["--dry-run"],
            extra=["--block-size", "1M", "--conv-flags", "fsync,noerror"],
        )
        plan = dict(CLIResponse.model_validate_json(result.stdout).data["plan"])
        self.assertEqual(plan["image_path"], IMAGE)
        self.assertEqual(plan["block_size"], "1M")
        self.assertEqual(plan["conv_flags"], ["fsync", "noerror"])

    def test_filesystem_check_plan_reports_effective_read_only(self):
        # --force turns read-only off inside run_fsck, and the plan must say so.
        result, _ = self.invoke(
            "filesystem-check", CASES["filesystem-check"][1], globals_=["--dry-run"]
        )
        plan = dict(CLIResponse.model_validate_json(result.stdout).data["plan"])
        self.assertFalse(plan["read_only"])

    def test_human_output_prints_the_plan(self):
        with (
            patch("tfqa.core.devices.get_device", return_value=SAFE),
            patch("tfqa.tests.capacity.quick.run_quick_capacity") as run_quick,
        ):
            result = self.runner.invoke(
                app, ["--dry-run", "quick-test", "--device", SAFE.path]
            )
        self.assertEqual(result.exit_code, 0, msg=result.stdout)
        self.assertIn("Dry run: quick-test plan prepared", result.stdout)
        self.assertIn("Plan:", result.stdout)
        run_quick.assert_not_called()
