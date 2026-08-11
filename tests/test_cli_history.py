"""Tests for the tfqa history CLI command."""

from __future__ import annotations

from typing import Any

import json
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from typer.testing import CliRunner

from tfqa.cli.main import app
from tfqa.core.models import CLIResponse


class HistoryCLITest(TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()

    def test_history_json_limit_and_device_filter(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            history_file = Path(tmpdir) / "history.jsonl"
            entries = [
                {
                    "timestamp": "2025-01-01T00:00:00Z",
                    "run_id": "run-1",
                    "command": "pipeline",
                    "device_path": "/dev/sdb",
                    "status": "ok",
                    "message": "Pipeline completed",
                },
                {
                    "timestamp": "2025-01-02T00:00:00Z",
                    "run_id": "run-2",
                    "command": "pipeline",
                    "device_path": "/dev/sdc",
                    "status": "failed",
                    "message": "Pipeline failed",
                },
            ]
            with history_file.open("w", encoding="utf-8") as fh:
                for entry in entries:
                    fh.write(json.dumps(entry) + "\n")

            with patch(
                "tfqa.reporting.history._default_history_path",
                return_value=history_file,
            ):
                invocation = self.runner.invoke(
                    app,
                    [
                        "history",
                        "--device",
                        "/dev/sdb",
                        "--limit",
                        "1",
                        "--output",
                        "json",
                    ],
                )

        self.assertEqual(invocation.exit_code, 0)
        response = CLIResponse.model_validate_json(invocation.stdout)
        self.assertEqual(response.status, "ok")
        filtered_entries = response.data["entries"]
        self.assertEqual(len(filtered_entries), 1)
        self.assertEqual(filtered_entries[0]["device_path"], "/dev/sdb")
        self.assertEqual(filtered_entries[0]["run_id"], "run-1")

    def test_history_json_preserves_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            history_file = Path(tmpdir) / "history.jsonl"
            metadata: dict[str, Any] = {
                "image_options": {
                    "image_path": "/tmp/image.bin",
                    "block_size": "4M",
                    "conv_flags": ["fsync", "noerror"],
                    "verify": True,
                    "write_timeout": 600.0,
                    "verify_timeout": 300.0,
                }
            }
            entry: dict[str, Any] = {
                "timestamp": "2025-01-03T00:00:00Z",
                "run_id": "run-3",
                "command": "image-flash",
                "device_path": "/dev/sde",
                "status": "ok",
                "message": "Image flash completed",
                "metadata": metadata,
            }
            with history_file.open("w", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")

            with patch(
                "tfqa.reporting.history._default_history_path",
                return_value=history_file,
            ):
                invocation = self.runner.invoke(
                    app,
                    [
                        "history",
                        "--limit",
                        "1",
                        "--output",
                        "json",
                    ],
                )

        self.assertEqual(invocation.exit_code, 0)
        response = CLIResponse.model_validate_json(invocation.stdout)
        entries = response.data["entries"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["metadata"], metadata)
