"""Tests for the validate-schemas CLI command."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from typer.testing import CliRunner

from tfqa.cli.main import app
from tfqa.core.models import CLIResponse


class SchemaValidationCLITest(TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()

    def _write_schema(self, path: Path, schema_data: dict[str, object]) -> None:
        path.write_text(json.dumps(schema_data), encoding="utf-8")

    def test_validate_schemas_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            schema_path = Path(tmpdir) / "cli_response.schema.json"
            self._write_schema(
                schema_path,
                {
                    "$schema": "http://json-schema.org/draft-07/schema#",
                    "title": "CLIResponse",
                    "description": "Valid schema",
                    "schema_version": "v1",
                    "type": "object",
                    "properties": {"status": {"type": "string"}},
                },
            )
            with patch.dict(os.environ, {"TFQA_SCHEMAS_DIR": tmpdir}):
                invocation = self.runner.invoke(
                    app,
                    ["validate-schemas", "--output", "json"],
                )

        self.assertEqual(invocation.exit_code, 0)
        response = CLIResponse.model_validate_json(invocation.stdout)
        self.assertEqual(response.status, "ok")
        self.assertEqual(response.data["validated"], 1)
        self.assertEqual(response.data["errors"], [])
        self.assertEqual(response.data["failed"], 0)
        files = response.data["files"]
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0]["status"], "ok")
        self.assertEqual(files[0]["errors"], [])

    def test_validate_schemas_reports_invalid_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            schema_path = Path(tmpdir) / "bad.schema.json"
            self._write_schema(
                schema_path,
                {
                    "$schema": "http://json-schema.org/draft-07/schema#",
                    "title": "Bad",
                    "schema_version": "v1",
                    "type": "object",
                    "properties": {"value": {"type": ["number", "unknown"]}},
                },
            )
            with patch.dict(os.environ, {"TFQA_SCHEMAS_DIR": tmpdir}):
                invocation = self.runner.invoke(
                    app,
                    ["validate-schemas", "--output", "json"],
                )

        self.assertEqual(invocation.exit_code, 0)
        response = CLIResponse.model_validate_json(invocation.stdout)
        self.assertEqual(response.status, "fail")
        self.assertEqual(response.data["validated"], 1)
        self.assertTrue(response.data["errors"])
        self.assertEqual(response.data["failed"], 1)
        files = response.data["files"]
        self.assertEqual(files[0]["status"], "error")
        self.assertTrue(files[0]["errors"])
        self.assertTrue(files[0]["hints"])
        error_entry = response.data["errors"][0]
        self.assertTrue(error_entry["schema"].endswith("bad.schema.json"))
        self.assertIn("unknown", error_entry["error"].lower())
