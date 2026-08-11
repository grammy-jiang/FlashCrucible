from __future__ import annotations

from typing import Any
from unittest import TestCase
from unittest.mock import patch

from typer.testing import CliRunner

from tfqa.cli.main import app
from tfqa.core.models import CLIResponse


class TrendsCLITest(TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()

    def _build_entry(
        self,
        run_id: str,
        stage: str,
        metrics: dict[str, float | int],
        *,
        status: str = "ok",
        duration: float = 1.0,
        include_details: bool = True,
    ) -> dict[str, Any]:
        metadata: dict[str, Any] = {"metrics": {stage: metrics}}
        if include_details:
            metadata["stage_details"] = {
                stage: {
                    "metrics": metrics,
                    "status": status,
                    "duration_seconds": duration,
                }
            }
        return {
            "run_id": run_id,
            "metadata": metadata,
        }

    def test_trends_command_aggregates_stage_metrics(self) -> None:
        entries = [
            self._build_entry(
                "run-1", "pipeline.quick-test", {"throughput_mbps": 250.0}
            ),
            self._build_entry(
                "run-2", "pipeline.quick-test", {"throughput_mbps": 270.0}
            ),
        ]
        with patch("tfqa.cli.main.history_mod.read_history", return_value=entries):
            invocation = self.runner.invoke(
                app,
                [
                    "trends",
                    "--stage",
                    "quick-test",
                    "--limit",
                    "2",
                    "--output",
                    "json",
                ],
            )

        self.assertEqual(invocation.exit_code, 0)
        response = CLIResponse.model_validate_json(invocation.stdout)
        trends_payload = response.data["trends"]
        self.assertEqual(trends_payload["entries_processed"], 2)
        stage_metrics = trends_payload["stage_metrics"]
        quick_metrics = stage_metrics["pipeline.quick-test"]
        self.assertEqual(quick_metrics["count"], 2)
        self.assertAlmostEqual(
            quick_metrics["averages"]["throughput_mbps"], 260.0, places=3
        )
        self.assertEqual(trends_payload["stage_filter"], "quick-test")
        self.assertEqual(quick_metrics["occurrences"], 2)
        self.assertEqual(quick_metrics["status_counts"].get("ok"), 2)
        self.assertEqual(quick_metrics["duration"]["count"], 2)

    def test_trends_human_output_shows_stage_metrics(self) -> None:
        entries = [
            self._build_entry(
                "run-1", "pipeline.quick-test", {"throughput_mbps": 250.0}
            ),
        ]
        with patch("tfqa.cli.main.history_mod.read_history", return_value=entries):
            result = self.runner.invoke(app, ["trends", "--limit", "1"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("Entries scanned: 1", result.stdout)
        self.assertIn("pipeline.quick-test", result.stdout)
        self.assertIn("statuses:", result.stdout)
        self.assertIn("avg throughput_mbps:", result.stdout)
