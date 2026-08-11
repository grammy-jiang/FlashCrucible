"""Regression tests for the destructive-operation safety guard.

`tfqa.core.safety.assert_safe_for_destructive` existed but was wired into
almost nothing, so commands that write raw blocks happily operated on a
mounted device or a system disk. These tests drive the real CLI against
genuinely unsafe DeviceInfo values rather than patching the guard itself, so
they fail if a command stops calling it.
"""

from __future__ import annotations

from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from typer.testing import CliRunner

from tfqa.cli.main import app
from tfqa.core.models import CLIResponse, DeviceInfo
from tfqa.orchestration.pipeline import plan_is_destructive
from tfqa.orchestration.profile import EnduranceProfile


MOUNTPOINT = {"mountpoint": "/run/media/user/boot", "fstype": "vfat"}


def make_device(
    path: str = "/dev/sdb",
    *,
    mountpoints: list[dict[str, str]] | None = None,
    is_system_disk: bool = False,
) -> DeviceInfo:
    return DeviceInfo(
        path=path,
        name=Path(path).name,
        model="Model",
        vendor="Vendor",
        serial="SN",
        size_bytes=1024,
        is_removable=True,
        is_system_disk=is_system_disk,
        mountpoints=mountpoints or [],
        transport="usb",
    )


MOUNTED = make_device(mountpoints=[MOUNTPOINT])
SYSTEM_DISK = make_device("/dev/sda", is_system_disk=True)
SAFE = make_device()

PROFILE = EnduranceProfile(
    name="default",
    description="Test profile",
    duration_seconds=1.0,
    pass_count=1,
    force=False,
    write_pattern="sequential",
)


def probe_command_stub(*_: object, **__: object) -> list[str]:
    # Keeps the f3probe lookup off the host PATH.
    return ["f3probe", "/dev/sdb"]


class SafetyGuardTestCase(TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()

    def assert_refused(self, result: object, *, expect_mounted: bool = True) -> None:
        assert hasattr(result, "exit_code") and hasattr(result, "stdout")
        self.assertEqual(result.exit_code, 3)  # type: ignore[attr-defined]
        resp = CLIResponse.model_validate_json(result.stdout)  # type: ignore[attr-defined]
        self.assertEqual(resp.status, "error")
        self.assertEqual(resp.error_code, "DEVICE_UNSAFE")
        if expect_mounted:
            self.assertEqual(resp.data["details"]["mountpoints"], [MOUNTPOINT])


class QuickTestGuard(SafetyGuardTestCase):
    def _invoke(self, device: DeviceInfo, *extra: str):
        with (
            patch("tfqa.core.devices.get_device", return_value=device),
            patch(
                "tfqa.tests.capacity.quick.describe_probe_command", probe_command_stub
            ),
            patch("tfqa.tests.capacity.quick.run_quick_capacity") as run_quick,
        ):
            result = self.runner.invoke(
                app,
                [*extra, "quick-test", "--device", device.path, "--output", "json"],
            )
        return result, run_quick

    def test_refuses_mounted_device(self):
        result, run_quick = self._invoke(MOUNTED)
        self.assert_refused(result)
        run_quick.assert_not_called()

    def test_refuses_system_disk(self):
        result, run_quick = self._invoke(SYSTEM_DISK)
        self.assert_refused(result, expect_mounted=False)
        resp = CLIResponse.model_validate_json(result.stdout)
        self.assertTrue(resp.data["details"]["is_system_disk"])
        run_quick.assert_not_called()

    def test_force_without_yes_is_still_refused(self):
        # A stray --force left in a script must not be enough on its own.
        with (
            patch("tfqa.core.devices.get_device", return_value=MOUNTED),
            patch(
                "tfqa.tests.capacity.quick.describe_probe_command", probe_command_stub
            ),
            patch("tfqa.tests.capacity.quick.run_quick_capacity") as run_quick,
        ):
            result = self.runner.invoke(
                app,
                [
                    "quick-test",
                    "--device",
                    MOUNTED.path,
                    "--force",
                    "--output",
                    "json",
                ],
            )
        self.assertEqual(result.exit_code, 3)
        run_quick.assert_not_called()

    def test_force_and_yes_overrides(self):
        with (
            patch("tfqa.core.devices.get_device", return_value=MOUNTED),
            patch(
                "tfqa.tests.capacity.quick.describe_probe_command", probe_command_stub
            ),
            patch(
                "tfqa.tests.capacity.quick.run_quick_capacity",
                return_value={"fake_detected": False, "coverage_percent": 90.0},
            ) as run_quick,
        ):
            result = self.runner.invoke(
                app,
                [
                    "--yes",
                    "quick-test",
                    "--device",
                    MOUNTED.path,
                    "--force",
                    "--output",
                    "json",
                ],
            )
        self.assertEqual(result.exit_code, 0)
        run_quick.assert_called_once()

    def test_safe_device_is_untouched(self):
        with (
            patch("tfqa.core.devices.get_device", return_value=SAFE),
            patch(
                "tfqa.tests.capacity.quick.describe_probe_command", probe_command_stub
            ),
            patch(
                "tfqa.tests.capacity.quick.run_quick_capacity",
                return_value={"fake_detected": False, "coverage_percent": 90.0},
            ) as run_quick,
        ):
            result = self.runner.invoke(
                app,
                ["quick-test", "--device", SAFE.path, "--output", "json"],
            )
        self.assertEqual(result.exit_code, 0)
        run_quick.assert_called_once()


class ImageFlashGuard(SafetyGuardTestCase):
    def test_refuses_mounted_device(self):
        image = Path(__file__)  # any existing file satisfies exists=True
        with (
            patch("tfqa.core.devices.get_device", return_value=MOUNTED),
            patch("tfqa.cli.main.run_image_flash") as run_flash,
        ):
            result = self.runner.invoke(
                app,
                [
                    "image-flash",
                    "--device",
                    MOUNTED.path,
                    "--image-path",
                    str(image),
                    "--output",
                    "json",
                ],
            )
        self.assert_refused(result)
        run_flash.assert_not_called()


class PerformanceGuard(SafetyGuardTestCase):
    def test_refuses_mounted_device(self):
        with (
            patch("tfqa.core.devices.get_device", return_value=MOUNTED),
            patch("tfqa.tests.performance.basic.run_seq_performance") as run_perf,
        ):
            result = self.runner.invoke(
                app,
                ["performance", "--device", MOUNTED.path, "--output", "json"],
            )
        self.assert_refused(result)
        run_perf.assert_not_called()


class EnduranceGuard(SafetyGuardTestCase):
    """Every pass overwrites the span, so it is guarded like any other writer.

    It was exempt while the engine refused to do device I/O -- guarding it then
    would have answered DEVICE_UNSAFE on a mounted card and hidden the fact
    that it was simply not implemented. The exemption expired with the stub.
    """

    def _invoke(self, *extra: str):
        with (
            patch("tfqa.core.devices.get_device", return_value=MOUNTED),
            patch("tfqa.orchestration.profile.load_profile", return_value=PROFILE),
        ):
            return self.runner.invoke(
                app,
                ["endurance", "--device", MOUNTED.path, "--output", "json", *extra],
            )

    def test_a_mounted_device_is_refused(self):
        resp = CLIResponse.model_validate_json(self._invoke().stdout)
        self.assertEqual(resp.error_code, "DEVICE_UNSAFE")

    def test_force_without_yes_is_still_refused(self):
        # A stray --force left in a script must not on its own arm a run that
        # overwrites the card several times over.
        resp = CLIResponse.model_validate_json(self._invoke("--force").stdout)
        self.assertEqual(resp.error_code, "DEVICE_UNSAFE")

    def test_force_and_yes_together_clear_the_guard(self):
        result = self._invoke("--force", "--yes")
        # It gets past the guard; what happens next is the engine's business,
        # and /dev/... does not exist here.
        resp = CLIResponse.model_validate_json(result.stdout)
        self.assertNotEqual(resp.error_code, "DEVICE_UNSAFE")


class FilesystemCheckGuard(SafetyGuardTestCase):
    def _invoke(self, *extra: str):
        with (
            patch("tfqa.core.devices.get_device", return_value=MOUNTED),
            patch("tfqa.cli.main.run_fsck") as run_fsck_mock,
        ):
            result = self.runner.invoke(
                app,
                [
                    "filesystem-check",
                    "--device",
                    MOUNTED.path,
                    *extra,
                    "--output",
                    "json",
                ],
            )
        return result, run_fsck_mock

    def test_repair_mode_refuses_mounted_device(self):
        # This call site used to pass force=True, yes=True hardcoded, which
        # made the guard permit everything.
        result, run_fsck_mock = self._invoke("--force", "--no-read-only")
        self.assert_refused(result)
        run_fsck_mock.assert_not_called()

    def test_force_with_default_read_only_refuses_mounted_device(self):
        # `--force` turns read-only off inside run_fsck, so guarding on the raw
        # --read-only flag let this exact invocation run a repair-capable fsck
        # on a mounted device unchecked.
        result, run_fsck_mock = self._invoke("--force")
        self.assert_refused(result)
        run_fsck_mock.assert_not_called()

    def test_read_only_check_allowed_on_mounted_device(self):
        with (
            patch("tfqa.core.devices.get_device", return_value=MOUNTED),
            patch("tfqa.cli.main.run_fsck") as run_fsck_mock,
        ):
            run_fsck_mock.return_value.status = "ok"
            run_fsck_mock.return_value.returncode = 0
            run_fsck_mock.return_value.clean = True
            run_fsck_mock.return_value.errors_fixed = False
            run_fsck_mock.return_value.needs_reboot = False
            run_fsck_mock.return_value.duration_seconds = 1.0
            run_fsck_mock.return_value.model_dump.return_value = {}
            result = self.runner.invoke(
                app,
                ["filesystem-check", "--device", MOUNTED.path, "--output", "json"],
            )
        self.assertEqual(result.exit_code, 0)
        run_fsck_mock.assert_called_once()
        self.assertTrue(run_fsck_mock.call_args.kwargs["read_only"])


class SurfaceScanGuard(SafetyGuardTestCase):
    def test_readonly_scan_allowed_on_mounted_device(self):
        with (
            patch("tfqa.core.devices.get_device", return_value=MOUNTED),
            patch(
                "tfqa.tests.surface.scan.run_surface_scan",
                return_value={"metrics": {}, "details": {}},
            ) as run_scan,
        ):
            result = self.runner.invoke(
                app,
                [
                    "surface-scan",
                    "--device",
                    MOUNTED.path,
                    "--mode",
                    "readonly",
                    "--output",
                    "json",
                ],
            )
        self.assertEqual(result.exit_code, 0)
        run_scan.assert_called_once()

    def test_destructive_scan_refused_on_mounted_device(self):
        with (
            patch("tfqa.core.devices.get_device", return_value=MOUNTED),
            patch("tfqa.tests.surface.scan.run_surface_scan") as run_scan,
        ):
            result = self.runner.invoke(
                app,
                [
                    "surface-scan",
                    "--device",
                    MOUNTED.path,
                    "--mode",
                    "destructive",
                    "--force",
                    "--output",
                    "json",
                ],
            )
        self.assert_refused(result)
        run_scan.assert_not_called()


class PipelineGuard(SafetyGuardTestCase):
    def test_read_only_plan_allowed_on_mounted_device(self):
        with (
            patch("tfqa.core.devices.get_device", return_value=MOUNTED),
            patch("tfqa.orchestration.profile.load_profile", return_value=PROFILE),
            patch(
                "tfqa.orchestration.pipeline.run_pipeline", return_value=[]
            ) as run_pipeline,
        ):
            result = self.runner.invoke(
                app,
                [
                    "pipeline",
                    "--device",
                    MOUNTED.path,
                    "--stages",
                    "detect,health,summary",
                    "--output",
                    "json",
                ],
            )
        self.assertEqual(result.exit_code, 0)
        run_pipeline.assert_called_once()

    def test_surface_scan_plan_allowed_on_mounted_device(self):
        # The pipeline surface stage is readonly; refusing it here would break
        # the documented "read-only plans stay usable" behaviour.
        with (
            patch("tfqa.core.devices.get_device", return_value=MOUNTED),
            patch("tfqa.orchestration.profile.load_profile", return_value=PROFILE),
            patch(
                "tfqa.orchestration.pipeline.run_pipeline", return_value=[]
            ) as run_pipeline,
        ):
            result = self.runner.invoke(
                app,
                [
                    "pipeline",
                    "--device",
                    MOUNTED.path,
                    "--stages",
                    "detect,surface-scan,filesystem-check",
                    "--output",
                    "json",
                ],
            )
        self.assertEqual(result.exit_code, 0)
        run_pipeline.assert_called_once()

    def test_profile_force_is_honoured_as_override_source(self):
        # A profile may supply force; the pipeline must honour it. --yes is
        # still required, so the profile alone cannot arm anything. Uses
        # quick-test because endurance no longer writes.
        forced_profile = EnduranceProfile(
            name="lab-heavy",
            description="Forced profile",
            duration_seconds=1.0,
            pass_count=1,
            force=True,
            write_pattern="sequential",
        )
        with (
            patch("tfqa.core.devices.get_device", return_value=MOUNTED),
            patch(
                "tfqa.orchestration.profile.load_profile", return_value=forced_profile
            ),
            patch(
                "tfqa.orchestration.pipeline.run_pipeline", return_value=[]
            ) as run_pipeline,
        ):
            refused = self.runner.invoke(
                app,
                [
                    "pipeline",
                    "--device",
                    MOUNTED.path,
                    "--stages",
                    "detect,quick-test",
                    "--output",
                    "json",
                ],
            )
            allowed = self.runner.invoke(
                app,
                [
                    "--yes",
                    "pipeline",
                    "--device",
                    MOUNTED.path,
                    "--stages",
                    "detect,quick-test",
                    "--output",
                    "json",
                ],
            )
        self.assertEqual(refused.exit_code, 3)
        self.assertEqual(allowed.exit_code, 0)
        run_pipeline.assert_called_once()

    def test_destructive_plan_refused_on_mounted_device(self):
        with (
            patch("tfqa.core.devices.get_device", return_value=MOUNTED),
            patch("tfqa.orchestration.profile.load_profile", return_value=PROFILE),
            patch("tfqa.orchestration.pipeline.run_pipeline") as run_pipeline,
        ):
            result = self.runner.invoke(
                app,
                [
                    "pipeline",
                    "--device",
                    MOUNTED.path,
                    "--stages",
                    "detect,quick-test",
                    "--output",
                    "json",
                ],
            )
        self.assert_refused(result)
        run_pipeline.assert_not_called()


class PlanIsDestructive(TestCase):
    def test_read_only_stages(self):
        self.assertFalse(plan_is_destructive(["detect", "health", "summary"]))

    def test_writing_stages(self):
        for stage in (
            "quick-test",
            "full-capacity-test",
            "performance",
            "image-flash",
        ):
            with self.subTest(stage=stage):
                self.assertTrue(plan_is_destructive(["detect", stage]))

    def test_pipeline_only_stages_are_read_only(self):
        # The pipeline runs surface-scan in readonly mode and fsck with
        # read_only=True, so neither writes. Only the standalone commands can,
        # and those guard themselves.
        # `endurance` joins these while it performs no device I/O.
        for stage in ("surface-scan", "filesystem-check", "endurance"):
            with self.subTest(stage=stage):
                self.assertFalse(plan_is_destructive(["detect", stage]))

    def test_accepts_prefixed_names(self):
        self.assertTrue(plan_is_destructive(["pipeline.quick-test"]))
        self.assertFalse(plan_is_destructive(["pipeline.health"]))

    def test_smallfiles_is_not_treated_as_raw_write(self):
        # It writes through a mounted filesystem, so it must stay runnable on a
        # mounted device.
        self.assertFalse(plan_is_destructive(["workload-smallfiles"]))

    def test_empty_plan(self):
        self.assertFalse(plan_is_destructive([]))
