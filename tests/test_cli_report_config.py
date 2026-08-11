import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from typer.testing import CliRunner

from tfqa.cli.main import app
from tfqa.core.errors import TFQAError
from tfqa.core.models import CLIResponse, ConfigModel

REPORT_FIXTURE_PATH = (
    Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "sample-run.jsonl"
)


class ReportCLITest(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()

    def _history_entry(self) -> dict[str, Any]:
        return {
            "timestamp": "2025-11-19T12:00:00Z",
            "run_id": "run-001",
            "command": "pipeline",
            "device_path": "/dev/sdb",
            "status": "ok",
            "message": "Pipeline recorded.",
            "log_path": str(REPORT_FIXTURE_PATH),
        }

    def test_report_json(self) -> None:
        entry = self._history_entry()
        with patch("tfqa.cli.main.history_mod.read_history", return_value=[entry]):
            result = self.runner.invoke(app, ["report", "--output", "json"])

        self.assertEqual(result.exit_code, 0)
        resp = CLIResponse.model_validate_json(result.stdout)
        self.assertEqual(resp.status, "ok")
        self.assertEqual(resp.command, "report")
        summary = resp.data.get("summary", {})
        self.assertEqual(summary.get("run_id"), "run-001")
        self.assertEqual(summary.get("event_count"), 4)
        self.assertEqual(len(summary.get("events", [])), 4)
        history_entry = resp.data.get("history_entry", {})
        self.assertEqual(history_entry.get("run_id"), entry["run_id"])
        self.assertAlmostEqual(summary.get("duration_seconds"), 315.0)
        self.assertIsInstance(summary.get("metrics_series_by_stage"), dict)

    def test_report_human_output_includes_summary(self) -> None:
        entry = self._history_entry()
        with patch("tfqa.cli.main.history_mod.read_history", return_value=[entry]):
            result = self.runner.invoke(app, ["report"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("Run summary for run-001.", result.stdout)
        self.assertIn("Run ID: run-001", result.stdout)
        self.assertIn("Events: 4", result.stdout)
        self.assertIn("Log path: ", result.stdout)
        self.assertIn("Duration: 315.0s", result.stdout)

    def test_report_invalid_run_id(self) -> None:
        entry = self._history_entry()
        with patch("tfqa.cli.main.history_mod.read_history", return_value=[entry]):
            result = self.runner.invoke(
                app, ["report", "--run-id", "run-999", "--output", "json"]
            )

        resp = CLIResponse.model_validate_json(result.stdout)
        self.assertEqual(resp.status, "error")
        self.assertEqual(resp.error_code, "INVALID_ARGUMENT")
        self.assertIn("Run ID not found", resp.message)

    def test_report_missing_history(self) -> None:
        with patch("tfqa.cli.main.history_mod.read_history", return_value=[]):
            result = self.runner.invoke(app, ["report", "--output", "json"])

        resp = CLIResponse.model_validate_json(result.stdout)
        self.assertEqual(resp.status, "error")
        self.assertEqual(resp.error_code, "INVALID_ARGUMENT")
        self.assertIn("No history entries", resp.message)

    def test_report_preserves_metadata(self) -> None:
        metadata: dict[str, Any] = {
            "stage_plan": ["detect", "quick-test"],
            "image_options": {
                "image_path": "/tmp/sample.bin",
                "block_size": "1M",
                "conv_flags": ["fsync"],
                "verify": False,
                "write_timeout": 120.0,
                "verify_timeout": 60.0,
            },
        }
        entry = self._history_entry()
        entry["metadata"] = metadata
        with patch("tfqa.cli.main.history_mod.read_history", return_value=[entry]):
            result = self.runner.invoke(app, ["report", "--output", "json"])

        resp = CLIResponse.model_validate_json(result.stdout)
        self.assertEqual(resp.status, "ok")
        history_entry = resp.data.get("history_entry", {})
        self.assertEqual(history_entry.get("metadata"), metadata)


class ConfigShowCLITest(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()

    def _stub_config(self) -> ConfigModel:
        return ConfigModel(
            log_dir=Path("/tmp/cli-log"), profiles_dir=Path("/tmp/profiles")
        )

    def test_config_show_json(self) -> None:
        config = self._stub_config()
        with patch("tfqa.cli.main.cfg_mod.load_config", return_value=config):
            result = self.runner.invoke(app, ["config", "show", "--output", "json"])

        resp = CLIResponse.model_validate_json(result.stdout)
        self.assertEqual(resp.status, "ok")
        payload = resp.data.get("config", {})
        self.assertEqual(payload.get("log_dir"), "/tmp/cli-log")
        self.assertEqual(payload.get("profiles_dir"), "/tmp/profiles")

    def test_config_show_human_outputs_values(self) -> None:
        config = self._stub_config()
        with patch("tfqa.cli.main.cfg_mod.load_config", return_value=config):
            result = self.runner.invoke(app, ["config", "show"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("Merged configuration loaded.", result.stdout)
        self.assertIn("log_dir: /tmp/cli-log", result.stdout)
        self.assertIn("profiles_dir: /tmp/profiles", result.stdout)


class ConfigValidateCLITest(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()

    def _stub_config(self) -> ConfigModel:
        return ConfigModel(
            log_dir=Path("/tmp/cli-log"), profiles_dir=Path("/tmp/profiles")
        )

    def test_config_validate_json(self) -> None:
        config = self._stub_config()
        with (
            patch("tfqa.cli.main.cfg_mod.load_config", return_value=config),
            patch(
                "tfqa.cli.main.cfg_mod.find_config_files",
                return_value=[Path("/tmp/a"), Path("/tmp/b")],
            ),
        ):
            result = self.runner.invoke(app, ["config", "validate", "--output", "json"])

        resp = CLIResponse.model_validate_json(result.stdout)
        self.assertEqual(resp.status, "ok")
        self.assertTrue(resp.data.get("valid"))
        self.assertEqual(resp.data.get("files"), ["/tmp/a", "/tmp/b"])

    def test_config_validate_human_outputs_files(self) -> None:
        config = self._stub_config()
        with (
            patch("tfqa.cli.main.cfg_mod.load_config", return_value=config),
            patch(
                "tfqa.cli.main.cfg_mod.find_config_files", return_value=[Path("/tmp/a")]
            ),
        ):
            result = self.runner.invoke(app, ["config", "validate"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("Configuration validated successfully.", result.stdout)
        self.assertIn("Loaded configuration files:", result.stdout)
        self.assertIn("/tmp/a", result.stdout)

    def test_config_validate_errors(self) -> None:
        config = self._stub_config()
        with (
            patch("tfqa.cli.main.cfg_mod.load_config", return_value=config),
            patch(
                "tfqa.cli.main.cfg_mod.find_config_files",
                side_effect=TFQAError("Failure", "INVALID_ARGUMENT"),
            ),
        ):
            result = self.runner.invoke(app, ["config", "validate", "--output", "json"])

        resp = CLIResponse.model_validate_json(result.stdout)
        self.assertEqual(resp.status, "error")
        self.assertEqual(resp.error_code, "INVALID_ARGUMENT")

    def test_config_validate_no_files(self) -> None:
        config = self._stub_config()
        with (
            patch("tfqa.cli.main.cfg_mod.load_config", return_value=config),
            patch("tfqa.cli.main.cfg_mod.find_config_files", return_value=[]),
        ):
            result = self.runner.invoke(app, ["config", "validate"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("No configuration files were found", result.stdout)
