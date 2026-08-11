import unittest
from typing import Any
from unittest.mock import patch

from typer.testing import CliRunner

from tfqa.cli.main import app
from tfqa.core.models import CLIResponse
from tfqa.orchestration.workflows import WorkloadCombo


class CombosProfilesCLITest(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()

    def _make_combos(self) -> list[WorkloadCombo]:
        return [
            WorkloadCombo(
                name="camera-logger",
                stages=["detect", "quick-test"],
                description="Camera focused combo",
                profile="camera-logger",
                image_options={"block_size": "4M", "conv_flags": ["fsync"]},
            ),
            WorkloadCombo(
                name="router-telemetry",
                stages=["detect", "health"],
                description="Router telemetry combo",
                profile="router-telemetry",
                image_options=None,
            ),
        ]

    def test_combos_json_returns_metadata(self) -> None:
        combos = self._make_combos()
        with patch("tfqa.orchestration.workflows.list_combos", lambda config: combos):
            result = self.runner.invoke(app, ["combos", "--output", "json"])
        self.assertEqual(result.exit_code, 0)
        response = CLIResponse.model_validate_json(result.stdout)
        self.assertIn("combos", response.data)
        self.assertEqual(len(response.data["combos"]), len(combos))
        combo_payload = response.data["combos"][0]
        self.assertEqual(combo_payload.get("name"), "camera-logger")
        self.assertEqual(combo_payload.get("profile"), "camera-logger")
        self.assertEqual(combo_payload.get("image_options", {}).get("block_size"), "4M")

    def test_combos_name_filter_is_case_insensitive(self) -> None:
        combos = self._make_combos()
        with patch("tfqa.orchestration.workflows.list_combos", lambda config: combos):
            result = self.runner.invoke(
                app, ["combos", "--name", "CAMERA-LOGGER", "--output", "json"]
            )
        self.assertEqual(result.exit_code, 0)
        response = CLIResponse.model_validate_json(result.stdout)
        self.assertEqual(len(response.data["combos"]), 1)
        self.assertEqual(response.data["combos"][0].get("name"), "camera-logger")

    def test_profiles_json_shows_all_metadata(self) -> None:
        sample_profiles: list[dict[str, Any]] = [
            {
                "name": "default",
                "description": "Default endurance profile",
                "duration_seconds": 1800.0,
                "pass_count": 2,
                "force": False,
                "write_pattern": "sequential",
                "path": "/tmp/profiles/default.toml",
            },
            {
                "name": "camera-logger",
                "description": "Camera-focused endurance",
                "duration_seconds": 3600.0,
                "pass_count": 4,
                "force": True,
                "write_pattern": "random",
                "path": "/tmp/profiles/camera-logger.toml",
            },
        ]
        with patch(
            "tfqa.orchestration.profile.list_profiles", lambda config: sample_profiles
        ):
            result = self.runner.invoke(app, ["profiles", "--output", "json"])
        self.assertEqual(result.exit_code, 0)
        response = CLIResponse.model_validate_json(result.stdout)
        profiles_payload = response.data.get("profiles", [])
        self.assertEqual(len(profiles_payload), 2)
        first = profiles_payload[0]
        self.assertEqual(first.get("duration_seconds"), 1800.0)
        self.assertEqual(first.get("pass_count"), 2)
        self.assertEqual(first.get("path"), "/tmp/profiles/default.toml")

    def test_profiles_name_filter_respects_case_insensitivity(self) -> None:
        sample_profiles: list[dict[str, Any]] = [
            {
                "name": "camera-logger",
                "description": "desc",
                "duration_seconds": 100.0,
                "pass_count": 1,
                "force": False,
                "write_pattern": "sequential",
                "path": "/tmp/camera.toml",
            },
        ]
        with patch(
            "tfqa.orchestration.profile.list_profiles", lambda config: sample_profiles
        ):
            result = self.runner.invoke(
                app, ["profiles", "--name", "CAMERA-LOGGER", "--output", "json"]
            )
        self.assertEqual(result.exit_code, 0)
        response = CLIResponse.model_validate_json(result.stdout)
        self.assertEqual(len(response.data.get("profiles", [])), 1)
        self.assertEqual(response.data["profiles"][0].get("name"), "camera-logger")

    def test_profiles_human_output_flags_an_unreadable_profile(self) -> None:
        # A malformed preset used to vanish from the listing entirely.
        sample_profiles: list[dict[str, Any]] = [
            {
                "name": "broken",
                "description": None,
                "path": "/tmp/broken.toml",
                "error": "Illegal character '\\n' (at line 2, column 106)",
            },
            {
                "name": "fine",
                "description": "desc",
                "duration_seconds": 100.0,
                "pass_count": 1,
                "force": False,
                "write_pattern": "sequential",
                "path": "/tmp/fine.toml",
                "error": None,
            },
        ]
        with patch(
            "tfqa.orchestration.profile.list_profiles", lambda config: sample_profiles
        ):
            result = self.runner.invoke(app, ["profiles"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("broken: UNREADABLE", result.stdout)
        self.assertIn("/tmp/broken.toml", result.stdout)
        self.assertIn("Illegal character", result.stdout)
        # The good profile is still listed normally.
        self.assertIn("fine: desc", result.stdout)
