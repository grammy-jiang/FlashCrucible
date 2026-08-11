"""Tests for the automation-report CLI command."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from typer.testing import CliRunner

from tfqa.cli.main import app
from tfqa.core.models import CLIResponse


class AutomationReportCLITest(TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()

    def test_automation_report_json_uses_history_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            history_file = Path(tmpdir) / "history.jsonl"
            log_file = Path(tmpdir) / "run-events.jsonl"
            history_entry = {
                "timestamp": "2025-01-01T00:00:00Z",
                "run_id": "automation-123",
                "command": "pipeline",
                "device_path": "/dev/sdb",
                "status": "ok",
                "message": "Pipeline completed",
                "stage_count": 2,
                "metadata": {
                    "stage_details": {
                        "quick-test": {
                            "metrics": {"throughput_mbps": 123.4},
                            "status": "ok",
                            "duration_seconds": 31.2,
                        }
                    }
                },
                "log_path": str(log_file),
            }
            history_file.write_text(json.dumps(history_entry) + "\n", encoding="utf-8")
            log_events = [
                {
                    "timestamp": "2025-01-01T00:00:00Z",
                    "phase": "pipeline",
                    "stage": "quick-test",
                    "status": "ok",
                    "metrics": {"throughput_mbps": 123.4},
                },
                {
                    "timestamp": "2025-01-01T00:01:00Z",
                    "phase": "pipeline",
                    "stage": "endurance",
                    "status": "ok",
                    "metrics": {"duration_seconds": 60},
                },
            ]
            log_file.write_text(
                "\n".join(json.dumps(event) for event in log_events) + "\n",
                encoding="utf-8",
            )
            with patch(
                "tfqa.reporting.history._default_history_path",
                return_value=history_file,
            ):
                invocation = self.runner.invoke(
                    app,
                    ["automation-report", "--output", "json"],
                )

        self.assertEqual(invocation.exit_code, 0)
        response = CLIResponse.model_validate_json(invocation.stdout)
        self.assertEqual(response.command, "automation-report")
        report = response.data["report"]
        self.assertEqual(report["history_entry"]["run_id"], "automation-123")
        self.assertEqual(report["summary"]["overall_status"], "ok")
        self.assertIn("stage_metrics", report["trends"])
