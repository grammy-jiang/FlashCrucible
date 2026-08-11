import unittest
from unittest.mock import patch

from typer.testing import CliRunner

from tfqa.cli.main import app
from tfqa.core.models import CLIResponse, Capabilities, ToolCapability


class DescribeCapabilitiesCLI(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()

    def test_describe_json(self) -> None:
        result = self.runner.invoke(app, ["describe", "detect", "--output", "json"])
        resp = CLIResponse.model_validate_json(result.stdout)
        self.assertEqual(resp.status, "ok")
        self.assertEqual(resp.command, "describe")
        describe_payload = resp.data.get("describe", {})
        self.assertIsInstance(describe_payload, dict)
        self.assertEqual(describe_payload.get("name"), "detect")

    def test_describe_human(self) -> None:
        result = self.runner.invoke(app, ["describe", "quick-test"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Command schema for 'quick-test'", result.stdout)

    def test_describe_unknown(self) -> None:
        result = self.runner.invoke(
            app, ["describe", "unknown-cmd", "--output", "json"]
        )
        resp = CLIResponse.model_validate_json(result.stdout)
        self.assertEqual(resp.status, "error")
        self.assertEqual(resp.error_code, "INVALID_ARGUMENT")
        self.assertIn("Command not found", resp.message)

    def test_describe_config_validate_entry(self) -> None:
        result = self.runner.invoke(
            app, ["describe", "config validate", "--output", "json"]
        )
        resp = CLIResponse.model_validate_json(result.stdout)
        self.assertEqual(resp.status, "ok")
        payload = resp.data.get("describe", {})
        self.assertEqual(payload.get("name"), "config validate")
        self.assertFalse(payload.get("destructive"))
        self.assertFalse(payload.get("requires_root"))
        options = payload.get("options", [])
        self.assertTrue(bool(options))
        # Check for output option by name 'output' or flags
        output_opt = next((o for o in options if o.get("name") == "output"), None)
        self.assertIsNotNone(output_opt)
        if output_opt:
            self.assertIn("--output", output_opt.get("flags", []))

    def test_describe_endurance_entry(self) -> None:
        result = self.runner.invoke(app, ["describe", "endurance", "--output", "json"])
        resp = CLIResponse.model_validate_json(result.stdout)
        self.assertEqual(resp.status, "ok")
        payload = resp.data.get("describe", {})
        self.assertEqual(payload.get("name"), "endurance")
        # device is an option in endurance command
        opts = payload.get("options", [])
        self.assertTrue(any(opt.get("name") == "device" for opt in opts))
        self.assertTrue(any(opt.get("name") == "profile" for opt in opts))

    def test_describe_pipeline_entry(self) -> None:
        result = self.runner.invoke(app, ["describe", "pipeline", "--output", "json"])
        resp = CLIResponse.model_validate_json(result.stdout)
        self.assertEqual(resp.status, "ok")
        payload = resp.data.get("describe", {})
        self.assertEqual(payload.get("name"), "pipeline")
        self.assertTrue(
            any(opt.get("name") == "profile" for opt in payload.get("options", []))
        )

    def test_describe_history_entry(self) -> None:
        result = self.runner.invoke(app, ["describe", "history", "--output", "json"])
        resp = CLIResponse.model_validate_json(result.stdout)
        self.assertEqual(resp.status, "ok")
        payload = resp.data.get("describe", {})
        self.assertEqual(payload.get("name"), "history")
        options = payload.get("options", [])
        self.assertTrue(any(opt.get("name") == "device" for opt in options))


class CapabilitiesCLI(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()

    def _sample_caps(self) -> Capabilities:
        tool = ToolCapability(
            name="f3probe",
            available=True,
            version="8.0",
            path="/usr/bin/f3probe",
        )
        return Capabilities(
            version="0.1.0",
            platform="linux x86_64",
            external_tools={"f3probe": tool},
            features={"capacity_quick": "hybrid"},
        )

    def test_capabilities_json(self) -> None:
        caps = self._sample_caps()
        with patch("tfqa.core.capabilities.probe_capabilities", return_value=caps):
            result = self.runner.invoke(app, ["capabilities", "--output", "json"])

        resp = CLIResponse.model_validate_json(result.stdout)
        self.assertEqual(resp.status, "ok")
        data = resp.data
        self.assertEqual(data.get("version"), "0.1.0")
        self.assertIn("f3probe", data.get("external_tools", {}))

    def test_capabilities_human(self) -> None:
        caps = self._sample_caps()
        with patch("tfqa.core.capabilities.probe_capabilities", return_value=caps):
            result = self.runner.invoke(app, ["capabilities"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("Capabilities probe successful", result.stdout)
        self.assertIn("f3probe", result.stdout)
