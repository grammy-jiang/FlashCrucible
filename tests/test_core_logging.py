"""Tests for tfqa.core.logging utilities."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tfqa.core.logging import create_logger, emit_event


class LoggingTest(unittest.TestCase):
    def test_create_logger_creates_file(self) -> None:
        run_id = "2025-11-18T10-00-00Z_test"
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir) / "logs"

            logfile = create_logger(run_id, log_dir=log_dir)

            self.assertTrue(logfile.exists())
            self.assertTrue(logfile.name.endswith(f"{run_id}.jsonl"))

    def test_emit_event_writes_jsonl(self) -> None:
        run_id = "run123"
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir) / "logs"

            event = {"phase": "start", "message": "testing"}
            logfile = emit_event(run_id, event, log_dir=log_dir)

            self.assertTrue(logfile.exists())

            content = logfile.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(content), 1)

            obj = json.loads(content[0])
            self.assertEqual(obj["run_id"], run_id)
            self.assertEqual(obj["phase"], "start")
            self.assertEqual(obj["message"], "testing")
            self.assertIn("timestamp", obj)

    def test_emit_multiple_events_appends(self) -> None:
        run_id = "run-multi"
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir) / "logs"

            emit_event(run_id, {"i": 1}, log_dir=log_dir)
            emit_event(run_id, {"i": 2}, log_dir=log_dir)

            logfile = log_dir / f"run-{run_id}.jsonl"
            lines = logfile.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 2)
