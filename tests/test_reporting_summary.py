from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import jsonschema

from tfqa.reporting import summary as summary_mod

FIXTURE_PATH = (
    Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "sample-run.jsonl"
)
SCHEMA_PATH = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "schemas"
    / "json"
    / "summary.schema.json"
)


class TestReportingSummary(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.tmp_path = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_summarize_single_log_includes_time_series(self) -> None:
        summary = summary_mod.summarize_run("run-001", log_path=FIXTURE_PATH)
        self.assertEqual(summary["event_count"], 4)
        self.assertEqual(len(summary["time_series"]), 4)
        last_entry = summary["time_series"][-1]
        self.assertEqual(last_entry["event_type"], "result")
        self.assertLess(abs(last_entry["metrics"]["throughput_mbps"] - 260.5), 1e-6)
        self.assertEqual(summary["metrics_by_stage"], {})
        self.assertEqual(summary["metrics_series_by_stage"], {})
        self.assertLess(abs(summary["duration_seconds"] - 315.0), 1e-6)

    def test_summarize_multiple_logs_aggregates_metrics(self) -> None:
        extra_log = self.tmp_path / "extra.jsonl"
        extra_event: dict[str, Any] = {
            "timestamp": "2025-11-18T10:22:00Z",
            "run_id": "run-001",
            "phase": "pipeline",
            "stage": "quick-test",
            "status": "ok",
            "metrics": {
                "throughput_mbps": 271.0,
                "error_rate": 0.0,
            },
        }
        extra_log.write_text(json.dumps(extra_event) + "\n")
        summary = summary_mod.summarize_run(
            "run-001",
            log_paths=[FIXTURE_PATH, extra_log],
        )
        self.assertEqual(summary["event_count"], 5)
        # Ensure time series is sorted and contains the new pipeline entry
        self.assertEqual(summary["time_series"][-1]["stage"], "quick-test")
        self.assertLess(
            abs(summary["metrics_by_stage"]["quick-test"]["throughput_mbps"] - 271.0),
            1e-6,
        )
        self.assertLess(
            abs(summary["metrics_by_stage"]["quick-test"]["error_rate"] - 0.0), 1e-6
        )
        series = summary["metrics_series_by_stage"].get("quick-test", [])
        self.assertEqual(len(series), 1)
        self.assertLess(abs(series[0]["metrics"]["throughput_mbps"] - 271.0), 1e-6)

    def test_summary_output_matches_schema(self) -> None:
        summary = summary_mod.summarize_run("run-001", log_path=FIXTURE_PATH)
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        jsonschema.validate(instance=summary, schema=schema)
