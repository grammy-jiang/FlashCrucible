"""Every command's `data` payload validates against its declared schema.

`cli_response.schema.json` constrains the envelope but leaves `data` as a
free-form object, so a caller could confirm a response *was* a CLIResponse
without being able to check it was a valid `quick-test` result, or discover
which keys to expect without reading the source.

A schema nobody validates against drifts, so these tests run the real commands
and check their output, rather than asserting the files merely parse -- which
`validate-schemas` already does.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from jsonschema import Draft7Validator
from typer.testing import CliRunner

from tfqa.cli.main import _collect_command_map, _result_schema_name, app
from tfqa.core import paths
from tfqa.core.models import CLIResponse, DeviceInfo

runner = CliRunner()

DEVICE = DeviceInfo(
    path="/dev/sdz",
    name="sdz",
    model="Model",
    vendor="Vendor",
    serial="SN",
    size_bytes=64 * 1024**3,
    is_removable=True,
    is_system_disk=False,
    mountpoints=[],
    transport="usb",
)

# Commands that need no device, run anywhere, and are safe to invoke directly.
READ_ONLY = [
    "detect",
    "capabilities",
    "profiles",
    "combos",
    "describe-schemas",
    "validate-schemas",
    "lint-schemas",
    "history",
    "trends",
]

# Device commands, exercised through --dry-run so nothing is written.
DEVICE_COMMANDS = [
    "quick-test",
    "performance",
    "surface-scan",
    "filesystem-check",
    "full-capacity-test",
    "workload-smallfiles",
    "endurance",
    "pipeline",
]


def _schema(command: str) -> Draft7Validator:
    name = _result_schema_name(command)
    assert name, f"{command} declares no result schema"
    document = json.loads((paths.DEFAULT_SCHEMAS_DIR / name).read_text())
    return Draft7Validator(document)


def _validate(command: str, data: dict[str, object]) -> None:
    errors = sorted(_schema(command).iter_errors(data), key=lambda e: list(e.path))
    assert not errors, "\n".join(
        f"{command}: {'/'.join(str(p) for p in e.path) or '<root>'}: {e.message}"
        for e in errors
    )


class TestSchemasAreDeclared:
    def test_every_command_that_ships_a_schema_links_to_it(self) -> None:
        for command in _collect_command_map():
            name = _result_schema_name(command)
            if name is None:
                continue
            assert (paths.DEFAULT_SCHEMAS_DIR / name).is_file()

    def test_the_device_commands_all_have_one(self) -> None:
        # These are the payloads automation acts on, so they are the ones that
        # most need to be checkable.
        missing = [c for c in DEVICE_COMMANDS if _result_schema_name(c) is None]
        assert not missing, f"no result schema for: {missing}"

    def test_only_command_groups_lack_a_schema(self) -> None:
        # `config` is a Typer group with no callback of its own; its
        # subcommands have their own schemas. Everything else must have one.
        without = {
            command
            for command in _collect_command_map()
            if _result_schema_name(command) is None
        }
        assert without == {"config"}, (
            f"commands with no result schema: {sorted(without)}"
        )

    def test_no_schema_file_is_orphaned(self) -> None:
        # A result schema for a command that no longer exists is a promise
        # nothing keeps.
        commands = {c.replace(" ", "-") for c in _collect_command_map()}
        for path in paths.DEFAULT_SCHEMAS_DIR.glob("*.result.schema.json"):
            stem = path.name.removesuffix(".result.schema.json")
            assert stem in commands, f"{path.name} describes no known command"


class TestRealOutputValidates:
    @pytest.mark.parametrize("command", READ_ONLY)
    def test_read_only_commands(self, command: str) -> None:
        result = runner.invoke(app, [command, "--output", "json"])
        assert result.exit_code == 0, result.stdout
        _validate(command, CLIResponse.model_validate_json(result.stdout).data)

    @pytest.mark.parametrize("command", DEVICE_COMMANDS)
    def test_device_commands_under_dry_run(self, command: str) -> None:
        extra = ["--image-path", __file__] if command == "image-flash" else []
        with patch("tfqa.core.devices.get_device", return_value=DEVICE):
            result = runner.invoke(
                app,
                [
                    "--dry-run",
                    command,
                    "--device",
                    DEVICE.path,
                    *extra,
                    "--output",
                    "json",
                ],
            )
        assert result.exit_code == 0, result.stdout
        _validate(command, CLIResponse.model_validate_json(result.stdout).data)

    def test_image_flash_under_dry_run(self) -> None:
        with patch("tfqa.core.devices.get_device", return_value=DEVICE):
            result = runner.invoke(
                app,
                [
                    "--dry-run",
                    "image-flash",
                    "--device",
                    DEVICE.path,
                    "--image-path",
                    __file__,
                    "--output",
                    "json",
                ],
            )
        assert result.exit_code == 0, result.stdout
        _validate("image-flash", CLIResponse.model_validate_json(result.stdout).data)

    def test_describe_validates_against_its_own_schema(self) -> None:
        result = runner.invoke(app, ["describe", "quick-test", "--output", "json"])
        assert result.exit_code == 0, result.stdout
        _validate("describe", CLIResponse.model_validate_json(result.stdout).data)

    def test_config_show(self) -> None:
        result = runner.invoke(app, ["config", "show", "--output", "json"])
        assert result.exit_code == 0, result.stdout
        _validate("config show", CLIResponse.model_validate_json(result.stdout).data)


class TestSchemasActuallyConstrain:
    """A schema that accepts anything is worse than none: it implies a check."""

    def test_detect_rejects_a_device_without_a_path(self) -> None:
        bad = {
            "devices": [
                {"size_bytes": 1, "is_removable": True, "is_system_disk": False}
            ]
        }
        assert list(_schema("detect").iter_errors(bad))

    def test_health_rejects_a_snapshot_without_availability(self) -> None:
        bad = {"snapshot": {"source": "sysfs", "cid": {}, "health": {}, "sources": {}}}
        assert list(_schema("health").iter_errors(bad))

    def test_capabilities_rejects_a_tool_without_availability(self) -> None:
        bad = {"external_tools": {"fio": {"name": "fio"}}}
        assert list(_schema("capabilities").iter_errors(bad))

    def test_profiles_rejects_an_entry_without_a_path(self) -> None:
        assert list(_schema("profiles").iter_errors({"profiles": [{"name": "x"}]}))

    def test_a_dry_run_plan_must_name_the_device(self) -> None:
        assert list(
            _schema("quick-test").iter_errors({"plan": {"free_space_only": True}})
        )
