import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from typer.testing import CliRunner

from tfqa.cli.main import app
from tfqa.core.errors import ArgumentError, get_exit_code
from tfqa.core.models import CLIResponse, ConfigModel, DeviceInfo, TestResult


class WorkloadSmallfilesCLITest(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()
        self.device = DeviceInfo(
            path="/dev/fake",
            name="fake",
            size_bytes=1_000_000,
            is_removable=True,
            is_system_disk=False,
            model="Test",
            vendor="Test",
        )

    def _stub_result(self) -> TestResult:
        now = datetime.now(timezone.utc)
        return TestResult(
            name="workload.smallfiles",
            status="ok",
            started_at=now,
            finished_at=now,
            duration_seconds=0.5,
            metrics={"files_created": 2, "total_bytes_written": 2048},
            details={},
            logs_path=Path("/tmp/workload-smallfiles.jsonl"),
        )

    def _stub_config(self) -> ConfigModel:
        return ConfigModel(log_dir=Path("/tmp/logs"))

    def test_workload_smallfiles_json(self) -> None:
        result = self._stub_result()
        entry_history = Path("/tmp/history.jsonl")
        with (
            patch(
                "tfqa.cli.main.cfg_mod.load_config", return_value=self._stub_config()
            ),
            patch("tfqa.cli.main.devices_mod.get_device", return_value=self.device),
            patch(
                "tfqa.cli.main.workload_smallfiles.run_small_file_workload",
                return_value=result,
            ),
            patch("tfqa.cli.main.history_mod.record_run", return_value=entry_history),
        ):
            cli_result = self.runner.invoke(
                app,
                [
                    "workload-smallfiles",
                    "--device",
                    "/dev/fake",
                    "--file-count",
                    "2",
                    "--file-size",
                    "1024",
                    "--output",
                    "json",
                ],
            )

        self.assertEqual(cli_result.exit_code, 0)
        resp = CLIResponse.model_validate_json(cli_result.stdout)
        self.assertEqual(resp.command, "workload-smallfiles")
        result_payload = resp.data.get("result", {})
        self.assertEqual(result_payload.get("metrics", {}).get("files_created"), 2)
        self.assertEqual(str(resp.log_path), "/tmp/workload-smallfiles.jsonl")

    def test_workload_smallfiles_human_output(self) -> None:
        result = self._stub_result()
        with (
            patch(
                "tfqa.cli.main.cfg_mod.load_config", return_value=self._stub_config()
            ),
            patch("tfqa.cli.main.devices_mod.get_device", return_value=self.device),
            patch(
                "tfqa.cli.main.workload_smallfiles.run_small_file_workload",
                return_value=result,
            ),
            patch(
                "tfqa.cli.main.history_mod.record_run",
                return_value=Path("/tmp/history.jsonl"),
            ),
        ):
            cli_result = self.runner.invoke(
                app,
                [
                    "workload-smallfiles",
                    "--device",
                    "/dev/fake",
                    "--file-count",
                    "2",
                    "--file-size",
                    "1024",
                ],
            )

        self.assertEqual(cli_result.exit_code, 0)
        self.assertIn("Small-file workload completed.", cli_result.stdout)
        self.assertIn("files_created", cli_result.stdout)

    def test_human_output_shows_warnings(self) -> None:
        # The engine records "Some file operations failed during the workload."
        # in `warnings`, and the human renderer never printed it -- so a person
        # saw only the metrics and a clean message.
        result = self._stub_result()
        result.warnings = ["Some file operations failed during the workload."]
        with (
            patch(
                "tfqa.cli.main.cfg_mod.load_config", return_value=self._stub_config()
            ),
            patch("tfqa.cli.main.devices_mod.get_device", return_value=self.device),
            patch(
                "tfqa.cli.main.workload_smallfiles.run_small_file_workload",
                return_value=result,
            ),
            patch(
                "tfqa.cli.main.history_mod.record_run",
                return_value=Path("/tmp/history.jsonl"),
            ),
        ):
            cli_result = self.runner.invoke(
                app, ["workload-smallfiles", "--device", "/dev/fake"]
            )

        self.assertEqual(cli_result.exit_code, 0, cli_result.stdout)
        self.assertIn("Warnings:", cli_result.stdout)
        self.assertIn("file operations failed", cli_result.stdout)

    def test_workload_smallfiles_dry_run_json(self) -> None:
        mock_engine = Mock()
        with (
            patch(
                "tfqa.cli.main.cfg_mod.load_config", return_value=self._stub_config()
            ),
            patch("tfqa.cli.main.devices_mod.get_device", return_value=self.device),
            patch(
                "tfqa.cli.main.workload_smallfiles.run_small_file_workload",
                mock_engine,
            ),
        ):
            cli_result = self.runner.invoke(
                app,
                [
                    "workload-smallfiles",
                    "--device",
                    "/dev/fake",
                    "--file-count",
                    "2",
                    "--file-size",
                    "1024",
                    "--dry-run",
                    "--output",
                    "json",
                ],
            )

        self.assertEqual(cli_result.exit_code, 0)
        resp = CLIResponse.model_validate_json(cli_result.stdout)
        self.assertEqual(resp.status, "ok")
        plan = resp.data.get("plan", {})
        self.assertEqual(plan.get("device_path"), "/dev/fake")
        self.assertEqual(plan.get("file_count"), 2)
        self.assertEqual(plan.get("file_size_bytes"), 1024)
        self.assertTrue(plan.get("delete_after"))
        mock_engine.assert_not_called()

    def test_workload_smallfiles_dry_run_human(self) -> None:
        mock_engine = Mock()
        with (
            patch(
                "tfqa.cli.main.cfg_mod.load_config", return_value=self._stub_config()
            ),
            patch("tfqa.cli.main.devices_mod.get_device", return_value=self.device),
            patch(
                "tfqa.cli.main.workload_smallfiles.run_small_file_workload",
                mock_engine,
            ),
        ):
            cli_result = self.runner.invoke(
                app,
                [
                    "workload-smallfiles",
                    "--device",
                    "/dev/fake",
                    "--file-count",
                    "2",
                    "--file-size",
                    "1024",
                    "--dry-run",
                ],
            )

        self.assertEqual(cli_result.exit_code, 0)
        self.assertIn("Dry run: small-file workload plan", cli_result.stdout)
        self.assertIn("Plan:", cli_result.stdout)
        mock_engine.assert_not_called()

    def test_workload_smallfiles_invalid_arguments(self) -> None:
        error = ArgumentError("file_count must be positive")
        with (
            patch(
                "tfqa.cli.main.cfg_mod.load_config", return_value=self._stub_config()
            ),
            patch("tfqa.cli.main.devices_mod.get_device", return_value=self.device),
            patch(
                "tfqa.cli.main.workload_smallfiles.run_small_file_workload",
                side_effect=error,
            ),
        ):
            cli_result = self.runner.invoke(
                app,
                [
                    "workload-smallfiles",
                    "--device",
                    "/dev/fake",
                    "--file-count",
                    "0",
                    "--output",
                    "json",
                ],
            )

        self.assertEqual(cli_result.exit_code, get_exit_code("INVALID_ARGUMENT"))
        resp = CLIResponse.model_validate_json(cli_result.stdout)
        self.assertEqual(resp.status, "error")
        self.assertEqual(resp.error_code, "INVALID_ARGUMENT")
        self.assertIn("file_count must be positive", resp.message)
