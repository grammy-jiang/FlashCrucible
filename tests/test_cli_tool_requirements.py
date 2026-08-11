"""`describe` declares which external tools each command needs.

`capabilities` reports which tools a host has, but nothing connected a missing
`fio` to the `performance` command, so a caller had to know the relationship
independently — hardcoding what the CLI already knew.
"""

from __future__ import annotations

import json
from typing import cast

from typer.testing import CliRunner

from tfqa.cli.main import _TOOL_REQUIREMENTS, app
from tfqa.core.capabilities import _DEFAULT_TOOLS
from tfqa.core.models import CLIResponse

runner = CliRunner()

# Commands that shell out to something, and so must say what they need.
DEVICE_COMMANDS = (
    "quick-test",
    "performance",
    "surface-scan",
    "filesystem-check",
    "image-flash",
    "health",
    "endurance",
    "full-capacity-test",
    "pipeline",
)


def _describe(command: str) -> dict[str, object]:
    result = runner.invoke(app, ["describe", command, "--output", "json"])
    assert result.exit_code == 0, result.stdout
    payload = CLIResponse.model_validate_json(result.stdout).data["describe"]
    return dict(payload)


class TestDescribeDeclaresTools:
    def test_every_command_carries_the_keys(self) -> None:
        # Present on every command, so automation has one shape to parse.
        described = _describe("detect")
        assert described["required_tools"] == []
        assert described["optional_tools"] == []
        assert described["degradation"] is None

    def test_performance_declares_fio(self) -> None:
        described = _describe("performance")
        assert described["required_tools"] == ["fio"]
        assert "EXT_TOOL_MISSING" in str(described["degradation"])

    def test_quick_test_declares_f3probe(self) -> None:
        assert _describe("quick-test")["required_tools"] == ["f3probe"]

    def test_surface_scan_declares_badblocks(self) -> None:
        assert _describe("surface-scan")["required_tools"] == ["badblocks"]

    def test_health_tools_are_optional(self) -> None:
        # health degrades to "unavailable" rather than failing.
        described = _describe("health")
        assert described["required_tools"] == []
        assert sorted(cast(list[str], described["optional_tools"])) == ["mmc", "sdmon"]

    def test_image_flash_verification_is_optional(self) -> None:
        described = _describe("image-flash")
        assert described["required_tools"] == ["dd"]
        assert described["optional_tools"] == ["cmp"]

    def test_endurance_says_what_it_needs_for_wear_data(self) -> None:
        # It measures now. What still degrades is the wear delta, which needs
        # eMMC registers or sdmon -- and a caller has to know that the run can
        # succeed while reporting no wear at all.
        described = _describe("endurance")
        assert "EXT_CSD" in str(described["degradation"])
        assert set(cast(list[str], described["optional_tools"])) == {"mmc", "sdmon"}

    def test_pipeline_explains_that_a_stage_is_skipped(self) -> None:
        assert "skipped" in str(_describe("pipeline")["degradation"])


class TestRequirementsStayHonest:
    def test_every_device_command_declares_something(self) -> None:
        # A command that touches hardware and says nothing about what it needs
        # is the gap this closed.
        for command in DEVICE_COMMANDS:
            described = _describe(command)
            declared = (
                described["required_tools"]
                or described["optional_tools"]
                or described["degradation"]
            )
            assert declared, f"{command} declares no tool requirements"

    def test_declared_tools_are_ones_capabilities_probes(self) -> None:
        # Otherwise `describe` could name a tool `capabilities` never reports
        # on, and a caller could not check for it.
        # No exemptions: a tool `describe` names but `capabilities` never
        # reports on is one a caller cannot check for before running.
        probed = set(_DEFAULT_TOOLS)
        for command, spec in _TOOL_REQUIREMENTS.items():
            named = set(spec.get("required_tools", [])) | set(
                spec.get("optional_tools", [])
            )
            unknown = named - probed
            assert not unknown, f"{command} names unprobed tools: {sorted(unknown)}"

    def test_describe_succeeds_and_emits_valid_json(self) -> None:
        # Asserting only that the output parses would pass on an error envelope.
        result = runner.invoke(app, ["describe", "performance", "--output", "json"])
        assert result.exit_code == 0, result.stdout
        payload = json.loads(result.stdout)
        assert payload["status"] == "ok"
