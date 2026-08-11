"""Tests for the describe-schemas CLI command."""

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


class SchemaDiscoveryCLITest(TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()

    def _write_schema(self, path: Path, title: str, version: str) -> None:
        content = {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "title": title,
            "description": f"Schema for {title}",
            "schema_version": version,
            "type": "object",
            "properties": {},
        }
        path.write_text(json.dumps(content), encoding="utf-8")

    def test_describe_schemas_json_lists_available_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            schema_path = Path(tmpdir) / "cli_response.schema.json"
            self._write_schema(schema_path, "CLIResponse", "v1")
            with patch.dict(os.environ, {"TFQA_SCHEMAS_DIR": tmpdir}):
                invocation = self.runner.invoke(
                    app,
                    ["describe-schemas", "--output", "json"],
                )

        self.assertEqual(invocation.exit_code, 0)
        response = CLIResponse.model_validate_json(invocation.stdout)
        entries = response.data["schemas"]
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry["name"], "cli_response.schema.json")
        self.assertEqual(entry["schema_version"], "v1")
        self.assertEqual(entry["title"], "CLIResponse")

    def test_describe_schemas_can_filter_by_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            schema_a = Path(tmpdir) / "cli_response.schema.json"
            schema_b = Path(tmpdir) / "capabilities.schema.json"
            self._write_schema(schema_a, "CLIResponse", "v1")
            self._write_schema(schema_b, "Capabilities", "v2")
            with patch.dict(os.environ, {"TFQA_SCHEMAS_DIR": tmpdir}):
                invocation = self.runner.invoke(
                    app,
                    [
                        "describe-schemas",
                        "--schema",
                        "capabilities",
                        "--output",
                        "json",
                    ],
                )

        self.assertEqual(invocation.exit_code, 0)
        response = CLIResponse.model_validate_json(invocation.stdout)
        entries = response.data["schemas"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["name"], "capabilities.schema.json")
