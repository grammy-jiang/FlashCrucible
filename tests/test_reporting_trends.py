from __future__ import annotations

import json
import unittest
from pathlib import Path

import jsonschema

from tfqa.reporting import trends as trends_mod


def _build_entry(
    run_id: str,
    stage: str,
    metrics: dict[str, float | int],
    *,
    status: str = "ok",
    duration: float = 1.0,
    include_details: bool = True,
) -> dict[str, object]:
    metadata: dict[str, object] = {
        "metrics": {
            stage: metrics,
        }
    }
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


SCHEMA_PATH = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "schemas"
    / "json"
    / "trends.schema.json"
)


class TestReportingTrends(unittest.TestCase):
    def test_aggregate_stage_metrics_accumulates_averages(self) -> None:
        entries = [
            _build_entry("run-1", "pipeline.quick-test", {"throughput_mbps": 250.0}),
            _build_entry("run-2", "pipeline.quick-test", {"throughput_mbps": 270.0}),
        ]

        summary = trends_mod.aggregate_stage_metrics(entries)

        self.assertEqual(summary["entries_processed"], 2)
        stage_metrics = summary["stage_metrics"]
        self.assertIn("pipeline.quick-test", stage_metrics)
        quick_metrics = stage_metrics["pipeline.quick-test"]
        self.assertEqual(quick_metrics["count"], 2)
        self.assertLess(abs(quick_metrics["averages"]["throughput_mbps"] - 260.0), 1e-6)
        self.assertLess(
            abs(quick_metrics["last_metrics"]["throughput_mbps"] - 270.0), 1e-6
        )
        self.assertEqual(quick_metrics["occurrences"], 2)
        self.assertEqual(quick_metrics["status_counts"]["ok"], 2)
        self.assertEqual(quick_metrics["duration"]["count"], 2)
        self.assertLess(abs(quick_metrics["duration"]["average"] - 1.0), 1e-6)
        self.assertLess(abs(quick_metrics["duration"]["last"] - 1.0), 1e-6)

    def test_aggregate_stage_metrics_respects_stage_filter(self) -> None:
        entries = [
            _build_entry("run-1", "pipeline.quick-test", {"throughput_mbps": 240.0}),
            _build_entry("run-2", "pipeline.endurance", {"total_bytes": 1000}),
        ]

        summary = trends_mod.aggregate_stage_metrics(entries, stage_filter="quick-test")

        self.assertEqual(summary["stage_filter"], "quick-test")
        stage_metrics = summary["stage_metrics"]
        self.assertIn("pipeline.quick-test", stage_metrics)
        self.assertNotIn("pipeline.endurance", stage_metrics)
        self.assertEqual(stage_metrics["pipeline.quick-test"]["count"], 1)

    def test_aggregate_stage_metrics_handles_no_data(self) -> None:
        entries: list[dict[str, object]] = []

        summary = trends_mod.aggregate_stage_metrics(entries)

        self.assertEqual(summary["entries_processed"], 0)
        self.assertEqual(summary["stage_metrics"], {})

    def test_aggregate_stage_metrics_matches_schema(self) -> None:
        entries = [
            _build_entry("run-1", "pipeline.quick-test", {"throughput_mbps": 250.0}),
            _build_entry("run-2", "pipeline.quick-test", {"throughput_mbps": 270.0}),
        ]
        aggregated = trends_mod.aggregate_stage_metrics(entries)
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        jsonschema.validate(instance=aggregated, schema=schema)
