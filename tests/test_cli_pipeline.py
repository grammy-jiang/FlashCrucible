"""Tests covering the tfqa pipeline CLI command."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any
from unittest import TestCase
from unittest.mock import patch

from typer.testing import CliRunner

from tfqa.cli.main import app
from tfqa.core.models import CLIResponse, DeviceInfo, RunContext, TestResult, TestStatus
from tfqa.orchestration import pipeline as pipeline_mod
from tfqa.orchestration import workflows as workflows_mod
from tfqa.orchestration.profile import EnduranceProfile


def _make_device(path: str) -> DeviceInfo:
    return DeviceInfo(
        path=path,
        name=Path(path).name,
        model="TestDevice",
        vendor="Vendor",
        serial="SN",
        size_bytes=64 * 1024**3,
        is_removable=True,
        is_system_disk=False,
        mountpoints=[],
        transport="usb",
    )


def _make_result(
    name: str, status: TestStatus, metrics: dict[str, float] | None = None
) -> TestResult:
    now = datetime.now(timezone.utc)
    return TestResult(
        name=name,
        status=status,
        started_at=now,
        finished_at=now,
        duration_seconds=1.0,
        metrics=metrics or {"throughput": 1.0},
        details={"note": "pipeline stage"},
    )


def _dummy_action(ctx: RunContext) -> dict[str, Any]:
    return {"status": "ok"}


class PipelineCLITest(TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()

    def test_pipeline_json_success(self) -> None:
        device = _make_device("/dev/sdb")
        profile = EnduranceProfile(
            name="cli-profile",
            description="CLI profile",
            duration_seconds=10.0,
            pass_count=1,
            force=False,
            write_pattern="sequential",
        )
        stage_plan = pipeline_mod.DEFAULT_STAGE_ORDER
        stage_results = [
            _make_result(f"pipeline.{stage}", "ok") for stage in stage_plan
        ]
        history_path = Path("/tmp/history.jsonl")

        with (
            patch("tfqa.core.devices.get_device", return_value=device),
            patch("tfqa.orchestration.profile.load_profile", return_value=profile),
            patch(
                "tfqa.orchestration.pipeline.run_pipeline", return_value=stage_results
            ),
            patch(
                "tfqa.reporting.history.record_run", return_value=history_path
            ) as history_stub,
        ):
            invocation = self.runner.invoke(
                app,
                [
                    "pipeline",
                    "--device",
                    "/dev/sdb",
                    "--profile",
                    "cli-profile",
                    "--output",
                    "json",
                ],
            )

        self.assertEqual(invocation.exit_code, 0)
        response = CLIResponse.model_validate_json(invocation.stdout)
        self.assertEqual(response.status, "ok")
        self.assertEqual(response.command, "pipeline")
        self.assertEqual(response.data["profile"], "cli-profile")
        self.assertEqual(response.data["history_path"], str(history_path))
        self.assertEqual(len(response.data["stages"]), len(stage_plan))
        history_stub.assert_called_once()
        self.assertEqual(response.data["stage_plan"], stage_plan)
        self.assertIsNone(response.data["requested_stage_plan"])
        self.assertEqual(
            history_stub.call_args[1]["metadata"]["stage_plan"], stage_plan
        )

    def test_pipeline_cli_status_failure(self) -> None:
        device = _make_device("/dev/sdc")
        profile = EnduranceProfile(
            name="cli-fail",
            description="With failure",
            duration_seconds=5.0,
            pass_count=1,
            force=False,
            write_pattern="sequential",
        )
        failed_result = _make_result("pipeline.detect", "failed")
        with (
            patch("tfqa.core.devices.get_device", return_value=device),
            patch("tfqa.orchestration.profile.load_profile", return_value=profile),
            patch(
                "tfqa.orchestration.pipeline.run_pipeline", return_value=[failed_result]
            ),
            patch(
                "tfqa.reporting.history.record_run", return_value=Path("/tmp/h.jsonl")
            ) as history_stub,
        ):
            invocation = self.runner.invoke(
                app,
                [
                    "pipeline",
                    "--device",
                    "/dev/sdc",
                    "--output",
                    "json",
                ],
            )

        response = CLIResponse.model_validate_json(invocation.stdout)
        self.assertEqual(response.status, "fail")
        history_stub.assert_called()
        self.assertEqual(history_stub.call_args[1]["status"], "failed")

    def test_pipeline_allows_custom_stage_plan(self) -> None:
        device = _make_device("/dev/sdd")
        profile = EnduranceProfile(
            name="cli-plan",
            description="Plan",
            duration_seconds=5.0,
            pass_count=1,
            force=False,
            write_pattern="sequential",
        )
        requested_plan = ["quick-test", "detect"]
        stage_results = [
            _make_result(f"pipeline.{stage}", "ok") for stage in requested_plan
        ]
        history_path = Path("/tmp/h-plan.jsonl")

        with (
            patch("tfqa.core.devices.get_device", return_value=device),
            patch("tfqa.orchestration.profile.load_profile", return_value=profile),
            patch(
                "tfqa.orchestration.pipeline.build_pipeline",
                return_value=[
                    pipeline_mod.PipelineStage(
                        stage,
                        _dummy_action,
                    )
                    for stage in requested_plan
                ],
            ) as build_stub,
            patch(
                "tfqa.orchestration.pipeline.run_pipeline", return_value=stage_results
            ),
            patch(
                "tfqa.reporting.history.record_run",
                return_value=history_path,
            ) as history_stub,
        ):
            invocation = self.runner.invoke(
                app,
                [
                    "pipeline",
                    "--device",
                    "/dev/sdd",
                    "--stages",
                    "quick-test,detect",
                    "--output",
                    "json",
                ],
            )

        response = CLIResponse.model_validate_json(invocation.stdout)
        self.assertEqual(response.status, "ok")
        build_stub.assert_called_once()
        self.assertEqual(build_stub.call_args[0][1], requested_plan)
        self.assertEqual(response.data["stage_plan"], requested_plan)
        self.assertEqual(response.data["requested_stage_plan"], requested_plan)
        self.assertEqual(
            history_stub.call_args[1]["metadata"]["stage_plan"],
            requested_plan,
        )

    def test_pipeline_image_stage_metadata(self) -> None:
        device = _make_device("/dev/sde")
        profile = EnduranceProfile(
            name="cli-image",
            description="Image stage",
            duration_seconds=5.0,
            pass_count=1,
            force=False,
            write_pattern="sequential",
        )
        requested_plan = ["detect", "image-flash"]
        stage_results = [
            _make_result("pipeline.detect", "ok"),
            _make_result("pipeline.image-flash", "ok"),
        ]
        history_path = Path("/tmp/h-image.jsonl")
        image_file = NamedTemporaryFile(delete=False)
        image_file.close()
        image_path = Path(image_file.name)

        def build_stub(
            profile_settings: EnduranceProfile,
            requested_stages: list[str],
            image_config: pipeline_mod.ImageFlashConfig | None = None,
        ) -> list[pipeline_mod.PipelineStage]:
            self.assertEqual(requested_stages, requested_plan)
            self.assertIsInstance(
                image_config,
                pipeline_mod.ImageFlashConfig,
                "image_config should be provided",
            )
            # Type guard for static analysis
            if image_config:
                config = image_config
                self.assertEqual(config.image_path, str(image_path))
                self.assertEqual(config.block_size, "4M")
                self.assertEqual(config.conv_flags, ("fsync", "noerror"))
                self.assertTrue(config.verify)
                self.assertEqual(config.write_timeout, 600.0)
                self.assertEqual(config.verify_timeout, 300.0)
            return [
                pipeline_mod.PipelineStage(stage, _dummy_action)
                for stage in requested_plan
            ]

        try:
            with (
                patch("tfqa.core.devices.get_device", return_value=device),
                patch("tfqa.orchestration.profile.load_profile", return_value=profile),
                patch(
                    "tfqa.orchestration.pipeline.build_pipeline",
                    side_effect=build_stub,
                ),
                patch(
                    "tfqa.orchestration.pipeline.run_pipeline",
                    return_value=stage_results,
                ),
                patch(
                    "tfqa.reporting.history.record_run",
                    return_value=history_path,
                ) as history_stub,
            ):
                invocation = self.runner.invoke(
                    app,
                    [
                        "pipeline",
                        "--device",
                        "/dev/sde",
                        "--stages",
                        "detect,image-flash",
                        "--image-path",
                        str(image_path),
                        "--image-conv-flags",
                        "fsync,noerror",
                        "--output",
                        "json",
                    ],
                )
            self.assertEqual(invocation.exit_code, 0)
            response = CLIResponse.model_validate_json(invocation.stdout)
            self.assertEqual(response.status, "ok")
            self.assertEqual(response.data["requested_stage_plan"], requested_plan)
            self.assertEqual(
                response.data["image_options"],
                {
                    "image_path": str(image_path),
                    "block_size": "4M",
                    "conv_flags": ["fsync", "noerror"],
                    "verify": True,
                    "write_timeout": 600.0,
                    "verify_timeout": 300.0,
                },
            )
            history_stub.assert_called_once()
            metadata = history_stub.call_args[1]["metadata"]
            self.assertEqual(metadata["image_options"], response.data["image_options"])
        finally:
            image_path.unlink()

    def test_pipeline_combo_defaults(self) -> None:
        device = _make_device("/dev/sdg")
        combo = workflows_mod.WorkloadCombo(
            name="combo-test",
            stages=["detect", "quick-test", "image-flash", "summary"],
            description="Combo",
            profile="combo-profile",
            image_options={
                "block_size": "8M",
                "conv_flags": ["fsync", "noerror"],
                "verify": False,
                "write_timeout": 500.0,
                "verify_timeout": 250.0,
            },
        )
        stage_plan = list(combo.stages)
        stage_results = [
            _make_result(f"pipeline.{stage}", "ok") for stage in stage_plan
        ]
        history_path = Path("/tmp/h-combo.jsonl")
        image_path = Path("/tmp/combo.img")
        image_path.touch()

        def build_stub(
            profile_settings: EnduranceProfile,
            requested_stages: list[str],
            image_config: pipeline_mod.ImageFlashConfig | None = None,
        ) -> list[pipeline_mod.PipelineStage]:
            self.assertEqual(requested_stages, stage_plan)
            self.assertIsNotNone(image_config)
            if image_config:
                self.assertEqual(image_config.block_size, "8M")
                self.assertEqual(image_config.conv_flags, ("fsync", "noerror"))
                self.assertFalse(image_config.verify)
                self.assertEqual(image_config.write_timeout, 500.0)
                self.assertEqual(image_config.verify_timeout, 250.0)
            return [
                pipeline_mod.PipelineStage(stage, _dummy_action) for stage in stage_plan
            ]

        try:
            with (
                patch("tfqa.core.devices.get_device", return_value=device),
                patch(
                    "tfqa.orchestration.profile.load_profile",
                    return_value=EnduranceProfile(
                        name="combo-profile",
                        description="Combo",
                        duration_seconds=5.0,
                        pass_count=1,
                        force=False,
                        write_pattern="sequential",
                    ),
                ),
                patch("tfqa.orchestration.workflows.load_combo", return_value=combo),
                patch(
                    "tfqa.orchestration.pipeline.build_pipeline",
                    side_effect=build_stub,
                ),
                patch(
                    "tfqa.orchestration.pipeline.run_pipeline",
                    return_value=stage_results,
                ),
                patch(
                    "tfqa.reporting.history.record_run",
                    return_value=history_path,
                ) as history_stub,
            ):
                invocation = self.runner.invoke(
                    app,
                    [
                        "pipeline",
                        "--device",
                        "/dev/sdg",
                        "--combo",
                        combo.name,
                        "--image-path",
                        str(image_path),
                        "--output",
                        "json",
                    ],
                )
        finally:
            image_path.unlink(missing_ok=True)

        self.assertEqual(invocation.exit_code, 0)
        response = CLIResponse.model_validate_json(invocation.stdout)
        self.assertEqual(response.data["profile"], combo.profile)
        self.assertEqual(response.data["stage_plan"], stage_plan)
        self.assertEqual(response.data["requested_stage_plan"], stage_plan)
        self.assertEqual(response.data["combo"]["name"], combo.name)
        self.assertEqual(
            response.data["combo"]["description"],
            combo.description,
        )
        self.assertEqual(response.data["image_options"]["block_size"], "8M")
        history_stub.assert_called_once()
        metadata_combo = history_stub.call_args[1]["metadata"]["combo"]
        self.assertEqual(metadata_combo["name"], combo.name)
        self.assertEqual(metadata_combo["profile"], combo.profile)
