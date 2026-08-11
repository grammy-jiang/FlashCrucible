"""Tests for the real JSON schemas in data/schemas/json."""

from __future__ import annotations

from unittest import TestCase

from typer.testing import CliRunner

from tfqa.cli.main import app
from tfqa.core.models import CLIResponse


class RealSchemaDiscoveryTest(TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()

    def test_describe_schemas_finds_automation_report(self) -> None:
        # Ensure we are using the default schema directory (unset env var if set)
        # But CliRunner isolates env? No, it doesn't isolate os.environ completely if not patched.
        # We rely on default behavior of _schema_default_directory which uses __file__ relative path.

        invocation = self.runner.invoke(
            app,
            ["describe-schemas", "--output", "json"],
        )

        self.assertEqual(invocation.exit_code, 0)
        response = CLIResponse.model_validate_json(invocation.stdout)
        entries = response.data["schemas"]

        names = [entry["name"] for entry in entries]
        self.assertIn("automation_report.schema.json", names)
        self.assertIn("history_entry.schema.json", names)
        self.assertIn("summary.schema.json", names)
        self.assertIn("trends.schema.json", names)

    def test_automation_report_schema_content(self) -> None:
        invocation = self.runner.invoke(
            app,
            ["describe-schemas", "--schema", "automation_report", "--output", "json"],
        )

        self.assertEqual(invocation.exit_code, 0)
        response = CLIResponse.model_validate_json(invocation.stdout)
        entries = response.data["schemas"]
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry["name"], "automation_report.schema.json")
        self.assertEqual(entry["title"], "TFQA automation report")

        schema = entry["schema"]
        self.assertIn("history_entry", schema["properties"])
        self.assertIn("summary", schema["properties"])
        self.assertIn("trends", schema["properties"])
