"""Tests for the lint-schemas CLI command."""

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


class SchemaLintCLITest(TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()

    def _write_schema(self, path: Path, schema_data: dict[str, object]) -> None:
        path.write_text(json.dumps(schema_data), encoding="utf-8")

    def test_lint_schemas_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            schema_path = Path(tmpdir) / "cli_response.schema.json"
            self._write_schema(
                schema_path,
                {
                    "$schema": "http://json-schema.org/draft-07/schema#",
                    "title": "CLIResponse",
                    "schema_version": "v1",
                    "type": "object",
                },
            )
            with patch.dict(os.environ, {"TFQA_SCHEMAS_DIR": tmpdir}):
                invocation = self.runner.invoke(
                    app,
                    ["lint-schemas", "--output", "json"],
                )

        self.assertEqual(invocation.exit_code, 0)
        response = CLIResponse.model_validate_json(invocation.stdout)
        self.assertEqual(response.status, "ok")
        self.assertEqual(response.data["inspected"], 1)
        self.assertEqual(response.data["issues"], [])

    def test_lint_schemas_reports_missing_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            schema_path = Path(tmpdir) / "minimal.schema.json"
            self._write_schema(
                schema_path,
                {
                    "$schema": "http://json-schema.org/draft-07/schema#",
                    "type": "object",
                },
            )
            with patch.dict(os.environ, {"TFQA_SCHEMAS_DIR": tmpdir}):
                invocation = self.runner.invoke(
                    app,
                    ["lint-schemas", "--output", "json"],
                )

        self.assertEqual(invocation.exit_code, 0)
        response = CLIResponse.model_validate_json(invocation.stdout)
        self.assertEqual(response.status, "fail")
        self.assertEqual(response.data["inspected"], 1)
        issues = response.data["issues"]
        self.assertEqual(len(issues), 1)
        self.assertListEqual(issues[0]["missing_fields"], ["title", "schema_version"])
        self.assertTrue(issues[0]["hint"])
