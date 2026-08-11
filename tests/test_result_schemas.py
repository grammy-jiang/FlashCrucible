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
from pathlib import Path
from unittest.mock import patch

import pytest
from jsonschema import Draft7Validator
from typer.testing import CliRunner

from tfqa.cli.main import _collect_command_map, _result_schema_name, app
from tfqa.core import paths
from tfqa.core.errors import DeviceNotFoundError
from tfqa.core.models import DeviceInfo

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
    "status",
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


def _validate(command: str, response: dict[str, object]) -> None:
    """Validate the whole envelope: the shape of `data` depends on `status`."""
    errors = sorted(_schema(command).iter_errors(response), key=lambda e: list(e.path))
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
        _validate(command, json.loads(result.stdout))

    @pytest.mark.parametrize("command", DEVICE_COMMANDS)
    def test_device_commands_under_dry_run(self, command: str) -> None:
        with patch("tfqa.core.devices.get_device", return_value=DEVICE):
            result = runner.invoke(
                app,
                ["--dry-run", command, "--device", DEVICE.path, "--output", "json"],
            )
        assert result.exit_code == 0, result.stdout
        _validate(command, json.loads(result.stdout))

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
        _validate("image-flash", json.loads(result.stdout))

    def test_a_detached_start_validates(self, tmp_path: Path) -> None:
        # --detach returns before there is any result, so its payload is
        # neither the normal result nor a plan. The schema rejected the only
        # response a caller polling `tfqa status` ever sees.
        image = tmp_path / "dev.img"
        image.write_bytes(b"\0" * 4096)
        device = DEVICE.model_copy(update={"path": str(image), "size_bytes": 4096})
        with (
            patch("tfqa.core.devices.get_device", return_value=device),
            patch("subprocess.Popen") as popen,
        ):
            popen.return_value.pid = 4242
            result = runner.invoke(
                app,
                [
                    "--log-dir",
                    str(tmp_path),
                    "--yes",
                    "full-capacity-test",
                    "--device",
                    str(image),
                    "--force",
                    "--detach",
                    "--output",
                    "json",
                ],
            )
        assert result.exit_code == 0, result.stdout
        payload = json.loads(result.stdout)
        assert payload["data"]["detached"] is True
        _validate("full-capacity-test", payload)

    def test_describe_validates_against_its_own_schema(self) -> None:
        result = runner.invoke(app, ["describe", "quick-test", "--output", "json"])
        assert result.exit_code == 0, result.stdout
        _validate("describe", json.loads(result.stdout))

    def test_config_show(self) -> None:
        result = runner.invoke(app, ["config", "show", "--output", "json"])
        assert result.exit_code == 0, result.stdout
        _validate("config show", json.loads(result.stdout))


class TestErrorResponsesValidate:
    """Validation must not fail exactly when automation needs the envelope."""

    def test_config_validate(self) -> None:
        result = runner.invoke(app, ["config", "validate", "--output", "json"])
        assert result.exit_code == 0, result.stdout
        _validate("config validate", json.loads(result.stdout))

    def test_detect_reports_a_device_error(self) -> None:
        with patch(
            "tfqa.core.devices.discover_devices",
            side_effect=DeviceNotFoundError("/dev/nope"),
        ):
            result = runner.invoke(app, ["detect", "--output", "json"])
        payload = json.loads(result.stdout)
        assert payload["status"] == "error"
        _validate("detect", payload)

    def test_an_error_payload_may_be_empty(self) -> None:
        # An unexpected host failure emits data={}; the schema must accept it,
        # since requiring `devices` unconditionally rejected exactly the
        # responses automation most needs to inspect.
        _validate(
            "detect",
            {"status": "error", "command": "detect", "message": "boom", "data": {}},
        )


class TestSchemasActuallyConstrain:
    """A schema that accepts anything is worse than none: it implies a check."""

    @pytest.mark.parametrize("command", DEVICE_COMMANDS + ["detect", "health"])
    def test_an_empty_payload_is_not_a_successful_result(self, command: str) -> None:
        # With nothing required and additionalProperties open, `{}` validated
        # as a successful result for every engine command.
        assert list(
            _schema(command).iter_errors(
                {"status": "ok", "command": command, "message": "m", "data": {}}
            )
        )

    def test_the_wrapper_shape_is_modelled(self) -> None:
        # filesystem-check emits {"result": ...}; a schema advertising metrics
        # at the root validated only because unknown keys were accepted, and
        # taught discovery clients the wrong layout.
        good = {
            "status": "ok",
            "command": "filesystem-check",
            "message": "m",
            "data": {"result": {"status": "ok", "returncode": 0}},
        }
        assert not list(_schema("filesystem-check").iter_errors(good))
        bad = {**good, "data": {"metrics": {}}}
        assert list(_schema("filesystem-check").iter_errors(bad))

    def test_a_plan_device_must_be_a_string(self) -> None:
        assert list(
            _schema("quick-test").iter_errors(
                {
                    "status": "ok",
                    "command": "quick-test",
                    "message": "m",
                    "data": {"plan": {"device": {"path": "/dev/sdz"}}},
                }
            )
        )

    @pytest.mark.parametrize(
        "command,data",
        [
            (
                "detect",
                {
                    "devices": [
                        {"size_bytes": 1, "is_removable": True, "is_system_disk": False}
                    ]
                },
            ),
            (
                "health",
                {"snapshot": {"source": "s", "cid": {}, "health": {}, "sources": {}}},
            ),
            ("capabilities", {"external_tools": {"fio": {"name": "fio"}}}),
            ("profiles", {"profiles": [{"name": "x"}]}),
            ("quick-test", {"plan": {"free_space_only": True}}),
        ],
    )
    def test_malformed_success_payloads_are_rejected(
        self, command: str, data: dict[str, object]
    ) -> None:
        assert list(
            _schema(command).iter_errors(
                {"status": "ok", "command": command, "message": "m", "data": data}
            )
        )
